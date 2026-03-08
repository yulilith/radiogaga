"""Generate DJ banter MP3 using macOS say + ffmpeg (no ElevenLabs needed).

Produces: demo_output/dj_music.mp3
"""

import asyncio
import io
import os
import struct
import random
import subprocess
import tempfile
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "demo_output"
STATIC_DURATION_MS = 2000


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


DJ_LINES = [
    (
        "Alright alright alright, you're locked in with DJ Spark on RadioAgent. "
        "We are live from the MIT Media Lab, middle of the HARD MODE hackathon, "
        "and the vibes in here are absolutely immaculate."
    ),
    (
        "People are building robots, soldering things at three in the morning, "
        "running on pure caffeine and ambition. I just walked past a team that's "
        "building an AI that composes music based on your heart rate. Wild."
    ),
    (
        "Shout out to Quincy and Cyrus for keeping this whole operation running. "
        "Two hundred hackers, sixth floor of the Media Lab, and somehow nothing "
        "has caught fire yet. Respect."
    ),
    (
        "This next track goes out to everyone pulling an all-nighter right now. "
        "Your code doesn't compile, your servo motor is making a weird noise, "
        "but you're building something incredible. Here we go."
    ),
]


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        speech_files = []

        for i, line in enumerate(DJ_LINES):
            print(f"  Synthesizing line {i+1}/{len(DJ_LINES)}: {line[:60]}...")

            # macOS say -> AIFF -> WAV
            aiff_path = tmp / f"dj_{i}.aiff"
            wav_path = tmp / f"dj_{i}.wav"

            subprocess.run([
                "say", "-v", "Samantha", "-r", "185", "-o", str(aiff_path), line,
            ], check=True)

            subprocess.run([
                "ffmpeg", "-y", "-i", str(aiff_path),
                "-ar", "22050", "-ac", "1", str(wav_path),
            ], capture_output=True, check=True)

            speech_files.append(wav_path)

        # Generate static
        static_in = tmp / "static_in.wav"
        static_out = tmp / "static_out.wav"
        static_in.write_bytes(generate_static_wav(STATIC_DURATION_MS))
        static_out.write_bytes(generate_static_wav(STATIC_DURATION_MS))

        # 400ms silence between lines
        silence = tmp / "silence.wav"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-t", "0.4", str(silence),
        ], capture_output=True, check=True)

        # Build concat list
        concat_list = tmp / "concat.txt"
        with open(concat_list, "w") as f:
            f.write(f"file '{static_in}'\n")
            for i, sf in enumerate(speech_files):
                f.write(f"file '{sf}'\n")
                if i < len(speech_files) - 1:
                    f.write(f"file '{silence}'\n")
            f.write(f"file '{static_out}'\n")

        # Concat
        raw_concat = tmp / "raw_concat.wav"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", str(raw_concat),
        ], capture_output=True, check=True)

        # Get duration
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(raw_concat),
        ], capture_output=True, text=True)
        total_dur = float(result.stdout.strip())
        fade_out_start = total_dur - (STATIC_DURATION_MS / 1000)

        # Apply fades and export
        out_path = OUTPUT_DIR / "dj_music.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_concat),
            "-af", (
                f"afade=t=in:st=0:d={STATIC_DURATION_MS/1000},"
                f"afade=t=out:st={fade_out_start}:d={STATIC_DURATION_MS/1000}"
            ),
            "-codec:a", "libmp3lame", "-q:a", "2",
            str(out_path),
        ], capture_output=True, check=True)

        print(f"\n  Saved: {out_path} ({total_dur:.1f}s)")


if __name__ == "__main__":
    main()
