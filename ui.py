# pygame window, wheel, and animation
# imports run_worker, LAST_ACTION, and ASSET_DIR from core
# All communication still flows through the shared status dict.

import math
import os
import random
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
    track_font = load_font("Poppins-Bold.ttf", 17)
    artist_font = load_font("Poppins-Regular.ttf", 12)
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

    # Centre of the platter. Lives up here because the background's grooves
    # are drawn around it too - the window is meant to read as one big record
    # with the carousel sitting on its spindle.
    CAR_CX, CAR_CY = W // 2, 262

    # ---------- Background ----------
    # The vinyl itself, seen up close: a deep three-stop gradient, concentric
    # grooves around the spindle, and a diagonal sheen where light catches the
    # surface. Built once at 2x and downscaled so the groove rings come out
    # smooth instead of stair-stepped.
    def build_background():
        S = 2
        surf = pygame.Surface((W * S, H * S))
        top, mid, bottom = (46, 50, 84), (30, 32, 54), (13, 14, 23)
        for y in range(H * S):
            f = y / (H * S)
            a, b, g = (top, mid, f / 0.55) if f < 0.55 else (mid, bottom, (f - 0.55) / 0.45)
            color = tuple(int(a[i] + (b[i] - a[i]) * g) for i in range(3))
            pygame.draw.line(surf, color, (0, y), (W * S, y))

        # Grooves: evenly spaced like a real record, fading out as they run
        # off toward the corners. Alternating brightness gives the surface a
        # bit of tooth without turning into a moiré pattern.
        cx, cy = CAR_CX * S, CAR_CY * S
        corner = math.hypot(max(cx, W * S - cx), max(cy, H * S - cy))
        r, i = int(30 * S), 0
        while r < corner:
            fade = 1.0 - (r / corner) ** 1.7
            k = (7.0 if i % 2 else 11.0) * fade
            pygame.draw.circle(surf, (int(70 * k / 11), int(74 * k / 11), int(96 * k / 11)),
                               (cx, cy), r, width=S)
            r += int(10 * S)
            i += 1

        # Sheen: a soft diagonal band, added rather than blended so it only
        # ever lifts the surface it crosses.
        sheen = pygame.Surface((W * S, H * S))
        band = int(W * S * 0.6)
        for x in range(band):
            w = math.sin(math.pi * x / band) ** 2
            pygame.draw.line(sheen, (int(15 * w), int(19 * w), int(28 * w)),
                             (x, 0), (x, H * S))
        sheen = pygame.transform.rotate(sheen, -28)
        surf.blit(sheen, sheen.get_rect(center=(W * S // 2, H * S // 2)),
                  special_flags=pygame.BLEND_RGB_ADD)

        # Phonograph cabinet + neck. The horn's bell lives on its own layer
        # (build_horn, below) so it can stand *between* the carousel's back
        # and front rows; only the parts records never overlap - the neck
        # sliver under the front record and the wooden body - are baked in
        # here. Drawn at 2x so the curves antialias on the way down.
        #
        # The box is in oblique perspective, like a crate icon: the front
        # face is the near plane and every depth edge runs parallel
        # up-and-to-the-right (with a small convergence on the far edges),
        # exposing the top face and the right side face. The whole box sits
        # shifted a little left of the window centre so the middle of its
        # TOP face - where the horn's neck sockets in - lands exactly under
        # the bell at x=260.
        fx0, fy0, fw, fh = 105 * S, 394 * S, 280 * S, 64 * S    # front face
        dpx, dpy = 30 * S, 20 * S                               # depth vector
        cvg = 4 * S                                             # far-edge convergence
        ftl = (fx0, fy0)                                        # front corners
        ftr = (fx0 + fw, fy0)
        fbr = (fx0 + fw, fy0 + fh)
        btl = (fx0 + dpx + cvg, fy0 - dpy)                      # back corners
        btr = (fx0 + fw + dpx - cvg, fy0 - dpy)
        bbr = (fx0 + fw + dpx - cvg, fy0 + fh - dpy + cvg)
        pygame.draw.rect(surf, (13, 12, 18),                    # ground shadow
                         (fx0 - 3 * S, fy0 + 6 * S, fw + dpx + 6 * S, fh + 4 * S),
                         border_radius=12 * S)
        pygame.draw.polygon(surf, (80, 59, 44), [ftl, ftr, btr, btl])  # top face
        pygame.draw.polygon(surf, (20, 14, 10), [ftr, btr, bbr, fbr])  # right side face
        # top-face grain, running between the receding side edges
        pygame.draw.line(surf, (62, 45, 34), (129 * S, 380 * S), (403 * S, 380 * S), S)
        pygame.draw.line(surf, (62, 45, 34), (119 * S, 386 * S), (396 * S, 386 * S), S)
        pygame.draw.line(surf, (62, 45, 34), (112 * S, 390 * S), (390 * S, 390 * S), S)
        pygame.draw.line(surf, (36, 25, 19), btl, btr, S)       # top back edge
        pygame.draw.line(surf, (54, 39, 29), ftr, btr, S)       # top-right depth edge
        pygame.draw.line(surf, (40, 29, 22), btr, bbr, S)       # back-right arris
        pygame.draw.rect(surf, (14, 11, 9),                     # back-right foot
                         (398 * S, 445 * S, 10 * S, 9 * S))
        pygame.draw.line(surf, (12, 9, 7), fbr, bbr, S)         # side bottom edge
        pygame.draw.ellipse(surf, (16, 12, 9),                  # socket hole
                            (252 * S, 382 * S, 16 * S, 5 * S))
        pygame.draw.polygon(surf, (96, 76, 48),                 # neck sliver
                            [(253 * S, 344 * S), (267 * S, 344 * S),
                             (264 * S, 385 * S), (256 * S, 385 * S)])
        pygame.draw.line(surf, (150, 122, 78),                  # neck glint
                         (257 * S, 348 * S), (258 * S, 382 * S), S)
        pygame.draw.ellipse(surf, (152, 124, 78),               # socket collar
                            (249 * S, 380 * S, 22 * S, 8 * S), width=S)
        # Front face: espresso wood lit from above - vertical gradient,
        # grain, and shaded outer edges. Square corners on purpose: the
        # side faces fold away along crisp arrises.
        wood = pygame.Surface((fw, fh), pygame.SRCALPHA)
        for i in range(fh):
            f = i / fh
            pygame.draw.line(wood, (int(46 - 19 * f), int(33 - 14 * f),
                                    int(25 - 10 * f), 255), (0, i), (fw, i))
        for gy in (0.25, 0.48, 0.72, 0.90):                     # wood grain
            yy = fh * gy
            pts = [(6 * S + i * (fw - 12 * S) / 40.0,
                    yy + math.sin(i * 0.7 + gy * 11.0) * 1.3 * S) for i in range(41)]
            pygame.draw.lines(wood, (33, 23, 18), False, pts, S)
        shade = pygame.Surface((fw, fh), pygame.SRCALPHA)       # edge falloff
        for i in range(18 * S):
            a = int(70 * (1.0 - i / (18.0 * S)) ** 1.5)
            pygame.draw.line(shade, (0, 0, 0, a), (i, 0), (i, fh))
            pygame.draw.line(shade, (0, 0, 0, a), (fw - 1 - i, 0), (fw - 1 - i, fh))
        wood.blit(shade, (0, 0))
        surf.blit(wood, (fx0, fy0))
        pygame.draw.line(surf, (84, 61, 45), (fx0, fy0),
                         (fx0 + fw, fy0), S)                    # lit top arris
        pygame.draw.line(surf, (20, 14, 11), (fx0, fy0),
                         (fx0, fy0 + fh), S)                    # front arrises
        pygame.draw.line(surf, (20, 14, 11), (fx0 + fw, fy0),
                         (fx0 + fw, fy0 + fh), S)
        pygame.draw.rect(surf, (30, 21, 16),                    # base plinth
                         (fx0, fy0 + fh - 6 * S, fw, 6 * S))
        pygame.draw.line(surf, (56, 40, 30), (fx0 + 4 * S, fy0 + fh - 6 * S),
                         (fx0 + fw - 4 * S, fy0 + fh - 6 * S), S)
        pygame.draw.rect(surf, (24, 17, 14), (121 * S, 402 * S, 248 * S, 50 * S),
                         border_radius=6 * S)                   # title panel
        # frame drawn 2px wide at a moderate brown: a 1px bright hairline on
        # near-black reads as white no matter its hue - width is what lets
        # the eye see the colour
        pygame.draw.rect(surf, (98, 68, 44), (121 * S, 402 * S, 248 * S, 50 * S),
                         width=2 * S, border_radius=6 * S)      # brown frame
        pygame.draw.rect(surf, (12, 9, 7), (125 * S, 406 * S, 240 * S, 42 * S),
                         width=S, border_radius=5 * S)          # inner groove
        for sx, sy in ((113, 407), (377, 407), (113, 447), (377, 447)):
            pygame.draw.circle(surf, (118, 92, 58), (sx * S, sy * S), 2 * S)
            pygame.draw.line(surf, (60, 46, 30), ((sx - 1) * S, sy * S),
                             ((sx + 1) * S, sy * S), S)         # screw slots
        # winding crank, poking out of the right side face
        pygame.draw.rect(surf, (150, 122, 78), (396 * S, 424 * S, 20 * S, 4 * S))
        pygame.draw.rect(surf, (104, 82, 52), (396 * S, 426 * S, 20 * S, 2 * S))
        pygame.draw.circle(surf, (150, 122, 78), (416 * S, 426 * S), 3 * S)
        pygame.draw.rect(surf, (128, 102, 64), (414 * S, 426 * S, 4 * S, 20 * S))
        pygame.draw.rect(surf, (74, 52, 36), (411 * S, 444 * S, 10 * S, 13 * S),
                         border_radius=4 * S)                   # grip
        pygame.draw.line(surf, (108, 78, 54), (413 * S, 446 * S),
                         (413 * S, 454 * S), S)
        for fx in (113, 351):                                   # tapered feet
            pygame.draw.polygon(surf, (16, 12, 10),
                                [(fx * S, fy0 + fh), ((fx + 26) * S, fy0 + fh),
                                 ((fx + 22) * S, fy0 + fh + 8 * S),
                                 ((fx + 4) * S, fy0 + fh + 8 * S)])

        surf = pygame.transform.smoothscale(surf, (W, H))

        # Vignette, built small and scaled up - a per-pixel loop at full size
        # would cost seconds. Pulls the corners down so the platter reads as
        # the lit part of the frame.
        vig = pygame.Surface((64, 64))
        for y in range(64):
            for x in range(64):
                d = math.hypot((x - 31.5) / 31.5, (y - 31.5) / 31.5) / 1.414
                k = int(255 * (1.0 - 0.5 * min(1.0, d) ** 2.0))
                vig.set_at((x, y), (k, k, k))
        surf.blit(pygame.transform.smoothscale(vig, (W, H)), (0, 0),
                  special_flags=pygame.BLEND_RGB_MULT)

        # ---------- Music notes ----------
        # A handful of bright accents around the frame. Placed big-to-small
        # with a generous mutual spacing so they ring the composition rather
        # than crowd it, kept off every foreground element, and drawn after
        # the vignette so corner notes keep their pop. Fixed seed: the same
        # arrangement every launch.
        NOTE_COLS = ((118, 190, 255), (168, 214, 255), (255, 202, 116),
                     (126, 226, 170))
        rng = random.Random(11)

        def note_sprite(size, col):
            d = size * 2  # drawn big, halved by rotozoom for antialiasing
            s = pygame.Surface((d, d), pygame.SRCALPHA)
            hw, hh, sw = int(d * 0.16), int(d * 0.11), max(2, d // 16)
            if rng.random() < 0.5:  # single quaver
                hx, hy, top = int(d * 0.34), int(d * 0.78), int(d * 0.22)
                pygame.draw.ellipse(s, col, (hx - hw, hy - hh, hw * 2, hh * 2))
                sx = hx + hw - sw
                pygame.draw.rect(s, col, (sx, top, sw, hy - top))
                pygame.draw.polygon(s, col, [                   # flag
                    (sx + sw, top), (sx + sw + int(d * 0.20), top + int(d * 0.16)),
                    (sx + sw + int(d * 0.12), top + int(d * 0.36)),
                    (sx + sw + int(d * 0.05), top + int(d * 0.32)),
                    (sx + sw + int(d * 0.11), top + int(d * 0.16))])
            else:  # beamed pair
                x1, y1, t1 = int(d * 0.26), int(d * 0.80), int(d * 0.30)
                x2, y2, t2 = int(d * 0.66), int(d * 0.74), int(d * 0.24)
                for hx, hy in ((x1, y1), (x2, y2)):
                    pygame.draw.ellipse(s, col, (hx - hw, hy - hh, hw * 2, hh * 2))
                pygame.draw.rect(s, col, (x1 + hw - sw, t1, sw, y1 - t1))
                pygame.draw.rect(s, col, (x2 + hw - sw, t2, sw, y2 - t2))
                pygame.draw.polygon(s, col, [(x1 + hw - sw, t1), (x2 + hw, t2),
                                             (x2 + hw, t2 + int(d * 0.10)),
                                             (x1 + hw - sw, t1 + int(d * 0.10))])
            s = pygame.transform.rotozoom(s, rng.uniform(-40, 40), 0.5)
            s.set_alpha(rng.randint(150, 215))
            return s

        def in_the_open(x, y, r):
            if ((x - CAR_CX) / (210.0 + r)) ** 2 + ((y - CAR_CY) / (150.0 + r)) ** 2 < 1.0:
                return False                          # platter
            if 160 - r < x < 360 + r and y < 190 + r:
                return False                          # horn bell
            if 70 - r < x < 460 + r and 368 - r < y < 486 + r:
                return False                          # phonograph + crank
            if 20 - r < x < 500 + r and 552 - r < y < 612 + r:
                return False                          # status pills
            if 150 - r < x < 372 + r and 458 - r < y < 522 + r:
                return False                          # EQ strip / status text
            if x < 340 + r and y < 78 + r:
                return False                          # wordmark + subtitle
            if x > 415 - r and y < 88 + r:
                return False                          # header logo
            if 130 - r < x < 390 + r and y > 618 - r:
                return False                          # quit hint
            return True

        placed = []
        for size in (62, 52, 44, 37, 31, 25):
            r = size * 0.42
            for _ in range(200):
                x, y = rng.uniform(22, W - 22), rng.uniform(26, H - 26)
                if (in_the_open(x, y, r) and
                        all(math.hypot(x - px, y - py) > r + pr + 70
                            for px, py, pr in placed)):
                    break
            else:
                continue  # no room left for this one
            placed.append((x, y, r))
            img = note_sprite(size, rng.choice(NOTE_COLS))
            surf.blit(img, img.get_rect(center=(int(x), int(y))))
        return surf

    bg = build_background()

    def build_horn():
        # The horn's bell on its own transparent layer: draw_carousel blits
        # it after the small back-row records but before the big front ones,
        # so the trumpet stands *between* the two rows. Drawn at 2x and
        # halved, like the background.
        S = 2
        HW, HH = 170, 118
        s = pygame.Surface((HW * S, HH * S), pygame.SRCALPHA)
        cx, cy = HW * S // 2, 50 * S
        brx, bry, tilt = 68 * S, 42 * S, 9 * S

        def bell_y(f):
            # Rings shift down as they shrink: the mouth sits low in the
            # rim, so the horn reads as tilted up toward the viewer - the
            # same shallow bird's-eye angle the carousel is drawn at.
            return cy + (1.0 - f) * tilt

        pygame.draw.polygon(s, (96, 76, 48),        # neck, running off the
                            [(cx - 7 * S, cy),      # sprite's bottom; the
                             (cx + 7 * S, cy),      # front record covers
                             (cx + 4 * S, HH * S),  # where it ends
                             (cx - 4 * S, HH * S)])
        pygame.draw.line(s, (150, 122, 78), (cx - 4 * S, cy + 44 * S),
                         (cx - 2 * S, HH * S - 2 * S), S)
        for f, col in ((1.0, (117, 94, 59)), (0.80, (100, 80, 50)),
                       (0.60, (82, 66, 42)), (0.40, (56, 45, 30))):
            rw, rh = int(brx * f), int(bry * f)
            pygame.draw.ellipse(s, col, (cx - rw, int(bell_y(f)) - rh, rw * 2, rh * 2))
        for k in range(8):                          # petal seams
            a = math.tau * (k + 0.5) / 8.0
            pygame.draw.line(s, (134, 108, 68),
                             (cx + 0.44 * brx * math.cos(a), bell_y(0.44) + 0.44 * bry * math.sin(a)),
                             (cx + 0.96 * brx * math.cos(a), bell_y(0.96) + 0.96 * bry * math.sin(a)), S)
        rw, rh = int(brx * 0.24), int(bry * 0.24)
        pygame.draw.ellipse(s, (22, 17, 13),        # the mouth
                            (cx - rw, int(bell_y(0.24)) - rh, rw * 2, rh * 2))
        pygame.draw.ellipse(s, (64, 51, 34),        # throat wall catch-light
                            (cx - rw, int(bell_y(0.24)) - rh, rw * 2, rh * 2), width=S)
        pygame.draw.ellipse(s, (158, 131, 85),      # rim light
                            (cx - brx, cy - bry, brx * 2, bry * 2), width=S)
        pygame.draw.arc(s, (214, 182, 120),         # glint on the upper rim
                        (cx - brx + 2 * S, cy - bry + 2 * S,
                         brx * 2 - 4 * S, bry * 2 - 4 * S),
                        math.radians(40), math.radians(140), 2 * S)
        pygame.draw.arc(s, (34, 27, 18),            # shade under the lower lip
                        (cx - int(brx * 0.92), int(bell_y(0.92)) - int(bry * 0.92),
                         int(brx * 0.92) * 2, int(bry * 0.92) * 2),
                        math.radians(215), math.radians(325), 2 * S)
        return pygame.transform.smoothscale(s, (HW, HH))

    horn = build_horn()
    horn_pos = (W // 2 - 85, 78)  # bell centre lands at (260, 128)
    HORN_LAYER = 0.65  # discs at least this big draw over the horn (the
                       # front row); everything smaller sits behind it

    # ---------- Sparks ----------
    # The other half of the name: embers lifting off the record and drifting
    # up the frame. They idle when paused and pick up with the music, so the
    # window breathes along with the platter.
    # Weighted warm - embers off the record, not a starfield. The one cool
    # tint keeps them tied to the logo blues.
    SPARK_TINTS = [(255, 176, 84), (255, 202, 118), (255, 228, 176), (140, 182, 230)]

    def make_spark(radius, color):
        d = radius * 2
        s = pygame.Surface((d, d), pygame.SRCALPHA)
        for y in range(d):
            for x in range(d):
                dist = math.hypot(x - radius + 0.5, y - radius + 0.5) / radius
                a = int(255 * max(0.0, 1.0 - dist) ** 2.4)
                if a:
                    s.set_at((x, y), (*color, a))
        return s

    spark_sprites = [make_spark(r, c) for c in SPARK_TINTS for r in (4, 7, 10)]
    sparks = []
    for _ in range(46):
        sparks.append({
            "x": random.uniform(0, W),
            "y": random.uniform(0, H),
            "sprite": random.choice(spark_sprites),
            "rise": random.uniform(7.0, 26.0),      # px/sec at full energy
            "sway": random.uniform(6.0, 20.0),
            "rate": random.uniform(0.4, 1.3),       # sway + twinkle speed
            "phase": random.uniform(0.0, math.tau),
            "peak": random.uniform(0.30, 1.0),      # brightest this one gets
        })

    def draw_sparks(t, dt, energy):
        # 0.28 keeps a slow drift alive while paused so the frame never goes
        # completely static.
        lift = 0.28 + 0.72 * energy
        for sp in sparks:
            sp["y"] -= sp["rise"] * lift * dt
            if sp["y"] < -12:
                sp["y"] = H + 12
                sp["x"] = random.uniform(0, W)
            x = sp["x"] + sp["sway"] * math.sin(t * sp["rate"] + sp["phase"])
            twinkle = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * sp["rate"] * 2.3 + sp["phase"]))
            img = sp["sprite"]
            img.set_alpha(int(150 * sp["peak"] * twinkle * lift))
            screen.blit(img, img.get_rect(center=(int(x), int(sp["y"]))))

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

    def draw_shuffle_glyph(surf, c):
        # Two crossing arrows, drawn at 2x with the rest of the disc.
        g = int(_D2 * 0.115)
        gy = int(g * 0.45)
        lw = max(4, int(_D2 * 0.016))
        col = (150, 154, 170)
        for sy in (-1, 1):
            pygame.draw.lines(surf, col, False,
                              [(c - g, c + gy * sy), (c + int(g * 0.62), c - gy * sy),
                               (c + int(g * 0.84), c - gy * sy)], lw)
            pygame.draw.polygon(surf, col,
                                [(c + int(g * 0.78), c - gy * sy - lw * 2),
                                 (c + int(g * 0.78), c - gy * sy + lw * 2),
                                 (c + g, c - gy * sy)])

    def make_disc(art, shuffle=False):
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
        elif shuffle:
            pygame.draw.circle(d2, CARD, (c, c), _art_d2 // 2)
            draw_shuffle_glyph(d2, c)
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
    shuffle_disc = make_disc(None, shuffle=True)
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
        if track and track.get("shuffle"):
            return shuffle_disc
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

    # Stands in for the track before this one when shuffle makes it
    # unknowable. Never enters car["wheel"] - it's drawn straight into the
    # frame - so nothing that reasons about the wheel has to know about it.
    SHUFFLE_SEAT = {"id": None, "shuffle": True}

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
        if not car["seeded"] and status.get("track_history") is not None:
            # The worker's one-shot recently-played seed, for contexts whose
            # running order isn't readable. Only ever applied at startup -
            # after this the wheel's own history is the truth.
            car["hist"] = list(status["track_history"])
            car["seeded"] = True
        hist = car["hist"]
        prevs = status.get("track_prevs")
        q = status.get("track_queue") or []
        right = [q[i] if i < len(q) else None for i in range(5)]
        right_ids = {tr["id"] for tr in right + [cur] if tr and tr.get("id")}

        def left_slot(k):
            # With the context's running order known, the k-th record back is
            # simply the k-th track back in the playlist - no session memory,
            # no dedupe (a track sitting on both sides of the wheel is the
            # playlist's own truth).
            if prevs is not None:
                return prevs[-k] if len(prevs) >= k else None
            # Fallback (radio, liked songs): session history. A left cover
            # that also sits on the right (tracks skipped past earlier
            # reappear in the queue) would show the same art twice - prefer
            # leaving the slot empty. Same when there's no history yet for
            # this slot - no guessed fallback, since a wrong guess is worse
            # than a blank seat.
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
                    if track is None and not (slot == 0 or (slot == 1 and prevs is None)):
                        continue  # matches the live-wheel filter just below
                    items.append((track, slot, 1.0 - pe))

        glow = glow_surface(cur)
        glow.set_alpha(int(115 + 55 * energy * (0.5 + 0.5 * math.sin(t * 2.2))))
        screen.blit(glow, glow.get_rect(center=(CAR_CX, CAR_CY)))

        for slot, track in car["wheel"].items():
            if track is None and not (slot == 0 or (slot == 1 and prevs is None)):
                # Empty seats stay empty, with two exceptions: the focused
                # seat always shows a disc (a bare platter would leave the
                # horn's neck dangling mid-air before the first track
                # arrives), and +1 gets a placeholder while the upcoming
                # side is still a guess.
                continue
            if fade:
                amult = pe  # fading in uniformly, in place
            else:
                amult = pe if (anim and slot == anim["in"]) else 1.0
            items.append((track, slot + offset, amult))
        if status.get("shuffle") and prevs is None and car["wheel"][-1] is None:
            # Shuffled with no readable playlist order: the previous track
            # is unknowable, so the seat holds a marked disc until a real
            # skip fills it in. (With the order known, shuffle still shows
            # real covers - the playlist as written.)
            items.append((SHUFFLE_SEAT, -1 + offset, pe if fade else 1.0))
        seats = ring_seats(RING_SEATS)
        drawlist = []
        for track, s, amult in items:
            x, y, scale, alpha = slot_params(s, seats)
            if alpha * amult > 2:
                drawlist.append((scale, x, y, alpha * amult, track))
        anim_active = car["anim"] is not None
        horn_up = False
        for scale, x, y, alpha, track in sorted(drawlist, key=lambda d: d[0]):
            if not horn_up and scale >= HORN_LAYER:
                # Back row is down - the horn stands in front of it, and the
                # front row (drawn next) stands in front of the horn.
                screen.blit(horn, horn_pos)
                horn_up = True
            size = max(2, int(COVER * scale))
            base = cover_surface(track)
            if track and track.get("id") and track.get("id") == car["cur_id"]:
                # Only the playing record spins (clockwise, like a turntable).
                surf = pygame.transform.rotozoom(base, -spin_deg, size / COVER_BASE)
            elif anim_active:
                surf = pygame.transform.smoothscale(base, (size, size))
            else:
                surf = scaled_disc(base, size)
            surf.set_alpha(int(alpha))
            screen.blit(surf, surf.get_rect(center=(int(x), int(y))))
        if not horn_up:  # empty wheel - the horn still stands there
            screen.blit(horn, horn_pos)
        return cur

    status = status = {"spotify": "Connecting to Spotify...", "spotify_state": "wait",
              "arduino": "Not connected", "arduino_state": "wait", "playing": False,
              "track_current": None, "track_prev": None, "track_queue": [],
              "track_history": None, "track_prevs": None, "context_uri": None,
              "shuffle": False}
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
            draw_sparks(t, dt, energy)

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
            # Cover wheel + track text. The text sits on the phonograph
            # body's title panel (baked into the background at 121-369 x
            # 402-452, centred on the box's front face rather than the
            # window - the box sits left of centre so the horn lands mid
            # top-face), so it's capped and centred to the panel.
            cur = draw_carousel(now, status, t, energy, spin_deg)
            box_cx = 245  # front-face centre of the cabinet
            text_y = 404
            if cur:
                name_img = track_font.render(fit_text(track_font, cur["name"], 230), True, TEXT)
                artist_img = artist_font.render(fit_text(artist_font, cur["artist"], 230), True, DIM)
                screen.blit(name_img, (box_cx - name_img.get_width() // 2, text_y))
                screen.blit(artist_img, (box_cx - artist_img.get_width() // 2,
                                         text_y + name_img.get_height() + 2))
            else:
                empty_img = artist_font.render("Nothing playing", True, DIM)
                screen.blit(empty_img, (box_cx - empty_img.get_width() // 2, text_y + 12))

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