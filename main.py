#!/usr/bin/env python3
"""RadioAgent — AI-powered radio with LLM agents, physical controls, and agent-to-agent interaction."""

import argparse
import asyncio
import hashlib
import signal
import uuid
import os
import sys
from dataclasses import dataclass

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
from content.agent import PreparedPreview
from content.session_memory import SessionMemory
from context.context_provider import ContextProvider
from network.discovery import AgentDiscovery
from network.peer_comm import (
    PeerServer, PeerClient,
    msg_cohost_prompt, msg_cohost_response, msg_callin_forward,
)

logger = get_logger("main")


@dataclass(slots=True)
class PreviewEntry:
    channel: str
    subchannel: str
    identity: str
    preview: PreparedPreview
    audio_bytes: bytes


class RadioAgent:
    """Main controller — wires together hardware, audio, content, and networking."""

    def __init__(self, channel: str = "news"):
        self.agent_id = str(uuid.uuid4())[:8]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._generation_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self.session_memory = SessionMemory()
        self._transition_lock: asyncio.Lock | None = None
        self._transition_request_id = 0
        self._producer_tasks: set[asyncio.Task] = set()
        self._preview_cache: dict[tuple[str, str], PreviewEntry] = {}
        self._preview_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._callin_active = False

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
        self.player = AudioPlayer()

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
        self.mic = MicController(max_seconds=CONFIG.get("CALLIN_MAX_SECONDS", 15))

        # Content channels
        self.channels = {
            "news": NewsChannel(self.context, CONFIG),
            "talkshow": TalkShowChannel(self.context, CONFIG),
            "sports": SportsChannel(self.context, CONFIG),
            "dj": DJChannel(self.context, CONFIG, self.spotify, self.music_manager),
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
        self._ensure_runtime_state()

    def _setup_peer_handlers(self):
        """Register handlers for incoming agent-to-agent messages."""
        self.peer_server.on("cohost_prompt", self._handle_cohost_prompt)
        self.peer_server.on("callin_forward", self._handle_callin_forward)
        self.peer_server.on("hello", self._handle_hello)

    def _ensure_runtime_state(self):
        if getattr(self, "session_memory", None) is None:
            self.session_memory = SessionMemory()
        if getattr(self, "_transition_lock", None) is None:
            self._transition_lock = asyncio.Lock()
        if not hasattr(self, "_producer_tasks") or self._producer_tasks is None:
            self._producer_tasks = set()
        if not hasattr(self, "_preview_cache") or self._preview_cache is None:
            self._preview_cache = {}
        if not hasattr(self, "_preview_tasks") or self._preview_tasks is None:
            self._preview_tasks = {}
        if not hasattr(self, "_callin_active"):
            self._callin_active = False

        for channel in getattr(self, "channels", {}).values():
            if hasattr(channel, "set_session_memory"):
                channel.set_session_memory(self.session_memory)

    def _current_generation(self) -> int:
        return getattr(self.player, "current_generation", 0)

    def _track_task(
        self,
        task: asyncio.Task,
        registry: set[asyncio.Task] | None = None,
    ) -> asyncio.Task:
        if registry is None:
            registry = self._producer_tasks
        registry.add(task)
        task.add_done_callback(lambda done: registry.discard(done))
        return task

    def _register_current_task(self):
        task = asyncio.current_task()
        if task and task is not self._generation_task:
            self._track_task(task)

    def _cancel_producer_tasks(self):
        current_task = asyncio.current_task()
        for task in list(self._producer_tasks):
            if task.done() or task is current_task:
                continue
            task.cancel()

    async def _await_interrupted_work(self, previous_task: asyncio.Task | None):
        current_task = asyncio.current_task()
        pending = []
        if previous_task and previous_task is not current_task:
            pending.append(previous_task)
        for task in list(self._producer_tasks):
            if task.done() or task is current_task:
                continue
            pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _resolve_target_subchannel(self, channel: str) -> str:
        dial_position = getattr(self.input, "dial_position", 0)
        return resolve_subchannel(channel, dial_position)

    def _preview_key(self, channel: str, subchannel: str) -> tuple[str, str]:
        return (channel, subchannel)

    def _schedule_preview_warm(self, channel: str, subchannel: str):
        self._ensure_runtime_state()
        if channel not in self.channels or not subchannel:
            return

        key = self._preview_key(channel, subchannel)
        task = self._preview_tasks.get(key)
        if task and not task.done():
            return

        warm_task = asyncio.create_task(self._warm_preview(channel, subchannel))
        self._preview_tasks[key] = warm_task
        warm_task.add_done_callback(lambda _: self._preview_tasks.pop(key, None))

    def _schedule_startup_preview_warms(self):
        for channel_id in self.channels:
            if channel_id == self.active_channel:
                continue
            self._schedule_preview_warm(
                channel_id,
                self._resolve_target_subchannel(channel_id),
            )

    async def _warm_preview(self, channel: str, subchannel: str):
        key = self._preview_key(channel, subchannel)
        channel_obj = self.channels.get(channel)
        if not channel_obj:
            return

        try:
            preview = await channel_obj.build_preview(subchannel)
            if not preview or not preview.text.strip():
                self._preview_cache.pop(key, None)
                return

            audio_bytes = await self.tts.synthesize(preview.text, preview.voice_id)
            identity = hashlib.sha1(
                f"{preview.voice_id}\n{preview.text}".encode("utf-8")
            ).hexdigest()
            self._preview_cache[key] = PreviewEntry(
                channel=channel,
                subchannel=subchannel,
                identity=identity,
                preview=preview,
                audio_bytes=audio_bytes,
            )
            logger.info(
                "preview warmed",
                extra={"channel": channel, "subchannel": subchannel, "identity": identity[:12]},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "preview warm failed: %s",
                exc,
                extra={"channel": channel, "subchannel": subchannel},
            )

    async def _play_cached_preview(
        self,
        channel: str,
        subchannel: str,
        generation: int,
    ) -> bool:
        entry = self._preview_cache.get(self._preview_key(channel, subchannel))
        if not entry or generation != self._current_generation():
            return False

        started = asyncio.Event()

        def _on_start():
            def _commit():
                self.session_memory.commit_heard(channel, subchannel, entry.preview.text)
                channel_obj = self.channels.get(channel)
                if channel_obj:
                    channel_obj.commit_preview_playback(subchannel, entry.preview)
                started.set()

            if self._loop:
                self._loop.call_soon_threadsafe(_commit)
            else:
                _commit()

        enqueued = self.player.enqueue_mp3(
            entry.audio_bytes,
            generation=generation,
            on_start=_on_start,
        )
        if not enqueued:
            return False

        try:
            await asyncio.wait_for(started.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.debug(
                "preview start confirmation timed out",
                extra={"channel": channel, "subchannel": subchannel},
            )
        return True

    def _make_heard_callback(self, channel: str, subchannel: str, text: str):
        def _on_start():
            def _commit():
                self.session_memory.commit_heard(channel, subchannel, text)

            if self._loop:
                self._loop.call_soon_threadsafe(_commit)
            else:
                _commit()

        return _on_start

    async def _interrupt_current_playback(self, reason: str) -> tuple[int, asyncio.Task | None]:
        self._ensure_runtime_state()
        generation = self.player.hard_stop(reason)

        current_channel = self.channels.get(self.active_channel)
        if current_channel:
            current_channel.cancel()

        previous_task = self._generation_task
        if previous_task:
            previous_task.cancel()

        self._cancel_producer_tasks()
        return generation, previous_task

    async def _pause_dj_if_needed(self, previous_channel: str):
        if previous_channel != "dj" or not self.spotify:
            return

        try:
            await self.spotify.pause()
        except Exception as exc:
            logger.warning("Spotify pause failed during switch: %s", exc)

    async def _transition_to(
        self,
        *,
        channel: str,
        subchannel: str,
        reason: str,
        sfx_path: str | None = None,
    ):
        self._ensure_runtime_state()
        self._transition_request_id += 1
        request_id = self._transition_request_id
        generation, previous_task = await self._interrupt_current_playback(reason)

        async with self._transition_lock:
            if request_id != self._transition_request_id:
                return

            previous_channel = self.active_channel
            previous_subchannel = self.active_subchannel
            await self._await_interrupted_work(previous_task)

            if request_id != self._transition_request_id:
                return

            await self._pause_dj_if_needed(previous_channel)

            if request_id != self._transition_request_id:
                return

            self.active_channel = channel
            self.active_subchannel = subchannel
            self.session_memory.record_switch(
                previous_channel,
                previous_subchannel,
                channel,
                subchannel,
            )

            if channel != previous_channel:
                self.leds.activate(channel)
                try:
                    self.discovery.update_channel(channel)
                except Exception as e:
                    logger.warning("Discovery update failed (non-fatal): %s", e)

            new_channel = self.channels[channel]
            new_channel.reset()

            if not self._callin_active and sfx_path and os.path.exists(sfx_path):
                self.player.play_file(sfx_path, generation=generation)

            if self._callin_active:
                self._generation_task = None
            else:
                self._generation_task = asyncio.create_task(
                    self._content_loop(channel, subchannel, generation)
                )

            if channel != previous_channel and not self._callin_active:
                await self._check_cohost()

            self._schedule_preview_warm(previous_channel, previous_subchannel)

    async def _start_callin_recording(self):
        self._ensure_runtime_state()
        if self._callin_active or self.mic.is_recording:
            return

        self.leds.set_callin(True)
        self._callin_active = True
        _, previous_task = await self._interrupt_current_playback("callin:start")
        self._generation_task = None
        await self._pause_dj_if_needed(self.active_channel)
        self.mic.start_recording()

        if not self.mic.is_recording:
            self._callin_active = False
            self.leds.set_callin(False)
            await self._await_interrupted_work(previous_task)
            await self._resume_content_after_callin()
            return

        await self._await_interrupted_work(previous_task)

    async def _resume_content_after_callin(self):
        self._ensure_runtime_state()
        if self._callin_active or self.mic.is_recording:
            return

        if self._generation_task and not self._generation_task.done():
            return

        channel = self.channels.get(self.active_channel)
        if not channel:
            return

        channel.reset()
        generation = self._current_generation()
        self._generation_task = asyncio.create_task(
            self._content_loop(
                self.active_channel,
                self.active_subchannel,
                generation,
                play_preview=False,
            )
        )

    async def _handle_cohost_prompt(self, data: dict) -> dict:
        """Another agent sent us a statement to respond to (co-host mode)."""
        self._ensure_runtime_state()
        self._register_current_task()
        statement = data.get("statement", "")
        channel_id = data.get("channel", "talkshow")
        generation = self._current_generation()

        channel = self.channels.get(channel_id)
        if hasattr(channel, "generate_cohost_response"):
            response_text = await channel.generate_cohost_response(
                statement, self.active_subchannel
            )
            # Play the response locally
            voice_id = channel.get_cohost_voice_id() if hasattr(channel, "get_cohost_voice_id") else channel.get_voice_id("")
            if generation != self._current_generation():
                return msg_cohost_response(response_text, voice_id)

            audio = await self.tts.synthesize(response_text, voice_id)
            if generation == self._current_generation():
                self.player.enqueue_mp3(
                    audio,
                    generation=generation,
                    on_start=self._make_heard_callback(channel_id, self.active_subchannel, response_text),
                )

            return msg_cohost_response(response_text, voice_id)
        return {"type": "error", "message": "Channel doesn't support co-hosting"}

    async def _handle_callin_forward(self, data: dict) -> dict:
        """A caller from another radio is calling into our show."""
        self._ensure_runtime_state()
        self._register_current_task()
        transcript = data.get("transcript", "")
        logger.info("Remote caller says: %s", transcript)
        generation = self._current_generation()

        channel = self.channels.get(self.active_channel)
        async for chunk in channel.handle_callin(transcript):
            if generation != self._current_generation():
                break
            audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
            if generation != self._current_generation():
                break
            self.player.enqueue_mp3(
                audio,
                generation=generation,
                on_start=self._make_heard_callback(self.active_channel, self.active_subchannel, chunk.text),
            )

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
            await self._start_callin_recording()

        elif event.event_type == "callin_stop":
            if self._callin_active or self.mic.is_recording:
                self._track_task(asyncio.create_task(self._handle_callin()))

    async def _switch_channel(self, channel: str):
        """Switch to a different content channel."""
        self._ensure_runtime_state()
        if channel == self.active_channel:
            return

        logger.info("Switching to: %s", CHANNELS[channel]['name'])
        await self._transition_to(
            channel=channel,
            subchannel=self._resolve_target_subchannel(channel),
            reason=f"switch:{self.active_channel}->{channel}",
            sfx_path="assets/sfx/channel_switch.wav",
        )

    async def _tune_subchannel(self, subchannel: str):
        """Tune to a different subchannel within the current channel."""
        self._ensure_runtime_state()
        if subchannel == self.active_subchannel:
            return

        name = get_subchannel_name(self.active_channel, subchannel)
        logger.info("Tuning to: %s", name)
        await self._transition_to(
            channel=self.active_channel,
            subchannel=subchannel,
            reason=f"tune:{self.active_channel}/{self.active_subchannel}->{subchannel}",
            sfx_path="assets/sfx/tuning_static.wav",
        )

    async def _handle_callin(self):
        """Process a completed call-in recording."""
        self._ensure_runtime_state()
        self._register_current_task()
        if not self._callin_active and not self.mic.is_recording:
            return

        self.leds.blink_callin()
        audio_bytes = self.mic.stop_recording()
        self._callin_active = False

        if not audio_bytes:
            self.leds.set_callin(False)
            await self._resume_content_after_callin()
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
            await self._resume_content_after_callin()
            return

        generation = self._current_generation()

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
            if generation != self._current_generation():
                break
            audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
            if generation != self._current_generation():
                break
            self.player.enqueue_mp3(
                audio,
                generation=generation,
                on_start=self._make_heard_callback(self.active_channel, self.active_subchannel, chunk.text),
            )

        if generation == self._current_generation():
            await self._resume_content_after_callin()

    async def _content_loop(
        self,
        channel_id: str | None = None,
        subchannel: str | None = None,
        generation: int | None = None,
        play_preview: bool = True,
    ):
        """Continuously generate and play content for the current channel."""
        self._ensure_runtime_state()
        channel_id = channel_id or self.active_channel
        subchannel = subchannel or self.active_subchannel
        generation = self._current_generation() if generation is None else generation
        channel = self.channels[channel_id]

        try:
            if play_preview:
                await self._play_cached_preview(channel_id, subchannel, generation)

            async for chunk in channel.stream_content(subchannel):
                if generation != self._current_generation():
                    return

                if chunk.text:
                    audio_bytes = await self.tts.synthesize(chunk.text, chunk.voice_id)
                    if generation != self._current_generation():
                        return
                    self.player.enqueue_mp3(
                        audio_bytes,
                        generation=generation,
                        on_start=self._make_heard_callback(channel_id, subchannel, chunk.text),
                    )

                if chunk.pause_after > 0:
                    await asyncio.sleep(chunk.pause_after)

                if generation != self._current_generation():
                    return

                if chunk.play_music and os.path.exists(chunk.play_music):
                    self.player.play_file(
                        chunk.play_music,
                        generation=generation,
                    )

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
        self._ensure_runtime_state()

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
        self._generation_task = asyncio.create_task(
            self._content_loop(
                self.active_channel,
                self.active_subchannel,
                self._current_generation(),
            )
        )
        self._schedule_startup_preview_warms()

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
        self._ensure_runtime_state()

        self._cancel_producer_tasks()
        for task in list(self._preview_tasks.values()):
            task.cancel()

        if self._generation_task:
            self._generation_task.cancel()
            try:
                await self._generation_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._producer_tasks or self._preview_tasks:
            await asyncio.gather(
                *list(self._producer_tasks),
                *list(self._preview_tasks.values()),
                return_exceptions=True,
            )

        await self.peer_server.stop()
        self.discovery.shutdown()
        self.player.stop()
        self.mic.cleanup()
        self.leds.cleanup()
        self.input.cleanup()

        logger.info("RadioAgent signing off. Goodbye!")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RadioAgent — AI-powered radio")
    parser.add_argument(
        "-c", "--channel",
        choices=list(CHANNELS.keys()),
        default="news",
        help="channel to start on (default: news)",
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
