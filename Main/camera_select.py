"""
CAMERA screen: lists every connected webcam, shows a live preview with hand
tracking so the player can see which camera actually sees their hand, and
saves the choice to settings.json.

Controls: click a camera (or Up/Down) to preview it, ENTER / "Use this camera"
to save, R to rescan, ESC to go back without changing anything.
"""
import threading

import pygame

import hand_input
from fonts import load_font
from hand_input import HandTracker, cam_rect, pinch_point
from settings import load_settings, save_settings

GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
SOFT = (190, 190, 205)
OK_GREEN = (110, 235, 140)
WARN = (255, 200, 80)
FAIL = (235, 120, 110)
BG = (15, 15, 30)
PANEL = (30, 30, 50)

# MediaPipe hand skeleton (pairs of landmark indices)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]


class _Scan:
    """Runs hand_input.list_cameras() on a thread so the window stays responsive."""

    def __init__(self):
        self.result = None
        self.done = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.result = hand_input.list_cameras()
        except Exception as e:
            print("Camera scan failed:", e)
            self.result = []
        self.done.set()


def _fit_text(font, text, color, max_w):
    """Render text, trimming with '...' so it never spills out of its box."""
    surf = font.render(text, True, color)
    if surf.get_width() <= max_w:
        return surf
    while len(text) > 1 and font.size(text + "...")[0] > max_w:
        text = text[:-1]
    return font.render(text + "...", True, color)


def _button(screen, font, rect, label, base, hover, mouse, enabled=True):
    color = (hover if rect.collidepoint(mouse) else base) if enabled else (55, 55, 65)
    pygame.draw.rect(screen, color, rect, border_radius=8)
    txt = font.render(label, True, WHITE if enabled else (140, 140, 150))
    screen.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2))


