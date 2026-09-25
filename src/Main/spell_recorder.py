import math

import pygame

from fonts import load_font
from hand_input import HandTracker, PinchState, StrokeBuilder, cam_rect, pinch_point
from settings import load_settings, pinch_scale
from sounds import play as play_sfx
from spell_recognizer import save_spell_data
from ui_kit import UIKit, INK, PLUM, TEXT_DARK

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GOLD = (255, 215, 0)
SOFT = (200, 190, 220)
OK = (110, 235, 140)
WARN = (255, 190, 90)
FAIL = (235, 120, 110)

MIN_POINTS = 15
RECOMMENDED_SAMPLES = 5

_fonts = {}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def font(bold, size):
    key = (bold, size)
    if key not in _fonts:
        _fonts[key] = load_font("MedievalSharp-Bold.ttf" if bold else "MedievalSharp-Book.ttf", size)
    return _fonts[key]


def ui_scale(H):
    return max(1.0, min(2.0, round(H / 600 * 4) / 4))


def fit_text(f, text, color, max_w):
    surf = f.render(text, True, color)
    if surf.get_width() <= max_w:
        return surf
    while len(text) > 1 and f.size(text + "...")[0] > max_w:
        text = text[:-1]
    return f.render(text + "...", True, color)


def draw_button(screen, kit, rect, label, f, px, mouse, enabled=True, color=TEXT_DARK):
    hover = enabled and rect.collidepoint(mouse)
    surf = kit.button(rect.w, rect.h, px, hover=hover)
    if not enabled:
        surf = surf.copy()
        surf.set_alpha(110)
    screen.blit(surf, surf.get_rect(center=rect.center))
    txt = f.render(label, True, (120, 40, 150) if hover else color)
    if not enabled:
        txt.set_alpha(140)
    screen.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2 - px))


def gesture_points(points, box, pad):
    if not points:
        return []
    pts = [(p["x"], p["y"]) if isinstance(p, dict) else p for p in points]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, min_y = min(xs), min(ys)
    span_x = max(max(xs) - min_x, 1)
    span_y = max(max(ys) - min_y, 1)
    avail_w, avail_h = box.w - 2 * pad, box.h - 2 * pad
    k = min(avail_w / span_x, avail_h / span_y)
    ox = box.x + pad + (avail_w - span_x * k) / 2
    oy = box.y + pad + (avail_h - span_y * k) / 2
    return [(ox + (x - min_x) * k, oy + (y - min_y) * k) for x, y in pts]


def draw_gesture(screen, points, box, color, width, pad=8, arrow=True):
    pts = gesture_points(points, box, pad)
    if len(pts) < 2:
        return
    pygame.draw.lines(screen, color, False, pts, width)
    pygame.draw.circle(screen, (0, 255, 120), (int(pts[0][0]), int(pts[0][1])), max(3, width + 1))
    if arrow:
        a, b = pts[max(0, len(pts) - 4)], pts[-1]
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        size = width * 3
        left = (b[0] - size * math.cos(ang - 0.5), b[1] - size * math.sin(ang - 0.5))
        right = (b[0] - size * math.cos(ang + 0.5), b[1] - size * math.sin(ang + 0.5))
        pygame.draw.polygon(screen, color, [b, left, right])


