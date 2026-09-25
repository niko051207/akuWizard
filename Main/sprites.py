"""
Sprite loading for War of Wizards.

Drop PNGs into assets/sprites/ and the game picks them up. Anything missing
falls back to the shape-drawn visuals, so you can add sprites one at a time.

NAMES THE GAME LOOKS FOR
    enemy_idle      enemy standing / floating (required for the enemy to use sprites)
    enemy_charge    while telegraphing an attack          (falls back to enemy_idle)
    enemy_stunned   after being interrupted                (falls back to enemy_idle)
    enemy_hurt      short flinch when hit                  (falls back to enemy_idle)
    enemy_dead      shown after you win                    (falls back to enemy_hurt / idle)
    enemy_taunt     gloating after she hits you / you lose (falls back to enemy_idle)
    enemy_talk      while a dialogue line is on screen     (falls back to enemy_idle)
    player_idle     your wizard at the bottom-left
    player_cast     plays once after a successful cast     (falls back to player_idle)
    player_hurt     short flinch when hit                  (falls back to player_idle)
    player_dead     shown after you lose                   (falls back to player_hurt / idle)
    proj_player     your projectile (tinted to the spell colour unless "tint": false)
    proj_<spell>    a projectile for one spell, e.g. proj_fireball.png, proj_hot_stuff.png
    proj_enemy      the enemy's projectile

FILE FORMATS (pick whichever your asset pack uses)
    1. A single image:           assets/sprites/player_idle.png
    2. A horizontal sprite strip: assets/sprites/enemy_idle.png  (6 frames side by side)
       Square frames are detected automatically (width = N * height).
       For non-square frames set "frames" in sprites.json.
    3. A folder of frames:        assets/sprites/enemy_idle/0.png, 1.png, 2.png ...
                                  (sorted by the number in the filename)

OPTIONAL assets/sprites/sprites.json, per sprite name:
    {
      "enemy_idle":  {"frames": 6, "fps": 8, "flip": true},
      "player_cast": {"fps": 14, "loop": false},
      "proj_player": {"fps": 12, "rotate": true, "tint": true},
      "enemy":       {"height": 1.4, "offset": [0, 10], "orb": [0.12, 0.08]}
    }
    frames   frame count in a strip (default: auto for square frames, else 1)
    fps      animation speed (default 8)
    loop     false = play once and hold the last frame (default true)
    flip     mirror horizontally, e.g. the enemy sheet faces right (default false)
    smooth   true = smooth scaling for hand-painted art; false keeps pixel art crisp
    rotate   projectiles: turn to face the direction of travel (default true)
    tint     projectiles: colour the sprite with the spell colour (default true for proj_player only)
    trim     true = crop empty transparent borders from every frame (useful for padded sheets)
    fade     fade the bottom of each frame to transparent, as a fraction of its height
             (for busts / portraits that are cut off at the shoulders), e.g. 0.3
    A key ending in "_*" sets defaults for every sprite with that prefix, e.g.
      "enemy_*": {"flip": true, "fade": 0.3}
    The "enemy" / "player" entries tune placement for all of that character's sprites:
    height   size relative to the hitbox height (default 1.0)
    offset   [x, y] pixel nudge at a 600 px tall arena
    orb      enemy only: where shots spawn, as fractions of the hitbox [x, y] (default [0.1, 0.06])
"""
import json
import os
import re

import pygame

from paths import resource_path

SPRITE_DIR = resource_path("assets", "sprites")
DEFAULT_FPS = 8

_FALLBACK = {
    "enemy_charge": "enemy_idle",
    "enemy_stunned": "enemy_idle",
    "enemy_hurt": "enemy_idle",
    "enemy_dead": "enemy_hurt",
    "enemy_taunt": "enemy_idle",
    "enemy_talk": "enemy_idle",
    "player_cast": "player_idle",
    "player_hurt": "player_idle",
    "player_dead": "player_hurt",
}


def _frame_key(filename):
    nums = re.findall(r"\d+", filename)
    return (int(nums[-1]) if nums else 0, filename)


