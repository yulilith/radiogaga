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
from context.context_provider import ContextProvider
from context.exa_search import ExaSearchService
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
        self._dry_run = CONFIG.get("DEBUG_LLM_WITHOUT_VOICE", False)
        self._transcript = TranscriptLogger()

        self.ALWAYS_ON_CHANNELS = {"talkshow", "dailybrief"}
        self.ON_DEMAND_CHANNELS = {"music", "memos"}

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
            "talkshow": TalkShowChannel(self.context, CONFIG, exa_service=self.exa, personas=list(self._persona_slots)),
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
            if self._dry_run:
                self._transcript.log_chunk(self.active_channel, self.active_subchannel, voice_id, "cohost", response_text)
                logger.info("[DRY-RUN] cohost: %s", response_text[:120])
            else:
                audio = await self.tts.synthesize(response_text, voice_id)
                gid = self.player._gen_id
                await asyncio.to_thread(self.player.enqueue_mp3, audio, gid)

            return msg_cohost_response(response_text, voice_id)
        return {"type": "error", "message": "Channel doesn't support co-hosting"}

    async def _handle_callin_forward(self, data: dict) -> dict:
        """A caller from another radio is calling into our show."""
        transcript = data.get("transcript", "")
        logger.info("Remote caller says: %s", transcript)

        channel = self.channels.get(self.active_channel)
        async for chunk in channel.handle_callin(transcript):
            if self._dry_run:
                self._transcript.log_chunk(self.active_channel, self.active_subchannel, chunk.voice_id, "callin_forward", chunk.text)
                logger.info("[DRY-RUN] callin_fwd: %s", chunk.text[:120])
            else:
                audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
                gid = self.player._gen_id
                await asyncio.to_thread(self.player.enqueue_mp3, audio, gid)

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
            if self.player:
                self.player.volume = event.volume / 100.0
            logger.info("Volume: %d%%", event.volume)
            self.display.update(
                channel=CHANNELS.get(self.active_channel, {}).get("name", self.active_channel),
                subchannel=get_subchannel_name(self.active_channel, self.active_subchannel),
                volume=event.volume,
            )

        elif event.event_type == "callin_start":
            self.leds.set_callin(True)
            ch = self.channels[self.active_channel]
            # #region agent log
            import json as _j, time as _t; open("/Users/marco@sierra.ai/playground/radiogaga/.cursor/debug-9dd316.log","a").write(_j.dumps({"sessionId":"9dd316","hypothesisId":"FLUSH","location":"main.py:callin_start_before","message":"queue state BEFORE flush","data":{"output_queue_size": ch._output_queue.qsize(),"warm_audio_len": len(ch._warm_audio),"warm_queue_size": ch._warm_queue.qsize(),"player_buffer": self.player.audio_queue.qsize() if self.player else 0},"timestamp":int(_t.time()*1000)})+"\n")
            # #endregion
            if self._audio_consumer_task:
                self._audio_consumer_task.cancel()
                try:
                    await self._audio_consumer_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._audio_consumer_task = None
            if self.player:
                self.player.stop_static()
                self.player.interrupt()
            self._drain_queue(self.active_channel)
            ch._warm_audio.clear()
            # #region agent log
            open("/Users/marco@sierra.ai/playground/radiogaga/.cursor/debug-9dd316.log","a").write(_j.dumps({"sessionId":"9dd316","hypothesisId":"FLUSH","location":"main.py:callin_start_after","message":"queue state AFTER flush","data":{"output_queue_size": ch._output_queue.qsize(),"warm_audio_len": len(ch._warm_audio),"warm_queue_size": ch._warm_queue.qsize(),"player_buffer": self.player.audio_queue.qsize() if self.player else 0},"timestamp":int(_t.time()*1000)})+"\n")
            # #endregion
            self.mic.start_recording()

        elif event.event_type == "callin_stop":
            await self._handle_callin()

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
            gid = self.player._gen_id
            for audio in warm:
                await asyncio.to_thread(self.player.enqueue_mp3, audio, gid)

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
                        audio = await self.tts.synthesize(chunk.text, chunk.voice_id)
                        await tts_queue.put((audio, chunk))
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
                        gid = self.player._gen_id
                        await asyncio.to_thread(self.player.enqueue_mp3, audio, gid)
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
        self.leds.blink_callin()
        audio_bytes = self.mic.stop_recording()

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
        # #region agent log
        import json as _j, time as _t; open("/Users/marco@sierra.ai/playground/radiogaga/.cursor/debug-9dd316.log","a").write(_j.dumps({"sessionId":"9dd316","hypothesisId":"FLUSH","location":"main.py:callin_stop_before_consumer","message":"state before new consumer","data":{"output_queue_size": channel._output_queue.qsize(),"warm_audio_len": len(channel._warm_audio),"player_buffer": self.player.audio_queue.qsize() if self.player else 0},"timestamp":int(_t.time()*1000)})+"\n")
        # #endregion
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
        voice_id = self.channels["memos"].get_voice_id("")
        announcement = f"NFC tag received. Content saved to memos: {text[:80]}"
        if self._dry_run:
            self._transcript.log_chunk(self.active_channel, self.active_subchannel, voice_id, "nfc", announcement)
            logger.info("[DRY-RUN] nfc: %s", announcement[:120])
        else:
            audio = await self.tts.synthesize(announcement, voice_id)
            gid = self.player._gen_id
            await asyncio.to_thread(self.player.enqueue_mp3, audio, gid)

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

        # Show startup splash on e-ink
        self.display.show_startup()

        # Start audio playback -- static crackle from boot until first TTS audio
        if not self._dry_run:
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
        default="dailybrief",
        help="channel to start on (default: dailybrief)",
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

    agent = RadioAgent(channel=args.channel)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
