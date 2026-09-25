"""
Pixel-art UI pieces for War of Wizards, built from assets/ui/ (cut from the
MagicUiFree pack by tools/cut_assets.py).

Everything is assembled at the pack's native resolution and then scaled up by
a whole number `px` with nearest-neighbour scaling, so pixels stay crisp at any
window size. If assets/ui/ is missing, every call falls back to plain shapes,
so the game still runs.

    kit = UIKit()
    px = kit.px_for(layout.scale)                  # 2 at a 600 px tall arena
    surf = kit.panel(300, 200, px, gem=True)       # the ornate menu panel
    surf = kit.panel(300, 80, px)                  # plain frame (HUD, toasts)
    surf = kit.button(220, 52, px, hover=True)
    surf = kit.bar(240, 18, 0.7, (60, 220, 90), px, trail=0.8)
    img  = kit.image("skull", px)                  # any PNG in assets/ui/
"""
import os
import random

import pygame

from paths import resource_path

UI_DIR = resource_path("assets", "ui")

# Kit palette (sampled from the sheet)
INK = (23, 7, 35)             # darkest outline
PLUM = (55, 23, 66)           # dark interior
VIOLET = (99, 39, 121)        # panel interior
PARCHMENT = (207, 181, 143)   # button face / trim
PARCHMENT_DARK = (130, 113, 90)
BROWN = (75, 64, 49)          # button outline
CREAM = (253, 234, 182)       # highlight trim
GEM_BLUE = (82, 157, 246)
TEXT_DARK = (48, 20, 60)

# --- where the 9-slice pieces live on panel.png (88 x 122) ---
P_TOP = 20                    # ornate top band height (gem + scalloped corners)
P_SIDE = 4                    # left / right border width
P_BOTTOM = 3                  # bottom border height
P_TILE = (8, 20, 72, 16)      # clean interior patch that gets tiled
P_SIDE_TILE_Y = (40, 16)      # rows of the side border that get tiled vertically
# top band, left to right: fixed corner | stretch | fixed gem section | stretch | fixed corner
P_TOP_SEGMENTS = [(0, 12, False), (12, 6, True), (18, 52, False), (70, 6, True), (76, 12, False)]

# button.png (54 x 17): 3 px corners, 5 px bottom (includes the shadow)
B_LEFT, B_RIGHT, B_TOP, B_BOTTOM = 3, 3, 3, 5


def _tile(dst, src, rect):
    """Tile surface src over rect of dst."""
    x0, y0, w, h = rect
    sw, sh = src.get_size()
    if sw <= 0 or sh <= 0:
        return
    clip = dst.get_clip()
    dst.set_clip(pygame.Rect(rect).clip(clip))
    for y in range(y0, y0 + h, sh):
        for x in range(x0, x0 + w, sw):
            dst.blit(src, (x, y))
    dst.set_clip(clip)


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


