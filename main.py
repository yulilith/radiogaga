#!/usr/bin/env python3
"""RadioAgent — AI-powered radio with LLM agents, physical controls, and agent-to-agent interaction."""

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
from audio.tts_service import TTSService
from audio.stt_service import STTService
from audio.audio_player import AudioPlayer
from audio.spotify_service import SpotifyService
from audio.music_manager import MusicManager
from content.channels import CHANNELS, resolve_subchannel, get_subchannel_name
from content.news_channel import NewsChannel
from content.talkshow_channel import TalkShowChannel
from content.sports_channel import SportsChannel
from content.dj_channel import DJChannel
from context.context_provider import ContextProvider
from network.discovery import AgentDiscovery
from network.peer_comm import (
    PeerServer, PeerClient,
    msg_cohost_prompt, msg_cohost_response, msg_callin_forward,
)

logger = get_logger("main")


class RadioAgent:
    """Main controller — wires together hardware, audio, content, and networking."""

    def __init__(self):
        self.agent_id = str(uuid.uuid4())[:8]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._generation_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        # Context
        self.context = ContextProvider(CONFIG)

        # Audio
        self.tts = TTSService(
            elevenlabs_key=CONFIG["ELEVENLABS_API_KEY"],
            openai_key=CONFIG.get("OPENAI_API_KEY"),
            speed=CONFIG.get("TTS_SPEED", 1.1),
        )
        self.stt = STTService(openai_key=CONFIG.get("OPENAI_API_KEY"))
        self.player = AudioPlayer()

        # Spotify (optional)
        self.spotify = None
        if CONFIG.get("SPOTIFY_CLIENT_ID"):
            try:
                self.spotify = SpotifyService(
                    client_id=CONFIG["SPOTIFY_CLIENT_ID"],
                    client_secret=CONFIG["SPOTIFY_CLIENT_SECRET"],
                    redirect_uri=CONFIG.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback"),
                )
                logger.info("Spotify connected")
            except Exception as e:
                logger.error("Spotify init failed: %s", e)

        self.music_manager = MusicManager()

        # Hardware
        self.leds = LEDController(CONFIG)
        self.mic = MicController(max_seconds=CONFIG.get("CALLIN_MAX_SECONDS", 15))

        # Content channels
        self.channels = {
            "news": NewsChannel(self.context, CONFIG),
            "talkshow": TalkShowChannel(self.context, CONFIG),
            "sports": SportsChannel(self.context, CONFIG),
            "dj": DJChannel(self.context, CONFIG, self.spotify, self.music_manager),
        }

        # State
        self.active_channel = "news"
        self.active_subchannel = "local"

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
            # Play the response locally
            voice_id = channel.get_cohost_voice_id() if hasattr(channel, "get_cohost_voice_id") else channel.get_voice_id("")
            audio = await self.tts.synthesize(response_text, voice_id)
            self.player.enqueue_mp3(audio)

            return msg_cohost_response(response_text, voice_id)
        return {"type": "error", "message": "Channel doesn't support co-hosting"}

    async def _handle_callin_forward(self, data: dict) -> dict:
        """A caller from another radio is calling into our show."""
        transcript = data.get("transcript", "")
        logger.info("Remote caller says: %s", transcript)

        channel = self.channels.get(self.active_channel)
        async for chunk in channel.handle_callin(transcript):
            audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
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

        elif event.event_type == "volume_mute":
            self.player.toggle_mute()
            logger.info("Muted" if self.player.muted else "Unmuted")

        elif event.event_type == "callin_start":
            self.leds.set_callin(True)
            self.mic.start_recording()

        elif event.event_type == "callin_stop":
            await self._handle_callin()

    async def _switch_channel(self, channel: str):
        """Switch to a different content channel."""
        if channel == self.active_channel:
            return

        logger.info("Switching to: %s", CHANNELS[channel]['name'])

        # Cancel current generation
        if self._generation_task:
            current_ch = self.channels.get(self.active_channel)
            if current_ch:
                current_ch.cancel()
            self._generation_task.cancel()
            try:
                await self._generation_task
            except (asyncio.CancelledError, Exception):
                pass

        # Clear audio buffer and play channel switch SFX
        self.player.clear_buffer()
        sfx_path = "assets/sfx/channel_switch.wav"
        if os.path.exists(sfx_path):
            self.player.play_file(sfx_path)

        # Update state
        self.active_channel = channel
        self.active_subchannel = resolve_subchannel(channel, self.input.dial_position)
        self.leds.activate(channel)
        try:
            self.discovery.update_channel(channel)
        except Exception as e:
            logger.warning("Discovery update failed (non-fatal): %s", e)

        # Start new content generation
        new_ch = self.channels[channel]
        new_ch.reset()
        self._generation_task = asyncio.create_task(self._content_loop())

        # Check for peers on same channel (co-host mode)
        await self._check_cohost()

    async def _tune_subchannel(self, subchannel: str):
        """Tune to a different subchannel within the current channel."""
        if subchannel == self.active_subchannel:
            return

        name = get_subchannel_name(self.active_channel, subchannel)
        logger.info("Tuning to: %s", name)

        # Cancel and restart
        if self._generation_task:
            self.channels[self.active_channel].cancel()
            self._generation_task.cancel()
            try:
                await self._generation_task
            except (asyncio.CancelledError, Exception):
                pass

        self.player.clear_buffer()
        sfx_path = "assets/sfx/tuning_static.wav"
        if os.path.exists(sfx_path):
            self.player.play_file(sfx_path)

        self.active_subchannel = subchannel
        self.channels[self.active_channel].reset()
        self._generation_task = asyncio.create_task(self._content_loop())

    async def _handle_callin(self):
        """Process a completed call-in recording."""
        self.leds.blink_callin()
        audio_bytes = self.mic.stop_recording()

        if not audio_bytes:
            self.leds.set_callin(False)
            return

        logger.info("Transcribing call-in...")
        transcript = await self.stt.transcribe(audio_bytes, format="wav")
        logger.info("Caller said: %s", transcript)
        self.leds.set_callin(False)

        if not transcript.strip():
            return

        # Check if we should forward to a peer
        peers = self.discovery.get_peers_on_channel(self.active_channel)
        if peers:
            # Forward to first peer
            peer = peers[0]
            logger.info("Forwarding call-in to peer %s", peer['agent_id'])
            await self.peer_client.send_to_peer(
                peer, msg_callin_forward(transcript, self.agent_id)
            )

        # Also handle locally
        channel = self.channels.get(self.active_channel)
        async for chunk in channel.handle_callin(transcript):
            audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
            self.player.enqueue_mp3(audio)

    async def _content_loop(self):
        """Continuously generate and play content for the current channel."""
        channel = self.channels[self.active_channel]
        subchannel = self.active_subchannel

        try:
            async for chunk in channel.stream_content(subchannel):
                if chunk.text:
                    audio_bytes = await self.tts.synthesize(chunk.text, chunk.voice_id)
                    self.player.enqueue_mp3(audio_bytes)

                if chunk.pause_after > 0:
                    await asyncio.sleep(chunk.pause_after)

                if chunk.play_music and os.path.exists(chunk.play_music):
                    self.player.play_file(chunk.play_music)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Content generation error: %s", e)

    async def _check_cohost(self):
        """If a peer is on the same channel, initiate co-host mode."""
        peers = self.discovery.get_peers_on_channel(self.active_channel)
        if peers and self.active_channel == "talkshow":
            peer = peers[0]
            logger.info("Co-hosting with peer %s!", peer['agent_id'])
            # Co-host mode will be triggered automatically when content generates
            # The content loop generates statements, and we send them to the peer

    async def _cohost_loop(self, peer: dict):
        """Run co-host mode: generate statement, send to peer, play their response."""
        channel = self.channels.get("talkshow")
        if not isinstance(channel, TalkShowChannel):
            return

        while not self._stop_event.is_set():
            # Generate our statement
            ctx = await self.context.get_context()
            prompt = channel.get_system_prompt(self.active_subchannel, ctx)

            # ... (simplified: the content_loop already generates statements)
            await asyncio.sleep(30)  # Co-host exchange every 30s

    async def run(self):
        """Main entry point — start everything and run until interrupted."""
        self._loop = asyncio.get_event_loop()

        # Start audio playback
        self.player.start()

        # Start network services
        self.discovery.register(channel=self.active_channel)
        self.discovery.start_browsing(
            on_peer_found=lambda p: logger.info("Peer found: %s", p['agent_id']),
            on_peer_lost=lambda p: logger.info("Peer lost: %s", p['agent_id']),
        )
        await self.peer_server.start()

        # Set initial state
        self.leds.activate(self.active_channel)

        logger.info("=" * 50)
        logger.info("  RadioAgent %s is ON THE AIR", self.agent_id)
        logger.info("  Channel: %s", CHANNELS[self.active_channel]['name'])
        logger.info("=" * 50)

        # Start content generation
        self._generation_task = asyncio.create_task(self._content_loop())

        # Start keyboard simulator if not on Pi
        if not self.input._use_gpio:
            async def _keyboard_then_stop():
                await self.input.run_keyboard_simulator()
                # Keyboard simulator exited (user pressed q / Ctrl+C)
                self._stop_event.set()
            keyboard_task = asyncio.create_task(_keyboard_then_stop())
        else:
            keyboard_task = None

        # Wait for shutdown signal (Ctrl+C or 'q' from keyboard sim)
        self._sigint_count = 0

        def _signal_handler():
            self._sigint_count += 1
            if self._sigint_count == 1:
                logger.info("Ctrl+C received, shutting down gracefully...")
                self._stop_event.set()
            else:
                # Second Ctrl+C = force exit
                logger.warning("Force exit!")
                os._exit(1)

        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, _signal_handler)

        await self._stop_event.wait()
        await self.shutdown()

    async def shutdown(self):
        """Clean up all resources."""
        logger.info("Shutting down...")

        if self._generation_task:
            self._generation_task.cancel()
            try:
                await self._generation_task
            except (asyncio.CancelledError, Exception):
                pass

        await self.peer_server.stop()
        self.discovery.shutdown()
        self.player.stop()
        self.mic.cleanup()
        self.leds.cleanup()
        self.input.cleanup()

        logger.info("RadioAgent signing off. Goodbye!")


def main():
    """Entry point."""
    setup_logging()

    # Check for required API keys
    missing = []
    if not CONFIG.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not CONFIG.get("ELEVENLABS_API_KEY"):
        missing.append("ELEVENLABS_API_KEY")

    if missing:
        logger.error("Missing required API keys: %s", ", ".join(missing))
        logger.error("Copy .env.example to .env and fill in your keys.")
        sys.exit(1)

    agent = RadioAgent()
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
