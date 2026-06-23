import os
import sys
import time
import threading
import serial
import spotipy
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

import pygame
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

# HC-05 appears as a virtual serial port after pairing.
# macOS: /dev/cu.HC-05  (name may vary — check System Settings → Bluetooth)
# Windows: adjust COM port to whichever number Windows assigned after pairing
# Linux: pair first with bluetoothctl, then bind with rfcomm bind
if sys.platform == "darwin":
    BT_PORT = "/dev/cu.HC-05"
elif sys.platform == "win32":
    BT_PORT = "COM7"
elif sys.platform.startswith("linux"):
    BT_PORT = "/dev/rfcomm0"
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

BAUD_RATE = 9600
SCOPE = "user-modify-playback-state user-read-playback-state"

VOLUME_STEP = 5
VOLUME_INTERVAL = 0.2

pygame.mixer.init()

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SPARC_assets")
SOUND_CONNECTED = os.path.join(ASSET_DIR, "connected.mp3")
SOUND_DISCONNECTED = os.path.join(ASSET_DIR, "disconnected.mp3")


def play_sound(path):
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
    except Exception as e:
        print(f"  Sound error: {e}")


def get_spotify():
    auth = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope=SCOPE,
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def send_current_volume(sp, ser):
    try:
        playback = sp.current_playback()
        if playback and playback.get("device"):
            vol = playback["device"].get("volume_percent", 0)
            ser.write(f"VOL{vol}\n".encode())
            ser.flush()
    except Exception:
        pass


# --- Volume ramping (background thread) ---

_volume_stop = threading.Event()
_volume_thread = None
_volume_lock = threading.Lock()


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

    if (direction == 1 and current >= 100) or (direction == -1 and current <= 0):
        _volume_stop.set()
        ser.write(b"VS\n")
        ser.flush()
        print(f"  Already at {'max' if direction == 1 else 'min'} volume")
        return

    while not _volume_stop.wait(VOLUME_INTERVAL):
        try:
            current = max(0, min(100, current + direction * VOLUME_STEP))
            sp.volume(int(current), device_id=device_id)
            print(f"  Volume: {current}%")
            ser.write(f"VOL{int(current)}\n".encode())
            ser.flush()
            if current in (0, 100):
                _volume_stop.set()
                ser.write(b"VS\n")
                ser.flush()
                break
        except Exception as e:
            print(f"  Volume error: {e}")
            break


def start_volume(sp, direction, ser):
    global _volume_thread
    with _volume_lock:
        _volume_stop.set()
        if _volume_thread is not None and _volume_thread.is_alive():
            _volume_thread.join(timeout=0.5)
        _volume_thread = threading.Thread(target=_ramp_volume, args=(sp, direction, ser), daemon=True)
        _volume_thread.start()


def stop_volume():
    _volume_stop.set()


def volume_active():
    if _volume_thread is None:
        return False
    return _volume_thread.is_alive()


# --- Spotify actions ---

def next_track(sp, ser):
    sp.next_track()
    print("  Next track")
    time.sleep(0.3)
    send_current_volume(sp, ser)


def prev_track(sp, ser):
    try:
        playback = sp.current_playback()
        if not playback:
            return
        position = playback.get("progress_ms", 0)
        if position > 3000:
            sp.seek_track(0)
            print("  Restarted track")
        else:
            sp.previous_track()
            print("  Previous track")
    except spotipy.exceptions.SpotifyException as e:
        if "403" in str(e):
            print("  Previous track unavailable")
        else:
            raise
    time.sleep(0.3)
    send_current_volume(sp, ser)


def toggle_pause(sp, ser):
    playback = sp.current_playback()
    if playback and playback.get("is_playing"):
        sp.pause_playback()
        print("  Paused")
    else:
        devices = sp.devices().get("devices", [])
        device_id = devices[0]["id"] if devices else None
        sp.start_playback(device_id=device_id)
        print("  Resumed")
    time.sleep(0.3)
    send_current_volume(sp, ser)


def handle_stop(sp, ser):
    if volume_active():
        stop_volume()
        if _volume_thread is not None:
            _volume_thread.join(timeout=0.5)
        ser.write(b"VS\n")
        ser.flush()
        print("  Volume stopped")
    else:
        toggle_pause(sp, ser)


def get_handlers(ser):
    return {
        "S+": lambda sp: next_track(sp, ser),
        "S-": lambda sp: prev_track(sp, ser),
        "V+": lambda sp: start_volume(sp, +1, ser),
        "V-": lambda sp: start_volume(sp, -1, ser),
        "P":  lambda sp: handle_stop(sp, ser),
    }


def main():
    sp = get_spotify()

    print("Connecting to Spotify...")
    try:
        user = sp.current_user()
        print(f"Logged in as: {user['display_name']}\n")
    except Exception as e:
        print(f"Spotify auth failed: {e}")
        return

    ser = None
    arduino_connected = False
    spotify_connected = False
    was_connected = False
    HANDLERS = {}
    last_heartbeat = 0.0

    while True:
        # --- Bluetooth reconnect ---
        if ser is None or not ser.is_open:
            try:
                print(f"Waiting for HC-05 on {BT_PORT}...")
                ser = serial.Serial(BT_PORT, BAUD_RATE, timeout=1)
                time.sleep(1)
                ser.reset_input_buffer()
                HANDLERS = get_handlers(ser)
                arduino_connected = True
                print("HC-05 connected.")
                try:
                    devices = sp.devices().get("devices", [])
                    spotify_connected = bool(devices)
                except Exception:
                    spotify_connected = False
            except serial.SerialException:
                arduino_connected = False
                time.sleep(2)
                continue

        # --- Evaluate combined state ---
        both_connected = arduino_connected and spotify_connected
        if both_connected and not was_connected:
            print("Connected.")
            play_sound(SOUND_CONNECTED)
            send_current_volume(sp, ser)
            was_connected = True
        elif not both_connected and was_connected:
            print("Disconnected.")
            play_sound(SOUND_DISCONNECTED)
            was_connected = False
            if ser and ser.is_open:
                try:
                    ser.write(b"VOL0\n")
                    ser.flush()
                except Exception:
                    pass

        if not arduino_connected:
            continue

        # --- Main loop ---
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                try:
                    devices = sp.devices().get("devices", [])
                    spotify_connected = bool(devices)
                except Exception:
                    spotify_connected = False
                now = time.time()
                if now - last_heartbeat >= 2.0:
                    if ser and ser.is_open:
                        try:
                            ser.write(b"HB\n")
                            ser.flush()
                        except Exception:
                            pass
                    last_heartbeat = now
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

        except serial.SerialException:
            print("HC-05 disconnected.")
            arduino_connected = False
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            was_connected = False

        except KeyboardInterrupt:
            print("\nExiting.")
            stop_volume()
            if ser and ser.is_open:
                try:
                    ser.write(b"VOL0\n")
                    ser.flush()
                except Exception:
                    pass
                ser.close()
            break


if __name__ == "__main__":
    main()
