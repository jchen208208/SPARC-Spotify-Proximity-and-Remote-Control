import time
import spotipy
import serial
import sys

print(sys.executable)
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

# --- Fill these in with your Arduino's serial connection details ---
SERIAL_PORT = "YOUR_SERIAL_PORT_HERE"  # e.g. "COM3" or "/dev/ttyUSB0"
BAUD_RATE = 9600
# --------------------------------------------------------------

TEST_MODE = True  # set this back to False once we have the Arduino
SCOPE = "user-modify-playback-state user-read-playback-state"
VOLUME_STEP = 10  # percent change per V+/V- command


def get_client():
    auth_manager = SpotifyOAuth(
        scope=SCOPE,
        cache_path=".spotify_cache",
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def get_active_device(sp):
    """Find the device currently playing, or fall back to the first available device."""
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


def volume_up(sp):
    device = get_active_device(sp)
    if not device:
        print("No active device found.")
        return
    current = device.get("volume_percent", 50)
    new_volume = min(current + VOLUME_STEP, 100)
    sp.volume(new_volume, device_id=device["id"])
    print(f"Volume up: {current}% -> {new_volume}%")


def volume_down(sp):
    device = get_active_device(sp)
    if not device:
        print("No active device found.")
        return
    current = device.get("volume_percent", 50)
    new_volume = max(current - VOLUME_STEP, 0)
    sp.volume(new_volume, device_id=device["id"])
    print(f"Volume down: {current}% -> {new_volume}%")


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
    "V+": volume_up,
    "V-": volume_down,
    "P": toggle_playback,
}


def main():
    sp = get_client()

    if TEST_MODE:
        print("TEST MODE: type commands (S+, S-, V+, V-, P) and press Enter. Ctrl+C to quit.")
        try:
            while True:
                line = input("> ").strip()
                if not line:
                    continue
                cmd = line.upper()
                action = COMMANDS.get(cmd)
                if not action:
                    print(f"Unrecognized input: '{line}'")
                    continue
                try:
                    action(sp)
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
                continue  # no data this cycle

            cmd = line.upper()
            action = COMMANDS.get(cmd)
            if not action:
                print(f"Unrecognized input from Arduino: '{line}'")
                continue

            try:
                action(sp)
            except spotipy.exceptions.SpotifyException as e:
                print(f"Spotify API error: {e}")
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()