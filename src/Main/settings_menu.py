import pygame

from fonts import load_font
from settings import (DEFAULTS, PINCH_LEVELS, WINDOW_SCALES, fitting_scales, load_settings, save_settings,
                      window_size)
from sounds import configure as configure_audio, play as play_sfx
from ui_kit import UIKit, starfield, CREAM, INK, TEXT_DARK

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GOLD = (255, 215, 0)
SOFT = (200, 190, 220)
DIM = (150, 140, 175)
BAR_COLOR = (170, 90, 230)
ON_COLOR = (110, 235, 140)
OFF_COLOR = (235, 120, 110)

# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
ROWS = [
    ("master_volume", "Master Volume", "slider", "Overall loudness"),
    ("music_volume", "Music", "slider", "Background music"),
    ("sfx_volume", "Sound Effects", "slider", "Spells, hits, UI"),
    ("fullscreen", "Fullscreen", "toggle", "Also: F11 on the main menu"),
    ("window_scale", "Window Size", "choice", "Windowed mode, keeps the camera's 4:3 shape"),
    ("show_fps", "Show FPS", "toggle", "Also: F3 during a fight"),
    ("screen_shake", "Screen Shake", "toggle", "Turn off if it makes you dizzy"),
    ("pinch_sensitivity", "Pinch Sensitivity", "choice", "HIGH if pinches aren't detected"),
    ("camera", "Camera", "action", "Pick which webcam to use"),
]

# ---------------------------------------------------------------------------
# Display & asset helpers
# ---------------------------------------------------------------------------
_fonts = {}
_bg = {}


def desktop_size():
    try:
        sizes = pygame.display.get_desktop_sizes()
    except pygame.error:
        sizes = []
    return sizes[0] if sizes else (1920, 1080)


def apply_display(settings):
    try:
        if settings["fullscreen"]:
            pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            pygame.display.set_mode(window_size(settings, desktop_size()))
    except pygame.error as e:
        print("Couldn't change display mode:", e)
    return pygame.display.get_surface()


def _font(bold, size):
    key = (bold, size)
    if key not in _fonts:
        _fonts[key] = load_font("MedievalSharp-Bold.ttf" if bold else "MedievalSharp-Book.ttf", size)
    return _fonts[key]


def _background(size):
    if size not in _bg:
        _bg.clear()
        _bg[size] = starfield(size)
    return _bg[size]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
