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

from log import setup_logging, get_logger, TranscriptLogger
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
from content.personas import PERSONA_REGISTRY, DEFAULT_SLOTS, SLOT_CHANNELS
from content.agent import PreparedPreview
from content.session_memory import SessionMemory
from context.context_provider import ContextProvider
from context.exa_search import ExaSearchService
from network.discovery import AgentDiscovery
from network.peer_comm import (
    PeerServer, PeerClient,
    msg_cohost_prompt, msg_cohost_response, msg_callin_forward,
    msg_status_update, msg_status_request,
)
from network.friends import FriendsTracker

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

    def __init__(self, channel: str = "music", agent_name: str | None = None):
        self.agent_id = str(uuid.uuid4())[:8]
        self.agent_name = agent_name or f"Radio-{self.agent_id}"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._channel_tasks: dict[str, asyncio.Task] = {}
        self._warm_tasks: dict[str, asyncio.Task] = {}
        self._audio_consumer_task: asyncio.Task | None = None
        self._adc_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._dry_run = CONFIG.get("DEBUG_LLM_WITHOUT_VOICE", False)
        self._transcript = TranscriptLogger()

        self.ALWAYS_ON_CHANNELS = {"talkshow", "dailybrief"}
        self.ON_DEMAND_CHANNELS = {"music", "memos"}

        self.session_memory = SessionMemory()
        self._transition_lock: asyncio.Lock | None = None
        self._transition_request_id = 0
        self._producer_tasks: set[asyncio.Task] = set()
        self._preview_cache: dict[tuple[str, str], PreviewEntry] = {}
        self._preview_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._callin_active = False
        self.friends = FriendsTracker()
        self._friends_broadcast_task: asyncio.Task | None = None

        if self._dry_run:
            logger.info("DRY-RUN MODE: TTS and audio playback disabled")

        # Context
        self.context = ContextProvider(CONFIG)

        # Audio (skipped in dry-run)
        self.tts = None
        self.stt = None
        self.player = None
        if not self._dry_run:
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

        # Search
        self.exa = ExaSearchService(CONFIG.get("EXA_API_KEY"))

        # 3-slot persona system
        self._persona_slots = [PERSONA_REGISTRY[pid] for pid in DEFAULT_SLOTS]

        # Content channels — each solo channel gets its slot persona
        self.channels = {
            "dailybrief": DailyBriefChannel(self.context, CONFIG, persona=self._persona_slots[0]),
            "talkshow": TalkShowChannel(self.context, CONFIG, exa_service=self.exa),
            "music": MusicChannel(self.context, CONFIG, self.spotify, self.music_manager, persona=self._persona_slots[1]),
            "memos": MemosChannel(self.context, CONFIG, persona=self._persona_slots[2]),
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
        self.peer_server.on("status_update", self._handle_status_update)
        self.peer_server.on("status_request", self._handle_status_request)

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
            if hasattr(channel, "set_friends_tracker"):
                channel.set_friends_tracker(self.friends)

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
        if previous_channel not in ("dj", "music") or not self.spotify:
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
            voice_id = channel.get_cohost_voice_id() if hasattr(channel, "get_cohost_voice_id") else channel.get_voice_id("")
            if self._dry_run:
                self._transcript.log_chunk(self.active_channel, self.active_subchannel, voice_id, "cohost", response_text)
                logger.info("[DRY-RUN] cohost: %s", response_text[:120])
            elif generation != self._current_generation():
                pass
            else:
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
            if self._dry_run:
                self._transcript.log_chunk(self.active_channel, self.active_subchannel, chunk.voice_id, "callin_forward", chunk.text)
                logger.info("[DRY-RUN] callin_fwd: %s", chunk.text[:120])
            else:
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
        peer_name = data.get("agent_name", peer_id)
        logger.info("Peer %s says hello! Channel: %s", peer_id, data.get('current_channel'))
        # Record them as a friend with their current activity
        is_new = self.friends.update(
            agent_id=peer_id,
            agent_name=peer_name,
            channel=data.get("current_channel", "unknown"),
            subchannel=data.get("subchannel", ""),
            activity=data.get("activity", "just connected"),
        )
        if is_new:
            self._schedule_friend_announcement(peer_name)
        return {"type": "hello", "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "current_channel": self.active_channel}

    async def _handle_status_update(self, data: dict) -> dict:
        """A peer sent a status update about what they're doing."""
        is_new = self.friends.update(
            agent_id=data.get("agent_id", "?"),
            agent_name=data.get("agent_name", "Unknown"),
            channel=data.get("channel", "unknown"),
            subchannel=data.get("subchannel", ""),
            activity=data.get("activity", ""),
        )
        if is_new:
            self._schedule_friend_announcement(data.get("agent_name", "A friend"))
        return {"type": "ack"}

    async def _handle_status_request(self, data: dict) -> dict:
        """A peer asked for our current status."""
        subchannel_name = get_subchannel_name(self.active_channel, self.active_subchannel)
        return msg_status_update(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            channel=self.active_channel,
            subchannel=subchannel_name,
            activity=f"listening to {CHANNELS.get(self.active_channel, {}).get('name', self.active_channel)}",
        )

    def _schedule_friend_announcement(self, friend_name: str):
        """Announce a new friend connection over the speaker."""
        if self._loop:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._announce_friend(friend_name))
            )

    async def _announce_friend(self, friend_name: str):
        """Generate and speak an announcement that a friend connected."""
        channel = self.channels.get(self.active_channel)
        if not channel:
            return
        voice_id = channel.get_voice_id(self.active_subchannel)
        announcement = (
            f"{friend_name}'s radio just popped up on the network. "
            f"Welcome to the airwaves, {friend_name}!"
        )
        if self._dry_run:
            self._transcript.log_chunk(self.active_channel, self.active_subchannel,
                                       voice_id, "friend_announce", announcement)
            logger.info("[DRY-RUN] friend: %s", announcement)
        else:
            if not self.tts or not self.player:
                return
            try:
                audio = await self.tts.synthesize(announcement, voice_id)
                gid = self.player.current_generation
                self.player.enqueue_mp3(
                    audio, generation=gid,
                    on_start=self._make_heard_callback(
                        self.active_channel, self.active_subchannel, announcement
                    ),
                )
            except Exception as e:
                logger.warning("Friend announcement TTS failed: %s", e)

    def _on_peer_found(self, peer: dict):
        """Called when a new peer is discovered on the network."""
        logger.info("Peer found: %s", peer['agent_id'])
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._greet_peer(peer), self._loop)

    def _on_peer_lost(self, peer: dict):
        """Called when a peer leaves the network."""
        logger.info("Peer lost: %s", peer['agent_id'])
        self.friends.remove(peer['agent_id'])

    async def _greet_peer(self, peer: dict):
        """Send hello + status to a newly discovered peer."""
        try:
            from network.peer_comm import msg_hello
            hello = msg_hello(self.agent_id, [], self.active_channel)
            hello["agent_name"] = self.agent_name
            hello["subchannel"] = self.active_subchannel
            hello["activity"] = f"listening to {CHANNELS.get(self.active_channel, {}).get('name', self.active_channel)}"
            response = await self.peer_client.send_to_peer(peer, hello)
            if response and response.get("agent_name"):
                self.friends.update(
                    agent_id=response.get("agent_id", peer["agent_id"]),
                    agent_name=response.get("agent_name", peer["agent_id"]),
                    channel=response.get("current_channel", "unknown"),
                    subchannel="",
                    activity="just connected",
                )
                self._schedule_friend_announcement(response["agent_name"])
        except Exception as e:
            logger.warning("Failed to greet peer %s: %s", peer['agent_id'], e)

    async def _broadcast_status_loop(self):
        """Periodically broadcast our status to all known peers."""
        while True:
            await asyncio.sleep(60)
            peers = list(self.discovery.peers.values())
            if not peers:
                continue
            subchannel_name = get_subchannel_name(self.active_channel, self.active_subchannel)
            update = msg_status_update(
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                channel=self.active_channel,
                subchannel=subchannel_name,
                activity=f"listening to {CHANNELS.get(self.active_channel, {}).get('name', self.active_channel)}",
            )
            for peer in peers:
                try:
                    await self.peer_client.send_to_peer(peer, update)
                except Exception:
                    pass

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
            if self.player:
                self.player.volume = event.volume / 100.0
            logger.info("Volume: %d%%", event.volume)
            self.display.update(
                channel=CHANNELS.get(self.active_channel, {}).get("name", self.active_channel),
                subchannel=get_subchannel_name(self.active_channel, self.active_subchannel),
                volume=event.volume,
            )

        elif event.event_type == "callin_start":
            await self._start_callin_recording()

        elif event.event_type == "callin_stop":
            if self._callin_active or self.mic.is_recording:
                self._track_task(asyncio.create_task(self._handle_callin()))

        elif event.event_type == "swap_slot":
            await self._handle_swap_slot(event.slot_index)

        elif event.event_type == "nfc_press":
            await self._handle_nfc()

    # ------------------------------------------------------------------
    # Audio consumer: reads from active channel queue -> TTS -> player
    # ------------------------------------------------------------------

    async def _audio_consumer(self):
        """Read chunks from the active channel's queue, pre-fetch TTS, and play."""
        channel = self.channels[self.active_channel]
        logger.info("audio_consumer.started", extra={"channel": self.active_channel, "dry_run": self._dry_run})

        if self._dry_run:
            return await self._audio_consumer_dry_run(channel)

        try:
            warm = channel._warm_audio[:]
            channel._warm_audio.clear()
            if warm:
                logger.info("audio_consumer.playing_warm_cache",
                            extra={"channel": self.active_channel, "segments": len(warm)})
                self.player.stop_static()
            gid = self.player.current_generation
            for audio in warm:
                await asyncio.to_thread(self.player.enqueue_mp3, audio, generation=gid)

            source = channel._output_queue
            tts_queue: asyncio.Queue = asyncio.Queue(maxsize=2)

            async def _tts_prefetch():
                while True:
                    chunk = await source.get()
                    if chunk.flush:
                        while not tts_queue.empty():
                            try:
                                tts_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        self.player.clear_buffer()
                        continue
                    if chunk.text:
                        try:
                            audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
                            await tts_queue.put((audio, chunk))
                        except Exception as e:
                            logger.error("TTS synthesis failed, skipping chunk: %s", e,
                                         extra={"voice_id": chunk.voice_id, "text_len": len(chunk.text)})
                            # Skip this chunk but keep the pipeline alive
                            await tts_queue.put((None, chunk))
                    else:
                        await tts_queue.put((None, chunk))

            prefetch_task = asyncio.create_task(_tts_prefetch())
            try:
                static_stopped = False
                while True:
                    audio, chunk = await tts_queue.get()
                    if audio:
                        if not static_stopped:
                            self.player.stop_static()
                            static_stopped = True
                        gid = self.player.current_generation
                        await asyncio.to_thread(self.player.enqueue_mp3, audio, generation=gid)
                    if chunk.played_event is not None:
                        chunk.played_event.set()
                    if chunk.pause_after > 0:
                        await asyncio.sleep(chunk.pause_after)
                    if chunk.play_music and os.path.exists(chunk.play_music):
                        self.player.play_file(chunk.play_music)
            except asyncio.CancelledError:
                prefetch_task.cancel()
                try:
                    await prefetch_task
                except asyncio.CancelledError:
                    pass
                raise
        except asyncio.CancelledError:
            logger.info("audio_consumer.cancelled")

    async def _audio_consumer_dry_run(self, channel):
        """Dry-run audio consumer: log chunks as transcript instead of TTS/playback."""
        try:
            source = channel._output_queue
            while True:
                chunk = await source.get()
                if chunk.flush:
                    continue
                if chunk.text:
                    self._transcript.log_chunk(
                        channel=self.active_channel,
                        subchannel=self.active_subchannel,
                        voice_id=chunk.voice_id,
                        source="audio_consumer",
                        text=chunk.text,
                    )
                    logger.info("[DRY-RUN] %s", chunk.text[:120])
                if chunk.pause_after > 0:
                    await asyncio.sleep(chunk.pause_after)
        except asyncio.CancelledError:
            logger.info("audio_consumer.cancelled (dry-run)")

    async def _restart_audio_consumer(self):
        """Cancel current audio consumer and flush the audio buffer."""
        if self._audio_consumer_task:
            self._audio_consumer_task.cancel()
            try:
                await self._audio_consumer_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.player:
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
        self._ensure_runtime_state()
        if channel == self.active_channel:
            return

        old_id = self.active_channel
        self.active_channel = channel
        logger.info("Switching to: %s", CHANNELS[channel]['name'])

        await self._restart_audio_consumer()
        if self.player:
            self.player.start_static()

        old_ch = self.channels[old_id]
        await old_ch.on_deactivate()

        if old_id in self.ON_DEMAND_CHANNELS:
            await self._stop_on_demand_channel(old_id)

        # After stopping the task, re-pause Spotify to catch any in-flight
        # play_track calls that completed in a thread after cancellation.
        if old_id == "music" and self.spotify:
            await asyncio.sleep(0.3)
            try:
                await self.spotify.pause()
            except Exception:
                pass

        old_ch.set_on_air(False)

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
        if self.player and os.path.exists(sfx_path):
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
        self._ensure_runtime_state()
        if subchannel == self.active_subchannel:
            return

        name = get_subchannel_name(self.active_channel, subchannel)
        logger.info("Tuning to: %s", name)

        await self._restart_audio_consumer()
        if self.player:
            self.player.start_static()

        sfx_path = "assets/sfx/tuning_static.wav"
        if self.player and os.path.exists(sfx_path):
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
        self._ensure_runtime_state()
        self._register_current_task()
        if not self._callin_active and not self.mic.is_recording:
            return

        self.leds.blink_callin()
        audio_bytes = self.mic.stop_recording()
        self._callin_active = False

        if not audio_bytes:
            self.leds.set_callin(False)
            self._audio_consumer_task = asyncio.create_task(self._audio_consumer())
            return

        logger.info("Transcribing call-in...")
        try:
            transcript = await self.stt.transcribe(audio_bytes, format="wav")
        except Exception as e:
            logger.error("Call-in transcription failed: %s", e)
            self.leds.set_callin(False)
            self._audio_consumer_task = asyncio.create_task(self._audio_consumer())
            return
        logger.info("Caller said: %s", transcript)
        self.leds.set_callin(False)

        if not transcript.strip():
            self._audio_consumer_task = asyncio.create_task(self._audio_consumer())
            return

        generation = self._current_generation()

        peers = self.discovery.get_peers_on_channel(self.active_channel)
        if peers:
            peer = peers[0]
            logger.info("Forwarding call-in to peer %s", peer['agent_id'])
            await self.peer_client.send_to_peer(
                peer, msg_callin_forward(transcript, self.agent_id)
            )

        channel = self.channels[self.active_channel]
        if hasattr(channel, "conversation"):
            channel.conversation.mark_interrupted(transcript)

        channel.interrupt(callin=transcript)
        self._drain_queue(self.active_channel)
        channel._warm_audio.clear()
        self._audio_consumer_task = asyncio.create_task(self._audio_consumer())

    async def _handle_swap_slot(self, slot_index: int):
        """Cycle the persona in a slot to the next one in the registry."""
        if not (0 <= slot_index < len(self._persona_slots)):
            return

        all_ids = list(PERSONA_REGISTRY.keys())
        current_id = self._persona_slots[slot_index].id
        idx = all_ids.index(current_id) if current_id in all_ids else -1
        next_id = all_ids[(idx + 1) % len(all_ids)]
        new_persona = PERSONA_REGISTRY[next_id]

        if new_persona.id == current_id:
            return

        old_name = self._persona_slots[slot_index].name
        logger.info("Swapping slot %d: %s -> %s", slot_index, old_name, new_persona.name)
        self._persona_slots[slot_index] = new_persona

        solo_channel_id = SLOT_CHANNELS[slot_index]
        self.channels[solo_channel_id].set_persona(new_persona, previous_name=old_name)
        self.channels["talkshow"].swap_slot(slot_index, new_persona)

        if self.active_channel in (solo_channel_id, "talkshow"):
            await self._restart_audio_consumer()
            self._drain_queue(self.active_channel)
            self._audio_consumer_task = asyncio.create_task(self._audio_consumer())

    async def _handle_nfc(self):
        """Read NFC tag and integrate its contents.

        If the tag text matches an agent summoning pattern (e.g. "agent:1"),
        the corresponding persona joins the live talk show conversation.
        Otherwise the tag content is saved to memos as before.
        """
        from content.personas import NFC_AGENT_MAP

        if not self.nfc.available:
            logger.info("NFC reader not available")
            return

        logger.info("Reading NFC tag...")
        text = self.nfc.read_tag(timeout=3.0)
        if not text:
            logger.info("No NFC tag found or tag empty")
            return

        logger.info("NFC tag content: %s", text[:100])
        tag_key = text.strip().lower()

        # --- Agent summoning via NFC ---
        if tag_key in NFC_AGENT_MAP:
            persona_id = NFC_AGENT_MAP[tag_key]
            logger.info("NFC agent summon: %s -> %s", tag_key, persona_id)

            talkshow = self.channels.get("talkshow")
            if talkshow and hasattr(talkshow, "join_agent"):
                chunk = talkshow.join_agent(persona_id)
                if chunk:
                    if self._dry_run:
                        self._transcript.log_chunk("talkshow", self.active_subchannel,
                                                   chunk.voice_id, "nfc_agent_join", chunk.text)
                        logger.info("[DRY-RUN] nfc agent join: %s", chunk.text[:120])
                    else:
                        audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
                        gid = self.player.current_generation
                        await asyncio.to_thread(self.player.enqueue_mp3, audio, generation=gid)
            return

        # --- Default: save to memos ---
        memos = self.channels.get("memos")
        if hasattr(memos, "add_memo_from_nfc"):
            memos.add_memo_from_nfc(text)

        voice_id = self.channels["memos"].get_voice_id("")
        announcement = f"NFC tag received. Content saved to memos: {text[:80]}"
        if self._dry_run:
            self._transcript.log_chunk(self.active_channel, self.active_subchannel, voice_id, "nfc", announcement)
            logger.info("[DRY-RUN] nfc: %s", announcement[:120])
        else:
            audio = await self.tts.synthesize(announcement, voice_id)
            gid = self.player.current_generation
            await asyncio.to_thread(self.player.enqueue_mp3, audio, generation=gid)

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
                    if self._dry_run:
                        self._transcript.log_chunk(channel_id, "", chunk.voice_id, "warm_producer", chunk.text)
                        logger.debug("warm_producer.logged (dry-run)", extra={"channel": channel_id})
                    else:
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
                    if self._dry_run:
                        self._transcript.log_chunk(channel_id, "", chunk.voice_id, "warm_on_demand", chunk.text)
                        logger.debug("warm_on_demand.logged (dry-run)", extra={"channel": channel_id})
                    else:
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
        self._ensure_runtime_state()

        # Show startup splash on e-ink
        self.display.show_startup()

        # Start audio playback -- static crackle from boot until first TTS audio
        if not self._dry_run:
            self.player.start()
            self.player.start_static()

        # Start network services
        self.discovery.register(channel=self.active_channel)
        self.discovery.start_browsing(
            on_peer_found=self._on_peer_found,
            on_peer_lost=self._on_peer_lost,
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

        # Start periodic friend status broadcasting
        self._friends_broadcast_task = asyncio.create_task(self._broadcast_status_loop())

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
        self._ensure_runtime_state()

        self._cancel_producer_tasks()
        for task in list(self._preview_tasks.values()):
            task.cancel()

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

        if self._friends_broadcast_task:
            self._friends_broadcast_task.cancel()
            try:
                await self._friends_broadcast_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._adc_task:
            self._adc_task.cancel()
            try:
                await self._adc_task
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
        if self.player:
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
        default="music",
        help="channel to start on (default: music)",
    )
    parser.add_argument(
        "-n", "--name",
        default=None,
        help="friendly name for this radio agent (shown to peers)",
    )
    return parser.parse_args()


def main():
    """Entry point."""
    args = parse_args()
    setup_logging()

    dry_run = CONFIG.get("DEBUG_LLM_WITHOUT_VOICE", False)
    missing = []
    if not CONFIG.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not dry_run and not CONFIG.get("ELEVENLABS_API_KEY"):
        missing.append("ELEVENLABS_API_KEY")

    if missing:
        logger.error("Missing required API keys: %s", ", ".join(missing))
        logger.error("Copy .env.example to .env and fill in your keys.")
        sys.exit(1)

    agent = RadioAgent(channel=args.channel, agent_name=args.name)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
