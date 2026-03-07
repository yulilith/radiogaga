# Raspberry Pi Spotify Setup

How to get Spotify streaming working on a headless Raspberry Pi for RadioAgent's DJ channel.

## Overview

The Spotify Web API only controls playback — it doesn't stream audio. You need a
**Spotify Connect client** running on the Pi to actually decode and output music.
We use [raspotify](https://github.com/dtcooper/raspotify), a Debian package that
wraps [librespot](https://github.com/librespot-org/librespot) (an open-source
Spotify Connect implementation in Rust).

**Architecture:**

```
┌──────────────────────┐         ┌──────────────────────┐
│  Spotify Web API     │         │  Raspberry Pi        │
│  (spotipy / Python)  │────────▶│                      │
│                      │  play/  │  raspotify (librespot)│
│  - discover devices  │  pause/ │  - decodes audio     │
│  - search tracks     │  queue  │  - outputs to ALSA   │
│  - control playback  │         │  - speakers / DAC    │
└──────────────────────┘         └──────────────────────┘
```

## Prerequisites

- Raspberry Pi (any model with networking — Pi 3/4/5 or Zero 2 W)
- Raspberry Pi OS (Bookworm or later)
- **Spotify Premium account** (required for Spotify Connect)
- Audio output configured (3.5mm jack, HDMI, USB DAC, or I2S HAT)

## Step 1: Install raspotify

```bash
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
```

This installs `raspotify` as a systemd service that starts automatically on boot.

Verify it's running:

```bash
sudo systemctl status raspotify
```

## Step 2: Configure raspotify

Edit the config file:

```bash
sudo nano /etc/raspotify/conf
```

Key settings to change:

```bash
# Device name (shows up in Spotify Connect and in our device discovery)
LIBRESPOT_NAME="RadioAgent"

# Audio output — use the Pi's default ALSA device
# For USB DAC:  LIBRESPOT_BACKEND="alsa"
# For specific device: LIBRESPOT_DEVICE="hw:1,0"
LIBRESPOT_BACKEND="alsa"

# Audio quality (96, 160, or 320 kbps — Premium required for 320)
LIBRESPOT_BITRATE="160"

# Initial volume (0-100)
LIBRESPOT_INITIAL_VOLUME="80"

# Disable audio normalization if you want raw volume control
LIBRESPOT_OPTS="--disable-audio-cache"
```

Restart after changes:

```bash
sudo systemctl restart raspotify
```

## Step 3: Spotify Developer App

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create an app (or use your existing one)
3. Set the redirect URI to `http://127.0.0.1:8888/callback`
4. Under **User Management**, add your Spotify account email
5. Copy the Client ID and Client Secret

## Step 4: Configure RadioAgent

In your `.env` file on the Pi:

```bash
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_PLAYBACK_MODE=pi
```

`SPOTIFY_PLAYBACK_MODE=pi` tells RadioAgent to:
- Look for a raspotify/librespot device by name
- Auto-start the raspotify service if it's not running
- Transfer playback to the Pi's librespot device

## Step 5: First-time OAuth

The first time you run RadioAgent with Spotify, it needs a one-time browser-based
OAuth login. On a headless Pi, do this:

```bash
# On the Pi, run RadioAgent — it will print an auth URL
python main.py -c dj

# Copy that URL, open it in a browser on your laptop/phone
# Log in with your Spotify Premium account
# It will redirect to localhost:8888/callback?code=...
# Copy that full redirect URL and paste it back in the Pi terminal
```

This creates a `.spotify_cache` file with your refresh token. Subsequent runs
authenticate automatically.

## Step 6: Audio output

Make sure your Pi's audio output is configured correctly:

```bash
# List audio devices
aplay -l

# Test speaker output
speaker-test -t wav -c 1

# Set default output (edit ALSA config if needed)
sudo raspi-config
# → System Options → Audio → choose your output
```

If using a USB DAC or I2S HAT, you may need to set `LIBRESPOT_DEVICE` in the
raspotify config to match your ALSA device (e.g., `hw:1,0`).

## Troubleshooting

### raspotify not showing as a device

```bash
# Check service status
sudo systemctl status raspotify

# View logs
journalctl -u raspotify -f

# Restart
sudo systemctl restart raspotify
```

Common causes:
- No network connection
- Spotify account not Premium
- Another librespot instance already running

### 403 "user is not registered"

- Add your Spotify email under **User Management** in the Developer Dashboard
- Delete `.spotify_cache` and re-authenticate:

```bash
rm .spotify_cache
python main.py -c dj
```

### No audio output

```bash
# Check ALSA levels
alsamixer

# Make sure output isn't muted and volume is up
# Check raspotify is using the right device
grep LIBRESPOT_DEVICE /etc/raspotify/conf
```

### High latency / choppy audio

- Use `LIBRESPOT_BITRATE="160"` instead of 320 on slower Pi models
- Ensure good Wi-Fi signal or use Ethernet
- Add `--disable-audio-cache` to `LIBRESPOT_OPTS` to reduce SD card wear

## Dev mode (Mac)

When developing on a Mac, set `SPOTIFY_PLAYBACK_MODE=mac` in `.env` and open the
Spotify desktop app. RadioAgent discovers it as a Connect device automatically —
no raspotify needed.
