import os
import sys
import time
import threading
import socket
import spotipy
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

import pygame
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

PORT = 5000
SCOPE = "user-modify-playback-state user-read-playback-state"

VOLUME_STEP = 5        # % per tick while ramping
VOLUME_INTERVAL = 0.2  # seconds between ticks

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


def send_current_volume(sp, conn):
    try:
        playback = sp.current_playback()
        if playback and playback.get("device"):
            vol = playback["device"].get("volume_percent", 0)
            conn.sendall(f"VOL{vol}\n".encode())
    except Exception:
        pass


# --- Volume ramping (background thread) ---

_volume_stop = threading.Event()
_volume_thread = None
_volume_lock = threading.Lock()


def _ramp_volume(sp, direction, conn):
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
        conn.sendall(b"VS\n")
        print(f"  Already at {'max' if direction == 1 else 'min'} volume")
        return

    while not _volume_stop.wait(VOLUME_INTERVAL):
        try:
            current = max(0, min(100, current + direction * VOLUME_STEP))
            sp.volume(int(current), device_id=device_id)
            print(f"  Volume: {current}%")
            conn.sendall(f"VOL{int(current)}\n".encode())
            if current in (0, 100):
                _volume_stop.set()
                conn.sendall(b"VS\n")
                break
        except Exception as e:
            print(f"  Volume error: {e}")
            break


def start_volume(sp, direction, conn):
    global _volume_thread
    with _volume_lock:
        _volume_stop.set()
        # Wait for any previous ramp thread to actually exit before
        # starting a new one. Without this join, a fast V+ -> V- (or
        # vice versa) could leave two ramp threads alive briefly, both
        # calling sp.volume()/conn.sendall() in opposite directions.
        if _volume_thread is not None and _volume_thread.is_alive():
            _volume_thread.join(timeout=0.5)
        _volume_thread = threading.Thread(target=_ramp_volume, args=(sp, direction, conn), daemon=True)
        _volume_thread.start()


def stop_volume():
    _volume_stop.set()


def volume_active():
    if _volume_thread is None:
        return False
    return _volume_thread.is_alive()


# --- Spotify actions ---

def next_track(sp, conn):
    sp.next_track()
    print("  Next track")
    time.sleep(0.3)
    send_current_volume(sp, conn)


def prev_track(sp, conn):
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
    send_current_volume(sp, conn)


def toggle_pause(sp, conn):
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
    send_current_volume(sp, conn)


def handle_stop(sp, conn):
    if volume_active():
        stop_volume()
        # Wait for the ramp thread to fully exit before announcing VS.
        # The thread already sent the correct final VOL<n> on its last
        # tick; re-querying Spotify here can return a stale value
        # (API propagation lag) that overwrites the correct LED state
        # with an old number once it arrives after the real VOL.
        if _volume_thread is not None:
            _volume_thread.join(timeout=0.5)
        conn.sendall(b"VS\n")
        print("  Volume stopped")
    else:
        toggle_pause(sp, conn)


def get_handlers(conn):
    return {
        "S+": lambda sp: next_track(sp, conn),
        "S-": lambda sp: prev_track(sp, conn),
        "V+": lambda sp: start_volume(sp, +1, conn),
        "V-": lambda sp: start_volume(sp, -1, conn),
        "P":  lambda sp: handle_stop(sp, conn),
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

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(1)

    conn = None
    arduino_connected = False
    spotify_connected = False
    was_connected = False
    HANDLERS = {}

    while True:
        # --- Arduino reconnect ---
        if conn is None:
            try:
                print("Waiting for Arduino...")
                server.settimeout(2)
                conn, addr = server.accept()
                conn.settimeout(1)
                HANDLERS = get_handlers(conn)
                arduino_connected = True
                print(f"Arduino connected from {addr}")
                try:
                    devices = sp.devices().get("devices", [])
                    spotify_connected = bool(devices)
                except Exception:
                    spotify_connected = False
            except socket.timeout:
                arduino_connected = False
                continue

        # --- Evaluate combined state ---
        both_connected = arduino_connected and spotify_connected
        if both_connected and not was_connected:
            print("Connected.")
            play_sound(SOUND_CONNECTED)
            send_current_volume(sp, conn)
            was_connected = True
        elif not both_connected and was_connected:
            print("Disconnected.")
            play_sound(SOUND_DISCONNECTED)
            was_connected = False
            if conn:
                try:
                    conn.sendall(b"VOL0\n")
                except Exception:
                    pass

        if not arduino_connected:
            continue

        # --- Main loop ---
        try:
            data = conn.recv(1024).decode("utf-8", errors="ignore").strip()
            if not data:
                try:
                    devices = sp.devices().get("devices", [])
                    spotify_connected = bool(devices)
                except Exception:
                    spotify_connected = False
                continue

            for line in data.splitlines():
                line = line.strip()
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

        except socket.timeout:
            try:
                devices = sp.devices().get("devices", [])
                spotify_connected = bool(devices)
            except Exception:
                spotify_connected = False

        except (ConnectionResetError, BrokenPipeError, OSError):
            print("Arduino disconnected.")
            arduino_connected = False
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            was_connected = False

        except KeyboardInterrupt:
            print("\nExiting.")
            stop_volume()
            if conn:
                try:
                    conn.sendall(b"VOL0\n")
                except Exception:
                    pass
                conn.close()
            server.close()
            break


if __name__ == "__main__":
    main()