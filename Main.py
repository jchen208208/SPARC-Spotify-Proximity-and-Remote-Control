import os
import time
import threading
import serial
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

SERIAL_PORT = "COM3"
BAUD_RATE = 9600
SCOPE = "user-modify-playback-state user-read-playback-state"

VOLUME_STEP = 5        # % per tick while ramping
VOLUME_INTERVAL = 0.2  # seconds between ticks


def get_spotify():
    auth = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope=SCOPE,
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


# --- Volume ramping (background thread) ---

_volume_stop = threading.Event()
_volume_thread = None


def _ramp_volume(sp, direction, ser):
    _volume_stop.clear()
    try:
        playback = sp.current_playback()
        if not playback or not playback.get("device"):
            return
        device = playback["device"]
        current = device.get("volume_percent", 50)
        device_id = device["id"]
    except Exception as e:
        print(f"  Volume error: {e}")
        return

    # Already at the limit, do nothing
    if (direction == 1 and current >= 100) or (direction == -1 and current <= 0):
        _volume_stop.set()
        ser.write(b"VS\n")
        print(f"  Already at {'max' if direction == 1 else 'min'} volume")
        return

    while not _volume_stop.wait(VOLUME_INTERVAL):
        try:
            current = max(0, min(100, current + direction * VOLUME_STEP))
            sp.volume(int(current), device_id=device_id)
            print(f"  Volume: {current}%")
            if current in (0, 100):
                _volume_stop.set()
                ser.write(b"VS\n")  # tell Arduino volume is done
                break
        except Exception as e:
            print(f"  Volume error: {e}")
            break


def start_volume(sp, direction, ser):
    global _volume_thread
    _volume_stop.set()  # stop any existing ramp
    _volume_thread = threading.Thread(target=_ramp_volume, args=(sp, direction, ser), daemon=True)
    _volume_thread.start()


def stop_volume():
    _volume_stop.set()


def volume_active():
    return _volume_thread is not None and _volume_thread.is_alive()


# --- Spotify actions ---

def next_track(sp):
    sp.next_track()
    print("  Next track")


def prev_track(sp):
    sp.previous_track()
    print("  Previous track")


def toggle_pause(sp):
    playback = sp.current_playback()
    if playback and playback.get("is_playing"):
        sp.pause_playback()
        print("  Paused")
    else:
        devices = sp.devices().get("devices", [])
        device_id = devices[0]["id"] if devices else None
        sp.start_playback(device_id=device_id)
        print("  Resumed")


def handle_stop(sp, ser):
    if volume_active():
        stop_volume()
        ser.write(b"VS\n")  # tell Arduino volume is done
        print("  Volume stopped")
    else:
        toggle_pause(sp)


def main():
    sp = get_spotify()

    print("Connecting to Spotify...")
    try:
        user = sp.current_user()
        print(f"Logged in as: {user['display_name']}\n")
    except Exception as e:
        print(f"Spotify auth failed: {e}")
        return

    print(f"Connecting to Arduino on {SERIAL_PORT}...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)
    ser.reset_input_buffer()
    print("Connected. Listening (Ctrl+C to quit)...\n")

    HANDLERS = {
        "S+": lambda sp: next_track(sp),
        "S-": lambda sp: prev_track(sp),
        "V+": lambda sp: start_volume(sp, +1, ser),
        "V-": lambda sp: start_volume(sp, -1, ser),
        "P":  lambda sp: handle_stop(sp, ser),
    }

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            print(f"← {line}")
            handler = HANDLERS.get(line)
            if handler:
                try:
                    handler(sp)
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify error: {e}")
                except Exception as e:
                    print(f"  Error: {e}")
            else:
                print(f"  [unrecognized] {repr(line)}")
    except KeyboardInterrupt:
        print("\nExiting.")
        stop_volume()
    finally:
        ser.close()


if __name__ == "__main__":
    main()
