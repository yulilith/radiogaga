"""Generate a ~45s talk show MP3 with host introductions and live discussion.

A host (DJ Spark) introduces the show and welcomes each agent one by one:
  1. Hiroshi (sushi chef)
  2. Dr. Elena (marine biologist)
  3. Lily (five-year-old from Alaska)

Then they have a live discussion.

Usage:
    python generate_talkshow_intro.py

Output:
    demo_output/talkshow_intro.mp3
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
STATIC_DURATION_MS = 2000
FADE_MS = 500

# Voice mappings
VOICE_HOST = resolve_voice_id("dj", CONFIG.get("VOICES"))  # DJ Spark as host
VOICE_HIROSHI = resolve_voice_id(PERSONA_REGISTRY["hiroshi"].voice_key, CONFIG.get("VOICES"))
VOICE_ELENA = resolve_voice_id(PERSONA_REGISTRY["dr_elena"].voice_key, CONFIG.get("VOICES"))
VOICE_LILY = resolve_voice_id(PERSONA_REGISTRY["lily_alaska"].voice_key, CONFIG.get("VOICES"))

# ---------------------------------------------------------------------------
# Script — ~45 seconds of speech
# ---------------------------------------------------------------------------

TALKSHOW_INTRO_SCRIPT = [
    # Host opens
    (VOICE_HOST, "Welcome to the Round Table on RadioAgent! I'm DJ Spark. Let's meet tonight's guests."),

    # Entrances
    (VOICE_HOST, "First up, sushi master from Tokyo — Hiroshi!"),
    (VOICE_HIROSHI, "Thank you Spark. Happy to be here."),

    (VOICE_HOST, "Next, marine biologist — Dr. Elena!"),
    (VOICE_ELENA, "Hey everyone! Great to be on the show."),

    (VOICE_HOST, "And our youngest guest, five years old from Alaska — Lily!"),
    (VOICE_LILY, "Hi! I'm Lily! Why is it so late?"),

    # Discussion
    (VOICE_HIROSHI, "In Tokyo it is already tomorrow, Lily."),
    (VOICE_LILY, "Do fish have feelings?"),
    (VOICE_ELENA, "That is actually a great research question, Lily. Some studies suggest they do."),
    (VOICE_HIROSHI, "In sushi, we believe you must honor the ingredient. Every cut matters."),
    (VOICE_ELENA, "And that is why I love this show. The questions that matter, from the most unexpected people."),
]

# ---------------------------------------------------------------------------
# Audio generation (reused from demo_snippets.py)
# ---------------------------------------------------------------------------

def generate_static_wav(duration_ms: int, sample_rate: int = 22050) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000)
    samples = bytes(random.randint(0, 255) for _ in range(num_samples * 2))
    wav = io.BytesIO()
    data_size = len(samples)
    wav.write(b"RIFF")
    wav.write(struct.pack("<I", 36 + data_size))
    wav.write(b"WAVE")
    wav.write(b"fmt ")
    wav.write(struct.pack("<I", 16))
    wav.write(struct.pack("<H", 1))
    wav.write(struct.pack("<H", 1))
    wav.write(struct.pack("<I", sample_rate))
    wav.write(struct.pack("<I", sample_rate * 2))
    wav.write(struct.pack("<H", 2))
    wav.write(struct.pack("<H", 16))
    wav.write(b"data")
    wav.write(struct.pack("<I", data_size))
    wav.write(samples)
    return wav.getvalue()


async def synthesize_script(tts: TTSService, script: list[tuple[str, str]]) -> list[tuple[str, bytes]]:
    results = []
    for voice_id, text in script:
        print(f"  Synthesizing ({voice_id[:8]}...): {text[:60]}...")
        try:
            audio = await tts.synthesize(text, voice_id)
            results.append((voice_id, audio))
        except RuntimeError as e:
            print(f"  WARNING: TTS failed for voice {voice_id}, retrying with default voice...")
            audio = await tts.synthesize(text, "pNInz6obpgDQGcFmaJgB")
            results.append(("pNInz6obpgDQGcFmaJgB", audio))
        await asyncio.sleep(1)
    return results


def save_static_wav(path: Path, duration_ms: int = STATIC_DURATION_MS):
    wav_bytes = generate_static_wav(duration_ms)
    path.write_bytes(wav_bytes)


def assemble_with_ffmpeg(speech_files: list[Path], output_path: Path, tmp_dir: Path):
    import subprocess

    static_in = tmp_dir / "static_in.wav"
    static_out = tmp_dir / "static_out.wav"
    save_static_wav(static_in)
    save_static_wav(static_out)

    silence = tmp_dir / "silence.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-t", "0.4", silence
    ], capture_output=True)

    concat_list = tmp_dir / "concat.txt"
    with open(concat_list, "w") as f:
        f.write(f"file '{static_in}'\n")
        for i, sf in enumerate(speech_files):
            f.write(f"file '{sf}'\n")
            if i < len(speech_files) - 1:
                f.write(f"file '{silence}'\n")
        f.write(f"file '{static_out}'\n")

    raw_concat = tmp_dir / "raw_concat.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", str(raw_concat),
    ], capture_output=True)

    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(raw_concat),
    ], capture_output=True, text=True)
    total_dur = float(result.stdout.strip())
    fade_out_start = total_dur - (STATIC_DURATION_MS / 1000)

    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw_concat),
        "-af", (
            f"afade=t=in:st=0:d={STATIC_DURATION_MS/1000},"
            f"afade=t=out:st={fade_out_start}:d={STATIC_DURATION_MS/1000}"
        ),
        "-codec:a", "libmp3lame", "-q:a", "2",
        str(output_path),
    ], capture_output=True)

    print(f"  Duration: {total_dur:.1f}s")


async def main():
    import tempfile

    OUTPUT_DIR.mkdir(exist_ok=True)

    tts = TTSService(
        elevenlabs_key=CONFIG["ELEVENLABS_API_KEY"],
        openai_key=CONFIG.get("OPENAI_API_KEY"),
        model=CONFIG.get("TTS_MODEL", "eleven_v3"),
        output_format=CONFIG.get("TTS_OUTPUT_FORMAT", "mp3_22050_32"),
        speed=CONFIG.get("TTS_SPEED", 1.1),
    )

    out_path = OUTPUT_DIR / "talkshow_intro.mp3"

    print(f"\n{'='*50}")
    print(f"Generating: talkshow_intro")
    print(f"{'='*50}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        segments = await synthesize_script(tts, TALKSHOW_INTRO_SCRIPT)

        import subprocess
        speech_files = []
        for i, (voice_id, mp3_bytes) in enumerate(segments):
            mp3_path = tmp / f"talkshow_intro_{i}.mp3"
            wav_path = tmp / f"talkshow_intro_{i}.wav"
            mp3_path.write_bytes(mp3_bytes)
            subprocess.run([
                "ffmpeg", "-y", "-i", str(mp3_path),
                "-ar", "22050", "-ac", "1", str(wav_path),
            ], capture_output=True)
            speech_files.append(wav_path)

        assemble_with_ffmpeg(speech_files, out_path, tmp)
        print(f"  Saved: {out_path}")

    print(f"\nDone! Output: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