def run_camera_select(screen):
    """Returns "MENU" or "QUIT"."""
    f_title = load_font("MedievalSharp-Bold.ttf", 36)
    f_item = load_font("MedievalSharp-Book.ttf", 18)
    f_small = load_font("MedievalSharp-Book.ttf", 16)
    f_button = load_font("MedievalSharp-Bold.ttf", 20)
    clock = pygame.time.Clock()

    settings = load_settings()
    saved_index = settings["camera_index"]

    scan = _Scan()
    cameras = []          # [(index, name)]
    selected = 0          # position in `cameras`
    tracker = None
    last_id = None
    cam_src = cam_rgb = None
    lm = None
    item_rects = []

    def preview(pos):
        """Switch the live preview to cameras[pos]."""
        nonlocal tracker, selected, last_id, cam_src, cam_rgb, lm
        if tracker is not None:
            tracker.stop(wait=False)     # different camera: no need to wait for release
        selected = pos
        last_id, cam_src, cam_rgb, lm = None, None, None, None
        tracker = HandTracker(camera_index=cameras[pos][0])

    def rescan():
        nonlocal scan, tracker, cameras, cam_src, cam_rgb, lm
        if tracker is not None:
            tracker.stop(wait=True)      # release the camera so the scan can open it
            tracker = None
        cameras, cam_src, cam_rgb, lm = [], None, None, None
        scan = _Scan()

    def confirm():
        if not cameras or (tracker is not None and tracker.error):
            return False    # don't save a camera that can't be opened
        idx, name = cameras[selected]
        save_settings({"camera_index": idx, "camera_name": name})
        return True

    try:
        while True:
            mouse = pygame.mouse.get_pos()
            screen = pygame.display.get_surface()
            W, H = screen.get_size()

            # --- layout ---
            list_w = max(180, min(280, int(W * 0.32)))
            list_x, top = 30, 90
            preview_box = pygame.Rect(list_x + list_w + 24, top, W - (list_x + list_w + 24) - 30, H - top - 120)
            btn_use = pygame.Rect(W - 30 - 230, H - 70, 230, 48)
            btn_back = pygame.Rect(30, H - 70, 150, 48)
            btn_rescan = pygame.Rect(list_x, top, list_w, 40)   # moved below the list when drawn

            # --- scan finished? ---
            if scan is not None and scan.done.is_set():
                cameras = scan.result or []
                scan = None
                if cameras:
                    indices = [c[0] for c in cameras]
                    preview(indices.index(saved_index) if saved_index in indices else 0)

            item_h, gap = 54, 8
            item_rects = [pygame.Rect(list_x, top + i * (item_h + gap), list_w, item_h)
                          for i in range(len(cameras))]
            btn_rescan.y = top + len(cameras) * (item_h + gap) + (4 if cameras else 60)

            # --- events ---
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return "QUIT"
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return "MENU"
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and scan is None:
                        if confirm():
                            return "MENU"
                    if event.key == pygame.K_r and scan is None:
                        rescan()
                    if cameras and scan is None and event.key in (pygame.K_DOWN, pygame.K_UP):
                        step = 1 if event.key == pygame.K_DOWN else -1
                        preview((selected + step) % len(cameras))
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if btn_back.collidepoint(event.pos):
                        return "MENU"
                    if scan is None:
                        if btn_use.collidepoint(event.pos) and confirm():
                            return "MENU"
                        if btn_rescan.collidepoint(event.pos):
                            rescan()
                        for i, r in enumerate(item_rects):
                            if r.collidepoint(event.pos) and i != selected:
                                preview(i)

            # --- newest preview frame ---
            if tracker is not None:
                frame = tracker.latest()
                if frame is not None and frame[0] != last_id:
                    last_id, cam_rgb, lm, _ = frame
                    fh, fw = cam_rgb.shape[:2]
                    cam_src = pygame.image.frombuffer(cam_rgb, (fw, fh), "RGB")

            # --- draw ---
            screen.fill(BG)
            title = f_title.render("CHOOSE YOUR CAMERA", True, GOLD)
            screen.blit(title, (W // 2 - title.get_width() // 2, 24))

            if scan is not None:
                msg = f_item.render("Looking for cameras...", True, SOFT)
                screen.blit(msg, (list_x, top + 10))
            elif not cameras:
                screen.blit(f_item.render("No cameras found.", True, FAIL), (list_x, top + 6))
                screen.blit(f_small.render("Plug one in, then Rescan.", True, SOFT), (list_x, top + 32))

            for i, ((idx, name), r) in enumerate(zip(cameras, item_rects)):
                is_sel = i == selected
                hover = r.collidepoint(mouse)
                pygame.draw.rect(screen, (45, 45, 72) if (is_sel or hover) else PANEL, r, border_radius=10)
                if is_sel:
                    pygame.draw.rect(screen, GOLD, r, 2, border_radius=10)
                screen.blit(_fit_text(f_item, name, WHITE, r.w - 24), (r.x + 12, r.y + 7))
                sub = f"Camera {idx + 1}" + ("  ·  in use" if idx == saved_index else "")
                screen.blit(f_small.render(sub, True, OK_GREEN if idx == saved_index else SOFT),
                            (r.x + 12, r.y + 30))

            if scan is None:
                _button(screen, f_small, btn_rescan, "Rescan (R)", (50, 50, 70), (75, 75, 100), mouse)

            # preview panel
            pygame.draw.rect(screen, (8, 8, 18), preview_box, border_radius=10)
            status, status_col = "", SOFT
            if tracker is not None and tracker.error:
                status, status_col = "This camera can't be opened. It may be in use by another app.", FAIL
                msg = f_item.render("No picture from this camera", True, (120, 120, 140))
                screen.blit(msg, (preview_box.centerx - msg.get_width() // 2, preview_box.centery - 10))
            elif cam_src is not None:
                area = cam_rect(preview_box.w, preview_box.h, *cam_src.get_size())
                area.move_ip(preview_box.x, preview_box.y)
                screen.blit(pygame.transform.scale(cam_src, area.size), area.topleft)
                if lm is not None:
                    pts = [(area.x + p.x * area.w, area.y + p.y * area.h) for p in lm]
                    for a, b in HAND_CONNECTIONS:
                        pygame.draw.line(screen, (0, 220, 140), pts[a], pts[b], 2)
                    for p in pts:
                        pygame.draw.circle(screen, WHITE, (int(p[0]), int(p[1])), 3)
                    cx, cy = pinch_point(lm, area)
                    pygame.draw.circle(screen, GOLD, (int(cx), int(cy)), 9, 2)
                    status, status_col = "Hand detected. This camera works", OK_GREEN
                else:
                    status, status_col = "Raise your hand to test hand tracking", WARN
            elif tracker is not None:
                status = "Starting camera..."
                dots = f_item.render(status, True, SOFT)
                screen.blit(dots, (preview_box.centerx - dots.get_width() // 2, preview_box.centery - 10))
            pygame.draw.rect(screen, (60, 60, 85), preview_box, 2, border_radius=10)
            if status:
                screen.blit(_fit_text(f_item, status, status_col, preview_box.w),
                            (preview_box.x, preview_box.bottom + 10))

            _button(screen, f_small, btn_back, "< BACK (ESC)", (60, 60, 75), (90, 90, 110), mouse)
            _button(screen, f_button, btn_use, "USE THIS CAMERA", (0, 120, 70), (0, 180, 100), mouse,
                    enabled=bool(cameras) and scan is None and not (tracker is not None and tracker.error))

            pygame.display.flip()
            clock.tick(60)
    finally:
        if tracker is not None:
            tracker.stop(wait=True)   # the game may open this camera right after