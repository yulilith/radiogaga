from waveshare_epd import epd2in13_V4
import time

try:
    print("Initializing display...")
    epd = epd2in13_V4.EPD()
    epd.init()
    
    print("Clearing display (should flash)...")
    epd.Clear(0xFF)
    
    print("Success! Putting display to sleep.")
    epd.sleep()
except Exception as e:
    print(f"Hardware Error: {e}")