class _Layout:
    def __init__(self, W, H):
        self.s = max(1.0, min(2.0, round(H / 600 * 4) / 4))
        s = self.s
        self.px = 3 if H >= 900 else 2
        self.f_title = _font(True, int(40 * s))
        self.f_label = _font(True, int(19 * s))
        self.f_value = _font(False, int(17 * s))
        self.f_hint = _font(False, int(14 * s))
        self.f_button = _font(True, int(18 * s))

        self.row_h = int(40 * s)
        self.panel_w = min(W - 40, int(560 * s))
        top_pad = 24 * self.px + int(6 * s)
        self.panel_h = top_pad + len(ROWS) * self.row_h + int(16 * s)
        self.title_y = max(10, int(H / 2 - (self.panel_h + 150 * s) / 2))
        self.panel = pygame.Rect(0, 0, self.panel_w, self.panel_h)
        self.panel.midtop = (W // 2, self.title_y + self.f_title.get_height() + int(18 * s))

        inner_x = self.panel.x + int(28 * s)
        inner_w = self.panel_w - int(56 * s)
        self.label_x = inner_x
        self.ctrl_w = int(inner_w * 0.46)
        self.ctrl_x = inner_x + inner_w - self.ctrl_w

        self.rows = []
        y = self.panel.y + top_pad
        for _ in ROWS:
            self.rows.append(pygame.Rect(inner_x - int(10 * s), y, inner_w + int(20 * s), self.row_h))
            y += self.row_h

        bw, bh = int(170 * s), int(40 * s)
        gap = int(20 * s)
        by = self.panel.bottom + int(22 * s)
        self.btn_back = pygame.Rect(W // 2 - bw - gap // 2, by, bw, bh)
        self.btn_reset = pygame.Rect(W // 2 + gap // 2, by, bw, bh)

    def control_rect(self, i):
        r = self.rows[i]
        h = int(self.row_h * 0.62)
        return pygame.Rect(self.ctrl_x, r.centery - h // 2, self.ctrl_w, h)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _value_text(key, kind, settings):
    if kind == "slider":
        return f"{round(settings[key] * 100)}%"
    if kind == "toggle":
        return "ON" if settings[key] else "OFF"
    if kind == "choice" and key == "window_scale":
        w, h = window_size(settings, desktop_size())
        k = WINDOW_SCALES[settings[key]]
        return f"AUTO  {w}x{h}" if k == 0 else f"{round(k * 100)}%  {w}x{h}"
    if kind == "choice":
        return PINCH_LEVELS[settings[key]][0]
    cam = settings["camera_name"] or f"Camera {settings['camera_index'] + 1}"
    return cam


def _choices(key):
    if key == "window_scale":
        fits = fitting_scales(desktop_size())
        return [0] + [i for i, k in enumerate(WINDOW_SCALES) if i and k in fits]
    return list(range(len(PINCH_LEVELS)))


def _fit(font, text, color, max_w):
    surf = font.render(text, True, color)
    if surf.get_width() <= max_w:
        return surf
    while len(text) > 1 and font.size(text + "...")[0] > max_w:
        text = text[:-1]
    return font.render(text + "...", True, color)


def _draw_button(screen, kit, rect, label, font, px, hover):
    surf = kit.button(rect.w, rect.h, px, hover=hover)
    screen.blit(surf, surf.get_rect(center=rect.center))
    txt = font.render(label, True, (120, 40, 150) if hover else TEXT_DARK)
    screen.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2 - px))


def _draw(screen, kit, L, settings, focus, mouse):
    W, H = screen.get_size()
    s, px = L.s, L.px
    screen.blit(_background((W, H)), (0, 0))

    # --- title & panel ---
    title = kit.text(L.f_title, "SETTINGS", GOLD, offset=3)
    screen.blit(title, (W // 2 - title.get_width() // 2, L.title_y))

    screen.blit(kit.panel(L.panel_w, L.panel_h, px, gem=True), L.panel.topleft)

    # --- rows ---
    for i, (key, label, kind, hint) in enumerate(ROWS):
        row = L.rows[i]
        hovered = row.collidepoint(mouse)
        if i == focus or hovered:
            hl = pygame.Surface(row.size, pygame.SRCALPHA)
            hl.fill((255, 255, 255, 34 if i == focus else 18))
            screen.blit(hl, row.topleft)
            if i == focus:
                pygame.draw.rect(screen, GOLD, (row.x, row.y + 4, max(2, px), row.h - 8))

        lab = kit.text(L.f_label, label, CREAM if i == focus else SOFT)
        screen.blit(lab, (L.label_x, row.centery - lab.get_height() // 2 - int(6 * s)))
        h = _fit(L.f_hint, hint, DIM, L.ctrl_x - L.label_x - 10)
        screen.blit(h, (L.label_x, row.centery + int(5 * s)))

        c = L.control_rect(i)
        val = _value_text(key, kind, settings)
        # --- controls ---
        if kind == "slider":
            pct_s = L.f_value.render(val, True, CREAM)
            bar_w = c.w - int(52 * s)
            screen.blit(kit.bar(bar_w, int(18 * s), settings[key], BAR_COLOR, px), (c.x, c.centery - int(9 * s)))
            hx = c.x + int(bar_w * settings[key])
            pygame.draw.rect(screen, INK, (hx - 3 * px, c.centery - 7 * px, 6 * px, 14 * px))
            pygame.draw.rect(screen, CREAM, (hx - 2 * px, c.centery - 6 * px, 4 * px, 12 * px))
            screen.blit(pct_s, (c.right - pct_s.get_width(), c.centery - pct_s.get_height() // 2))
        elif kind == "toggle":
            on = settings[key]
            sw = pygame.Rect(0, 0, int(88 * s), c.h)
            sw.midright = c.midright
            _draw_button(screen, kit, sw, val, L.f_button, px, sw.collidepoint(mouse))
            dot = (sw.x - int(14 * s), sw.centery)
            pygame.draw.circle(screen, INK, dot, int(6 * s) + 1)
            pygame.draw.circle(screen, ON_COLOR if on else OFF_COLOR, dot, int(6 * s))
        elif kind == "choice":
            arrow_w = int(34 * s)
            left = pygame.Rect(c.x, c.y, arrow_w, c.h)
            right = pygame.Rect(c.right - arrow_w, c.y, arrow_w, c.h)
            _draw_button(screen, kit, left, "<", L.f_button, px, left.collidepoint(mouse))
            _draw_button(screen, kit, right, ">", L.f_button, px, right.collidepoint(mouse))
            v = kit.text(L.f_label, val, GOLD)
            screen.blit(v, (c.centerx - v.get_width() // 2, c.centery - v.get_height() // 2))
        else:
            _draw_button(screen, kit, c, "", L.f_button, px, c.collidepoint(mouse))
            v = _fit(L.f_value, f"{val}  >", TEXT_DARK, c.w - 16)
            screen.blit(v, (c.centerx - v.get_width() // 2, c.centery - v.get_height() // 2 - px))

    # --- footer ---
    _draw_button(screen, kit, L.btn_back, "< BACK (ESC)", L.f_button, px, L.btn_back.collidepoint(mouse))
    _draw_button(screen, kit, L.btn_reset, "RESET", L.f_button, px, L.btn_reset.collidepoint(mouse))

    foot = L.f_hint.render("Arrow keys to adjust  |  Changes save automatically", True, DIM)
    screen.blit(foot, (W // 2 - foot.get_width() // 2, H - foot.get_height() - 10))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_settings(screen, focus=0):
    kit = UIKit()
    clock = pygame.time.Clock()
    settings = load_settings()
    dragging = None
    last_preview = 0

    # --- actions ---
    def commit(key=None):
        nonlocal screen, last_preview
        save_settings(settings)
        configure_audio(settings)
        if key in ("fullscreen", "window_scale"):
            screen = apply_display(settings)
        if key in ("master_volume", "sfx_volume"):
            now = pygame.time.get_ticks()
            if now - last_preview > 180:
                play_sfx("cast", volume=0.6)
                last_preview = now

    def change(i, direction):
        key, _, kind, _ = ROWS[i]
        if kind == "slider":
            step = direction or 0
            settings[key] = round(max(0.0, min(1.0, settings[key] + 0.05 * step)), 2)
        elif kind == "toggle":
            settings[key] = not settings[key]
        elif kind == "choice":
            opts = _choices(key)
            cur = opts.index(settings[key]) if settings[key] in opts else 0
            settings[key] = opts[(cur + (direction or 1)) % len(opts)]
        else:
            return "CAMERA" if direction == 0 else None
        commit(key)
        return None

    def set_slider_from_mouse(i, mx, L):
        key = ROWS[i][0]
        c = L.control_rect(i)
        bar_w = c.w - int(52 * L.s)
        settings[key] = round(max(0.0, min(1.0, (mx - c.x) / max(1, bar_w))), 2)
        commit(key)

    while True:
        clock.tick(60)
        screen = pygame.display.get_surface()
        W, H = screen.get_size()
        L = _Layout(W, H)
        mouse = pygame.mouse.get_pos()

        # --- events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "QUIT"
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return "MENU"
                if event.key in (pygame.K_UP, pygame.K_w):
                    focus = (focus - 1) % len(ROWS)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    focus = (focus + 1) % len(ROWS)
                elif event.key in (pygame.K_LEFT, pygame.K_a):
                    change(focus, -1)
                elif event.key in (pygame.K_RIGHT, pygame.K_d):
                    change(focus, +1)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    if ROWS[focus][2] != "slider" and change(focus, 0) == "CAMERA":
                        return "CAMERA"
                elif event.key == pygame.K_F11:
                    settings["fullscreen"] = not settings["fullscreen"]
                    commit("fullscreen")
            elif event.type == pygame.MOUSEWHEEL:
                for i, row in enumerate(L.rows):
                    if row.collidepoint(mouse) and ROWS[i][2] == "slider":
                        focus = i
                        change(i, 1 if event.y > 0 else -1)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if L.btn_back.collidepoint(event.pos):
                    return "MENU"
                if L.btn_reset.collidepoint(event.pos):
                    keep = {k: settings[k] for k in ("camera_index", "camera_name")}
                    was = (settings["fullscreen"], settings["window_scale"])
                    settings = dict(DEFAULTS, **keep)
                    commit("fullscreen" if was != (settings["fullscreen"], settings["window_scale"]) else None)
                    continue
                for i, row in enumerate(L.rows):
                    if not row.collidepoint(event.pos):
                        continue
                    focus = i
                    kind = ROWS[i][2]
                    c = L.control_rect(i)
                    if kind == "slider":
                        if c.inflate(0, 12).collidepoint(event.pos):
                            dragging = i
                            set_slider_from_mouse(i, event.pos[0], L)
                    elif kind == "choice":
                        change(i, -1 if event.pos[0] < c.centerx else 1)
                    elif change(i, 0) == "CAMERA":
                        return "CAMERA"
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                dragging = None
            elif event.type == pygame.MOUSEMOTION and dragging is not None:
                set_slider_from_mouse(dragging, event.pos[0], L)

        # --- render ---
        screen = pygame.display.get_surface()
        L = _Layout(*screen.get_size())
        _draw(screen, kit, L, settings, focus, mouse)
        pygame.display.flip()