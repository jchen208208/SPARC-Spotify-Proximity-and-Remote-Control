import time
import spotipy
import serial
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

SERIAL_PORT = "YOUR_SERIAL_PORT_HERE"  # e.g. "COM3" or "/dev/ttyUSB0"
BAUD_RATE = 9600

TEST_MODE = True  # set this back to False once we have the Arduino
SCOPE = "user-modify-playback-state user-read-playback-state"
VOLUME_STEP = 5  # percent per V+/V- step (matches 3cm bucket granularity)


def get_client():
    # Opens a browser for OAuth on first run, then caches the token in .spotify_cache.
    # Requires SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI in .env.
    auth_manager = SpotifyOAuth(scope=SCOPE, cache_path=".spotify_cache")
    return spotipy.Spotify(auth_manager=auth_manager)


def get_active_device(sp):
    playback = sp.current_playback()
    if playback and playback.get("device"):
        return playback["device"]
    devices = sp.devices().get("devices", [])
    return devices[0] if devices else None


def skip_track(sp):
    sp.next_track()
    print("Skipped to next track.")


def previous_track(sp):
    sp.previous_track()
    print("Went back to previous track.")


def toggle_playback(sp):
    playback = sp.current_playback()
    if playback and playback.get("is_playing"):
        sp.pause_playback()
        print("Playback paused.")
    else:
        device = get_active_device(sp)
        sp.start_playback(device_id=device["id"] if device else None)
        print("Playback resumed.")


COMMANDS = {
    "S+": skip_track,
    "S-": previous_track,
    "P": toggle_playback,
}


def dispatch(sp, line, state):
    cmd = line.upper()

    if cmd == "V_START":
        # Arduino just armed volume mode — read the real current Spotify volume
        # so that the user's hand position maps to where the volume actually is.
        playback = sp.current_playback()
        vol = 50  # safe fallback if playback state is unavailable
        if playback and playback.get("device"):
            vol = playback["device"].get("volume_percent", 50)
        state["tracked_volume"] = vol
        print(f"Volume mode armed — anchored at {vol}%")
        return

    if cmd in ("V+", "V-"):
        if state["tracked_volume"] is None:
            return  # V_START not received yet, ignore stray commands
        delta = VOLUME_STEP if cmd == "V+" else -VOLUME_STEP
        state["tracked_volume"] = max(0, min(100, state["tracked_volume"] + delta))
        device = get_active_device(sp)
        if device:
            sp.volume(state["tracked_volume"], device_id=device["id"])
            print(f"Volume: {state['tracked_volume']}%")
        return

    action = COMMANDS.get(cmd)
    if action:
        action(sp)
    else:
        print(f"Unrecognized command: '{line}'")


def main():
    sp = get_client()
    state = {"tracked_volume": None}

    if TEST_MODE:
        print("TEST MODE: type commands (S+, S-, V_START, V+, V-, P) and press Enter. Ctrl+C to quit.")
        try:
            while True:
                line = input("> ").strip()
                if not line:
                    continue
                try:
                    dispatch(sp, line, state)
                except spotipy.exceptions.SpotifyException as e:
                    print(f"Spotify API error: {e}")
        except KeyboardInterrupt:
            print("\nExiting.")
        return

    print(f"Connecting to Arduino on {SERIAL_PORT} at {BAUD_RATE} baud...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)  # give the Arduino time to reset after the connection opens
    print("Connected. Listening for commands (Ctrl+C to quit)...")

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                dispatch(sp, line, state)
            except spotipy.exceptions.SpotifyException as e:
                print(f"Spotify API error: {e}")
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
