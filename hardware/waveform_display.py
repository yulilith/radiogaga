"""Animated radio-frequency waveform for the Waveshare 2.13" e-ink display.

Generates organic, human-feeling waveforms that make the radio feel alive.
Uses layered sine waves, breathing envelopes, and speech-like cadence
to create movement that feels like a living machine.

Can run standalone (saves preview frames) or be called from DisplayController.

Usage:
    python -m hardware.waveform_display          # preview mode (saves GIF)
    python -m hardware.waveform_display --eink    # live on e-ink hardware
"""

import math
import random
import time
from log import get_logger

logger = get_logger(__name__)


# ── Tunable constants ──────────────────────────────────────────────
BREATH_RATE = 0.35           # breathing cycle speed (~4s inhale/exhale)
DRIFT_RATE = 0.08            # how fast frequencies wander
SYLLABLE_RATE = 1.8          # speech-like burst cadence
TREMOR_AMOUNT = 0.04         # micro-jitter (hand-drawn feel)
HEARTBEAT_BPM = 66           # subtle underlying pulse
BURST_PROBABILITY = 0.06     # chance of an energy burst per frame
BURST_DECAY = 0.88           # how fast bursts fade
PLAYHEAD_SPEED = 0.4         # scanning cursor speed


class WaveformEngine:
    """Generates organic, breathing waveform data — pure math, no numpy."""

    def __init__(self, seed=None):
        rng = random.Random(seed)
        # Fixed phase offsets so each "voice" is unique but deterministic
        self._offsets = [rng.uniform(0, 100) for _ in range(16)]
        self._burst_energy = 0.0
        self._burst_center = 0.5

    # ── building blocks ────────────────────────────────────────────

    def _noise(self, x, seed_idx=0):
        """Smooth organic noise from layered sines (cheap Perlin substitute)."""
        s = self._offsets[seed_idx % len(self._offsets)]
        return (math.sin(x * 0.73 + s * 13.37) * 0.50
                + math.sin(x * 1.37 + s * 7.13) * 0.30
                + math.sin(x * 2.91 + s * 3.71) * 0.15
                + math.sin(x * 5.17 + s * 1.41) * 0.05)

    def _breathing(self, t):
        """Slow inhale/exhale envelope — asymmetric, slightly irregular."""
        # Primary breath
        b = math.sin(t * BREATH_RATE) * 0.3 + 0.65
        # Irregularity (second slower wave)
        b += math.sin(t * BREATH_RATE * 0.41 + 1.2) * 0.1
        # Tiny drift
        b += self._noise(t * 0.03, 10) * 0.05
        return max(0.15, min(1.0, b))

    def _speech_envelope(self, x, t):
        """Mimics the amplitude contour of human speech — bursts and pauses."""
        # Syllable-level energy (sharper peaks)
        syl_raw = self._noise(x * 5.0 + t * SYLLABLE_RATE, 3)
        syl = syl_raw * syl_raw  # squaring gives sharper transients
        syl = abs(syl) ** 0.6    # then soften slightly for organic feel

        # Word-level gaps (clearer pauses between words)
        word_raw = math.sin(x * 1.8 + t * 0.7 + self._offsets[6])
        word = max(0.0, word_raw * 0.6 + 0.4)
        # Occasional hard gaps
        gap = self._noise(x * 2.2 + t * 0.3, 12)
        if gap < -0.6:
            word *= 0.1  # near silence

        # Sentence-level phrasing
        phrase = (math.sin(t * 0.22 + self._offsets[7]) * 0.25 + 0.75)
        return syl * word * phrase

    def _heartbeat(self, t, x):
        """Subtle rhythmic pulse — like a heartbeat under the noise."""
        beat_t = (t * HEARTBEAT_BPM / 60.0) % 1.0
        # Double-bump cardiac shape: lub-dub
        lub = math.exp(-(beat_t ** 2) * 80)
        dub = math.exp(-((beat_t - 0.18) ** 2) * 120) * 0.6
        pulse = lub + dub
        # Spatially localized near a wandering center
        cx = 0.5 + self._noise(t * 0.1, 8) * 0.3
        spatial = math.exp(-((x - cx) ** 2) * 25)
        return pulse * spatial * 0.2

    # ── main generator ─────────────────────────────────────────────

    def generate(self, num_points, t, channel=0):
        """Produce one frame of waveform: list of floats in [-1, 1].

        Args:
            num_points: number of x samples (display width)
            t: global time in seconds (drives animation)
            channel: 0 or 1 for L/R offset
        """
        ch = channel * 5.0  # decorrelate channels
        breath = self._breathing(t)

        # Random burst events
        if random.random() < BURST_PROBABILITY:
            self._burst_energy = random.uniform(0.5, 1.0)
            self._burst_center = random.uniform(0.2, 0.8)
        self._burst_energy *= BURST_DECAY

        points = []
        for i in range(num_points):
            x = i / num_points

            # ── harmonic stack (voice-like) ──
            # Frequencies drift slowly over time
            f1 = 3.0 + self._noise(t * DRIFT_RATE, 0 + channel) * 1.8
            f2 = 7.5 + self._noise(t * DRIFT_RATE * 1.3, 1 + channel) * 2.5
            f3 = 14.0 + self._noise(t * DRIFT_RATE * 1.7, 2 + channel) * 4.0
            f4 = 22.0 + self._noise(t * DRIFT_RATE * 0.9, 4 + channel) * 6.0

            # Richer harmonic stack — more overtones = denser waveform
            f5 = 35.0 + self._noise(t * DRIFT_RATE * 0.6, 9 + channel) * 8.0
            f6 = 50.0 + self._noise(t * DRIFT_RATE * 0.4, 11 + channel) * 10.0

            val = (math.sin(x * f1 * math.tau + t * 2.1 + ch) * 0.40
                   + math.sin(x * f2 * math.tau + t * 3.7 + ch) * 0.22
                   + math.sin(x * f3 * math.tau + t * 5.3 + ch) * 0.15
                   + math.sin(x * f4 * math.tau + t * 7.1 + ch) * 0.10
                   + math.sin(x * f5 * math.tau + t * 9.3 + ch) * 0.08
                   + math.sin(x * f6 * math.tau + t * 11.7 + ch) * 0.05)

            # ── speech envelope ──
            env = self._speech_envelope(x, t + ch * 0.1)
            val *= env

            # ── breathing ──
            val *= breath

            # ── heartbeat undertone ──
            val += self._heartbeat(t, x) * breath

            # ── burst energy ──
            if self._burst_energy > 0.05:
                burst_env = math.exp(-((x - self._burst_center) ** 2) * 8)
                val += (self._burst_energy * burst_env
                        * math.sin(x * 25 * math.tau + t * 11) * 0.5)

            # ── micro-tremor (hand-drawn imperfection) ──
            val += self._noise(x * 30 + t * 9, 5 + channel) * TREMOR_AMOUNT

            points.append(val)

        # Normalize — push peaks close to full amplitude
        peak = max(abs(v) for v in points) or 1.0
        scale = 0.98 / peak
        # Soft-clip for punch: tanh compression keeps peaks full
        return [math.tanh(v * scale * 1.5) for v in points]


