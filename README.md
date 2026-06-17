# 🎵 SPARC

<img width="485" height="400" alt="Screenshot 2026-06-17 at 3 04 28 PM" src="https://github.com/user-attachments/assets/b5467583-4feb-4fdb-be22-021c79f3054c" />


A desk-mounted gesture controller that lets you control Spotify hands-free using nothing but hand movements in the air. Built with an Arduino Uno, an HC-SR04 ultrasonic distance sensor, and a Python backend — no touchscreen, no buttons, no phone.

> Built as a hardware/software integration project exploring embedded systems, serial communication, and API-driven automation.

---

## Demo

> 📸 *Photo/video coming soon*

---

## Overview

GestureFM is a two-part embedded system:

- **Firmware (C++)** — runs on an Arduino Uno. Reads distance values from an ultrasonic sensor every 50ms, applies a multi-layer false-trigger filter, and classifies hand motions into discrete gesture events using a direction and duration-based decision tree. Sends gesture labels as plain text over USB Serial.

- **Backend (Python)** — runs on the host laptop. Listens on the Serial port, maps incoming gesture labels to Spotify Web API calls via Spotipy, plays audio connection feedback, and logs every interaction to a SQLite database. An optional Flask dashboard visualizes session analytics.

The two sides communicate exclusively through plain text over USB Serial — a clean, language-agnostic interface that lets the firmware and backend be developed and tested independently.

---

## Gesture Reference

| Gesture | Motion | Action |
|---|---|---|
| Swift approach | Quick hand move toward sensor | ⏭ Next track |
| Swift retreat | Quick hand move away from sensor | ⏮ Previous track |
| Double entry | Hand enters zone twice within 1 second | ⏸ Pause / Resume |
| Hold | Hand stays still in zone for 1.5+ seconds | 🔊 Enter volume mode |
| Hold + move closer | Hand approaches sensor slowly | 🔉 Volume down |
| Hold + move away | Hand retreats from sensor slowly | 🔊 Volume up |
| Quad entry | Hand enters zone four times rapidly | 🎯 Switch to focus playlist |

Gestures are direction-based and intentionally intuitive — moving toward the sensor goes forward, moving away goes back. Volume mode is entered by holding the hand still and exited automatically when the hand leaves the detection zone.

---

## Hardware

| Component | Purpose |
|---|---|
| Arduino Uno | Microcontroller — runs C++ firmware |
| HC-SR04 Ultrasonic Sensor | Measures hand distance (detection zone: 1–8cm) |
| RGB LED | Visual gesture confirmation and system state indicator |
| Toggle Switch | Hardware arm/disarm for the sensor circuit |
| Breadboard + Jumper Wires | No-solder prototyping |

**Total hardware cost: ~$30–45**

### Wiring

```
HC-SR04 VCC  → Toggle Switch → Arduino 5V
HC-SR04 GND  → Arduino GND
HC-SR04 TRIG → Arduino Pin 9
HC-SR04 ECHO → Arduino Pin 10

RGB LED R    → Arduino Pin 3 (PWM)
RGB LED G    → Arduino Pin 5 (PWM)
RGB LED B    → Arduino Pin 6 (PWM)
RGB LED GND  → Arduino GND
```

---

## Software Stack

| Tool | Role |
|---|---|
| C++ (Arduino) | Firmware — sensor reading, gesture classification, LED control |
| Python 3 | Backend — Serial listener, Spotify integration, session logging |
| pyserial | Reads plain text gesture labels from USB Serial port |
| Spotipy | Spotify Web API wrapper — track control, volume, playback |
| playsound | Audio feedback on connection and disconnection |
| SQLite | Persistent gesture event logging |
| Flask *(optional)* | Session analytics dashboard |

---

## System Architecture

```
[HC-SR04 Sensor]
      │ distance in cm (every 50ms)
      ▼
[Arduino Uno — C++ Firmware]
  • 3-layer false trigger filter
  • Direction + duration gesture classifier
  • RGB LED state indicator
      │ plain text over USB Serial ("next", "prev", "pause"...)
      ▼
[Python Backend — host laptop]
  • Startup health check (Arduino + Spotify)
  • Audio feedback ("connected" / "disconnected")
  • Spotify Web API calls via Spotipy
  • SQLite gesture event logging
      │
      ▼
[Spotify Web API → Spotify Client]
```

