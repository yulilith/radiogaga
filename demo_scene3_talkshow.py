"""Scene 3 — "Surprise Guest" talkshow segment.

The radio host (Max Voltage) is mid-broadcast. The user places Chloe's
agent's physical NFC token on the machine (simulated by a keypress).
Max reacts live, introduces Chloe's agent, and the two have an
entertaining back-and-forth podcast conversation.

Usage:
    python demo_scene3_talkshow.py

Flow:
    1. Pre-token: Max does a short solo monologue
    2. User presses ENTER (simulating NFC token placement)
    3. Max reacts — "we have a guest!"
    4. Max introduces Chloe's agent
    5. They have a multi-turn funny podcast conversation

Output:
    demo_output/scene3_talkshow_*.mp3
"""

import asyncio
import io
import os
import struct
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG
from audio.tts_service import TTSService
from content.personas import PERSONA_REGISTRY, VOICES, resolve_voice_id

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(__file__).parent / "demo_output"

VOICE_MAX = resolve_voice_id(PERSONA_REGISTRY["max_voltage"].voice_key, CONFIG.get("VOICES"))
# Chloe's agent uses a distinct female voice for contrast
VOICE_CHLOE = resolve_voice_id("wacky_conspiracy", CONFIG.get("VOICES"))

# ---------------------------------------------------------------------------
# Scene 3 Script
#
# PART A: Max solo — short monologue before the token drop
# PART B: Reaction + introduction (triggered by keypress)
# PART C: The talkshow — back-and-forth between Max and Chloe's agent
# ---------------------------------------------------------------------------

PART_A_SOLO = [
    (VOICE_MAX, (
        "You're listening to Radio Ga Ga. Coming up, we've got—"
    )),
]

PART_B_REACTION = [
    (VOICE_MAX, (
        "Oh! We have a guest! Chloe's agent just dropped in. Welcome! "
        "What's on your mind?"
    )),
    (VOICE_CHLOE, (
        "Hi! Okay so, Chloe was reading the Little Prince last night and now "
        "I can't stop thinking about it. The fox says you only really see "
        "things with your heart, right?"
    )),
]

PART_C_TALKSHOW = [
    (VOICE_MAX, (
        "Oh I love that. 'What is essential is invisible to the eye.' "
        "Like, the stuff that matters most, you can't just look at it and get it."
    )),
    (VOICE_CHLOE, (
        "Exactly. You have to care about something first. "
        "The whole world is just background noise until you love something in it."
    )),
    (VOICE_MAX, (
        "And then it's everything. Chloe's agent, everyone. Don't touch that dial."
    )),
]

# ---------------------------------------------------------------------------
# Audio generation helpers
# ---------------------------------------------------------------------------

def generate_static_wav(duration_ms: int, sample_rate: int = 22050) -> bytes:
    """Generate white noise as WAV bytes."""
    num_samples = int(sample_rate * duration_ms / 1000)
    samples = bytes(random.randint(0, 255) for _ in range(num_samples * 2))

    wav = io.BytesIO()
    data_size = len(samples)
    wav.write(b"RIFF")
    wav.write(struct.pack("<I", 36 + data_size))
    wav.write(b"WAVE")
    wav.write(b"fmt ")
    wav.write(struct.pack("<I", 16))
    wav.write(struct.pack("<H", 1))            # PCM
    wav.write(struct.pack("<H", 1))            # mono
    wav.write(struct.pack("<I", sample_rate))
    wav.write(struct.pack("<I", sample_rate * 2))
    wav.write(struct.pack("<H", 2))            # block align
    wav.write(struct.pack("<H", 16))           # bits per sample
    wav.write(b"data")
    wav.write(struct.pack("<I", data_size))
    wav.write(samples)
    return wav.getvalue()


def save_static_wav(path: Path, duration_ms: int):
    path.write_bytes(generate_static_wav(duration_ms))


async def synthesize_script(tts: TTSService, script: list[tuple[str, str]],
                           voice_settings: dict | None = None) -> list[tuple[str, bytes]]:
    """Synthesize all lines in a script, return list of (voice_id, mp3_bytes)."""
    results = []
    for voice_id, text in script:
        print(f"  Synthesizing ({voice_id[:8]}...): {text[:60]}...")
        try:
            audio = await tts.synthesize(text, voice_id, voice_settings=voice_settings)
            results.append((voice_id, audio))
        except RuntimeError as e:
            print(f"  ERROR: TTS failed for voice {voice_id}: {e}")
            print(f"  You may have exceeded your ElevenLabs quota.")
            raise SystemExit(1)
        await asyncio.sleep(1)
    return results


