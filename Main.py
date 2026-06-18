import os
import time
import spotipy
import serial
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import MemoryCacheHandler
from dotenv import load_dotenv

load_dotenv()

SERIAL_PORT = "YOUR_SERIAL_PORT_HERE"  # e.g. "COM3" or "/dev/ttyUSB0"
BAUD_RATE = 9600

TEST_MODE = True  # set this back to False once we have the Arduino
SCOPE = "user-modify-playback-state user-read-playback-state"
VOLUME_STEP = 5  # percent per V+/V- step (matches 3cm bucket granularity)


def get_client():
    required_vars = ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    token_info = {
        "access_token": os.getenv("SPOTIFY_ACCESS_TOKEN"),
        "token_type": os.getenv("SPOTIFY_TOKEN_TYPE", "Bearer"),
        "refresh_token": os.getenv("SPOTIFY_REFRESH_TOKEN"),
        "expires_at": 0,  # force spotipy to refresh immediately
        "scope": SCOPE,
    }
    cache_handler = MemoryCacheHandler(token_info=token_info)

    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope=SCOPE,
        cache_handler=cache_handler,
    )
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


def _adjust_volume(sp, delta):
    device = get_active_device(sp)
    if not device:
        print("No active Spotify device found — is anything playing?")
        return
    current_vol = device.get("volume_percent", 50)
    new_vol = max(0, min(100, current_vol + delta))
    sp.volume(new_vol, device_id=device["id"])
    print(f"Volume: {new_vol}%")


def volume_up(sp):
    _adjust_volume(sp, VOLUME_STEP)


def volume_down(sp):
    _adjust_volume(sp, -VOLUME_STEP)


COMMANDS = {
    "S+": skip_track,
    "S-": previous_track,
    "V+": volume_up,
    "V-": volume_down,
    "P": toggle_playback,
}


def dispatch(sp, line):
    cmd = line.upper()
    action = COMMANDS.get(cmd)
    if action:
        action(sp)
    else:
        print(f"Unrecognized command: '{line}'")


def main():
    sp = get_client()

    if TEST_MODE:
        print("TEST MODE: type commands (S+, S-, V+, V-, P) and press Enter. Ctrl+C to quit.")
        try:
            while True:
                line = input("> ").strip()
                if not line:
                    continue
                try:
                    dispatch(sp, line)
                except spotipy.exceptions.SpotifyException as e:
                    print(f"Spotify API error: {e}")
        except KeyboardInterrupt:
            print("\nExiting.")
        return

    print(f"Connecting to Arduino on {SERIAL_PORT} at {BAUD_RATE} baud...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)
    print("Connected. Listening for commands (Ctrl+C to quit)...")

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                dispatch(sp, line)
            except spotipy.exceptions.SpotifyException as e:
                print(f"Spotify API error: {e}")
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()