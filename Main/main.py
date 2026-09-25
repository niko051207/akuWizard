import math
import sys

import pygame

from fonts import load_font
from camera_select import run_camera_select
from game import run_game
from settings import load_settings
from spell_recognizer import load_spell_data, spell_role_short
from sprites import SpriteBank
from ui_kit import UIKit, starfield, CREAM, INK, PLUM, TEXT_DARK

pygame.init()
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
pygame.display.set_caption("War of Wizards")
clock = pygame.time.Clock()

font_title = load_font("MedievalSharp-Bold.ttf", 48)
font_tagline = load_font("MedievalSharp-Book.ttf", 20)
font_button = load_font("MedievalSharp-Bold.ttf", 26)
font_card_title = load_font("MedievalSharp-Bold.ttf", 22)
font_card_small = load_font("MedievalSharp-Book.ttf", 16)
font_howto_title = load_font("MedievalSharp-Bold.ttf", 36)
font_howto_hint = load_font("MedievalSharp-Book.ttf", 18)

kit = UIKit()
sprites = SpriteBank()
_bg_cache = {}

CARD_W, CARD_H, CARD_GAP = 190, 250, 24
CARDS_TOP = 130


def background(size):
    """Starry purple backdrop, cached per window size."""
    if size not in _bg_cache:
        _bg_cache.clear()
        _bg_cache[size] = starfield(size)
    return _bg_cache[size]


def ui_px(win_h):
    return 3 if win_h >= 900 else 2


def normalize_points(points, box, pad=14):
    """Scale a recorded gesture's raw points to fit inside `box`, preserving aspect ratio."""
    if not points:
        return []
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1)
    span_y = max(max_y - min_y, 1)
    avail_w = box.width - 2 * pad
    avail_h = box.height - 2 * pad
    scale = min(avail_w / span_x, avail_h / span_y)
    offset_x = box.x + pad + (avail_w - span_x * scale) / 2
    offset_y = box.y + pad + (avail_h - span_y * scale) / 2
    return [(offset_x + (p["x"] - min_x) * scale, offset_y + (p["y"] - min_y) * scale) for p in points]


def card_grid(n, win_w, win_h):
    """Pick the column count that lets the spell cards be as large as possible
    while still fitting in the window. Returns (cols, scale)."""
    best = (1, 0.0)
    for cols in range(1, min(n, 4) + 1):
        rows = math.ceil(n / cols)
        fit_w = (win_w - 60 - (cols - 1) * CARD_GAP) / cols / CARD_W
        fit_h = (win_h - CARDS_TOP - 20 - (rows - 1) * CARD_GAP) / rows / CARD_H
        s = min(1.0, fit_w, fit_h)
        if s > best[1]:
            best = (cols, s)
    return best


def draw_arrowhead(surface, color, a, b, size):
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    left = (b[0] - size * math.cos(ang - 0.5), b[1] - size * math.sin(ang - 0.5))
    right = (b[0] - size * math.cos(ang + 0.5), b[1] - size * math.sin(ang + 0.5))
    pygame.draw.polygon(surface, color, [b, left, right])


