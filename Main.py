import math
import os
import queue
import shutil
import subprocess
import sys
import time
import threading
import serial
import serial.tools.list_ports
import json
import spotipy

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

import pygame
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# --- Resolve base directory whether frozen (PyInstaller) or running as .py ---
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, '.env'))

BAUD_RATE = 9600
HANDSHAKE_TIMEOUT = 4.0  # a port must answer within this to count as our board
SCOPE = "user-modify-playback-state user-read-playback-state"

VOLUME_STEP = 5
VOLUME_INTERVAL = 0.2

pygame.mixer.init()

ASSET_DIR = os.path.join(BASE_DIR, "SPARC_assets")
SOUND_CONNECTED = os.path.join(ASSET_DIR, "connected.mp3")
SOUND_DISCONNECTED = os.path.join(ASSET_DIR, "disconnected.mp3")


# The Nano and the Uno both reach us through an HC-05 module; the ESP32 uses
# its own radio and advertises itself as "SPARC" (see sketch_esp). Past the
# open() all three speak the same line protocol, so the board only ever differs
# in which serial port it shows up as - hence one script for all three.
# Espressif's OUI - the first three bytes of every ESP32's Bluetooth MAC. Windows
# hides the device name but puts the MAC in the port's hwid, so this is what finds
# the board there. It identifies the *vendor*, not the board: if two ESP32s are
# paired, both match and the handshake picks whichever answers - set BT_PORT to
# pin one. (Boards from other Espressif batches have a different OUI and fall
# through to the Bluetooth-port sweep below, which finds them too, just slower.)
ESP32_OUI = "8C94DF"

DEVICE_HINTS = ("SPARC", "HC-05", "HC05", "ESP32", ESP32_OUI)


def candidate_ports(verbose=False):
    """Serial ports that might be a SPARC controller, most likely first.

    The two OSes tell us completely different things about a Bluetooth port, so
    we match on whatever each one actually gives us:

    macOS names the port after the device (/dev/cu.SPARC, /dev/cu.HC-05), so the
    name hints match outright - but it never exposes a MAC (hwid is "n/a").

    Windows is the mirror image: the description is always "Standard Serial over
    Bluetooth link", with no trace of the device name, but the MAC *is* there in
    the hwid. So we match Espressif's OUI - the first three bytes of every ESP32's
    MAC - which is how the Windows side has always identified the board. Anything
    Bluetooth-ish that we can't identify is still offered up as a last resort, and
    open_device()'s handshake decides which one is really ours.
    """
    named, fallback, seen = [], [], []
    for port in serial.tools.list_ports.comports():
        blob = " ".join(filter(None, (port.device, port.description, port.hwid))).upper()
        seen.append(f"{port.device} | {port.description} | {port.hwid}")
        if any(hint in blob for hint in DEVICE_HINTS):
            named.append(port.device)
        elif sys.platform == "win32" and ("BTHENUM" in blob or "BLUETOOTH" in blob):
            # Windows only: on macOS this would just add the useless
            # /dev/cu.Bluetooth-Incoming-Port and cost 4s probing a dead end.
            fallback.append(port.device)
    if verbose and not named and not fallback:
        # Turn "it doesn't find the port" into something diagnosable rather than
        # a silent empty list - this is the only place that knows what the OS saw.
        print("No candidate ports. Serial ports visible to this machine:")
        for line in seen or ["  (none at all)"]:
            print(f"    {line}")
        print("  If the board is paired, add its port to .env as BT_PORT=<port>.")
    return named + fallback


PORT_CACHE = os.path.join(os.path.expanduser("~"), ".sparc_cache", "port.json")


def _load_cached_port():
    """The port that answered last time this machine ran. Probing a Bluetooth
    port that turns out to be dead costs ~4s, so on a machine with several
    boards paired a cold scan is slow; remembering the winner makes every run
    after the first go straight to it."""
    try:
        with open(PORT_CACHE) as f:
            return json.load(f).get("port")
    except (OSError, ValueError):
        return None