---

## False Trigger Prevention

Reliable gesture detection in a real desk environment required three layers of filtering:

**1. Hard distance cap** — the firmware ignores all sensor readings beyond 8cm. The sensor physically detects up to 200cm, but anything outside the intentional zone is discarded in code before any gesture logic runs.

**2. Consecutive confirmation** — a hand must appear in at least 3 consecutive sensor readings before it registers as present. This eliminates single-frame noise and electrical jitter.

**3. Gesture cooldown** — after any gesture is classified, the system ignores all input for 1 second. This prevents sloppy or lingering hand movements from registering as follow-up gestures.

---

## LED State Reference

| Color | Meaning |
|---|---|
| Dim white pulse | Idle — armed and listening |
| Green flash | Next track |
| Yellow flash | Previous track |
| Red flash | Pause / Resume |
| Solid blue | Volume mode active |
| Purple flash | Focus playlist activated |
| Off | Toggle switch is off |

---

## Three-Condition Activation

The system only operates when all three conditions are simultaneously true:

1. **Toggle switch is ON** — powers the sensor; enforced entirely in hardware, no code required
2. **Python script is running** — listening on the Serial port and connected to Spotify API
3. **Spotify is open and active** — a song must be playing for playback controls to work

On startup, the Python backend checks conditions 2 and 3 and plays `connected.wav` or `disconnected.wav` accordingly.

---

## Setup

### Prerequisites

- Arduino IDE
- Python 3.8+
- A Spotify Premium account (required for playback control via API)
- A free Spotify Developer account

### Hardware

1. Wire components as shown in the wiring diagram above
2. Open Arduino IDE → select board `Arduino Uno` → select correct COM port
3. Upload `firmware/gesture_controller.ino`
4. Open Serial Monitor at 9600 baud and verify distance readings appear when hand is within 8cm

### Software

```bash
git clone https://github.com/yourusername/gesturefm.git
cd gesturefm
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
SERIAL_PORT=/dev/cu.usbmodem14201   # Windows: COM3
FOCUS_PLAYLIST_URI=spotify:playlist:your_playlist_id
```

Run the backend:

```bash
python backend/main.py
```

On first run, a browser window will open for Spotify OAuth authentication. After authorizing, the token is cached locally and future runs connect automatically.

### Auto-start on Login *(optional)*

**macOS:** Add a launchd plist to `~/Library/LaunchAgents/`  
**Windows:** Add the script to Task Scheduler with a "On log on" trigger  
**Linux:** Add `@reboot python /path/to/main.py` to crontab

---

## Project Structure

```
gesturefm/
├── firmware/
│   └── gesture_controller.ino   # Arduino C++ firmware
├── backend/
│   ├── main.py                  # Serial listener + Spotify integration
│   ├── spotify_client.py        # Spotipy wrapper
│   ├── logger.py                # SQLite gesture logging
│   └── dashboard.py             # Flask analytics dashboard (optional)
├── audio/
│   ├── connected.wav
│   └── disconnected.wav
├── requirements.txt
├── .env.example
└── README.md
```

---

## Analytics Dashboard *(optional)*

When enabled, a local Flask server at `http://localhost:5000` displays:

- Total gestures by type (today / all time)
- Hourly activity heatmap
- Focus mode session history and duration
- Most-skipped time of day

---

## Built With

- [Arduino](https://www.arduino.cc/) — embedded firmware platform
- [Spotipy](https://spotipy.readthedocs.io/) — Spotify Web API Python library
- [pyserial](https://pyserial.readthedocs.io/) — Python Serial communication
- [Flask](https://flask.palletsprojects.com/) — analytics dashboard

---

## Authors

**[Your Name]** — Hardware & Firmware (Arduino C++)  
**[Partner's Name]** — Software & Backend (Python, Spotify API)

---

## License

MIT License — see `LICENSE` for details.