class UIKit:
    def __init__(self, folder=UI_DIR):
        self.folder = folder
        self._img = {}
        self._cache = {}
        self.ok = os.path.exists(os.path.join(folder, "panel.png"))

    # -- basics --
    @staticmethod
    def px_for(scale, base=2):
        """Screen pixels per kit pixel for a layout scale (1.0 = 600 px tall)."""
        return max(1, int(round(base * scale)))

    def raw(self, name):
        """An unscaled PNG from assets/ui/, or None."""
        if name not in self._img:
            path = os.path.join(self.folder, name + ".png")
            img = None
            if os.path.exists(path):
                try:
                    img = pygame.image.load(path)
                    if pygame.display.get_surface():
                        img = img.convert_alpha()
                except pygame.error as e:
                    print(f"[ui] Couldn't load {path}: {e}")
            self._img[name] = img
        return self._img[name]

    def image(self, name, px, tint=None):
        """A PNG from assets/ui/ scaled by px (cached). tint recolours white icons."""
        key = ("img", name, px, tint)
        if key not in self._cache:
            src = self.raw(name)
            if src is None:
                return None
            img = pygame.transform.scale(src, (src.get_width() * px, src.get_height() * px))
            if tint:
                img = img.copy()
                img.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self._store(key, img)
        return self._cache[key]

    def _store(self, key, surf):
        if len(self._cache) > 300:
            self._cache.clear()
        self._cache[key] = surf
        return surf

    @staticmethod
    def _up(native, px):
        return pygame.transform.scale(native, (native.get_width() * px, native.get_height() * px))

    # -- panels --
    def panel(self, w, h, px, gem=False, alpha=255):
        """A framed panel about w x h screen pixels (rounded to whole kit pixels).
        gem=True: the ornate top with the blue gem (needs some height and width)."""
        nw, nh = max(12, round(w / px)), max(8, round(h / px))
        if gem:
            nw, nh = max(nw, 76), max(nh, P_TOP + P_BOTTOM + 4)
        key = ("panel", nw, nh, px, gem, alpha)
        if key in self._cache:
            return self._cache[key]
        src = self.raw("panel")
        if src is None:
            surf = self._fallback_panel(nw * px, nh * px, px)
        else:
            surf = self._up(self._build_panel(src, nw, nh, gem), px)
        if alpha < 255:
            surf.set_alpha(alpha)
        return self._store(key, surf)

    def _build_panel(self, src, nw, nh, gem):
        sw, sh = src.get_size()
        out = pygame.Surface((nw, nh), pygame.SRCALPHA)
        top = P_TOP if gem else P_BOTTOM
        # interior
        _tile(out, src.subsurface(P_TILE), (0, 0, nw, nh))
        # left / right borders
        ty, th = P_SIDE_TILE_Y
        _tile(out, src.subsurface((0, ty, P_SIDE, th)), (0, top, P_SIDE, nh - top - P_BOTTOM))
        _tile(out, src.subsurface((sw - P_SIDE, ty, P_SIDE, th)), (nw - P_SIDE, top, P_SIDE, nh - top - P_BOTTOM))
        # bottom border (corners fixed, middle tiled)
        by = sh - P_BOTTOM
        bottom = pygame.Surface((nw, P_BOTTOM), pygame.SRCALPHA)
        _tile(bottom, src.subsurface((P_SIDE, by, sw - 2 * P_SIDE, P_BOTTOM)), (0, 0, nw, P_BOTTOM))
        bottom.blit(src.subsurface((0, by, P_SIDE, P_BOTTOM)), (0, 0))
        bottom.blit(src.subsurface((sw - P_SIDE, by, P_SIDE, P_BOTTOM)), (nw - P_SIDE, 0))
        out.blit(bottom, (0, nh - P_BOTTOM))

        if not gem:
            # plain frame: the bottom border mirrored makes the top
            out.fill((0, 0, 0, 0), (0, 0, nw, P_BOTTOM))
            out.blit(pygame.transform.flip(bottom, False, True), (0, 0))
            return out

        # ornate top band: the stretchable parts share out the extra width
        out.fill((0, 0, 0, 0), (0, 0, nw, P_TOP))
        extra = nw - sw
        stretch = [s for s in P_TOP_SEGMENTS if s[2]]
        x = 0
        k = 0
        for sx, seg_w, stretchy in P_TOP_SEGMENTS:
            piece = src.subsurface((sx, 0, seg_w, P_TOP))
            if stretchy:
                share = extra // len(stretch) + (extra % len(stretch) if k == len(stretch) - 1 else 0)
                width = max(0, seg_w + share)
                _tile(out, piece, (x, 0, width, P_TOP))
                k += 1
            else:
                width = seg_w
                out.blit(piece, (x, 0))
            x += width
        return out

    @staticmethod
    def _fallback_panel(w, h, px):
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (*VIOLET, 235), surf.get_rect(), border_radius=4 * px)
        pygame.draw.rect(surf, PARCHMENT, surf.get_rect(), max(2, px), border_radius=4 * px)
        return surf

    # -- buttons --
    def button(self, w, h, px, hover=False, pressed=False):
        nw, nh = max(B_LEFT + B_RIGHT + 2, round(w / px)), max(B_TOP + B_BOTTOM + 2, round(h / px))
        key = ("button", nw, nh, px, hover, pressed)
        if key in self._cache:
            return self._cache[key]
        src = self.raw("button")
        if src is None:
            surf = pygame.Surface((nw * px, nh * px), pygame.SRCALPHA)
            col = _lerp(PARCHMENT, (255, 255, 255), 0.25) if hover else PARCHMENT
            pygame.draw.rect(surf, col, surf.get_rect(), border_radius=3 * px)
            pygame.draw.rect(surf, BROWN, surf.get_rect(), px, border_radius=3 * px)
            return self._store(key, surf)
        sw, sh = src.get_size()
        out = pygame.Surface((nw, nh), pygame.SRCALPHA)
        L, R, T, B = B_LEFT, B_RIGHT, B_TOP, B_BOTTOM
        mw, mh = sw - L - R, sh - T - B
        cols = [(0, L, 0, L), (L, mw, L, nw - L - R), (sw - R, R, nw - R, R)]
        rows = [(0, T, 0, T), (T, mh, T, nh - T - B), (sh - B, B, nh - B, B)]
        for sx, sw_, dx, dw in cols:
            for sy, sh_, dy, dh in rows:
                piece = src.subsurface((sx, sy, sw_, sh_))
                if (dw, dh) != (sw_, sh_):
                    piece = pygame.transform.scale(piece, (dw, dh))   # flat colours, so stretching is clean
                out.blit(piece, (dx, dy))
        if hover:
            out.fill((28, 24, 18, 0), special_flags=pygame.BLEND_RGBA_ADD)
        if pressed:
            shifted = pygame.Surface((nw, nh), pygame.SRCALPHA)
            shifted.blit(out, (0, 1))
            out = shifted
        return self._store(key, self._up(out, px))

    # -- bars --
    def bar(self, w, h, pct, color, px, trail=None):
        """A framed pixel bar. trail (0..1, >= pct) shows recent damage as a pale segment."""
        nw, nh = max(8, round(w / px)), max(5, round(h / px))
        pct = max(0.0, min(1.0, pct))
        inner_w, inner_h = nw - 4, nh - 4
        fill = int(round(inner_w * pct))
        tr = int(round(inner_w * max(pct, min(1.0, trail)))) if trail is not None else fill
        key = ("bar", nw, nh, fill, tr, color, px)
        if key in self._cache:
            return self._cache[key]
        s = pygame.Surface((nw, nh), pygame.SRCALPHA)
        s.fill(INK, (1, 0, nw - 2, nh))
        s.fill(INK, (0, 1, nw, nh - 2))
        s.fill(PARCHMENT, (1, 1, nw - 2, nh - 2))
        s.fill(PARCHMENT_DARK, (2, nh - 2, nw - 4, 1))
        s.fill(PLUM, (2, 2, inner_w, inner_h))
        s.fill(INK, (2, 2, inner_w, 1))                          # inner shadow
        if tr > fill:
            s.fill(_lerp(color, (255, 255, 255), 0.65), (2 + fill, 2, tr - fill, inner_h))
        if fill > 0:
            s.fill(color, (2, 2, fill, inner_h))
            s.fill(_lerp(color, (255, 255, 255), 0.45), (2, 2, fill, 1))
            if inner_h > 2:
                s.fill(_lerp(color, (0, 0, 0), 0.35), (2, 1 + inner_h, fill, 1))
            # little tick marks every 10%
            for i in range(1, 10):
                x = 2 + int(inner_w * i / 10)
                if x < 2 + fill:
                    s.fill(_lerp(color, (0, 0, 0), 0.18), (x, 3, 1, max(1, inner_h - 2)))
        return self._store(key, self._up(s, px))

    # -- text helpers --
    @staticmethod
    def text(font, text, color, shadow=INK, offset=None):
        """Text with a 1-step drop shadow, pixel-art style."""
        fg = font.render(text, True, color)
        if shadow is None:
            return fg
        d = offset or max(1, font.get_height() // 14)
        out = pygame.Surface((fg.get_width() + d, fg.get_height() + d), pygame.SRCALPHA)
        out.blit(font.render(text, True, shadow), (d, d))
        out.blit(fg, (0, 0))
        return out

    @staticmethod
    def wrap(font, text, max_w):
        words, lines, line = text.split(), [], ""
        for w in words:
            test = f"{line} {w}".strip()
            if font.size(test)[0] <= max_w or not line:
                line = test
            else:
                lines.append(line)
                line = w
        if line:
            lines.append(line)
        return lines


def starfield(size, seed=7, density=0.00035):
    """A deep-purple backdrop with a few pixel stars (for menus)."""
    w, h = size
    surf = pygame.Surface((w, h))
    for y in range(0, h, 4):
        t = y / max(1, h)
        surf.fill(_lerp((26, 10, 40), (8, 6, 18), t), (0, y, w, 4))
    rng = random.Random(seed)
    for _ in range(int(w * h * density)):
        x, y = rng.randrange(w), rng.randrange(h)
        c = rng.choice([(90, 70, 130), (140, 120, 190), CREAM])
        s = rng.choice([2, 2, 2, 4])
        surf.fill(c, (x, y, s, s))
    return surf