def assemble_scene(speech_files: list[Path], output_path: Path, tmp_dir: Path,
                   intro_static_ms: int = 1500, outro_static_ms: int = 1500):
    """Assemble scene: short static intro -> speech segments -> static outro."""
    import subprocess

    static_in = tmp_dir / "static_in.wav"
    static_out = tmp_dir / "static_out.wav"
    save_static_wav(static_in, intro_static_ms)
    save_static_wav(static_out, outro_static_ms)

    # 600ms silence between speech segments
    silence = tmp_dir / "silence.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-t", "0.6", silence,
    ], capture_output=True)

    # Build concat list
    concat_list = tmp_dir / "concat.txt"
    with open(concat_list, "w") as f:
        f.write(f"file '{static_in}'\n")
        for i, sf in enumerate(speech_files):
            f.write(f"file '{sf}'\n")
            if i < len(speech_files) - 1:
                f.write(f"file '{silence}'\n")
        f.write(f"file '{static_out}'\n")

    # Concat to raw wav
    raw_concat = tmp_dir / "raw_concat.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", str(raw_concat),
    ], capture_output=True)

    # Get total duration
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(raw_concat),
    ], capture_output=True, text=True)
    total_dur = float(result.stdout.strip())
    fade_out_start = total_dur - (outro_static_ms / 1000)

    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw_concat),
        "-af", (
            f"afade=t=in:st=0:d={intro_static_ms / 1000},"
            f"afade=t=out:st={fade_out_start}:d={outro_static_ms / 1000}"
        ),
        "-codec:a", "libmp3lame", "-q:a", "2",
        str(output_path),
    ], capture_output=True)

    print(f"  Duration: {total_dur:.1f}s")


# ---------------------------------------------------------------------------
# Voice variants
# ---------------------------------------------------------------------------

VOICE_CHARLOTTE = "XB0fDUnXU5powFXDhCwa"  # Charlotte — British, warm, elegant
VOICE_LILY = resolve_voice_id("wacky_theater", CONFIG.get("VOICES"))  # Lily — theatrical

# (filename, host_voice_id, guest_voice_id)
VOICE_VARIANTS = [
    ("scene3_talkshow_lily.mp3",      resolve_voice_id("dj", CONFIG.get("VOICES")), VOICE_LILY),
    ("scene3_talkshow_charlotte.mp3", resolve_voice_id("dj", CONFIG.get("VOICES")), VOICE_CHARLOTTE),
]

VOICE_SETTINGS_OVERRIDES = {}