def draw_howto(screen, mouse_pos, spell_data, btn_back):
    W, H = screen.get_size()
    px = ui_px(H)
    screen.blit(background((W, H)), (0, 0))
    txt_title = kit.text(font_howto_title, "HOW TO PLAY", (255, 215, 0))
    screen.blit(txt_title, (W // 2 - txt_title.get_width() // 2, 30))
    hint = kit.text(font_howto_hint,
                    "Pinch to start drawing, release to cast. Lightning stuns a charging enemy; Shield just before impact reflects.",
                    (215, 205, 230))
    if hint.get_width() > W - 20:
        hint = pygame.transform.smoothscale(hint, (W - 20, int(hint.get_height() * (W - 20) / hint.get_width())))
    screen.blit(hint, (W // 2 - hint.get_width() // 2, 80))

    names = list(spell_data.keys())
    if not names:
        empty = kit.text(font_card_small, "No spells found. Run record_spells.py to create some!", (255, 120, 120))
        screen.blit(empty, (W // 2 - empty.get_width() // 2, H // 2))
    else:
        cols, s = card_grid(len(names), W, H)
        cw, ch, gap = int(CARD_W * s), int(CARD_H * s), CARD_GAP
        row_w = cols * cw + (cols - 1) * gap
        start_x = W // 2 - row_w // 2
        for i, name in enumerate(names):
            card = pygame.Rect(start_x + (i % cols) * (cw + gap), CARDS_TOP + (i // cols) * (ch + gap), cw, ch)
            entry = spell_data[name]
            is_defense = entry.get("type", "attack") == "defense"
            accent = (120, 195, 255) if is_defense else (255, 160, 80)
            screen.blit(kit.panel(cw, ch, px), card.topleft)

            name_txt = kit.text(font_card_title, name, (255, 255, 255))
            if name_txt.get_width() > cw - 16:
                name_txt = pygame.transform.smoothscale(
                    name_txt, (cw - 16, int(name_txt.get_height() * (cw - 16) / name_txt.get_width())))
            screen.blit(name_txt, (card.centerx - name_txt.get_width() // 2, card.y + 12))
            type_txt = kit.text(font_card_small, "DEFENSE" if is_defense else "ATTACK", accent)
            screen.blit(type_txt, (card.centerx - type_txt.get_width() // 2, card.y + 40))

            preview = pygame.Rect(card.x + 14, card.y + 62, cw - 28, int(110 * s))
            pygame.draw.rect(screen, PLUM, preview)
            pygame.draw.rect(screen, INK, preview, px)
            gestures = entry.get("gestures", [])
            pts = normalize_points(gestures[0], preview) if gestures else []
            if len(pts) > 1:
                pygame.draw.lines(screen, (255, 215, 0), False, pts, 4)
                pygame.draw.circle(screen, (0, 255, 120), (int(pts[0][0]), int(pts[0][1])), 6)  # start here
                back = pts[max(0, len(pts) - 4)]
                draw_arrowhead(screen, (255, 215, 0), back, pts[-1], 12)
            else:
                na = font_card_small.render("No gesture yet", True, (150, 140, 170))
                screen.blit(na, (preview.centerx - na.get_width() // 2, preview.centery - 8))

            role = spell_role_short(name, entry)
            if role:
                role_s = kit.text(font_card_small, role, accent)
                if role_s.get_width() > cw - 16:
                    role_s = pygame.transform.smoothscale(
                        role_s, (cw - 16, int(role_s.get_height() * (cw - 16) / role_s.get_width())))
                screen.blit(role_s, (card.centerx - role_s.get_width() // 2, card.bottom - 56))

            if is_defense:
                info = f"Cost: {entry.get('cost', 25)} mana"
            else:
                info = f"DMG: {entry.get('damage', 0)}  Cost: {entry.get('cost', 25)}"
            info_s = kit.text(font_card_small, info, (230, 220, 240))
            screen.blit(info_s, (card.centerx - info_s.get_width() // 2, card.bottom - 32))

        legend = kit.text(font_card_small, "Green dot = start of the gesture", (160, 150, 185))
        screen.blit(legend, (W // 2 - legend.get_width() // 2, H - 26))

    hover = btn_back.collidepoint(mouse_pos)
    screen.blit(kit.button(btn_back.w, btn_back.h, px, hover=hover), btn_back.topleft)
    txt_back = font_card_small.render("< BACK (ESC)", True, TEXT_DARK)
    screen.blit(txt_back, (btn_back.centerx - txt_back.get_width() // 2, btn_back.centery - txt_back.get_height() // 2 - px))


def draw_button(screen, rect, label, mouse_pos, px):
    """A parchment button from the UI kit; lights up under the mouse."""
    hover = rect.collidepoint(mouse_pos)
    surf = kit.button(rect.w, rect.h, px, hover=hover)
    screen.blit(surf, surf.get_rect(center=rect.center))
    txt = font_button.render(label, True, (120, 40, 150) if hover else TEXT_DARK)
    # centre on the face, not the shadow under it
    screen.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2 - px))


def draw_queen(screen, t, x_center, bottom, k):
    """The Dark Queen looming on the menu (enemy_idle, scaled k times)."""
    anim = sprites.get("enemy_idle")
    if anim is None:
        return
    img = anim.frame(t, anim.frames[0].get_height() * k)
    bob = math.sin(t * 1.6) * 4
    glow = pygame.Surface((img.get_width(), img.get_width()), pygame.SRCALPHA)
    r = img.get_width() // 2
    for i, a in ((r, 18), (int(r * 0.75), 22)):
        pygame.draw.circle(glow, (120, 60, 200, a), (r, r), i)
    screen.blit(glow, (x_center - r, bottom - img.get_height() * 0.95))
    screen.blit(img, (x_center - img.get_width() // 2, bottom - img.get_height() + bob))


def draw_menu(screen, mouse_pos, buttons, t):
    W, H = screen.get_size()
    px = ui_px(H)
    screen.blit(background((W, H)), (0, 0))

    # the queen on the right if there's room, otherwise the menu is centred
    queen = sprites.has("enemy_idle") and W >= 700
    col_x = int(W * 0.32) if queen else W // 2
    if queen:
        k = max(1, int(H * 0.66 / 128))
        while k > 1 and 128 * k > W * 0.52:
            k -= 1
        draw_queen(screen, t, W - int(128 * k * 0.5) - 10, H + 10, k)

    title = kit.text(font_title, "WAR OF WIZARDS", (255, 215, 0), offset=3)
    ty = max(20, H // 2 - 250)
    screen.blit(title, (col_x - title.get_width() // 2, ty))
    tag = kit.text(font_tagline, "Draw your spells. Dethrone the Dark Queen.", CREAM)
    screen.blit(tag, (col_x - tag.get_width() // 2, ty + title.get_height() + 2))

    bw, bh, gap = 220, 13 * px * 2, 12
    n = len(buttons)
    panel_h = 24 * px + n * bh + (n - 1) * gap + 12 * px
    panel = kit.panel(bw + 30 * px, panel_h, px, gem=True)
    top = max(ty + title.get_height() + 40, H // 2 - panel.get_height() // 2 + 30)
    pr = panel.get_rect(midtop=(col_x, top))
    screen.blit(panel, pr)
    y = pr.y + 24 * px
    for rect, label in buttons:
        rect.size = (bw, bh)
        rect.midtop = (col_x, y)
        draw_button(screen, rect, label, mouse_pos, px)
        y += bh + gap
    return pr


btn_play = pygame.Rect(0, 0, 220, 52)
btn_howto = pygame.Rect(0, 0, 220, 52)
btn_camera = pygame.Rect(0, 0, 220, 52)
btn_quit = pygame.Rect(0, 0, 220, 52)
btn_back = pygame.Rect(30, 30, 150, 44)
MENU_BUTTONS = [(btn_play, "PLAY"), (btn_howto, "HOW TO PLAY"), (btn_camera, "CAMERA"), (btn_quit, "QUIT")]

state = "MENU"
spell_data_cache = None
settings_cache = load_settings()
t = 0.0

while True:
    dt = clock.tick(60) / 1000.0   # the menu used to spin one CPU core at 100%
    t += dt
    mouse_pos = pygame.mouse.get_pos()
    screen = pygame.display.get_surface()
    WIDTH, HEIGHT = screen.get_size()

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and state == "HOWTO":
                state = "MENU"
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if state == "MENU":
                if btn_play.collidepoint(mouse_pos):
                    state = "GAME"
                elif btn_howto.collidepoint(mouse_pos):
                    spell_data_cache = load_spell_data()  # reload in case spells were just recorded
                    state = "HOWTO"
                elif btn_camera.collidepoint(mouse_pos):
                    state = "CAMERA"
                elif btn_quit.collidepoint(mouse_pos):
                    pygame.quit()
                    sys.exit()
            elif state == "HOWTO" and btn_back.collidepoint(mouse_pos):
                state = "MENU"

    if state == "MENU":
        draw_menu(screen, mouse_pos, MENU_BUTTONS, t)
        cam = settings_cache["camera_name"] or f"Camera {settings_cache['camera_index'] + 1}"
        cam_txt = kit.text(font_card_small, f"Camera: {cam}", (170, 160, 195))
        screen.blit(cam_txt, (10, HEIGHT - cam_txt.get_height() - 8))
        pygame.display.flip()

    elif state == "HOWTO":
        draw_howto(screen, mouse_pos, spell_data_cache or {}, btn_back)
        pygame.display.flip()

    elif state == "CAMERA":
        if run_camera_select(screen) == "QUIT":
            pygame.quit()
            sys.exit()
        settings_cache = load_settings()   # show the newly chosen camera on the menu
        state = "MENU"

    elif state == "GAME":
        result = run_game(screen)
        if result == "QUIT":
            pygame.quit()
            sys.exit()
        state = "MENU"