class WaveformRenderer:
    """Renders animated waveforms onto a PIL Image for the e-ink display."""

    HEADER_H = 16        # top bar height
    DIVIDER_H = 2        # gap between L and R
    MARGIN_X = 4         # left/right padding
    MARGIN_Y = 2         # top/bottom padding

    def __init__(self, width=250, height=122):
        self.width = width
        self.height = height
        self.engine_l = WaveformEngine(seed=42)
        self.engine_r = WaveformEngine(seed=137)
        self._frame_count = 0

    def render(self, draw, t, channel_name="RADIO", freq_text="FM 98.7",
               font=None, font_small=None):
        """Draw one complete frame onto a PIL ImageDraw context.

        Args:
            draw: PIL ImageDraw instance (1-bit image)
            t: time in seconds
            channel_name: display name (top-left)
            freq_text: frequency readout (top-right)
            font: large font (or None for default)
            font_small: small font (or None for default)
        """
        W, H = self.width, self.height
        self._frame_count += 1

        # ── Header bar ─────────────────────────────────────────────
        if font_small:
            draw.text((self.MARGIN_X, 1), channel_name, font=font_small, fill=0)
            draw.text((W - 70, 1), freq_text, font=font_small, fill=0)
        else:
            draw.text((self.MARGIN_X, 1), channel_name, fill=0)
            draw.text((W - 70, 1), freq_text, fill=0)

        # Recording dot (blinks)
        if self._frame_count % 4 < 3:
            cx, cy = W - 8, 7
            draw.ellipse([(cx - 3, cy - 3), (cx + 3, cy + 3)], fill=0)

        # Header underline
        draw.line([(self.MARGIN_X, self.HEADER_H),
                   (W - self.MARGIN_X, self.HEADER_H)], fill=0, width=1)

        # ── Waveform geometry ──────────────────────────────────────
        wave_top = self.HEADER_H + self.MARGIN_Y + 1
        wave_bottom = H - self.MARGIN_Y
        total_h = wave_bottom - wave_top
        wave_h = (total_h - self.DIVIDER_H) // 2
        num_pts = W - self.MARGIN_X * 2

        # ── L channel ──────────────────────────────────────────────
        l_data = self.engine_l.generate(num_pts, t, channel=0)
        l_center = wave_top + wave_h // 2
        self._draw_wave(draw, l_data, self.MARGIN_X, l_center,
                        wave_h // 2 - 1)

        # Channel label
        lbl_y = wave_top + 1
        if font_small:
            draw.text((self.MARGIN_X + 1, lbl_y), "L", font=font_small, fill=0)
        else:
            draw.text((self.MARGIN_X + 1, lbl_y), "L", fill=0)

        # ── Divider ────────────────────────────────────────────────
        div_y = wave_top + wave_h
        draw.line([(self.MARGIN_X, div_y), (W - self.MARGIN_X, div_y)],
                  fill=0, width=1)

        # ── R channel ──────────────────────────────────────────────
        r_data = self.engine_r.generate(num_pts, t, channel=1)
        r_center = div_y + self.DIVIDER_H + wave_h // 2
        self._draw_wave(draw, r_data, self.MARGIN_X, r_center,
                        wave_h // 2 - 1)

        lbl_y2 = div_y + self.DIVIDER_H + 1
        if font_small:
            draw.text((self.MARGIN_X + 1, lbl_y2), "R", font=font_small, fill=0)
        else:
            draw.text((self.MARGIN_X + 1, lbl_y2), "R", fill=0)

        # ── Scanning playhead ──────────────────────────────────────
        playhead_x = self.MARGIN_X + int(
            ((t * PLAYHEAD_SPEED) % 1.0) * num_pts
        )
        draw.line([(playhead_x, wave_top), (playhead_x, wave_bottom)],
                  fill=0, width=1)

    def _draw_wave(self, draw, data, x_start, center_y, max_amp):
        """Render a filled waveform — vertical lines from center outward.

        Draws each sample as a vertical bar mirrored around center_y.
        Dense regions naturally look darker/thicker on e-ink.
        """
        # Draw faint center baseline
        draw.line([(x_start, center_y),
                   (x_start + len(data), center_y)], fill=0, width=1)

        for i, val in enumerate(data):
            x = x_start + i
            amp = int(abs(val) * max_amp)
            if amp < 1:
                continue
            top = center_y - amp
            bot = center_y + amp
            draw.line([(x, top), (x, bot)], fill=0, width=1)
            # For high amplitude, add 1px neighbor for extra density
            if amp > max_amp * 0.7 and i > 0:
                draw.line([(x - 1, top + 1), (x - 1, bot - 1)], fill=0,
                          width=1)


# ── Integration with DisplayController ─────────────────────────────

def add_waveform_to_display(display_controller):
    """Monkey-patch waveform methods onto an existing DisplayController.

    Usage:
        from hardware.waveform_display import add_waveform_to_display
        add_waveform_to_display(self.display)
        self.display.start_waveform("Talk Show", "FM 101.3")
    """
    dc = display_controller
    dc._waveform_renderer = WaveformRenderer(dc._width, dc._height)
    dc._waveform_active = False
    dc._waveform_t0 = 0

    def show_waveform_frame(self, channel_name="RADIO", freq_text="FM 98.7"):
        """Render and display a single waveform frame."""
        if not self._epd:
            return
        t = time.time() - self._waveform_t0
        image = self._Image.new("1", (self._width, self._height), 255)
        draw = self._ImageDraw.Draw(image)
        self._waveform_renderer.render(
            draw, t,
            channel_name=channel_name,
            freq_text=freq_text,
            font_small=self._font_small,
        )
        self._epd.displayPartial(self._epd.getbuffer(image))

    def start_waveform(self, channel_name="RADIO", freq_text="FM 98.7",
                       fps=3):
        """Start the animated waveform loop in a background thread."""
        import threading
        self._waveform_active = True
        self._waveform_t0 = time.time()

        def _loop():
            interval = 1.0 / fps
            while self._waveform_active:
                try:
                    show_waveform_frame(self, channel_name, freq_text)
                except Exception as e:
                    logger.warning("Waveform frame error: %s", e)
                time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="waveform")
        t.start()
        logger.info("Waveform animation started (%.0f fps)", fps)

    def stop_waveform(self):
        """Stop the waveform animation."""
        self._waveform_active = False

    # Bind methods
    import types
    dc.show_waveform_frame = types.MethodType(show_waveform_frame, dc)
    dc.start_waveform = types.MethodType(start_waveform, dc)
    dc.stop_waveform = types.MethodType(stop_waveform, dc)


# ── Standalone preview mode ────────────────────────────────────────

def _preview_gif(output_path="waveform_preview.gif", seconds=6, fps=8):
    """Generate an animated GIF preview (runs without e-ink hardware)."""
    from PIL import Image, ImageDraw, ImageFont

    renderer = WaveformRenderer(250, 122)
    frames = []
    total_frames = int(seconds * fps)

    try:
        font_sm = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except OSError:
        try:
            # macOS fallback
            font_sm = ImageFont.truetype(
                "/System/Library/Fonts/Helvetica.ttc", 12)
        except OSError:
            font_sm = ImageFont.load_default()

    logger.info("Generating %d frames (%.1fs @ %d fps)...", total_frames,
                seconds, fps)

    for i in range(total_frames):
        t = i / fps
        img = Image.new("1", (250, 122), 255)
        draw = ImageDraw.Draw(img)
        renderer.render(draw, t,
                        channel_name="TALK SHOW",
                        freq_text="FM 101.3",
                        font_small=font_sm)
        # Convert to P mode for GIF
        frames.append(img.convert("P"))

        if (i + 1) % 10 == 0:
            logger.info("  frame %d/%d", i + 1, total_frames)

    # Save animated GIF
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
    )
    logger.info("Saved preview: %s (%d frames, %.1fs)", output_path,
                total_frames, seconds)


def _run_eink():
    """Run the waveform animation on real e-ink hardware."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from config import CONFIG
    from hardware.display_controller import DisplayController

    dc = DisplayController(CONFIG)
    if not dc.available:
        logger.error("E-ink display not available")
        return

    # DisplayController already starts its own waveform loop in __init__,
    # so we just set the labels via update() and let it run.
    try:
        logger.info("Starting waveform on e-ink (Ctrl+C to stop)")
        dc.update(channel="TALK SHOW", subchannel="FM 101.3")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        dc.cleanup()
        logger.info("Waveform stopped")


if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")

    if "--eink" in sys.argv:
        _run_eink()
    else:
        out = "waveform_preview.gif"
        secs = 6
        for arg in sys.argv[1:]:
            if arg.startswith("--out="):
                out = arg.split("=", 1)[1]
            elif arg.startswith("--seconds="):
                secs = float(arg.split("=", 1)[1])
        _preview_gif(output_path=out, seconds=secs)
