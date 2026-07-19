# pygame window, wheel, and animation
# imports run_worker, LAST_ACTION, and ASSET_DIR from core
# All communication still flows through the shared status dict.

import math
import os
import threading
import time

import pygame

from core import ASSET_DIR, LAST_ACTION, run_worker


def main():
    pygame.init()
    W, H = 520, 660
    logo = None
    title = None
    try:
        # icon.png is the macOS-style app icon (white rounded plate, blue
        # symbol); logo.png stays the in-window header art.
        pygame.display.set_icon(pygame.image.load(os.path.join(ASSET_DIR, "icon.png")))
    except Exception as e:
        print(f"  Icon error: {e}")
    try:
        logo = pygame.image.load(os.path.join(ASSET_DIR, "logo_titleless.png"))
    except Exception as e:
        print(f"  Logo error: {e}")
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("SPARC Controller")
    if logo:
        logo = logo.convert_alpha()
        logo = pygame.transform.smoothscale(logo, (int(50 * logo.get_width() / logo.get_height()), 50))

    def load_font(filename, size):
        try:
            return pygame.font.Font(os.path.join(ASSET_DIR, filename), size)
        except Exception:
            return pygame.font.SysFont("helveticaneue,helvetica,arial", size)

    wordmark_font = load_font("Poppins-Bold.ttf", 26)
    sub_font = load_font("Poppins-Regular.ttf", 11)
    track_font = load_font("Poppins-Bold.ttf", 22)
    artist_font = load_font("Poppins-Regular.ttf", 14)
    label_font = load_font("Poppins-SemiBold.ttf", 13)
    status_font = load_font("Poppins-Regular.ttf", 11)
    hint_font = load_font("Poppins-Regular.ttf", 11)

    TEXT = (238, 240, 246)
    DIM = (132, 136, 154)
    CARD = (28, 30, 44)
    GREEN = (30, 215, 96)
    AMBER = (235, 170, 60)
    RED = (226, 85, 85)
    STATE_COLORS = {"ok": GREEN, "wait": RED, "err": RED}
    LOGO_BLUES = [(26, 54, 93), (37, 84, 146), (66, 122, 193), (120, 170, 220)]

    bg = pygame.Surface((W, H))
    top, bottom = (44, 48, 74), (18, 19, 30)
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

    def draw_pill(x, y, w, h, label, text, state, t):
        pygame.draw.rect(screen, CARD, pygame.Rect(x, y, w, h), border_radius=14)
        color = STATE_COLORS.get(state, AMBER)
        cy = y + h // 2
        radius = 5 if state != "wait" else 4 + 1.5 * (0.5 + 0.5 * math.sin(t * 4))
        pygame.draw.circle(screen, tuple(c // 3 for c in color), (x + 21, cy), int(radius) + 4)
        pygame.draw.circle(screen, color, (x + 21, cy), int(radius))
        label_img = label_font.render(label, True, TEXT)
        screen.blit(label_img, (x + 36, y + 6))
        text_img = status_font.render(fit_text(status_font, text, w - 48), True, DIM)
        screen.blit(text_img, (x + 36, y + 8 + label_img.get_height()))

    # ---------- Cover carousel ----------
    # Covers stand on a circular wheel seen from the front and slightly above,
    # like a record carousel on a table: slot 0 faces the viewer, slots ±1 are
    # part-way around the rim, slots ±2 are at the back - raised, small and dim
    # but visible over the top of the front cover thanks to the bird's-eye
    # tilt. A track change spins the whole wheel one slot, so covers visibly
    # rotate to the back on one side and around to the front on the other.
    #
    # That spin is a *guess* - it assumes the new track is exactly one step
    # forward or back from the old one, and picks a spin direction on that
    # assumption. A jump to an arbitrary track in the same playlist (picked
    # from Spotify itself, a queue reorder, anything non-adjacent) breaks
    # that assumption, so it's handled as its own case: no spin, just a
    # crossfade from the old cover to whatever's actually there now. See
    # _classify_transition.
    CAR_CX, CAR_CY = W // 2, 262
    COVER = 195                 # on-screen size of the focused cover
    COVER_BASE = 300            # cached surface size (art is fetched at ~300px)
    RING_SEATS = 11             # one seat per wheel slot; the ring never
                                # shrinks when slots are empty, so neighbouring
                                # records always overlap by the same sliver
    R_X = 175                   # ring horizontal radius on screen, in px
    E_Y = 55                    # ring vertical half-height: the circle seen at
                                # a shallow bird's-eye tilt becomes this ellipse
    KS = 2.8                    # size falloff with depth
    WHEEL_DUR = 0.65
    FADE_DUR = 0.45             # crossfade duration for a same-context jump
    SPIN_DPS = 45.0             # playing record's spin speed (deg/s, ~8s per turn)

    # The records sit on a true ellipse (a circle in perspective). Seats are
    # NOT evenly spaced by angle - even angles bunch discs at the sides and
    # scatter them at the back. Instead each gap gets arc length proportional
    # to the two discs beside it, so every record overlaps its neighbour by
    # the same slight amount all the way around, and the ring closes with no
    # hole at the back.
    _ELL_N = 720
    _ell_th = [2.0 * math.pi * i / _ELL_N for i in range(_ELL_N + 1)]
    _ell_arc = [0.0]
    for _i in range(1, len(_ell_th)):
        _p0 = (R_X * math.sin(_ell_th[_i - 1]), E_Y * math.cos(_ell_th[_i - 1]))
        _p1 = (R_X * math.sin(_ell_th[_i]), E_Y * math.cos(_ell_th[_i]))
        _ell_arc.append(_ell_arc[-1] + math.dist(_p0, _p1))
    _ELL_P = _ell_arc[-1]

    def _theta_at_arc(a):
        a %= _ELL_P
        lo, hi = 0, _ELL_N
        while lo < hi:
            mid = (lo + hi) // 2
            if _ell_arc[mid] < a:
                lo = mid + 1
            else:
                hi = mid
        i = max(1, lo)
        seg = _ell_arc[i] - _ell_arc[i - 1]
        f = (a - _ell_arc[i - 1]) / seg if seg else 0.0
        return _ell_th[i - 1] + f * (_ell_th[i] - _ell_th[i - 1])

    def _disc_scale(th):
        back = (1.0 - math.cos(th)) / 2.0
        return 1.0 / (1.0 + KS * back)

    _seats_cache = {}

    def ring_seats(n):
        if n not in _seats_cache:
            seats = [2.0 * math.pi * k / n for k in range(n)]
            for _ in range(3):  # sizes depend on angles and vice versa; settle
                d = [_disc_scale(t) for t in seats]
                gaps = [(d[k] + d[(k + 1) % n]) / 2.0 for k in range(n)]
                total = sum(gaps)
                a, seats = 0.0, []
                for k in range(n):
                    seats.append(_theta_at_arc(a))
                    a += gaps[k] / total * _ELL_P
            _seats_cache[n] = seats
        return _seats_cache[n]

    def slot_params(s, seats):
        n = len(seats)
        f = s % n
        i = int(f)
        t0 = seats[i]
        t1 = seats[(i + 1) % n] + (2.0 * math.pi if i + 1 >= n else 0.0)
        th = t0 + (t1 - t0) * (f - i)
        c = math.cos(th)
        back = (1.0 - c) / 2.0           # 0 at the front .. 1 at the back apex
        # Queue on the right: advancing a track carries the front of the
        # carousel leftward.
        x = CAR_CX + R_X * math.sin(th)
        y = CAR_CY - E_Y * (1.0 - c)
        scale = 1.0 / (1.0 + KS * back)
        alpha = 255 * (0.33 + 0.67 * ((c + 1.0) / 2.0) ** 0.7)
        return x, y, scale, alpha

    # Every cover is a vinyl record: black disc with faint grooves, the album
    # art cropped to a circle inside, and a spindle dot dead center. Discs are
    # drawn at 2x and downscaled so the circle edges antialias.
    ART_FRAC = 0.62  # art circle diameter as a fraction of the disc
    _D2 = COVER_BASE * 2
    _art_d2 = int(_D2 * ART_FRAC)
    _art_mask = pygame.Surface((_art_d2, _art_d2), pygame.SRCALPHA)
    pygame.draw.circle(_art_mask, (255, 255, 255, 255),
                       (_art_d2 // 2, _art_d2 // 2), _art_d2 // 2)

    def make_disc(art):
        d2 = pygame.Surface((_D2, _D2), pygame.SRCALPHA)
        c = _D2 // 2
        pygame.draw.circle(d2, (16, 16, 21), (c, c), c)                  # vinyl body
        for rr in (0.70, 0.78, 0.86, 0.93):                              # grooves
            pygame.draw.circle(d2, (52, 54, 64), (c, c), int(c * rr), width=2)
        pygame.draw.circle(d2, (104, 107, 120), (c, c), c - 1, width=2)  # rim light
        if art is not None:
            a = pygame.transform.smoothscale(art, (_art_d2, _art_d2)).convert_alpha()
            a.blit(_art_mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            d2.blit(a, (c - _art_d2 // 2, c - _art_d2 // 2))
        else:
            pygame.draw.circle(d2, CARD, (c, c), _art_d2 // 2)
            for i, blue in enumerate(LOGO_BLUES):
                bh = 92 + 44 * (i % 2)
                pygame.draw.rect(d2, blue, (c - 88 + i * 48, c + 80 - bh, 28, bh),
                                 border_radius=8)
        pygame.draw.circle(d2, (10, 10, 14), (c, c), int(_D2 * 0.032))   # spindle
        pygame.draw.circle(d2, (120, 122, 134), (c, c), int(_D2 * 0.032), width=2)
        return pygame.transform.smoothscale(d2, (COVER_BASE, COVER_BASE))

    placeholder = make_disc(None)
    cover_cache = {}
    scaled_cache = {}

    def scaled_disc(base, size):
        # Static frames redraw the same discs at the same sizes 60x a second;
        # cache them. Animation frames bypass this (sizes change every frame).
        key = (id(base), size)
        if key not in scaled_cache:
            if len(scaled_cache) > 96:
                scaled_cache.clear()
            scaled_cache[key] = pygame.transform.smoothscale(base, (size, size))
        return scaled_cache[key]

    def cover_surface(track):
        art = track.get("art") if track else None
        if art is None:
            return placeholder
        key = id(art)  # art surfaces are cached per URL, so identity is stable
        if key not in cover_cache:
            if len(cover_cache) > 32:
                cover_cache.clear()
            cover_cache[key] = make_disc(art)
        return cover_cache[key]

    # Ambient glow behind the wheel, tinted with the current cover's colour.
    _glow_dot = pygame.Surface((64, 64), pygame.SRCALPHA)
    for gy in range(64):
        for gx in range(64):
            d = math.hypot(gx - 31.5, gy - 31.5) / 32.0
            _glow_dot.set_at((gx, gy), (255, 255, 255, int(120 * max(0.0, 1.0 - d) ** 2.2)))
    _glow_base = pygame.transform.smoothscale(_glow_dot, (560, 560))
    glow_cache = {}

    def glow_surface(track):
        art = track.get("art") if track else None
        key = id(art) if art is not None else None
        if key not in glow_cache:
            if art is not None:
                r, g, b, _ = pygame.transform.average_color(art)
                m = max(r, g, b, 1)
                color = tuple(min(255, int(c * 210 / m)) for c in (r, g, b))
            else:
                color = (66, 122, 193)
            if len(glow_cache) > 32:
                glow_cache.clear()
            tinted = _glow_base.copy()
            tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            glow_cache[key] = tinted
        return glow_cache[key]

    def ease(p):
        return p * p * (3.0 - 2.0 * p)

    # hist is the played-so-far order (UI-side memory, oldest first, capped);
    # it fills the back-left of the wheel with real tracks, which the worker's
    # single inferred "prev" can't. wheel maps slot -> track for slots -5..5.
    # pins hold tracks the UI knows belong in a slot ahead of what the queue
    # API reports - after a back-skip, the song just left IS the next song,
    # but Spotify's queue endpoint can lag behind for a poll or two. Each pin
    # overrides its slot until the live data catches up (or it expires).
    # fade holds an in-flight crossfade (see _classify_transition's "jump"
    # case): a snapshot of the wheel as it was right before the jump, plus a
    # start time, so the old covers can be faded out in place while the new
    # ones (already the live, correct wheel) fade in over them.
    car = {"cur_id": None, "anim": None, "hist": [], "pins": {}, "seeded": False,
            "wheel": {s: None for s in range(-5, 6)}, "fade": None}

    def _classify_transition(new_id, now):
        # Forward and backward are the two moves we can predict: forward
        # always lands on the previously-known "next" track (queue head,
        # respecting pins); backward always lands back on whatever was
        # current a moment ago (the freshest history entry). Anything else -
        # a track picked from elsewhere in the same playlist, a skip to a
        # non-adjacent point in the queue, a reorder, etc. - is a jump: we
        # know the track changed, but not which "direction" it came from, so
        # guessing a spin direction would just as often be wrong as right.
        old_next = car["wheel"][1]
        old_prev = car["wheel"][-1]
        if old_next is not None and new_id == old_next.get("id"):
            return "forward"
        if old_prev is not None and new_id == old_prev.get("id"):
            return "backward"
        if old_next is None and old_prev is None:
            # Cold start - nothing on the wheel yet to compare against.
            # Fall back to whatever gesture was just sent, if any.
            if LAST_ACTION["name"] in ("next", "prev") and now - LAST_ACTION["time"] < 8.0:
                return "backward" if LAST_ACTION["name"] == "prev" else "forward"
        return "jump"

    def draw_carousel(now, status, t, energy, spin_deg):
        cur = status.get("track_current")
        new_id = cur.get("id") if cur else None
        if new_id != car["cur_id"]:
            if (car["cur_id"] is not None and new_id is not None
                    and car["anim"] is None and car["fade"] is None):
                transition = _classify_transition(new_id, now)
                if transition == "backward":
                    # Outgoing: the far queue cover rotates around the back,
                    # crossfading with the history cover entering at slot -5.
                    car["anim"] = {"t0": now, "dir": -1, "out": car["wheel"][5],
                                   "base": 6, "in": -5}
                    if car["hist"]:
                        car["hist"].pop()
                    car["pins"] = {s: (car["wheel"][s - 1], now + 6.0)
                                   for s in range(1, 6) if car["wheel"][s - 1]}
                elif transition == "forward":
                    car["anim"] = {"t0": now, "dir": 1, "out": car["wheel"][-5],
                                   "base": -6, "in": 5}
                    if car["wheel"][0]:
                        car["hist"] = (car["hist"] + [car["wheel"][0]])[-12:]
                    car["pins"] = {}
                else:
                    # Jump: no spin, no pin guesses about the new queue - just
                    # crossfade every slot from its old cover to whatever's
                    # really there now. The track we jumped away from was
                    # still just playing, so it still becomes history.
                    if car["wheel"][0]:
                        car["hist"] = (car["hist"] + [car["wheel"][0]])[-12:]
                    car["pins"] = {}
                    car["fade"] = {"t0": now, "wheel": dict(car["wheel"])}
            car["cur_id"] = new_id
        hist = car["hist"]
        q = status.get("track_queue") or []
        right = [q[i] if i < len(q) else None for i in range(5)]
        right_ids = {tr["id"] for tr in right + [cur] if tr and tr.get("id")}

        def left_slot(k):
            # A left cover that also sits on the right (tracks skipped past
            # earlier reappear in the queue) would show the same art twice -
            # prefer leaving the slot empty. Same when there's no history yet
            # for this slot (hist only grows from real spins this session) -
            # no guessed fallback, since a wrong guess is worse than a blank
            # seat.
            tr = hist[-k] if len(hist) >= k else None
            return tr if tr and tr.get("id") not in right_ids else None

        car["wheel"] = {0: cur}
        for k in range(1, 6):
            car["wheel"][-k] = left_slot(k)
            car["wheel"][k] = right[k - 1]
        for slot, (tr, expiry) in list(car["pins"].items()):
            live = car["wheel"][slot]
            if now > expiry or (live and live.get("id") == tr.get("id")):
                del car["pins"][slot]  # live data caught up (or gave up waiting)
            else:
                car["wheel"][slot] = tr

        offset, pe = 0.0, 1.0
        items = []
        anim = car["anim"]
        fade = car["fade"]
        if anim:
            p = (now - anim["t0"]) / WHEEL_DUR
            if p >= 1.0:
                car["anim"] = anim = None
            else:
                pe = ease(p)
                offset = anim["dir"] * (1.0 - pe)
                if anim["out"]:
                    items.append((anim["out"], anim["base"] + offset, 1.0 - pe))
        elif fade:
            p = (now - fade["t0"]) / FADE_DUR
            if p >= 1.0:
                car["fade"] = fade = None
            else:
                # Same slot, no lateral movement - the old cover just fades
                # out while the new one (added below, from the live wheel)
                # fades in on top of it.
                pe = ease(p)
                for slot, track in fade["wheel"].items():
                    if track is None and slot != 1:
                        continue  # matches the live-wheel filter just below
                    items.append((track, slot, 1.0 - pe))

        glow = glow_surface(cur)
        glow.set_alpha(int(115 + 55 * energy * (0.5 + 0.5 * math.sin(t * 2.2))))
        screen.blit(glow, glow.get_rect(center=(CAR_CX, CAR_CY)))

        for slot, track in car["wheel"].items():
            if track is None and slot != 1:
                continue  # empty seats stay empty; only +1 shows a placeholder
            if fade:
                amult = pe  # fading in uniformly, in place
            else:
                amult = pe if (anim and slot == anim["in"]) else 1.0
            items.append((track, slot + offset, amult))
        seats = ring_seats(RING_SEATS)
        drawlist = []
        for track, s, amult in items:
            x, y, scale, alpha = slot_params(s, seats)
            if alpha * amult > 2:
                drawlist.append((scale, x, y, alpha * amult, track))
        anim_active = car["anim"] is not None
        for scale, x, y, alpha, track in sorted(drawlist, key=lambda d: d[0]):
            size = max(2, int(COVER * scale))
            base = cover_surface(track)
            if track and track.get("id") == car["cur_id"]:
                # Only the playing record spins (clockwise, like a turntable).
                surf = pygame.transform.rotozoom(base, -spin_deg, size / COVER_BASE)
            elif anim_active:
                surf = pygame.transform.smoothscale(base, (size, size))
            else:
                surf = scaled_disc(base, size)
            surf.set_alpha(int(alpha))
            screen.blit(surf, surf.get_rect(center=(int(x), int(y))))
        return cur

    status = status = {"spotify": "Connecting to Spotify...", "spotify_state": "wait",
              "arduino": "Not connected", "arduino_state": "wait", "playing": False,
              "track_current": None, "track_prev": None, "track_queue": [],
              "track_history": None, "context_uri": None}
    stop_event = threading.Event()
    worker = threading.Thread(target=run_worker, args=(stop_event, status), daemon=True)
    worker.start()

    clock = pygame.time.Clock()
    t0 = time.time()
    eq_t = 0.0
    energy = 0.0
    spin_deg = 0.0
    was_connected = False
    last_context_uri = None
    running = True
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            t = time.time() - t0
            now = time.time()
            connected = status["spotify_state"] == "ok" and status["arduino_state"] == "ok"
            connected = status["spotify_state"] == "ok" and status["arduino_state"] == "ok"
            if connected and not was_connected:
                # Reconnecting shouldn't wipe the played-so-far history - only
                # the animation/pin state, which can be left stale mid-drop.
                car["pins"] = {}
                car["cur_id"] = None
                car["anim"] = None
                car["fade"] = None
            was_connected = connected

            context_uri = status.get("context_uri")
            if context_uri and context_uri != last_context_uri:
                if last_context_uri is not None:
                    # Switched playlist/album/context - queue and history
                    # belong to the old one, so hard-cut the wheel instead of
                    # animating through or carrying over stale covers.
                    car["hist"] = []
                    car["pins"] = {}
                    car["cur_id"] = None
                    car["anim"] = None
                    car["fade"] = None
                last_context_uri = context_uri
            playing = status["playing"]
            if LAST_ACTION["name"] in ("play", "pause") and now - LAST_ACTION["time"] < 2.0:
                playing = LAST_ACTION["name"] == "play"
            dt = clock.get_time() / 1000.0
            energy += ((1.0 if (connected and playing) else 0.0) - energy) * min(1.0, dt * 7.0)
            eq_t += dt * energy
            # Riding on energy makes the record spin down/up smoothly around
            # pause/play, like a real platter.
            spin_deg = (spin_deg + dt * SPIN_DPS * energy) % 360.0

            screen.blit(bg, (0, 0))

            # Header
            for i, blue in enumerate(LOGO_BLUES):
                bh = 10 + 14 * (0.5 + 0.5 * math.sin(t * (1.6 + 0.5 * i) + i * 1.3))
                pygame.draw.rect(screen, blue, (26 + i * 9, 60 - bh, 6, bh), border_radius=2)
            try:
                title = pygame.image.load(os.path.join(ASSET_DIR, "title.png"))
            except Exception as e:
                print(f"  Title error: {e}")
            sub_img = sub_font.render("Spotify Proximity and Remote Control", True, DIM)
            screen.blit(sub_img, (70, 52))
            if logo:
                screen.blit(logo, (W - 24 - logo.get_width(), 20))
            if title:
                title = title.convert_alpha()
                title = pygame.transform.smoothscale(title, (int(24 * title.get_width() / title.get_height()), 24))
                screen.blit(title, (67, 28))
            # Cover wheel + track text
            cur = draw_carousel(now, status, t, energy, spin_deg)
            text_y = 404
            if cur:
                name_img = track_font.render(fit_text(track_font, cur["name"], W - 70), True, TEXT)
                artist_img = artist_font.render(fit_text(artist_font, cur["artist"], W - 90), True, DIM)
                screen.blit(name_img, (W // 2 - name_img.get_width() // 2, text_y))
                screen.blit(artist_img, (W // 2 - artist_img.get_width() // 2,
                                         text_y + name_img.get_height() + 2))
            else:
                empty_img = artist_font.render("Nothing playing", True, DIM)
                screen.blit(empty_img, (W // 2 - empty_img.get_width() // 2, text_y + 12))

            # EQ strip / connection state
            eq_base = 516
            if connected:
                bars, bar_w, gap = 15, 8, 5
                x0 = (W - (bars * bar_w + (bars - 1) * gap)) // 2
                dim = (38, 88, 58)
                color = tuple(int(dim[i] + (GREEN[i] - dim[i]) * energy) for i in range(3))
                for i in range(bars):
                    wave = 0.55 * (0.5 + 0.5 * math.sin(eq_t * (2.0 + (i % 5) * 0.55) + i * 0.9))
                    wave += 0.45 * (0.5 + 0.5 * math.sin(eq_t * 3.1 + i * 0.5))
                    bh = 5 + 24 * wave
                    pygame.draw.rect(screen, color, (x0 + i * (bar_w + gap), eq_base - bh, bar_w, bh),
                                     border_radius=3)
                if energy < 0.85:
                    p_img = hint_font.render("PAUSED", True, (110, 160, 128))
                    p_img.set_alpha(int(255 * (1.0 - energy / 0.85)))
                    screen.blit(p_img, (W // 2 - p_img.get_width() // 2, eq_base - 48))
            else:
                flatline_y = eq_base - 12
                pygame.draw.line(screen, (150, 70, 70), (W // 2 - 110, flatline_y),
                                 (W // 2 + 110, flatline_y), 2)
                nc_img = label_font.render("NOT CONNECTED", True, RED)
                nc_img.set_alpha(int(160 + 95 * math.sin(t * 2.5)))
                screen.blit(nc_img, (W // 2 - nc_img.get_width() // 2, flatline_y - 32))

            # Gesture overlay - drawn over the focused cover.
            ap = (now - LAST_ACTION["time"]) / 0.8
            if connected and LAST_ACTION["name"] and 0.0 <= ap < 1.0:
                ease_a = 1 - (1 - ap) ** 3
                alpha = int(235 * (1 - ap))
                white = (245, 246, 250, alpha)
                overlay = pygame.Surface((W, H), pygame.SRCALPHA)
                cx, cy = CAR_CX, CAR_CY
                pygame.draw.circle(overlay, (8, 9, 14, int(110 * (1 - ap))),
                                   (cx, cy), COVER // 2)
                action = LAST_ACTION["name"]
                if action in ("next", "prev"):
                    slide = 44 * ease_a * (1 if action == "next" else -1)
                    for k in (-22, 2):
                        x0 = cx + k + slide
                        if action == "next":
                            pts = [(x0, cy - 15), (x0, cy + 15), (x0 + 22, cy)]
                        else:
                            pts = [(x0 + 22, cy - 15), (x0 + 22, cy + 15), (x0, cy)]
                        pygame.draw.polygon(overlay, white, pts)
                elif action == "restart":
                    slide = 44 * ease_a * -1
                    x0 = cx + slide
                    pts = [(x0 + 22, cy - 15), (x0 + 22, cy + 15), (x0, cy)]
                    pygame.draw.polygon(overlay, white, pts)
                elif action == "play":
                    s = 12 + 10 * ease_a
                    pygame.draw.polygon(overlay, white,
                                        [(cx - s * 0.7, cy - s), (cx - s * 0.7, cy + s), (cx + s, cy)])
                elif action == "pause":
                    s = 12 + 6 * ease_a
                    pygame.draw.rect(overlay, white, (cx - s - 4, cy - s, 10, 2 * s), border_radius=3)
                    pygame.draw.rect(overlay, white, (cx + s - 6, cy - s, 10, 2 * s), border_radius=3)
                elif action in ("volup", "voldown"):
                    rise = 16 * ease_a * (1 if action == "volup" else -1)
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

            # Status pills
            pill_w = (W - 48 - 12) // 2
            draw_pill(24, 560, pill_w, 46, "Spotify", status["spotify"], status["spotify_state"], t)
            draw_pill(24 + pill_w + 12, 560, pill_w, 46, "SPARC",
                      status["arduino"], status["arduino_state"], t)

            hint_img = hint_font.render("Close this window to quit", True, (104, 106, 120))
            screen.blit(hint_img, (W // 2 - hint_img.get_width() // 2, H - 28))

            pygame.display.flip()
            clock.tick(60)
    except KeyboardInterrupt:
        pass

    print("\nExiting.")
    stop_event.set()
    worker.join(timeout=3)
    pygame.quit()


if __name__ == "__main__":
    main()