def _save_cached_port(port):
    try:
        os.makedirs(os.path.dirname(PORT_CACHE), exist_ok=True)
        with open(PORT_CACHE, "w") as f:
            json.dump({"port": port}, f)
    except OSError:
        pass


def resolve_ports(preferred=None):
    """Ports to try this round: last known good, then the .env override, then
    the port cached from a previous run, then whatever autodetect turns up.
    Re-run on every reconnect rather than once at startup, so a board powered on
    after the app is still picked up. A stale entry costs nothing: it just fails
    the handshake and we fall through to the next candidate."""
    ports = []
    for port in (preferred, os.getenv("BT_PORT"), _load_cached_port()):
        if port and port not in ports:
            ports.append(port)
    for port in candidate_ports(verbose=not ports):
        if port not in ports:
            ports.append(port)
    return ports


def open_device(port):
    """Open `port` and prove a real controller is on the other end, else None.

    A successful open() means nothing by itself: macOS hands back the port of a
    paired-but-powered-off HC-05 quite happily, and on Windows we may well be
    probing some unrelated Bluetooth device. Without this proof we'd flip to
    "connected", time out on the silence seconds later, reconnect, and loop
    forever spamming the connect/disconnect sounds. The probe is re-sent each
    tick so a still-booting board isn't rejected for missing the first one.
    The Nano and ESP32 answer "ACK" and the Uno answers "HB" - any line at all
    is proof of life, so we needn't care which board we got.
    """
    ser = serial.Serial(port, BAUD_RATE, timeout=0.3)
    try:
        ser.reset_input_buffer()
        deadline = time.time() + HANDSHAKE_TIMEOUT
        while time.time() < deadline:
            ser.write(b"HB\n")
            ser.flush()
            if ser.readline().decode("utf-8", errors="ignore").strip():
                return ser
    except (serial.SerialException, OSError):
        pass
    try:
        ser.close()  # don't leak a half-open port on every failed probe
    except Exception:
        pass
    return None


def _blueutil_path():
    """Locate blueutil even when PATH is minimal (e.g. a PyInstaller app
    launched from Finder, which doesn't inherit a shell's PATH)."""
    return (shutil.which("blueutil")
            or next((p for p in ("/opt/homebrew/bin/blueutil",
                                 "/usr/local/bin/blueutil")
                     if os.path.exists(p)), None))


