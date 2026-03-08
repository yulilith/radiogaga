#!/usr/bin/env python3
"""RadioAgent — AI-powered radio with LLM agents, physical controls, and agent-to-agent interaction."""

import argparse
import asyncio
import signal
import uuid
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from log import setup_logging, get_logger
from config import CONFIG
from hardware.input_controller import InputController, InputEvent
from hardware.led_controller import LEDController
from hardware.mic_controller import MicController
from hardware.nfc_controller import NFCController
from hardware.display_controller import DisplayController
from audio.tts_service import TTSService
from audio.stt_service import STTService
from audio.audio_player import AudioPlayer
from audio.spotify_service import SpotifyService
from audio.music_manager import MusicManager
from content.channels import CHANNELS, resolve_subchannel, get_subchannel_name
from content.daily_brief_channel import DailyBriefChannel
from content.talkshow_channel import TalkShowChannel
from content.music_channel import MusicChannel
from content.memos_channel import MemosChannel
from context.context_provider import ContextProvider
from network.discovery import AgentDiscovery
from network.peer_comm import (
    PeerServer, PeerClient,
    msg_cohost_prompt, msg_cohost_response, msg_callin_forward,
)

logger = get_logger("main")


class RadioAgent:
    """Main controller — wires together hardware, audio, content, and networking."""

    def __init__(self, channel: str = "news"):
        self.agent_id = str(uuid.uuid4())[:8]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._channel_tasks: dict[str, asyncio.Task] = {}
        self._warm_tasks: dict[str, asyncio.Task] = {}
        self._audio_consumer_task: asyncio.Task | None = None
        self._adc_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self.ALWAYS_ON_CHANNELS = {"talkshow", "dailybrief"}
        self.ON_DEMAND_CHANNELS = {"music", "memos"}

        # Context
        self.context = ContextProvider(CONFIG)

        # Audio
        self.tts = TTSService(
            elevenlabs_key=CONFIG["ELEVENLABS_API_KEY"],
            openai_key=CONFIG.get("OPENAI_API_KEY"),
            speed=CONFIG.get("TTS_SPEED", 1.1),
        )
        self.stt = STTService(
            deepgram_key=CONFIG.get("DEEPGRAM_API_KEY"),
            model=CONFIG.get("DEEPGRAM_MODEL", "nova-3"),
        )
        if not CONFIG.get("DEEPGRAM_API_KEY"):
            logger.warning("DEEPGRAM_API_KEY not set, call-in transcription is unavailable")
        self.player = AudioPlayer(
            radio_filter_strength=CONFIG.get("RADIO_FILTER_STRENGTH", 0.7),
        )

        # Spotify (optional)
        self.spotify = None
        if CONFIG.get("SPOTIFY_CLIENT_ID"):
            try:
                self.spotify = SpotifyService(
                    client_id=CONFIG["SPOTIFY_CLIENT_ID"],
                    client_secret=CONFIG["SPOTIFY_CLIENT_SECRET"],
                    redirect_uri=CONFIG.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback"),
                    playback_mode=CONFIG.get("SPOTIFY_PLAYBACK_MODE", "pi"),
                )
                logger.info("Spotify connected", extra={
                    "playback_mode": CONFIG.get("SPOTIFY_PLAYBACK_MODE", "pi"),
                })
            except Exception as e:
                logger.error("Spotify init failed: %s", e)

        self.music_manager = MusicManager()

        # Hardware
        self.leds = LEDController(CONFIG)
        self.mic = MicController(config=CONFIG, max_seconds=CONFIG.get("CALLIN_MAX_SECONDS", 15))
        self.nfc = NFCController(CONFIG)
        self.display = DisplayController(CONFIG)

        # Content channels
        self.channels = {
            "dailybrief": DailyBriefChannel(self.context, CONFIG),
            "talkshow": TalkShowChannel(self.context, CONFIG),
            "music": MusicChannel(self.context, CONFIG, self.spotify, self.music_manager),
            "memos": MemosChannel(self.context, CONFIG),
        }

        # State
        self.active_channel = channel
        self.active_subchannel = resolve_subchannel(channel, 0)

        # Networking (agent-to-agent)
        self.discovery = AgentDiscovery(self.agent_id, CONFIG.get("AGENT_PORT", 8765))
        self.peer_server = PeerServer(CONFIG.get("AGENT_PORT", 8765))
        self.peer_client = PeerClient()
        self._setup_peer_handlers()

        # Input controller (initialized last, starts emitting events)
        self.input = InputController(CONFIG, self._on_input_event)

    def _setup_peer_handlers(self):
        """Register handlers for incoming agent-to-agent messages."""
        self.peer_server.on("cohost_prompt", self._handle_cohost_prompt)
        self.peer_server.on("callin_forward", self._handle_callin_forward)
        self.peer_server.on("hello", self._handle_hello)

    async def _handle_cohost_prompt(self, data: dict) -> dict:
        """Another agent sent us a statement to respond to (co-host mode)."""
        statement = data.get("statement", "")
        channel_id = data.get("channel", "talkshow")

        channel = self.channels.get(channel_id)
        if hasattr(channel, "generate_cohost_response"):
            response_text = await channel.generate_cohost_response(
                statement, self.active_subchannel
            )
            voice_id = channel.get_cohost_voice_id() if hasattr(channel, "get_cohost_voice_id") else channel.get_voice_id("")
            self.player.start_static()
            audio = await self.tts.synthesize(response_text, voice_id)
            self.player.stop_static()
            self.player.enqueue_mp3(audio)

            return msg_cohost_response(response_text, voice_id)
        return {"type": "error", "message": "Channel doesn't support co-hosting"}

    async def _handle_callin_forward(self, data: dict) -> dict:
        """A caller from another radio is calling into our show."""
        transcript = data.get("transcript", "")
        logger.info("Remote caller says: %s", transcript)

        channel = self.channels.get(self.active_channel)
        async for chunk in channel.handle_callin(transcript):
            self.player.start_static()
            audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
            self.player.stop_static()
            self.player.enqueue_mp3(audio)

        return {"type": "ack"}

    async def _handle_hello(self, data: dict) -> dict:
        """Another agent introduced itself."""
        peer_id = data.get("agent_id", "?")
        logger.info("Peer %s says hello! Channel: %s", peer_id, data.get('current_channel'))
        return {"type": "hello", "agent_id": self.agent_id,
                "current_channel": self.active_channel}

    def _on_input_event(self, event: InputEvent):
        """Handle hardware input events (may be called from GPIO thread)."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._safe_handle_event(event), self._loop)

    async def _safe_handle_event(self, event: InputEvent):
        try:
            await self._handle_event(event)
        except Exception as e:
            logger.error("Unhandled exception in event handler: %s", e, exc_info=True)

    async def _handle_event(self, event: InputEvent):
        """Process an input event."""
        if event.event_type == "button_press":
            await self._switch_channel(event.channel)

        elif event.event_type == "dial_change":
            await self._tune_subchannel(event.subchannel)

        elif event.event_type == "volume_change":
            self.player.volume = event.volume / 100.0
            logger.info("Volume: %d%%", event.volume)
            self.display.update(
                channel=CHANNELS.get(self.active_channel, {}).get("name", self.active_channel),
                subchannel=get_subchannel_name(self.active_channel, self.active_subchannel),
                volume=event.volume,
            )

        elif event.event_type == "callin_start":
            self.leds.set_callin(True)
            self.mic.start_recording()

        elif event.event_type == "callin_stop":
            await self._handle_callin()

        elif event.event_type == "nfc_press":
            await self._handle_nfc()

    # ------------------------------------------------------------------
    # Audio consumer: reads from active channel queue -> TTS -> player
    # ------------------------------------------------------------------

    async def _audio_consumer(self):
        """Read chunks from the active channel's queue, synthesize, and play."""
        channel = self.channels[self.active_channel]
        logger.info("audio_consumer.started", extra={"channel": self.active_channel})
        try:
            warm = channel._warm_audio[:]
            channel._warm_audio.clear()
            if warm:
                logger.info("audio_consumer.playing_warm_cache",
                            extra={"channel": self.active_channel, "segments": len(warm)})
                self.player.stop_static()
            for audio in warm:
                self.player.enqueue_mp3(audio)

            while True:
                self.player.start_static()
                chunk = await channel._output_queue.get()

                if chunk.text:
                    audio_bytes = await self.tts.synthesize(chunk.text, chunk.voice_id)
                    self.player.stop_static()
                    self.player.enqueue_mp3(audio_bytes)

                if chunk.pause_after > 0:
                    await asyncio.sleep(chunk.pause_after)

                if chunk.play_music and os.path.exists(chunk.play_music):
                    self.player.stop_static()
                    self.player.play_file(chunk.play_music)
        except asyncio.CancelledError:
            logger.info("audio_consumer.cancelled")
        finally:
            self.player.stop_static()

    async def _restart_audio_consumer(self):
        """Cancel current audio consumer and flush the audio buffer."""
        if self._audio_consumer_task:
            self._audio_consumer_task.cancel()
            try:
                await self._audio_consumer_task
            except (asyncio.CancelledError, Exception):
                pass
        self.player.interrupt()

    def _drain_queue(self, channel_id: str):
        """Discard any stale chunks sitting in a channel's output queue."""
        q = self.channels[channel_id]._output_queue
        discarded = 0
        while not q.empty():
            try:
                q.get_nowait()
                discarded += 1
            except asyncio.QueueEmpty:
                break
        if discarded:
            logger.debug("Drained stale chunks", extra={"channel": channel_id, "count": discarded})

    # ------------------------------------------------------------------
    # Channel switching
    # ------------------------------------------------------------------

    async def _switch_channel(self, channel: str):
        """Switch to a different content channel."""
        if channel == self.active_channel:
            return

        old_id = self.active_channel
        logger.info("Switching to: %s", CHANNELS[channel]['name'])

        await self._restart_audio_consumer()
        self.player.start_static()

        old_ch = self.channels[old_id]
        await old_ch.on_deactivate()

        if old_id in self.ON_DEMAND_CHANNELS:
            await self._stop_on_demand_channel(old_id)

        old_ch.set_on_air(False)

        self.active_channel = channel
        self.active_subchannel = resolve_subchannel(channel, self.input.dial_position)
        self.channels[channel].set_subchannel(self.active_subchannel)
        self.leds.activate(channel)
        try:
            self.discovery.update_channel(channel)
        except Exception as e:
            logger.warning("Discovery update failed (non-fatal): %s", e)

        self.display.update(
            channel=CHANNELS[channel]["name"],
            subchannel=get_subchannel_name(channel, self.active_subchannel),
            volume=self.input.volume,
        )

        sfx_path = "assets/sfx/channel_switch.wav"
        if os.path.exists(sfx_path):
            self.player.play_file(sfx_path)

        new_ch = self.channels[channel]
        await new_ch.on_activate()

        if channel in self.ON_DEMAND_CHANNELS:
            self._start_on_demand_channel(channel)

        self._drain_queue(channel)
        new_ch.set_on_air(True)
        self._audio_consumer_task = asyncio.create_task(self._audio_consumer())

        if old_id in self.ON_DEMAND_CHANNELS:
            asyncio.create_task(self._warm_on_demand(old_id))

        await self._check_cohost()

    async def _tune_subchannel(self, subchannel: str):
        """Tune to a different subchannel within the current channel."""
        if subchannel == self.active_subchannel:
            return

        name = get_subchannel_name(self.active_channel, subchannel)
        logger.info("Tuning to: %s", name)

        await self._restart_audio_consumer()
        self.player.start_static()

        sfx_path = "assets/sfx/tuning_static.wav"
        if os.path.exists(sfx_path):
            self.player.play_file(sfx_path)

        self.active_subchannel = subchannel
        ch = self.channels[self.active_channel]
        ch.set_subchannel(subchannel)
        ch.interrupt()

        self._drain_queue(self.active_channel)
        self._audio_consumer_task = asyncio.create_task(self._audio_consumer())

        self.display.update(
            channel=CHANNELS[self.active_channel]["name"],
            subchannel=name,
            volume=self.input.volume,
        )

    async def _handle_callin(self):
        """Process a completed call-in recording."""
        self.leds.blink_callin()
        audio_bytes = self.mic.stop_recording()

        if not audio_bytes:
            self.leds.set_callin(False)
            return

        logger.info("Transcribing call-in...")
        try:
            transcript = await self.stt.transcribe(audio_bytes, format="wav")
        except Exception as e:
            logger.error("Call-in transcription failed: %s", e)
            self.leds.set_callin(False)
            return
        logger.info("Caller said: %s", transcript)
        self.leds.set_callin(False)

        if not transcript.strip():
            return

        peers = self.discovery.get_peers_on_channel(self.active_channel)
        if peers:
            peer = peers[0]
            logger.info("Forwarding call-in to peer %s", peer['agent_id'])
            await self.peer_client.send_to_peer(
                peer, msg_callin_forward(transcript, self.agent_id)
            )

        await self._restart_audio_consumer()

        channel = self.channels[self.active_channel]
        channel.interrupt(callin=transcript)

        self._drain_queue(self.active_channel)
        self._audio_consumer_task = asyncio.create_task(self._audio_consumer())

    async def _handle_nfc(self):
        """Read NFC tag and integrate its contents."""
        if not self.nfc.available:
            logger.info("NFC reader not available")
            return

        logger.info("Reading NFC tag...")
        text = self.nfc.read_tag(timeout=3.0)
        if not text:
            logger.info("No NFC tag found or tag empty")
            return

        logger.info("NFC tag content: %s", text[:100])

        # Add to memos channel
        memos = self.channels.get("memos")
        if hasattr(memos, "add_memo_from_nfc"):
            memos.add_memo_from_nfc(text)

        # Announce via TTS
        voice_id = CONFIG["VOICES"].get("memo_host", "pNInz6obpgDQGcFmaJgB")
        announcement = f"NFC tag received. Content saved to memos: {text[:80]}"
        self.player.start_static()
        audio = await self.tts.synthesize(announcement, voice_id)
        self.player.stop_static()
        self.player.enqueue_mp3(audio)

    def _start_always_on_channels(self):
        """Start background tasks for always-on channels (talkshow, dailybrief)."""
        for channel_id in self.ALWAYS_ON_CHANNELS:
            channel = self.channels[channel_id]
            subchannel = resolve_subchannel(channel_id, self.input.dial_position)
            channel.set_subchannel(subchannel)
            self._channel_tasks[channel_id] = asyncio.create_task(
                channel.run_background()
            )
            self._warm_tasks[channel_id] = asyncio.create_task(
                self._warm_producer(channel_id)
            )

    async def _warm_producer(self, channel_id: str):
        """Pre-synthesize ONE TTS segment for an off-air always-on channel.

        Only synthesizes when the warm cache is empty. Once a segment is
        cached, subsequent off-air chunks are discarded (LLM history still
        updates but we don't burn ElevenLabs credits on audio nobody hears).
        """
        channel = self.channels[channel_id]
        try:
            while True:
                chunk = await channel._warm_queue.get()
                if chunk.text and not channel._warm_audio:
                    try:
                        audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
                        channel._warm_audio = [audio]
                        logger.debug("warm_producer.cached", extra={"channel": channel_id})
                    except Exception as e:
                        logger.warning("warm_producer.synthesis_failed: %s", e,
                                       extra={"channel": channel_id})
        except asyncio.CancelledError:
            pass

    async def _warm_on_demand(self, channel_id: str):
        """Pre-generate and synthesize a warm preview for an on-demand channel."""
        channel = self.channels[channel_id]
        try:
            chunks = await channel.generate_warm_preview()
            for chunk in chunks:
                if chunk.text:
                    audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
                    channel._warm_audio = [audio]
                    logger.info("warm_on_demand.cached", extra={"channel": channel_id})
                    break
        except Exception as e:
            logger.warning("warm_on_demand.failed: %s", e, extra={"channel": channel_id})

    async def _warm_all_inactive(self):
        """At startup, warm all channels that are not the initial active channel."""
        tasks = []
        for channel_id in self.channels:
            if channel_id == self.active_channel:
                continue
            if channel_id in self.ON_DEMAND_CHANNELS:
                tasks.append(self._warm_on_demand(channel_id))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start_on_demand_channel(self, channel_id: str):
        """Start the background task for an on-demand channel."""
        channel = self.channels[channel_id]
        subchannel = resolve_subchannel(channel_id, self.input.dial_position)
        channel.set_subchannel(subchannel)
        self._channel_tasks[channel_id] = asyncio.create_task(
            channel.run_background()
        )

    async def _stop_on_demand_channel(self, channel_id: str):
        """Cancel the background task for an on-demand channel."""
        task = self._channel_tasks.pop(channel_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _check_cohost(self):
        """If a peer is on the same channel, initiate co-host mode."""
        peers = self.discovery.get_peers_on_channel(self.active_channel)
        if peers and self.active_channel == "talkshow":
            peer = peers[0]
            logger.info("Co-hosting with peer %s!", peer['agent_id'])

    async def run(self):
        """Main entry point — start everything and run until interrupted."""
        self._loop = asyncio.get_event_loop()

        # Show startup splash on e-ink
        self.display.show_startup()

        # Start audio playback -- static crackle from boot until first TTS audio
        self.player.start()
        self.player.start_static()

        # Start network services
        self.discovery.register(channel=self.active_channel)
        self.discovery.start_browsing(
            on_peer_found=lambda p: logger.info("Peer found: %s", p['agent_id']),
            on_peer_lost=lambda p: logger.info("Peer lost: %s", p['agent_id']),
        )
        await self.peer_server.start()

        # Set initial state
        self.leds.activate(self.active_channel)
        self.display.update(
            channel=CHANNELS[self.active_channel]["name"],
            subchannel=get_subchannel_name(self.active_channel, self.active_subchannel),
            volume=self.input.volume,
        )

        logger.info("=" * 50)
        logger.info("  RadioAgent %s is ON THE AIR", self.agent_id)
        logger.info("  Channel: %s", CHANNELS[self.active_channel]['name'])
        logger.info("=" * 50)

        self._start_always_on_channels()

        if self.active_channel in self.ON_DEMAND_CHANNELS:
            self._start_on_demand_channel(self.active_channel)

        self.channels[self.active_channel].set_on_air(True)
        self._audio_consumer_task = asyncio.create_task(self._audio_consumer())

        asyncio.create_task(self._warm_all_inactive())

        if self.input._use_gpio:
            self._adc_task = asyncio.create_task(self.input.start_adc_polling())

        # Start keyboard simulator if not on Pi
        if not self.input._use_gpio:
            async def _keyboard_then_stop():
                await self.input.run_keyboard_simulator()
                self._stop_event.set()
            keyboard_task = asyncio.create_task(_keyboard_then_stop())
        else:
            keyboard_task = None

        # Wait for shutdown signal
        self._sigint_count = 0

        def _signal_handler():
            self._sigint_count += 1
            if self._sigint_count == 1:
                logger.info("Ctrl+C received, shutting down gracefully...")
                self._stop_event.set()
            else:
                logger.warning("Force exit!")
                os._exit(1)

        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, _signal_handler)

        await self._stop_event.wait()
        await self.shutdown()

    async def shutdown(self):
        """Clean up all resources."""
        logger.info("Shutting down...")

        if self._audio_consumer_task:
            self._audio_consumer_task.cancel()
            try:
                await self._audio_consumer_task
            except (asyncio.CancelledError, Exception):
                pass

        for task in self._warm_tasks.values():
            task.cancel()
        for task in self._warm_tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._warm_tasks.clear()

        for task in self._channel_tasks.values():
            task.cancel()
        for task in self._channel_tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._channel_tasks.clear()

        if self._adc_task:
            self._adc_task.cancel()
            try:
                await self._adc_task
            except (asyncio.CancelledError, Exception):
                pass

        await self.peer_server.stop()
        self.discovery.shutdown()
        self.player.stop()
        self.mic.cleanup()
        self.nfc.cleanup()
        self.display.cleanup()
        self.leds.cleanup()
        self.input.cleanup()

        logger.info("RadioAgent signing off. Goodbye!")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RadioAgent — AI-powered radio")
    parser.add_argument(
        "-c", "--channel",
        choices=list(CHANNELS.keys()),
        default="dailybrief",
        help="channel to start on (default: dailybrief)",
    )
    return parser.parse_args()


def main():
    """Entry point."""
    args = parse_args()
    setup_logging()

    missing = []
    if not CONFIG.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not CONFIG.get("ELEVENLABS_API_KEY"):
        missing.append("ELEVENLABS_API_KEY")

    if missing:
        logger.error("Missing required API keys: %s", ", ".join(missing))
        logger.error("Copy .env.example to .env and fill in your keys.")
        sys.exit(1)

    agent = RadioAgent(channel=args.channel)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
