import math
import sys

import pygame

from fonts import load_font
from camera_select import run_camera_select
from game import run_game
from settings import load_settings
from spell_recognizer import load_spell_data, spell_role_short

pygame.init()
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
pygame.display.set_caption("War of Wizards")
clock = pygame.time.Clock()

font_title = load_font("MedievalSharp-Bold.ttf", 42)
font_button = load_font("MedievalSharp-Bold.ttf", 26)
font_card_title = load_font("MedievalSharp-Bold.ttf", 22)
font_card_small = load_font("MedievalSharp-Book.ttf", 16)
font_howto_title = load_font("MedievalSharp-Bold.ttf", 36)
font_howto_hint = load_font("MedievalSharp-Book.ttf", 18)

CARD_W, CARD_H, CARD_GAP = 190, 250, 24
CARDS_TOP = 130


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
    screen.fill((15, 15, 30))
    txt_title = font_howto_title.render("HOW TO PLAY", True, (255, 215, 0))
    screen.blit(txt_title, (W // 2 - txt_title.get_width() // 2, 30))
    hint = font_howto_hint.render(
        "Pinch to start drawing, release to cast. Lightning stuns a charging enemy; Shield just before impact reflects.",
        True, (200, 200, 200))
    screen.blit(hint, (W // 2 - hint.get_width() // 2, 80))

    names = list(spell_data.keys())
    if not names:
        empty = font_card_small.render("No spells found. Run record_spells.py to create some!", True, (255, 120, 120))
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
            accent = (100, 180, 255) if is_defense else (255, 140, 60)
            pygame.draw.rect(screen, (30, 30, 50), card, border_radius=12)
            pygame.draw.rect(screen, accent, card, 3, border_radius=12)

            name_txt = font_card_title.render(name, True, (255, 255, 255))
            if name_txt.get_width() > cw - 12:
                name_txt = pygame.transform.smoothscale(
                    name_txt, (cw - 12, int(name_txt.get_height() * (cw - 12) / name_txt.get_width())))
            screen.blit(name_txt, (card.centerx - name_txt.get_width() // 2, card.y + 10))
            type_txt = font_card_small.render("DEFENSE" if is_defense else "ATTACK", True, accent)
            screen.blit(type_txt, (card.centerx - type_txt.get_width() // 2, card.y + 38))

            preview = pygame.Rect(card.x + 12, card.y + 62, cw - 24, int(110 * s))
            pygame.draw.rect(screen, (10, 10, 20), preview, border_radius=8)
            gestures = entry.get("gestures", [])
            pts = normalize_points(gestures[0], preview) if gestures else []
            if len(pts) > 1:
                pygame.draw.lines(screen, (255, 215, 0), False, pts, 4)
                pygame.draw.circle(screen, (0, 255, 120), (int(pts[0][0]), int(pts[0][1])), 6)  # start here
                back = pts[max(0, len(pts) - 4)]
                draw_arrowhead(screen, (255, 215, 0), back, pts[-1], 12)
            else:
                na = font_card_small.render("No gesture yet", True, (150, 150, 150))
                screen.blit(na, (preview.centerx - na.get_width() // 2, preview.centery - 8))

            role = spell_role_short(name, entry)
            if role:
                role_s = font_card_small.render(role, True, accent)
                if role_s.get_width() > cw - 12:
                    role_s = pygame.transform.smoothscale(
                        role_s, (cw - 12, int(role_s.get_height() * (cw - 12) / role_s.get_width())))
                screen.blit(role_s, (card.centerx - role_s.get_width() // 2, card.bottom - 54))

            if is_defense:
                info = f"Cost: {entry.get('cost', 25)} mana"
            else:
                info = f"DMG: {entry.get('damage', 0)}  Cost: {entry.get('cost', 25)}"
            info_s = font_card_small.render(info, True, (220, 220, 220))
            screen.blit(info_s, (card.centerx - info_s.get_width() // 2, card.bottom - 30))

        legend = font_card_small.render("Green dot = start of the gesture", True, (150, 150, 170))
        screen.blit(legend, (W // 2 - legend.get_width() // 2, H - 26))

    color_back = (90, 90, 110) if btn_back.collidepoint(mouse_pos) else (60, 60, 75)
    pygame.draw.rect(screen, color_back, btn_back, border_radius=8)
    txt_back = font_card_small.render("< BACK (ESC)", True, (255, 255, 255))
    screen.blit(txt_back, (btn_back.centerx - txt_back.get_width() // 2, btn_back.centery - txt_back.get_height() // 2))


def draw_button(screen, rect, label, base, hover, mouse_pos):
    pygame.draw.rect(screen, hover if rect.collidepoint(mouse_pos) else base, rect, border_radius=10)
    txt = font_button.render(label, True, (255, 255, 255))
    screen.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2))


btn_play = pygame.Rect(0, 0, 200, 60)
btn_howto = pygame.Rect(0, 0, 200, 60)
btn_camera = pygame.Rect(0, 0, 200, 60)
btn_quit = pygame.Rect(0, 0, 200, 60)
btn_back = pygame.Rect(30, 30, 120, 45)

state = "MENU"
spell_data_cache = None
settings_cache = load_settings()

while True:
    mouse_pos = pygame.mouse.get_pos()
    screen = pygame.display.get_surface()
    WIDTH, HEIGHT = screen.get_size()
    btn_play.center = (WIDTH // 2, HEIGHT // 2 - 40)
    btn_howto.center = (WIDTH // 2, HEIGHT // 2 + 35)
    btn_camera.center = (WIDTH // 2, HEIGHT // 2 + 110)
    btn_quit.center = (WIDTH // 2, HEIGHT // 2 + 185)

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
        screen.fill((15, 15, 30))
        txt_title = font_title.render("WAR OF WIZARDS", True, (255, 215, 0))
        screen.blit(txt_title, (WIDTH // 2 - txt_title.get_width() // 2, max(30, HEIGHT // 2 - 220)))
        draw_button(screen, btn_play, "PLAY", (0, 120, 70), (0, 180, 100), mouse_pos)
        draw_button(screen, btn_howto, "HOW TO PLAY", (0, 75, 130), (0, 110, 190), mouse_pos)
        draw_button(screen, btn_camera, "CAMERA", (90, 60, 130), (125, 85, 180), mouse_pos)
        draw_button(screen, btn_quit, "QUIT", (140, 30, 30), (200, 50, 50), mouse_pos)
        cam = settings_cache["camera_name"] or f"Camera {settings_cache['camera_index'] + 1}"
        cam_txt = font_card_small.render(f"Camera: {cam}", True, (150, 150, 175))
        screen.blit(cam_txt, (WIDTH // 2 - cam_txt.get_width() // 2, HEIGHT - 30))
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

    clock.tick(60)  # the menu used to spin one CPU core at 100%