def _resolve_bt_addr(port):
    """macOS: map the serial port back to its Bluetooth MAC so a stale link can
    be forced down on disconnect. macOS otherwise keeps the dropped RFCOMM
    channel half-open and refuses to re-establish it until the device is
    manually removed and re-paired. Honours a BT_MAC override in .env."""
    if sys.platform != "darwin" or not port:
        return None
    addr = os.getenv("BT_MAC")
    if addr:
        return addr
    blueutil = _blueutil_path()
    if not blueutil:
        return None
    name = os.path.basename(port).replace("cu.", "").replace("tty.", "")
    try:
        out = subprocess.run([blueutil, "--paired"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if f'name: "{name}"' in line:
            return line.split("address:")[1].split(",")[0].strip()
    return None


def force_bt_disconnect(port):
    """Tear down the OS-level Bluetooth link to the board. After an abrupt power
    loss macOS leaves the RFCOMM channel half-open, which blocks every reconnect
    attempt until it's dropped - this is the programmatic equivalent of 'forget
    & re-add' minus the unpairing, so the next port open() negotiates a fresh
    link. Best-effort and a no-op off macOS / without blueutil."""
    addr = _resolve_bt_addr(port)
    if not addr:
        return
    blueutil = _blueutil_path()
    if not blueutil:
        return
    try:
        subprocess.run([blueutil, "--disconnect", addr],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def play_sound(path):
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
    except Exception as e:
        print(f"  Sound error: {e}")


def get_spotify():
    cache_dir = os.path.join(os.path.expanduser("~"), ".sparc_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, ".spotify_cache")
    auth = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope=SCOPE,
        open_browser=True,
        cache_path=cache_path,
    )
    return spotipy.Spotify(auth_manager=auth)


_ser_write_lock = threading.Lock()


def ser_write(ser, data):
    """Best-effort thread-safe write to the shared serial connection.
    Several threads (heartbeat, volume ramp, command handlers) can write
    to the Arduino concurrently - the lock keeps their messages from
    interleaving mid-line, and a bad/closed port is just silently skipped
    the same way every call site already treated it."""
    if not ser or not ser.is_open:
        return
    try:
        with _ser_write_lock:
            ser.write(data)
            ser.flush()
    except Exception:
        pass


def send_current_volume(sp, ser):
    try:
        playback = sp.current_playback()
        if playback and playback.get("device"):
            vol = playback["device"].get("volume_percent", 0)
            ser_write(ser, f"VOL{vol}\n".encode())
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
        ser_write(ser, b"VS\n")
        print(f"  Already at {'max' if direction == 1 else 'min'} volume")
        return

    while not _volume_stop.wait(VOLUME_INTERVAL):
        try:
            current = max(0, min(100, current + direction * VOLUME_STEP))
            sp.volume(int(current), device_id=device_id)
            print(f"  Volume: {current}%")
            ser_write(ser, f"VOL{int(current)}\n".encode())
            if current in (0, 100):
                _volume_stop.set()
                ser_write(ser, b"VS\n")
                break
        except Exception as e:
            print(f"  Volume error: {e}")
            break


def start_volume(sp, direction, ser):
    global _volume_thread
    note_action("volup" if direction > 0 else "voldown")
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

LAST_ACTION = {"name": None, "time": 0.0}


def note_action(name):
    LAST_ACTION["name"] = name
    LAST_ACTION["time"] = time.time()


def next_track(sp, ser):
    sp.next_track()
    note_action("next")
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
            note_action("restart")
            print("  ↩ Restarted track")
        else:
            sp.previous_track()
            note_action("prev")
            print("  ⏮ Previous track")
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
        note_action("pause")
        print("  Paused")
    else:
        devices = sp.devices().get("devices", [])
        device_id = devices[0]["id"] if devices else None
        sp.start_playback(device_id=device_id)
        note_action("play")
        print("  Resumed")
    time.sleep(0.3)
    send_current_volume(sp, ser)


def handle_stop(sp, ser):
    if volume_active():
        stop_volume()
        if _volume_thread is not None:
            _volume_thread.join(timeout=0.5)
        ser_write(ser, b"VS\n")
        print("  Volume stopped")
    else:
        toggle_pause(sp, ser)


def get_handlers(ser):
    return {
        "S+": lambda sp: next_track(sp, ser),
        "S-": lambda sp: prev_track(sp, ser),
        "V+": lambda sp: start_volume(sp, +1, ser),
        "V-": lambda sp: start_volume(sp, -1, ser),
        "P": lambda sp: handle_stop(sp, ser),
    }


def _serial_reader(ser, line_queue, stop_flag):
    """Runs in its own thread and does nothing but pull lines off the wire.

    If the HC-05 loses power, the underlying OS Bluetooth stack can leave
    a blocking read() hanging well past pyserial's own `timeout` setting -
    often 20-30s - while it waits out its own link-supervision timeout
    before reporting the port as dead. Isolating the read here means that
    hang never stops the main loop from independently noticing (on its own
    clock, via the queue below) that no data has arrived in a while and
    reacting immediately, instead of being stuck waiting for this call to
    return.
    """
    while not stop_flag.is_set():
        try:
            raw = ser.readline()
        except Exception:
            line_queue.put(None)  # sentinel: the port has died
            return
        if not raw:
            continue
        line = raw.decode("utf-8", errors="ignore").strip()
        if line:
            line_queue.put(line)


def _dispatch_command(handler, sp, line):
    """Runs a Spotify command handler on its own thread, so a slow Spotify
    API call can never block the main loop's disconnect-detection timing -
    that coupling was what caused occasional timeouts unrelated to the
    Arduino actually going away."""
    try:
        handler(sp)
    except spotipy.exceptions.SpotifyException as e:
        print(f"  Spotify error: {e}")
    except Exception as e:
        print(f"  Error: {e}")


def run_worker(stop_event, status):
    global BT_PORT, BT_ADDR

    sp = get_spotify()

    print("Connecting to Spotify...")
    try:
        user = sp.current_user()
        user_name = user["display_name"]
        status["spotify"] = "Checking Spotify device..."
        status["spotify_state"] = "wait"
        print(f"Logged in as: {user_name}\n")
    except Exception as e:
        status["spotify"] = "Login failed"
        status["spotify_state"] = "err"
        print(f"Spotify auth failed: {e}")
        return

    ser = None
    active_port = None  # last port that actually handshook - retried first
    arduino_connected = False
    spotify_connected = False
    was_connected = False
    HANDLERS = {}
    line_queue = queue.Queue()
    reader_stop = threading.Event()
    last_heartbeat = 0.0
    last_rx_time = time.time()
    last_spotify_check = 0.0
    # Command handlers now run on their own thread (see _dispatch_command),
    # so a slow Spotify call can no longer stall this loop - ARDUINO_TIMEOUT
    # only has to cover real silence from the Arduino itself.
    ARDUINO_TIMEOUT = 3.0
    HEARTBEAT_INTERVAL = 1.0
    SPOTIFY_CHECK_INTERVAL = 5.0

    def close_arduino_link(state):
        nonlocal ser, arduino_connected, was_connected
        print("Disconnected.")
        status["arduino"] = "Arduino disconnected"
        status["arduino_state"] = state
        arduino_connected = False
        if was_connected:
            play_sound(SOUND_DISCONNECTED)
            was_connected = False
            ser_write(ser, b"VOL0\n")
        reader_stop.set()
        try:
            ser.close()
        except Exception:
            pass
        ser = None
        # Force macOS to drop the (now half-open) Bluetooth channel so the
        # reconnect loop below can negotiate a fresh link when the board comes
        # back - without this, reopening the port silently reuses the stale
        # channel and never reconnects.
        force_bt_disconnect(active_port)

    def update_spotify_status():
        nonlocal spotify_connected
        playing = False
        try:
            playback = sp.current_playback()
            if playback and playback.get("device"):
                spotify_connected = True
                playing = bool(playback.get("is_playing"))
            else:
                devices = sp.devices().get("devices", [])
                spotify_connected = bool(devices)
        except Exception:
            spotify_connected = False
        status["playing"] = playing
        if spotify_connected:
            status["spotify"] = f"Logged in as {user_name}"
            status["spotify_state"] = "ok"
        else:
            status["spotify"] = "No active Spotify device"
            status["spotify_state"] = "err"

    update_spotify_status()
    last_spotify_check = time.time()

    while not stop_event.is_set():
        # --- Bluetooth reconnect FIRST ---
        # This runs before the Spotify check so plugging the Arduino back in
        # is picked up on the very next loop tick instead of waiting behind a
        # (potentially slow) Spotify API round trip. No `continue` on failure
        # here - that's what let the disconnect-sound check get skipped
        # before; instead we just fall through to the state evaluation below
        # every time, connected or not.
        if ser is None or not ser.is_open:
            arduino_connected = False
            ports = resolve_ports(active_port)
            if not ports:
                status["arduino"] = "No controller found"
                status["arduino_state"] = "wait"
            # Probe each candidate in turn: whichever one answers the handshake
            # is the board, be it a Nano, an Uno or an ESP32. Windows can't tell
            # us the device name of a Bluetooth port, so this is what stands in
            # for recognising it by name there.
            for port in ports:
                if stop_event.is_set():
                    break
                status["arduino"] = f"Waiting for controller on {port}..."
                status["arduino_state"] = "wait"
                print(f"Waiting for controller on {port}...")
                try:
                    ser = open_device(port)
                except (serial.SerialException, OSError):
                    ser = None
                if ser:
                    active_port = port
                    _save_cached_port(port)
                    break

            if ser:
                HANDLERS = get_handlers(ser)
                arduino_connected = True
                last_rx_time = time.time()
                last_heartbeat = 0.0
                status["arduino"] = f"Connected on {active_port}"
                status["arduino_state"] = "ok"
                print(f"Controller connected on {active_port}.")
                # Force the Spotify check below to fire immediately, so
                # "both connected" is reflected right away instead of
                # waiting up to SPOTIFY_CHECK_INTERVAL seconds.
                last_spotify_check = 0.0
                # Fresh queue + reader thread for this connection. The old
                # reader thread (if any) is left to exit on its own; it holds
                # its own references and won't touch this new queue.
                line_queue = queue.Queue()
                reader_stop = threading.Event()
                threading.Thread(target=_serial_reader, args=(ser, line_queue, reader_stop), daemon=True).start()

        # --- Periodic Spotify check (independent of serial activity) ---
        now = time.time()
        if now - last_spotify_check >= SPOTIFY_CHECK_INTERVAL:
            update_spotify_status()
            last_spotify_check = now

        # --- Evaluate combined state ---
        # Always runs (nothing above skips past it), so a disconnect is
        # never missed even while reconnect attempts keep failing.
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
            ser_write(ser, b"VOL0\n")

        if not arduino_connected:
            if stop_event.wait(0.5):
                break
            continue

        # --- Main loop: drain lines from the reader thread ---
        # Polling the queue (rather than calling ser.readline() here
        # directly) means this loop's timing is never at the mercy of a
        # blocking read - the timeout check below runs on schedule every
        # ~0.2s no matter how long the reader thread's read() call happens
        # to be stuck for.
        try:
            line = line_queue.get(timeout=0.2)
        except queue.Empty:
            line = ""

        if line is None:
            # Reader thread hit a hard serial error - the link is gone.
            close_arduino_link("wait")
            continue

        if not line:
            # No data right now - check whether the Arduino has gone quiet
            # for too long, or send a heartbeat to check that it's alive.
            if time.time() - last_rx_time > ARDUINO_TIMEOUT:
                close_arduino_link("wait")
                continue
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                ser_write(ser, b"HB\n")
                last_heartbeat = now
            continue

        # Heartbeat reply — update rx time, skip print. The Nano and ESP32
        # sketches answer "ACK", the Uno sketch echoes "HB"; both mean alive.
        if line in ("ACK", "HB"):
            last_rx_time = time.time()
            continue

        last_rx_time = time.time()
        print(f"← {line}")
        handler = HANDLERS.get(line)
        if handler:
            # Dispatched on its own thread - a slow Spotify call here must
            # never delay the next iteration of this loop.
            threading.Thread(target=_dispatch_command, args=(handler, sp, line), daemon=True).start()
        else:
            print(f"  [unrecognized] {repr(line)}")

    # --- Cleanup on quit ---
    stop_volume()
    reader_stop.set()
    ser_write(ser, b"VOL0\n")
    if ser and ser.is_open:
        try:
            ser.close()
        except Exception:
            pass
    # Closing the port is not enough: macOS keeps the RFCOMM channel half-open
    # after we let go of it, and the next run silently reopens that dead channel
    # instead of negotiating a fresh one - which is what forces a manual "forget
    # this device & re-pair" between runs. Drop the link on the way out.
    force_bt_disconnect(active_port)


def main():
    pygame.init()
    W, H = 480, 330
    logo = None
    try:
        logo = pygame.image.load(os.path.join(ASSET_DIR, "logo.png"))
        pygame.display.set_icon(logo)
    except Exception as e:
        print(f"  Logo error: {e}")
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("SPARC Controller")
    if logo:
        logo = logo.convert_alpha()
        logo = pygame.transform.smoothscale(logo, (int(64 * logo.get_width() / logo.get_height()), 64))

    def load_font(filename, size):
        try:
            return pygame.font.Font(os.path.join(ASSET_DIR, filename), size)
        except Exception:
            return pygame.font.SysFont("helveticaneue,helvetica,arial", size)

    title_font = load_font("Poppins-Bold.ttf", 33)
    sub_font = load_font("Poppins-Regular.ttf", 12)
    label_font = load_font("Poppins-SemiBold.ttf", 16)
    status_font = load_font("Poppins-Regular.ttf", 13)
    hint_font = load_font("Poppins-Regular.ttf", 11)

    TEXT = (236, 238, 244)
    DIM = (135, 138, 152)
    CARD = (31, 33, 46)
    GREEN = (30, 215, 96)
    AMBER = (235, 170, 60)
    RED = (226, 85, 85)
    STATE_COLORS = {"ok": GREEN, "wait": RED, "err": RED}
    LOGO_BLUES = [(26, 54, 93), (37, 84, 146), (66, 122, 193), (120, 170, 220)]

    bg = pygame.Surface((W, H))
    top, bottom = (24, 26, 38), (11, 11, 16)
    for y in range(H):
        f = y / H
        color = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        pygame.draw.line(bg, color, (0, y), (W, y))

    def fit_text(font, text, max_width):
        if font.size(text)[0] <= max_width:
            return text
        while text and font.size(text + "…")[0] > max_width:
            text = text[:-1]
        return text + "…"

    def draw_card(y, label, text, state, t):
        rect = pygame.Rect(24, y, W - 48, 52)
        pygame.draw.rect(screen, CARD, rect, border_radius=12)
        color = STATE_COLORS.get(state, AMBER)
        cy = y + 26
        radius = 6 if state != "wait" else 5 + 1.5 * (0.5 + 0.5 * math.sin(t * 4))
        pygame.draw.circle(screen, tuple(c // 3 for c in color), (46, cy), int(radius) + 4)
        pygame.draw.circle(screen, color, (46, cy), int(radius))
        label_img = label_font.render(label, True, TEXT)
        screen.blit(label_img, (64, cy - label_img.get_height() // 2))
        text_img = status_font.render(fit_text(status_font, text, 270), True, DIM)
        screen.blit(text_img, (rect.right - 16 - text_img.get_width(), cy - text_img.get_height() // 2))

    status = {"spotify": "Connecting to Spotify...", "spotify_state": "wait",
              "arduino": "Not connected", "arduino_state": "wait", "playing": False}
    stop_event = threading.Event()
    worker = threading.Thread(target=run_worker, args=(stop_event, status), daemon=True)
    worker.start()

    clock = pygame.time.Clock()
    t0 = time.time()
    eq_t = 0.0
    energy = 0.0
    running = True
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            t = time.time() - t0
            connected = status["spotify_state"] == "ok" and status["arduino_state"] == "ok"
            screen.blit(bg, (0, 0))

            # Header
            for i, blue in enumerate(LOGO_BLUES):
                bh = 14 + 18 * (0.5 + 0.5 * math.sin(t * (1.6 + 0.5 * i) + i * 1.3))
                pygame.draw.rect(screen, blue, (26 + i * 11, 66 - bh, 7, bh), border_radius=2)
            title_img = title_font.render("SPARC", True, TEXT)
            screen.blit(title_img, (78, 16))
            sub_img = sub_font.render("Spotify Proximity and Remote Control", True, DIM)
            screen.blit(sub_img, (80, 58))
            if logo:
                screen.blit(logo, (W - 24 - logo.get_width(), 12))

            draw_card(92, "Spotify", status["spotify"], status["spotify_state"], t)
            draw_card(152, "Arduino", status["arduino"], status["arduino_state"], t)

            eq_base, eq_max = 288, 60
            now = time.time()
            playing = status["playing"]
            if LAST_ACTION["name"] in ("play", "pause") and now - LAST_ACTION["time"] < 2.0:
                playing = LAST_ACTION["name"] == "play"
            dt = clock.get_time() / 1000.0
            energy += ((1.0 if (connected and playing) else 0.0) - energy) * min(1.0, dt * 7.0)
            eq_t += dt * energy

            if connected:
                bars, bar_w, gap = 20, 14, 8
                dim = (38, 88, 58)
                color = tuple(int(dim[i] + (GREEN[i] - dim[i]) * energy) for i in range(3))
                for i in range(bars):
                    wave = 0.55 * (0.5 + 0.5 * math.sin(eq_t * (2.0 + (i % 5) * 0.55) + i * 0.9))
                    wave += 0.45 * (0.5 + 0.5 * math.sin(eq_t * 3.1 + i * 0.5))
                    bh = 8 + eq_max * wave
                    x = 24 + i * (bar_w + gap)
                    pygame.draw.rect(screen, color, (x, eq_base - bh, bar_w, bh), border_radius=4)
                if energy < 0.85:
                    p_img = hint_font.render("PAUSED", True, (110, 160, 128))
                    p_img.set_alpha(int(255 * (1.0 - energy / 0.85)))
                    screen.blit(p_img, (W // 2 - p_img.get_width() // 2, eq_base - eq_max - 22))
            else:
                flatline_y = eq_base - 18
                pygame.draw.line(screen, (150, 70, 70), (24, flatline_y), (W - 24, flatline_y), 2)
                nc_img = label_font.render("NOT CONNECTED", True, RED)
                nc_img.set_alpha(int(160 + 95 * math.sin(t * 2.5)))
                screen.blit(nc_img, (W // 2 - nc_img.get_width() // 2, flatline_y - 36))

            # Gesture overlay
            ap = (now - LAST_ACTION["time"]) / 0.8
            if connected and LAST_ACTION["name"] and 0.0 <= ap < 1.0:
                ease = 1 - (1 - ap) ** 3
                alpha = int(235 * (1 - ap))
                white = (245, 246, 250, alpha)
                overlay = pygame.Surface((W, H), pygame.SRCALPHA)
                cx, cy = W // 2, eq_base - 34
                action = LAST_ACTION["name"]
                if action in ("next", "prev"):
                    slide = 44 * ease * (1 if action == "next" else -1)
                    for k in (-22, 2):
                        x0 = cx + k + slide
                        if action == "next":
                            pts = [(x0, cy - 15), (x0, cy + 15), (x0 + 22, cy)]
                        else:
                            pts = [(x0 + 22, cy - 15), (x0 + 22, cy + 15), (x0, cy)]
                        pygame.draw.polygon(overlay, white, pts)
                elif action == "restart":
                    slide = 44 * ease * -1
                    x0 = cx + slide
                    pts = [(x0 + 22, cy - 15), (x0 + 22, cy + 15), (x0, cy)]
                    pygame.draw.polygon(overlay, white, pts)
                elif action == "play":
                    s = 12 + 10 * ease
                    pygame.draw.polygon(overlay, white,
                                        [(cx - s * 0.7, cy - s), (cx - s * 0.7, cy + s), (cx + s, cy)])
                elif action == "pause":
                    s = 12 + 6 * ease
                    pygame.draw.rect(overlay, white, (cx - s - 4, cy - s, 10, 2 * s), border_radius=3)
                    pygame.draw.rect(overlay, white, (cx + s - 6, cy - s, 10, 2 * s), border_radius=3)
                elif action in ("volup", "voldown"):
                    rise = 16 * ease * (1 if action == "volup" else -1)
                    for j in range(3):
                        stage = max(0.0, min(1.0, ap * 3 - j * 0.6))
                        a_j = int(alpha * stage)
                        if a_j <= 0:
                            continue
                        yy = cy + (14 - j * 13) * (1 if action == "volup" else -1) - rise
                        tip = -9 if action == "volup" else 9
                        pygame.draw.lines(overlay, (245, 246, 250, a_j), False,
                                          [(cx - 15, yy), (cx, yy + tip), (cx + 15, yy)], 5)
                screen.blit(overlay, (0, 0))

            hint_img = hint_font.render("Close this window to quit", True, (110, 112, 126))
            screen.blit(hint_img, (W // 2 - hint_img.get_width() // 2, H - 26))

            pygame.display.flip()
            clock.tick(30)
    except KeyboardInterrupt:
        pass

    print("\nExiting.")
    stop_event.set()
    worker.join(timeout=3)
    pygame.quit()


if __name__ == "__main__":
    main()