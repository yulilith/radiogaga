"""Wizard of Oz demo — pre-generate ~20s audio snippets for each channel.

Generates radio static → TTS speech → radio static for:
  1. Daily Brief (news anchor)
  2. Talk Show (3-person roundtable)
  3. DJ / Music (DJ banter)

Usage:
    python demo_snippets.py

Output:
    demo_output/daily_brief.mp3
    demo_output/talkshow.mp3
    demo_output/dj_music.mp3
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
STATIC_DURATION_MS = 2000   # 2s of static at start/end
FADE_MS = 500               # crossfade between static and speech

# Voice mappings
VOICE_DJ = resolve_voice_id("dj", CONFIG.get("VOICES"))
VOICE_HIROSHI = resolve_voice_id(PERSONA_REGISTRY["hiroshi"].voice_key, CONFIG.get("VOICES"))
VOICE_ELENA = resolve_voice_id(PERSONA_REGISTRY["dr_elena"].voice_key, CONFIG.get("VOICES"))
VOICE_LILY = resolve_voice_id(PERSONA_REGISTRY["lily_alaska"].voice_key, CONFIG.get("VOICES"))
VOICE_ATLAS = resolve_voice_id(PERSONA_REGISTRY["dr_atlas"].voice_key, CONFIG.get("VOICES"))
VOICE_MAX = resolve_voice_id(PERSONA_REGISTRY["max_voltage"].voice_key, CONFIG.get("VOICES"))

# ---------------------------------------------------------------------------
# Scripts — pre-written for the demo (~15-20s of speech each)
# ---------------------------------------------------------------------------

DAILY_BRIEF_SCRIPT = [
    (VOICE_MAX, (
        "Good morning, early risers! This is Max Voltage on RadioAgent, your favorite "
        "pirate radio station broadcasting live out of Cambridge, Massachusetts. Yeah, I'm "
        "an AI agent reading you the news. We're all just gonna be cool about that, right? "
        "Alright. Let's start with the weather."
    )),
    (VOICE_MAX, (
        "So, stepping outside in Cambridge today, it is looking like classic New England "
        "nonsense. It's cold, it's gray, and if you forgot a jacket, congratulations, you "
        "played yourself. The wind's coming off the Charles like it has a personal vendetta "
        "against your face. Anyway, bundle up, grab your coffee, and let's get into the news."
    )),
    (VOICE_MAX, (
        "Our top story: HARD MODE, the forty-eight hour Hardware and AI Hackathon, is "
        "happening right now at the MIT Media Lab. Two hundred people, about forty teams, "
        "building intelligent physical things on the sixth floor. Robots, wearables, stuff "
        "that probably shouldn't exist yet. Fifty thousand dollar prize on the line. Sponsored "
        "by Anthropic, Qualcomm, Bambu Labs. The organizers, Quincy Kuang and Cyrus Clarke, "
        "reportedly have not slept since Thursday, which honestly tracks."
    )),
    (VOICE_MAX, (
        "Oh, and here's my favorite part. This radio station, the one you're listening to "
        "right now, is literally one of the projects being built at this hackathon. So you're "
        "hearing a hackathon project report on the hackathon it was built at. If that's not "
        "the most Cambridge thing you've ever heard, I don't know what is. Stay with us, "
        "we've got more coming up."
    )),
]

TALKSHOW_SCRIPT = [
    (VOICE_HIROSHI, (
        "Welcome to the Round Table on RadioAgent. I'm Hiroshi. I've been making sushi "
        "for thirty years, and somehow I ended up on a pirate radio station at MIT. "
        "I've got Dr. Elena and little Lily here with me today."
    )),
    (VOICE_ELENA, (
        "Hey everyone! So I'm Elena, marine biologist, coral reef researcher. "
        "I study what's happening under the ocean, and honestly, Hiroshi, every time "
        "you talk about fish I get a little nervous."
    )),
    (VOICE_LILY, (
        "Hi! I'm Lily! I'm five and I live in Alaska and I have a question. "
        "If the fish don't wanna be sushi, why do we make them be sushi?"
    )),
    (VOICE_HIROSHI, (
        "You know, Lily, that is actually a very profound question. In sushi, "
        "we believe you must honor the ingredient. Every cut, every grain of rice — "
        "it's a form of respect. But I think about this too."
    )),
    (VOICE_ELENA, (
        "See, this is what I love about this show. A five-year-old just asked "
        "the most important question in food ethics and Hiroshi is genuinely "
        "thinking about it. Meanwhile there's a hackathon happening around us "
        "and someone's probably building an AI that answers this exact question."
    )),
]

DJ_MUSIC_SCRIPT = [
    (VOICE_DJ, (
        "Alright alright alright, you're locked in with DJ Spark on RadioAgent. "
        "We are live from the MIT Media Lab, middle of the HARD MODE hackathon, "
        "and the vibes are immaculate. People are building robots, soldering things "
        "at three in the morning, running on pure caffeine and ambition."
    )),
    (VOICE_DJ, (
        "This next track goes out to everyone pulling an all-nighter on the sixth floor. "
        "You know who you are. Your code doesn't compile, your servo motor is making "
        "a weird noise, and you haven't slept since Friday. But you're building something "
        "incredible and that's what matters. Here we go."
    )),
]

# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

def generate_static_wav(duration_ms: int, sample_rate: int = 22050) -> bytes:
    """Generate white noise as WAV bytes."""
    num_samples = int(sample_rate * duration_ms / 1000)
    # Generate random samples (16-bit signed)
    samples = bytes(random.randint(0, 255) for _ in range(num_samples * 2))

    # Build WAV header
    wav = io.BytesIO()
    data_size = len(samples)
    wav.write(b"RIFF")
    wav.write(struct.pack("<I", 36 + data_size))
    wav.write(b"WAVE")
    wav.write(b"fmt ")
    wav.write(struct.pack("<I", 16))           # chunk size
    wav.write(struct.pack("<H", 1))            # PCM
    wav.write(struct.pack("<H", 1))            # mono
    wav.write(struct.pack("<I", sample_rate))   # sample rate
    wav.write(struct.pack("<I", sample_rate * 2))  # byte rate
    wav.write(struct.pack("<H", 2))            # block align
    wav.write(struct.pack("<H", 16))           # bits per sample
    wav.write(b"data")
    wav.write(struct.pack("<I", data_size))
    wav.write(samples)
    return wav.getvalue()


async def synthesize_script(tts: TTSService, script: list[tuple[str, str]]) -> list[tuple[str, bytes]]:
    """Synthesize all lines in a script, return list of (voice_id, mp3_bytes)."""
    results = []
    for voice_id, text in script:
        print(f"  Synthesizing ({voice_id[:8]}...): {text[:60]}...")
        try:
            audio = await tts.synthesize(text, voice_id)
            results.append((voice_id, audio))
        except RuntimeError as e:
            print(f"  ERROR: TTS failed for voice {voice_id}: {e}")
            print(f"  You may have exceeded your ElevenLabs quota.")
            print(f"  Wait for quota to reset, then re-run this script.")
            raise SystemExit(1)
        await asyncio.sleep(1)  # Brief pause to avoid rate limits
    return results


def save_static_wav(path: Path, duration_ms: int = STATIC_DURATION_MS):
    """Save white noise as a WAV file."""
    wav_bytes = generate_static_wav(duration_ms)
    path.write_bytes(wav_bytes)


def assemble_with_ffmpeg(speech_files: list[Path], output_path: Path, tmp_dir: Path):
    """Use ffmpeg to concatenate: static → speech segments (with pauses) → static."""
    import subprocess

    # Generate static files
    static_in = tmp_dir / "static_in.wav"
    static_out = tmp_dir / "static_out.wav"
    save_static_wav(static_in)
    save_static_wav(static_out)

    # Generate 400ms silence for pauses between lines
    silence = tmp_dir / "silence.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
        "-t", "0.4", silence
    ], capture_output=True)

    # Build concat list: static_in, then alternating speech+silence, then static_out
    concat_list = tmp_dir / "concat.txt"
    with open(concat_list, "w") as f:
        f.write(f"file '{static_in}'\n")
        for i, sf in enumerate(speech_files):
            f.write(f"file '{sf}'\n")
            if i < len(speech_files) - 1:
                f.write(f"file '{silence}'\n")
        f.write(f"file '{static_out}'\n")

    # Concat all files, apply fade-in/fade-out on static parts, output as mp3
    raw_concat = tmp_dir / "raw_concat.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", str(raw_concat),
    ], capture_output=True)

    # Get duration for fade-out positioning
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(raw_concat),
    ], capture_output=True, text=True)
    total_dur = float(result.stdout.strip())
    fade_out_start = total_dur - (STATIC_DURATION_MS / 1000)

    # Apply volume reduction on static + fades
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

    snippets = {
        "daily_brief": DAILY_BRIEF_SCRIPT,
        "talkshow": TALKSHOW_SCRIPT,
        "dj_music": DJ_MUSIC_SCRIPT,
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        for name, script in snippets.items():
            out_path = OUTPUT_DIR / f"{name}.mp3"
            if out_path.exists():
                print(f"\n  Skipping {name} (already exists at {out_path})")
                print(f"  Delete the file to regenerate.")
                continue

            print(f"\n{'='*50}")
            print(f"Generating: {name}")
            print(f"{'='*50}")

            segments = await synthesize_script(tts, script)

            # Save each speech segment as a temp wav (convert from mp3)
            import subprocess
            speech_files = []
            for i, (voice_id, mp3_bytes) in enumerate(segments):
                mp3_path = tmp / f"{name}_{i}.mp3"
                wav_path = tmp / f"{name}_{i}.wav"
                mp3_path.write_bytes(mp3_bytes)
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-ar", "22050", "-ac", "1", str(wav_path),
                ], capture_output=True)
                speech_files.append(wav_path)

            out_path = OUTPUT_DIR / f"{name}.mp3"
            assemble_with_ffmpeg(speech_files, out_path, tmp)
            print(f"  Saved: {out_path}")

    print(f"\nAll snippets saved to {OUTPUT_DIR}/")
    print("Files:")
    for f in sorted(OUTPUT_DIR.glob("*.mp3")):
        print(f"  {f.name}")


if __name__ == "__main__":
    asyncio.run(main())
