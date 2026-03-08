"""One-shot display script — shows a static message on the e-ink screen.

Usage:
    python -m hardware.display_message
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CONFIG
from log import get_logger

logger = get_logger(__name__)

MESSAGE_LINE1 = "RADIO GAGA"
MESSAGE_LINE2 = "IS AT"
MESSAGE_LINE3 = "HARDMODE"

def run():
    try:
        from waveshare_epd import epd2in13_V4
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        logger.error("Missing dependency: %s", e)
        return

    width  = CONFIG.get("DISPLAY", {}).get("width",  250)
    height = CONFIG.get("DISPLAY", {}).get("height", 122)

    epd = epd2in13_V4.EPD()
    epd.init()
    epd.Clear(0xFF)

    try:
        font_big = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_mid = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font_big = ImageFont.load_default()
        font_mid = font_big

    image = Image.new("1", (width, height), 255)
    draw  = ImageDraw.Draw(image)

    # Centre each line
    for font, text, y in [
        (font_mid, MESSAGE_LINE1, 14),
        (font_mid, MESSAGE_LINE2, 46),
        (font_big, MESSAGE_LINE3, 70),
    ]:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, font=font, fill=0)

    # Decorative lines top and bottom
    draw.line([(10, 10), (width - 10, 10)], fill=0, width=2)
    draw.line([(10, height - 10), (width - 10, height - 10)], fill=0, width=2)

    # 180° flip to match physical mounting
    image = image.rotate(180)

    epd.display(epd.getbuffer(image))
    logger.info("Message displayed.")

    epd.sleep()


if __name__ == "__main__":
    run()