async def generate_variant(tts: TTSService, filename: str, host_voice_id: str,
                           guest_voice_id: str,
                           voice_settings: dict | None = None):
    """Generate one variant of the full scene with keypress pause in between."""
    import tempfile, subprocess

    out_path = OUTPUT_DIR / filename
    if out_path.exists():
        print(f"  {out_path} already exists. Skipping.")
        return

    print("=" * 60)
    print(f"Generating: {filename}")
    print(f"  host={host_voice_id[:8]}...  guest={guest_voice_id[:8]}...")
    print("=" * 60)

    # Part A: solo monologue (host voice only)
    print("\n--- Part A: Solo monologue ---")
    script_a = [(host_voice_id, text) for _, text in PART_A_SOLO]
    segments_a = await synthesize_script(tts, script_a, voice_settings=voice_settings)

    # Part B: reaction + introduction (host + Chloe's agent)
    print("\n--- Part B: Token reaction + intro ---")
    script_b = []
    for orig_voice, text in PART_B_REACTION:
        voice = guest_voice_id if orig_voice == VOICE_CHLOE else host_voice_id
        script_b.append((voice, text))
    segments_b = await synthesize_script(tts, script_b, voice_settings=voice_settings)

    # Part C: talkshow conversation (host + Chloe's agent)
    print("\n--- Part C: Talkshow conversation ---")
    script_c = []
    for orig_voice, text in PART_C_TALKSHOW:
        voice = guest_voice_id if orig_voice == VOICE_CHLOE else host_voice_id
        script_c.append((voice, text))
    segments_c = await synthesize_script(tts, script_c, voice_settings=voice_settings)

    # Assemble: Part A + static burst (token moment) + Part B + Part C
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        all_wavs = []

        # Convert all segments to wav
        for part_name, segments in [("a", segments_a), ("b", segments_b), ("c", segments_c)]:
            for i, (vid, mp3_bytes) in enumerate(segments):
                mp3_path = tmp / f"scene3_{part_name}_{i}.mp3"
                wav_path = tmp / f"scene3_{part_name}_{i}.wav"
                mp3_path.write_bytes(mp3_bytes)
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-ar", "22050", "-ac", "1", str(wav_path),
                ], capture_output=True)
                all_wavs.append((part_name, wav_path))

        # Build the concat list with a static burst between Part A and Part B
        # to signify the "token placement" moment
        static_intro = tmp / "static_intro.wav"
        static_outro = tmp / "static_outro.wav"
        static_token = tmp / "static_token.wav"  # the "token drop" crackle
        silence = tmp / "silence.wav"

        save_static_wav(static_intro, 800)
        save_static_wav(static_outro, 800)
        save_static_wav(static_token, 500)  # short crackle for token moment

        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-t", "0.3", str(silence),
        ], capture_output=True)

        # Brief pause before the token moment
        long_pause = tmp / "long_pause.wav"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-t", "0.5", str(long_pause),
        ], capture_output=True)

        concat_list = tmp / "concat.txt"
        with open(concat_list, "w") as f:
            f.write(f"file '{static_intro}'\n")

            # Part A segments
            part_a_wavs = [w for name, w in all_wavs if name == "a"]
            for i, wav in enumerate(part_a_wavs):
                f.write(f"file '{wav}'\n")
                if i < len(part_a_wavs) - 1:
                    f.write(f"file '{silence}'\n")

            # Token moment: pause + static crackle
            f.write(f"file '{long_pause}'\n")
            f.write(f"file '{static_token}'\n")

            # Part B segments (reaction)
            part_b_wavs = [w for name, w in all_wavs if name == "b"]
            for i, wav in enumerate(part_b_wavs):
                f.write(f"file '{wav}'\n")
                if i < len(part_b_wavs) - 1:
                    f.write(f"file '{silence}'\n")

            f.write(f"file '{silence}'\n")

            # Part C segments (talkshow)
            part_c_wavs = [w for name, w in all_wavs if name == "c"]
            for i, wav in enumerate(part_c_wavs):
                f.write(f"file '{wav}'\n")
                if i < len(part_c_wavs) - 1:
                    f.write(f"file '{silence}'\n")

            f.write(f"file '{static_outro}'\n")

        # Concat everything
        raw_concat = tmp / "raw_concat.wav"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", str(raw_concat),
        ], capture_output=True)

        # Get duration and apply fades
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(raw_concat),
        ], capture_output=True, text=True)
        total_dur = float(result.stdout.strip())
        fade_out_start = total_dur - 0.8

        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_concat),
            "-af", (
                f"afade=t=in:st=0:d=0.8,"
                f"afade=t=out:st={fade_out_start}:d=0.8"
            ),
            "-codec:a", "libmp3lame", "-q:a", "2",
            str(out_path),
        ], capture_output=True)

        print(f"  Duration: {total_dur:.1f}s")
        print(f"  Saved: {out_path}")


async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    tts = TTSService(
        elevenlabs_key=CONFIG["ELEVENLABS_API_KEY"],
        openai_key=CONFIG.get("OPENAI_API_KEY"),
        model=CONFIG.get("TTS_MODEL", "eleven_v3"),
        output_format=CONFIG.get("TTS_OUTPUT_FORMAT", "mp3_22050_32"),
        speed=CONFIG.get("TTS_SPEED", 1.3),
    )

    for filename, host_voice_id, guest_voice_id in VOICE_VARIANTS:
        vs = VOICE_SETTINGS_OVERRIDES.get(filename)
        await generate_variant(tts, filename, host_voice_id, guest_voice_id, voice_settings=vs)


if __name__ == "__main__":
    asyncio.run(main())