# ---------------------------------------------------------------------------
# Recorder screen
# ---------------------------------------------------------------------------
def run_recorder(screen, spells, name):
    kit = UIKit()
    clock = pygame.time.Clock()
    settings = load_settings()
    entry = spells[name]
    color = tuple(entry.get("color", (0, 200, 255)))

    tracker = HandTracker(camera_index=settings["camera_index"])
    pinch = PinchState(sensitivity=pinch_scale(settings))
    stroke = StrokeBuilder()
    pending = None
    message = ("Pinch to start drawing, release to finish.", SOFT)
    last_id, last_t = None, None
    cam_src = None
    lm = None

    # --- actions ---
    def keep():
        nonlocal pending, message
        entry["gestures"].append([{"x": x, "y": y} for x, y in pending])
        save_spell_data(spells)
        pending = None
        n = len(entry["gestures"])
        message = (f"Saved! {n} sample{'s' if n != 1 else ''}. Draw the next one.", OK)
        play_sfx("cast", volume=0.6)

    def redraw():
        nonlocal pending, message
        pending = None
        message = ("Discarded. Draw again.", SOFT)

    def undo():
        nonlocal message
        if entry["gestures"]:
            entry["gestures"].pop()
            save_spell_data(spells)
            message = (f"Removed the last sample. {len(entry['gestures'])} left.", WARN)
            play_sfx("fail", volume=0.5)

    try:
        while True:
            clock.tick(60)
            screen = pygame.display.get_surface()
            W, H = screen.get_size()
            s = ui_scale(H)
            px = 3 if H >= 900 else 2
            mouse = pygame.mouse.get_pos()

            # --- layout ---
            margin = int(20 * s)
            bh = int(40 * s)
            by = H - margin - bh
            btn_undo = pygame.Rect(margin, by, int(170 * s), bh)
            btn_done = pygame.Rect(W - margin - int(170 * s), by, int(170 * s), bh)
            bw = int(150 * s)
            btn_redraw = pygame.Rect(W // 2 - int(6 * s) - bw, by, bw, bh)
            btn_keep = pygame.Rect(W // 2 + int(6 * s), by, bw, bh)

            # --- events ---
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return "QUIT"
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return "BACK"
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE) and pending:
                        keep()
                    elif event.key in (pygame.K_BACKSPACE, pygame.K_r) and pending:
                        redraw()
                    elif event.key == pygame.K_d and not stroke.active:
                        undo()
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if btn_done.collidepoint(event.pos):
                        return "BACK"
                    if btn_undo.collidepoint(event.pos) and not stroke.active:
                        undo()
                    if pending and btn_keep.collidepoint(event.pos):
                        keep()
                    elif pending and btn_redraw.collidepoint(event.pos):
                        redraw()

            # --- camera frame + hand input ---
            frame = None if tracker.error else tracker.latest()
            if frame is not None and frame[0] != last_id:
                fid, cam_rgb, lm, t = frame
                cam_dt = (t - last_t) if last_t else 1 / 30
                last_id, last_t = fid, t
                fh, fw = cam_rgb.shape[:2]
                cam_src = pygame.image.frombuffer(cam_rgb, (fw, fh), "RGB")
                arena = cam_rect(W, H, fw, fh)

                ev = pinch.update(lm, fw / fh)
                if pending is None:
                    if ev == "start":
                        stroke.begin()
                    elif ev == "release" and stroke.active:
                        pts = stroke.finish()
                        if len(pts) >= MIN_POINTS:
                            pending = pts
                            message = ("Keep this one? ENTER = keep, BACKSPACE = redraw", GOLD)
                        else:
                            message = ("Stroke too short, try again.", FAIL)
                    elif ev == "cancel" and stroke.active:
                        stroke.cancel()
                        message = ("Hand left the camera. Draw again.", FAIL)
                    if stroke.active and pinch.pinching and lm is not None:
                        x, y = pinch_point(lm, arena)
                        stroke.add(x, y, cam_dt, arena.h)

            # --- camera ---
            screen.fill((8, 6, 18))
            if cam_src is not None:
                arena = cam_rect(W, H, *cam_src.get_size())
                screen.blit(pygame.transform.scale(cam_src, arena.size), arena.topleft)
                shade = pygame.Surface(arena.size, pygame.SRCALPHA)
                shade.fill((20, 8, 35, 70))
                screen.blit(shade, arena.topleft)
                if lm is not None:
                    cx, cy = pinch_point(lm, arena)
                    col = (0, 255, 100) if pinch.pinching else (255, 90, 90)
                    pygame.draw.circle(screen, col, (int(cx), int(cy)), int(10 * s), 0 if pinch.pinching else 2)
            elif tracker.error:
                err = fit_text(font(False, int(18 * s)), f"Camera error: {tracker.error}", FAIL, W - 40)
                screen.blit(err, (W // 2 - err.get_width() // 2, H // 2 - err.get_height() // 2))
            else:
                wait = font(False, int(20 * s)).render("Opening the camera...", True, SOFT)
                screen.blit(wait, (W // 2 - wait.get_width() // 2, H // 2 - wait.get_height() // 2))

            # --- strokes ---
            if len(stroke.points) > 1:
                pygame.draw.lines(screen, GOLD, False, stroke.points, max(4, int(6 * s)))
            if pending and len(pending) > 1:
                pygame.draw.lines(screen, color, False, pending, max(4, int(6 * s)))

            # --- info panel ---
            pw, ph = min(W - 2 * margin, int(560 * s)), int(92 * s)
            panel = pygame.Rect(W // 2 - pw // 2, int(10 * s), pw, ph)
            screen.blit(kit.panel(pw, ph, px), panel.topleft)
            tx = panel.x + int(16 * s)
            title = kit.text(font(True, int(22 * s)), f"RECORDING  ·  {name}", GOLD)
            screen.blit(title, (tx, panel.y + int(10 * s)))
            n = len(entry["gestures"])
            count_col = OK if n >= RECOMMENDED_SAMPLES else WARN
            count = font(False, int(16 * s)).render(
                f"Samples: {n}   (aim for {RECOMMENDED_SAMPLES}+, same direction every time)", True, count_col)
            screen.blit(count, (tx, panel.y + int(40 * s)))
            msg = fit_text(font(False, int(16 * s)), message[0], message[1], pw - int(32 * s))
            screen.blit(msg, (tx, panel.y + int(62 * s)))

            # --- reference sample ---
            if entry["gestures"]:
                rs = int(120 * s)
                ref = pygame.Rect(W - margin - rs, panel.bottom + int(14 * s), rs, rs)
                box = pygame.Surface(ref.size, pygame.SRCALPHA)
                box.fill((*PLUM, 200))
                screen.blit(box, ref.topleft)
                pygame.draw.rect(screen, INK, ref, px)
                draw_gesture(screen, entry["gestures"][0], ref, color, max(2, int(3 * s)), pad=int(14 * s))
                lab = font(False, int(14 * s)).render("REFERENCE", True, SOFT)
                screen.blit(lab, (ref.centerx - lab.get_width() // 2, ref.bottom + int(4 * s)))

            # --- buttons ---
            bf = font(True, int(16 * s))
            draw_button(screen, kit, btn_undo, "UNDO LAST (D)", bf, px, mouse, enabled=bool(entry["gestures"]))
            draw_button(screen, kit, btn_done, "DONE (ESC)", bf, px, mouse)
            if pending:
                draw_button(screen, kit, btn_redraw, "REDRAW", bf, px, mouse)
                draw_button(screen, kit, btn_keep, "KEEP", bf, px, mouse, color=(20, 110, 60))

            pygame.display.flip()
    finally:
        tracker.stop()
