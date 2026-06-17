"""
GestureFM - Backend Controller
--------------------------------
Listens for gesture signals from the Arduino over USB Serial,
and translates them into Spotify playback commands.

This is a TEMPLATE - hardware integration and Spotify auth
will be filled in as the project develops.
"""

import os
import serial
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# ============================================================
# CONFIG
# ============================================================

load_dotenv()  # loads variables from a .env file

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/cu.usbmodem14201")  # change to match your partner's Arduino port
BAUD_RATE = 9600

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
FOCUS_PLAYLIST_URI = os.getenv("FOCUS_PLAYLIST_URI")  # spotify:playlist:xxxxx

SPOTIFY_SCOPE = "user-modify-playback-state user-read-playback-state"


# ============================================================
# AUDIO FEEDBACK
# ============================================================

def play_sound(filename):
    """Play a short audio cue. Mac-only for now using afplay."""
    os.system(f"afplay {filename}")


# ============================================================
# SPOTIFY SETUP
# ============================================================

def init_spotify():
    """Create and return an authenticated Spotipy client."""
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE
    ))
    return sp


# ============================================================
# SERIAL SETUP
# ============================================================

def init_serial():
    """Open the Serial connection to the Arduino. Returns None if it fails."""
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        return ser
    except serial.SerialException as e:
        print(f"[ERROR] Could not open Serial port {SERIAL_PORT}: {e}")
        return None


# ============================================================
# STARTUP CHECK
# ============================================================

def startup_check(sp, ser):
    """
    Verify everything is ready before entering the main loop:
    - Arduino is connected (ser is not None)
    - Spotify is reachable and actively playing something
    """
    if ser is None:
        print("[STARTUP] Arduino not connected.")
        play_sound("audio/disconnected.wav")
        return False

    try:
        current = sp.current_playback()
        if current is None:
            print("[STARTUP] Spotify is not currently playing. Open Spotify and start a song.")
            play_sound("audio/disconnected.wav")
            return False
    except Exception as e:
        print(f"[STARTUP] Could not reach Spotify API: {e}")
        play_sound("audio/disconnected.wav")
        return False

    print("[STARTUP] All systems connected.")
    play_sound("audio/connected.wav")
    return True


# ============================================================
# GESTURE HANDLER
# ============================================================

def handle_gesture(sp, gesture):
    """
    Map a gesture string to a Spotify action.
    TODO: fill in real behavior for each gesture as firmware solidifies.
    """
    print(f"[GESTURE] Received: {gesture}")

    if gesture == "next":
        sp.next_track()

    elif gesture == "prev":
        sp.previous_track()

    elif gesture == "pause":
        current = sp.current_playback()
        if current and current.get("is_playing"):
            sp.pause_playback()
        else:
            sp.start_playback()

    elif gesture == "volume_up":
        current = sp.current_playback()
        if current:
            new_vol = min(100, current["device"]["volume_percent"] + 8)
            sp.volume(new_vol)

    elif gesture == "volume_down":
        current = sp.current_playback()
        if current:
            new_vol = max(0, current["device"]["volume_percent"] - 8)
            sp.volume(new_vol)

    elif gesture == "focus":
        if FOCUS_PLAYLIST_URI:
            sp.start_playback(context_uri=FOCUS_PLAYLIST_URI)
        else:
            print("[WARN] FOCUS_PLAYLIST_URI not set in .env")

    else:
        print(f"[WARN] Unknown gesture: {gesture}")


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    sp = init_spotify()
    ser = init_serial()

    if not startup_check(sp, ser):
        print("Startup checks failed. Exiting.")
        return

    print("Listening for gestures... (Ctrl+C to stop)")

    try:
        while True:
            if ser.in_waiting > 0:
                raw = ser.readline()
                gesture = raw.decode("utf-8").strip()
                if gesture:
                    handle_gesture(sp, gesture)

    except KeyboardInterrupt:
        print("\nShutting down.")
        play_sound("audio/disconnected.wav")


# ============================================================
# TEST MODE — run gesture handling without any hardware
# ============================================================

def test_mode():
    """
    Lets you test Spotify integration without the Arduino connected.
    Run this instead of main() while your partner is still building firmware.
    """
    sp = init_spotify()

    test_gestures = ["next", "prev", "pause", "volume_up", "volume_down"]

    for g in test_gestures:
        handle_gesture(sp, g)
        input(f"Pressed Enter to continue after '{g}'...")  # pause so you can check Spotify between calls


if __name__ == "__main__":
    # Swap this line to main() once your partner's Arduino is ready
    test_mode()
    # main()