class Animation:
    def __init__(self, frames, fps=DEFAULT_FPS, loop=True, smooth=False):
        self.frames = frames
        self.fps = max(0.1, float(fps))
        self.loop = loop
        self.smooth = smooth
        self._scaled = {}

    @property
    def duration(self):
        return len(self.frames) / self.fps

    def index(self, t):
        i = int(max(0.0, t) * self.fps)
        n = len(self.frames)
        return i % n if self.loop else min(i, n - 1)

    def frame(self, t, height=None):
        """Frame at time t, optionally scaled to a pixel height (cached)."""
        i = self.index(t)
        src = self.frames[i]
        if not height:
            return src
        height = max(1, int(height))
        key = (i, height)
        surf = self._scaled.get(key)
        if surf is None:
            if len(self._scaled) > 256:
                self._scaled.clear()
            w = max(1, round(src.get_width() * height / src.get_height()))
            scale = pygame.transform.smoothscale if self.smooth else pygame.transform.scale
            surf = scale(src, (w, height))
            self._scaled[key] = surf
        return surf


class SpriteBank:
    """Loads sprites lazily by name; get() returns None for anything that isn't there."""

    def __init__(self, folder=SPRITE_DIR):
        self.folder = folder
        self._cache = {}
        self.config = {}
        cfg_path = os.path.join(folder, "sprites.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f:
                    self.config = json.load(f)
            except (OSError, ValueError) as e:
                print(f"[sprites] Couldn't read {cfg_path}: {e}")

    def opts(self, name):
        """Options for a sprite: its own entry on top of any "<prefix>_*" group defaults."""
        merged = {}
        if "_" in name:
            group = self.config.get(name.split("_", 1)[0] + "_*", {})
            if isinstance(group, dict):
                merged.update(group)
        o = self.config.get(name, {})
        if isinstance(o, dict):
            merged.update(o)
        return merged

    def get(self, name, fallback=True):
        if name not in self._cache:
            self._cache[name] = self._load(name)
        anim = self._cache[name]
        if anim is None and fallback and name in _FALLBACK:
            return self.get(_FALLBACK[name])
        return anim

    def has(self, name):
        return self.get(name, fallback=False) is not None

    # -- loading --
    def _load(self, name):
        o = self.opts(name)
        frames = None
        folder = os.path.join(self.folder, name)
        png = os.path.join(self.folder, name + ".png")
        try:
            if os.path.isdir(folder):
                files = sorted((f for f in os.listdir(folder) if f.lower().endswith(".png")), key=_frame_key)
                frames = [self._image(os.path.join(folder, f)) for f in files]
            elif os.path.exists(png):
                frames = self._slice(self._image(png), o.get("frames"))
        except (pygame.error, OSError, ValueError) as e:
            print(f"[sprites] Failed to load '{name}': {e}")
            return None
        if not frames:
            return None
        if o.get("flip"):
            frames = [pygame.transform.flip(f, True, False) for f in frames]
        frames = [self._trim(f) for f in frames] if o.get("trim", False) else frames
        if o.get("fade"):
            frames = [self._fade_bottom(f, float(o["fade"])) for f in frames]
        return Animation(frames, o.get("fps", DEFAULT_FPS), o.get("loop", True), o.get("smooth", False))

    @staticmethod
    def _image(path):
        img = pygame.image.load(path)
        return img.convert_alpha() if pygame.display.get_surface() else img

    @staticmethod
    def _slice(sheet, count=None):
        w, h = sheet.get_size()
        if not count:
            count = w // h if h and w % h == 0 and w > h else 1
        count = max(1, int(count))
        fw = w // count
        return [sheet.subsurface((i * fw, 0, fw, h)).copy() for i in range(count)]

    @staticmethod
    def _fade_bottom(surf, fraction):
        """Multiply alpha by a ramp so the bottom `fraction` of the image fades out."""
        w, h = surf.get_size()
        n = max(1, min(h, int(h * fraction)))
        out = surf.convert_alpha() if pygame.display.get_surface() else surf.copy()
        ramp = pygame.Surface((w, n), pygame.SRCALPHA)
        for i in range(n):
            a = int(255 * (1 - (i + 1) / n) ** 1.5)
            ramp.fill((255, 255, 255, a), (0, i, w, 1))
        out.blit(ramp, (0, h - n), special_flags=pygame.BLEND_RGBA_MULT)
        return out

    @staticmethod
    def _trim(surf):
        r = surf.get_bounding_rect()
        return surf.subsurface(r).copy() if r.w and r.h else surf


def tinted(surf, color):
    """Multiply a (usually white/grey) sprite by a colour, keeping its alpha."""
    out = surf.copy()
    out.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def flashed(surf, amount):
    """Brighten toward white by amount 0..1 (hit flash), keeping alpha."""
    out = surf.copy()
    v = int(255 * max(0.0, min(1.0, amount)))
    out.fill((v, v, v, 0), special_flags=pygame.BLEND_RGBA_ADD)
    return out
