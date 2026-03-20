"""Scene 2 — "Friends & Around Town" segment.

The radio host continues broadcasting while the user gets dressed.
The content shifts from weather/general morning to personalized gossip
about the user's friends and their AI agents — delivered like a local
community news segment.

Usage:
    python demo_scene2_friends.py

Output:
    demo_output/scene2_friends_dj.mp3
    demo_output/scene2_friends_voice1.mp3
    demo_output/scene2_friends_voice2.mp3
    demo_output/scene2_friends_dj_natural.mp3
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

# ---------------------------------------------------------------------------
# Scene 2 Script — "Friends & Around Town"
#
# Plays while the user is at their closet / getting dressed. The radio host
# delivers friend updates like a community bulletin — warm, gossipy, funny.
# Each friend blurb is its own beat so there's natural breathing room.
# ---------------------------------------------------------------------------

SCENE2_SCRIPT = [
    # Beat 1: Transition — entering the friend segment
    (VOICE_MAX, (
        "Alright, time for your morning friend report."
    )),

    # Beat 2: Chloe — cat talk show
    (VOICE_MAX, (
        "Chloe's agent has decided to host a talk show for her cat. "
        "Live interview with Corbu at two o'clock. "
        "Chloe says she'll try to tune in."
    )),

    # Beat 3: Quincy — fighting with his AI parrot
    (VOICE_MAX, (
        "Quincy is in a full-blown argument with his AI parrot. "
        "The parrot told him his outfit was, quote, 'a cry for help,' "
        "Hour fourteen of this standoff. I'll keep you posted."
    )),

    # Beat 4: Margot — wholesome agent chaos
    (VOICE_MAX, (
        "And Margot wanted to recommend a new song she's been looping on repeat."
        "Check it out:"
    )),

    # Beat 5: Sign-off
    (VOICE_MAX, (
        "That's your friend report. Coming up next, music to carry you out the door."
    )),
]

# ---------------------------------------------------------------------------
# Audio generation helpers (same as opening scene)
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


def assemble_scene(speech_files: list[Path], output_path: Path, tmp_dir: Path):
    """Assemble scene: short static intro → speech segments → static outro."""
    import subprocess

    # Scene 2 is mid-broadcast, so shorter static bookends than the opening
    intro_static_ms = 1500
    outro_static_ms = 1500

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

    # Gentle fade-in/out (mid-broadcast feel, not a cold open)
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
# Voice variants (same structure as opening scene)
# ---------------------------------------------------------------------------

VOICE_VARIANTS = {
    "scene2_friends_dj.mp3":          resolve_voice_id("dj", CONFIG.get("VOICES")),
    "scene2_friends_voice1.mp3":      resolve_voice_id("voice_1", CONFIG.get("VOICES")),
    "scene2_friends_voice2.mp3":      resolve_voice_id("voice_2", CONFIG.get("VOICES")),
    "scene2_friends_dj_natural.mp3":  resolve_voice_id("dj", CONFIG.get("VOICES")),
    "scene2_friends_dj_fast.mp3":         resolve_voice_id("dj", CONFIG.get("VOICES")),
    "scene2_friends_dj_natural_fast.mp3": resolve_voice_id("dj", CONFIG.get("VOICES")),
    "scene2_friends_voice3.mp3":          resolve_voice_id("voice_3", CONFIG.get("VOICES")),
    "scene2_friends_voice4.mp3":          resolve_voice_id("voice_4", CONFIG.get("VOICES")),
    "scene2_friends_voice5.mp3":          resolve_voice_id("voice_5", CONFIG.get("VOICES")),
}

VOICE_SETTINGS_OVERRIDES = {
    "scene2_friends_dj_natural.mp3": {
        "stability": 0.65,
        "similarity_boost": 0.5,
        "speed": 1.15,
    },
    "scene2_friends_dj_fast.mp3": {
        "stability": 0.5,
        "similarity_boost": 0.75,
        "speed": 1.7,
    },
    "scene2_friends_dj_natural_fast.mp3": {
        "stability": 0.65,
        "similarity_boost": 0.5,
        "speed": 1.7,
    },
}


async def main():
    import tempfile

    OUTPUT_DIR.mkdir(exist_ok=True)

    tts = TTSService(
        elevenlabs_key=CONFIG["ELEVENLABS_API_KEY"],
        openai_key=CONFIG.get("OPENAI_API_KEY"),
        model=CONFIG.get("TTS_MODEL", "eleven_v3"),
        output_format=CONFIG.get("TTS_OUTPUT_FORMAT", "mp3_22050_32"),
        speed=CONFIG.get("TTS_SPEED", 1.3),
    )

    for filename, voice_id in VOICE_VARIANTS.items():
        out_path = OUTPUT_DIR / filename
        if out_path.exists():
            print(f"  {out_path} already exists. Skipping.")
            continue

        # Build script with this voice
        script = [(voice_id, text) for _, text in SCENE2_SCRIPT]

        print("=" * 50)
        print(f"Generating: {filename} (voice {voice_id[:8]}...)")
        print("=" * 50)

        vs = VOICE_SETTINGS_OVERRIDES.get(filename)
        segments = await synthesize_script(tts, script, voice_settings=vs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            import subprocess

            speech_files = []
            for i, (vid, mp3_bytes) in enumerate(segments):
                mp3_path = tmp / f"scene2_{i}.mp3"
                wav_path = tmp / f"scene2_{i}.wav"
                mp3_path.write_bytes(mp3_bytes)
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-ar", "22050", "-ac", "1", str(wav_path),
                ], capture_output=True)
                speech_files.append(wav_path)

            assemble_scene(speech_files, out_path, tmp)

        print(f"  Saved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
