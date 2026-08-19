# 🎵 SPARC

<img width="485" height="400" alt="SPARC" src="https://github.com/user-attachments/assets/b5467583-4feb-4fdb-be22-021c79f3054c" />

**S**potify **P**roximity **a**nd **R**emote **C**ontrol — a desk-mounted gesture controller.
Wave your hand over it to skip tracks, change volume, or pause. No touchscreen, no buttons,
no phone.

Built for situations where looking at a screen is inconvenient or unsafe — adjusting music
while driving, mainly.

---

## How it works

SPARC pairs to your phone or laptop as a **Bluetooth media remote** — the same class of
device as the play/pause button on a pair of headphones.

It does not talk to Spotify. It sends standard HID media-key presses to the operating
system, which routes them to whatever app is currently playing. That means:

- **No app to install** — pair it in Bluetooth settings and it works
- **No Spotify account, login, or Premium subscription**
- Works on **iOS, Android, macOS, Windows, Linux**
- Works with **any** media app — Spotify, Apple Music, YouTube, podcasts

It also works with the screen off and the phone in your pocket, for the same reason your
headphone buttons do.

---

## Gestures

Two zones above the sensor:

| Zone | Range | Controls |
|---|---|---|
| 1 | 2–15 cm | Tracks |
| 2 | 15–30 cm | Volume |

| Gesture | Zone 1 | Zone 2 |
|---|---|---|
| Single pass | ⏭ Next track | 🔊 Volume up |
| Double pass (within 0.8s) | ⏮ Previous track | 🔉 Volume down |
| Hold (0.3s) | ⏯ Play/pause | ⏹ Stop the volume ramp |

Volume ramps continuously once started. Hold your hand still to stop it.

---

## Hardware

| Component | Notes |
|---|---|
| ESP32 (WROOM-32) | Built-in Bluetooth — no separate radio module |
| VL53L0X | Time-of-flight distance sensor, I²C on SDA 21 / SCL 22 |
| 8× WS2812B | Volume bar, data on GPIO 18 |
| LED | Play/pause flash, GPIO 4 |

Runs off a power bank.

---

## Flashing

Requires the ESP32 board core, plus the `NimBLE-Arduino`, `Adafruit_VL53L0X` and `FastLED`
libraries.

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 --port /dev/cu.usbserial-0001 --upload sketch_esp_hid
```

Then pair "SPARC" from your device's Bluetooth settings. That's the whole setup.

> A BLE device stops advertising while connected. To pair it to something else, disconnect
> — or **forget** it — on the current device first, or it won't be discoverable.

---

## Repo layout

| Path | |
|---|---|
| `sketch_esp_hid/` | **The product.** BLE HID firmware |
| `sketch_esp/` | Older firmware that talks to the desktop app over Bluetooth Serial |
| `Main.py`, `ui.py`, `core.py` | Desktop app — demo only, see below |
| `Wired_version/`, `Uno_version/`, `sketch_nano/` | Earlier hardware revisions, kept for reference |

**The desktop app is a demo artifact, not part of the product.** It shows album art, a queue
wheel and gesture animations, but it needs the Spotify Web API — which is exactly the
limitation the HID firmware exists to escape. It only works with `sketch_esp/` firmware
flashed; the two builds are mutually exclusive.

---

## Project timeline

What we tried, what broke, and what we changed.

| Stage | Problem | Fix |
|---|---|---|
| **Proximity volume** | Mapping hand *distance* to volume was buggy and misread hand position constantly. Roughly a full day lost to it. | Scrapped continuous tracking for discrete swipes — single pass, double pass, hold. |
| **USB tether** | The board had to stay plugged into the laptop, so it wasn't really a remote. | Went wireless. Tried WiFi first, settled on Bluetooth with an HC-05. |
| **HC-05 on macOS** | Pairing and holding a stable serial connection took a lot of troubleshooting. | Worked through the port/pairing issues, then moved to an ESP32 — its built-in radio removed the separate module entirely. |
| **Terminal window** | The packaged executable still opened a terminal on launch. Not a product. | Built a proper macOS `.app` with a status UI and gesture animations. |
| **Spotify API cap** ⚠️ | Spotify cut Development Mode to **5 users** in Feb 2026 and required Premium. Extended quota has been organisations-only since May 2025 — no application path for us. **Hard ceiling on the entire product.** | Stopped using the API. Became a **BLE HID device** instead, so the OS routes the commands. Removed the user cap, the Premium requirement, and the app install in one change — and made iPhone possible for the first time, since iOS never supported the old Bluetooth Serial transport. |

### Along the way

- **Leaked credentials.** Early commits contained Spotify tokens. Moved to `.env`, gitignored, purged from history.
- **Sensor took the whole board down.** On a cold power-up the VL53L0X is still booting when `begin()` runs. Failing silently wedged `setup()`, so Bluetooth never came up and the board looked dead. Now it retries five times and carries on regardless.
- **The on-screen keyboard trap.** Off-the-shelf ESP32 HID libraries declare themselves as keyboards, which makes iOS hide the on-screen keyboard system-wide — you couldn't type in any app while SPARC was connected. Fixed by hand-writing a report descriptor that declares media keys *only*.

---

## What's next

- Solder onto perfboard, ditch the breadboard
- Build a case
- A pairing gesture, so switching between devices doesn't mean digging through Bluetooth settings

---

## License

MIT
