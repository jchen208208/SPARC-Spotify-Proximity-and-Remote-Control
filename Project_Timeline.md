# SPARC — Project Timeline

**SPARC (Spotify Proximity and Remote Control)** is a desk-mounted gesture controller that lets you control Spotify hands-free using an Arduino, an ultrasonic distance sensor, and a Python backend connected to the Spotify Web API.

This document tracks the project from the original idea to the current state: what we built at each stage, the problems we ran into, and how we solved them.

---

## Phase 0 — The Idea (early June 2026)

The idea started about a month before the repo existed: control Spotify with hand gestures in the air — no touchscreen, no buttons, no phone. The long-term vision was a product for situations where looking at a screen is inconvenient or unsafe, like adjusting music while driving.

The idea was pitched to two friends, and the three of us formed the team.

---

## Phase 1 — Software Foundation: Python + Spotify API (June 16–17)

**Commits:** `Initial commit` → `toggle playback`

- Created the GitHub repo, README, and MIT license (June 16).
- Set up the Spotify Developer account, created the app project, and connected to the Spotify Web API using **Spotipy**.
- Wrote the first draft of `Main.py` — the backend script that listens for gesture commands and translates them into Spotify API calls (play/pause, next/previous track, volume).
- Wrote the first Arduino firmware draft (`sketch.ino`) for reading the HC-SR04 ultrasonic sensor.

**Issue — leaked credentials:** Early commits included the Spotify cache/token and hardcoded credentials. We moved all private data (client ID, client secret) into a `.env` file, added it to `.gitignore`, and purged the cached tokens from version control (`used a .env folder for private data`, `security fix`, `Delete .spotify_cache`).

---

## Phase 2 — Hardware Build + First Gesture System (June 17–18)

**Commits:** `Create sketch.ino` → `serial signal rework`

- Built the circuit on a breadboard: Arduino Uno + HC-SR04 ultrasonic sensor + jumper wires.
- Programmed the Arduino to classify hand motions and send gesture labels as plain text over USB Serial to the Python script.

**Original design — proximity-based volume:** Hold your hand in a zone, then move it *closer* to the sensor to decrease volume and *farther away* to increase it.

**Issue — the proximity approach didn't work:** The zone/distance-tracking system was buggy and unreliable — it kept misreading hand position and never behaved the way we wanted. Roughly a full day of commits (`pauses fix`, `pause fix again`, `killed V_start`, multiple `sketch.ino` rewrites) went into trying to stabilize it.

**Solution — pivot to swipe gestures.** We scrapped continuous distance tracking and switched to discrete swipe motions:

- **One swipe** → volume up / next track
- **Two swipes** → volume down / previous track
- **Hold** → stop volume change, or toggle play/pause

The commit `new gesture system that ACTUALLY WORKS` (June 18) marks the turning point. After reprogramming the Arduino and testing, everything worked great. We also added false-trigger filtering (distance cap, consecutive-reading confirmation, gesture cooldown) and tuned the volume step rate (`step: 7%, interval: 0.1s`).

---

## Phase 3 — Polish: Sound Effects + Volume LEDs (June 18)

**Commits:** `mp3 files` → `LED FINAL PULL FROM HERE IF EXPLOSION`

- Added **connected / disconnected sound effects** so the user gets audio feedback when the Python script links up with (or loses) the Arduino and Spotify.
- Added **volume indicator LEDs** to the breadboard that light up to show the current volume percentage inside the Spotify app. Got them fully working the same day.

---

## Phase 4 — Going Wireless (June 18–22)

**Commits:** `WiFi IMPLEMENTATION` → `BLUETOOTH WORKS!`

Up to this point, the Arduino had to be plugged into the laptop by USB cable for serial communication — not exactly a remote control.

- First explored a WiFi approach (`WiFi IMPLEMENTATION`).
- Preserved the working USB version in a `Wired_version/` folder as a stable fallback before making wireless changes.
- Settled on Bluetooth using an **HC-05 module**.

**Issue — Mac ↔ HC-05 connection problems:** Getting macOS to reliably pair and hold a serial connection with the HC-05 took a lot of troubleshooting (`BLUETOOTH WORKS!` followed immediately by `fixing bluetooth issue`). After working through the pairing/port issues, the Bluetooth link became stable.

**Result:** The device now runs fully wireless — the Arduino is powered by a power bank and communicates with the laptop over Bluetooth. We also fixed the LEDs to turn off completely when the program disconnects.

---

## Phase 5 — From Script to App (July 5)

**Commits:** `executable file` → `added gesture corresponding animations in the app ui`

The next barrier to a customer-ready product: you had to open an IDE and manually run the Python script.

1. **First attempt — standalone executable:** Packaged `Main.py` into an executable. Problem: it still opened a terminal window when launched, which isn't a good user experience.
2. **Solution — a proper macOS `.app`:** Built a real application with a UI that shows:
   - Whether the **sensor (Arduino)** is connected
   - Whether **Spotify** is connected
   - When both are connected, a "playing" state with **animations for each gesture action**
3. Added the SPARC logo to the UI and as the dock icon, and fixed remaining security issues.

This is the current state of the project: a wireless, power-bank-powered gesture sensor controlled through a double-clickable desktop app.

---

## Current Challenges

### Scaling / Spotify API limits
Each Spotify Developer app in development mode only supports a small number of allowlisted users (~5 active users). Spinning up more developer accounts per batch of users is not maintainable. Moving past this requires applying for **extended quota access**, but Spotify grants that to established companies with significant monthly active users — which isn't feasible for us right now. This makes the project hard to scale into a full startup at the current moment.

---

## What's Next

- **Ditch the breadboard:** Move to an **Arduino Nano** soldered onto a **perfboard** with all wires and resistors, making the whole device far more compact.
- **Build a displayable case:** Enclose the electronics so it looks like an actual product instead of a sensor attached to wires and a microcontroller.

---

## Timeline at a Glance

| Date | Milestone |
|---|---|
| ~Early June 2026 | Idea conceived and pitched; team of 3 formed |
| June 16 | Repo created; README + MIT license |
| June 17 | Spotify Developer setup, API connected, first `Main.py` and Arduino sketch |
| June 17–18 | Circuit built; proximity-based volume control attempted and abandoned |
| June 18 | Swipe-gesture system working; sound effects + volume LEDs added |
| June 18–19 | Wired version stabilized; wireless work begins |
| June 22 | Bluetooth (HC-05) working after Mac pairing troubleshooting |
| July 5 | Standalone executable → full `.app` with status UI, logo, and gesture animations |
| Next | Solder onto perfboard with Arduino Nano; build product case |
