import json
import math
import random
import time

import pygame
from dollarpy import Point

from fonts import load_font
from hand_input import HandTracker, PinchState, StrokeBuilder, cam_rect, pinch_point
from paths import user_data_path
from settings import load_settings
from sounds import play as play_sfx, play_music, stop_music
from spell_recognizer import setup_recognizer, spell_effect, spell_role_short

# ---------------------------------------------------------------------------
# Tuning (all speeds are per SECOND at a 600 px tall arena; they scale with the window)
# ---------------------------------------------------------------------------
MIN_ACCURACY = 0.50        # best match must score at least this...
MIN_MARGIN = 0.10          # ...and beat the next-best *different* spell by this much
MIN_STROKE_POINTS = 10     # shorter strokes are treated as accidental pinches
SIZE_MULT_RANGE = (0.7, 1.3)   # drawing bigger helps a little (was up to 2.5x)

PLAYER_MAX_HP = 500
ENEMY_MAX_HP = 1000
MANA_MAX = 100
MANA_REGEN = 12.0          # mana per second
SHIELD_DURATION = 1.5
FLINCH_DUR = 0.25
COUNTDOWN = 3.0
PLAYER_PROJ_SPEED = 1100   # px/s
PLAYER_HIT_RADIUS = 60
VOLLEY_SPREAD_DEG = 6

# Spell effects
BURN_DURATION = 3.0        # "burn": extra damage over time after the hit...
BURN_FRACTION = 0.4        # ...totalling 40% of the hit
BURN_TICK = 0.5
STUN_DURATION = 1.4        # "interrupt": a charging enemy is stunned this long
PARRY_WINDOW = 0.35        # Shield cast this close before impact reflects the shot
REFLECT_MULT = 2           # reflected shots hit twice as hard
COUNTER_SPEED = 600        # "counter": slower orb that destroys enemy shots it touches
COUNTER_RADIUS_MULT = 1.5

# Enemy wards (phase II+): block everything except "interrupt" spells
WARD_DURATION = 3.0
WARD_CHANCE = {1: 0.0, 2: 0.35, 3: 0.5}

# Phase 1 (HP > 50%): slow and predictable
# Phase 2 (HP 25-50%): faster, more aggressive, starts raising wards
# Phase 3 (HP < 25%): rapid volleys, desperate
PHASE_PARAMS = {
    1: {"idle_dur": 2.5, "telegraph_dur": 1.2, "cooldown": 0.6, "attack": "heavy",
        "speed": 420, "dmg": 50, "radius": 18, "color": (255, 80, 80)},
    2: {"idle_dur": 1.8, "telegraph_dur": 0.8, "cooldown": 0.6, "attack": "heavy",
        "speed": 560, "dmg": 50, "radius": 18, "color": (255, 80, 80)},
    3: {"idle_dur": 0.8, "telegraph_dur": 0.5, "cooldown": 0.3, "attack": "volley",
        "speed": 700, "dmg": 20, "radius": 12, "color": (255, 140, 30)},
}

GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
SOFT = (200, 200, 210)
FAIL = (230, 120, 110)
SHIELD_BLUE = (100, 180, 255)
MANA_BLUE = (60, 140, 255)
WARD_VIOLET = (150, 120, 255)
EMBER = (255, 140, 40)
BG = (8, 8, 18)

RECORDS_FILE = user_data_path("records.json")


def load_best_time():
    try:
        with open(RECORDS_FILE) as f:
            return json.load(f).get("best_win_time")
    except (OSError, ValueError):
        return None


def save_best_time(seconds):
    try:
        with open(RECORDS_FILE, "w") as f:
            json.dump({"best_win_time": round(seconds, 2)}, f)
    except OSError as e:
        print("Couldn't save best time:", e)


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# ---------------------------------------------------------------------------
# Layout: everything positioned relative to the letterboxed camera arena
# ---------------------------------------------------------------------------
class Layout:
    def __init__(self, arena):
        self.arena = arena
        self.scale = arena.h / 600
        s = self.scale
        self.enemy_rect = pygame.Rect(
            arena.x + int(arena.w * 0.72), arena.y + int(arena.h * 0.3), int(130 * s), int(160 * s)
        )
        self.enemy_center = self.enemy_rect.center
        r = self.enemy_rect
        self.staff_orb = (r.x + int(r.w * 0.1), r.y + int(r.h * 0.06))   # enemy spells come from here
        self.player_pos = (arena.x + int(arena.w * 0.25), arena.y + int(arena.h * 0.70))


# ---------------------------------------------------------------------------
# Combat state (a rematch is just a new Combat())
# ---------------------------------------------------------------------------
class Combat:
    def __init__(self, spell_data=None):
        spell_data = spell_data or {}
        # the enemy only raises wards if the player owns a spell that can break them
        self.interrupt_spell = next(
            (n for n, e in spell_data.items() if spell_effect(n, e) == "interrupt"), None)

        self.enemy_hp = ENEMY_MAX_HP
        self.player_hp = PLAYER_MAX_HP
        self.mana = float(MANA_MAX)
        self.ai_state = "idle"         # idle -> telegraphing -> cooldown (or stunned)
        self.ai_timer = 0.0
        self.flinch = 0.0
        self.shield = 0.0
        self.ward = 0.0
        self.ward_hint_shown = False
        self.burn_time = 0.0
        self.burn_dps = 0.0
        self.burn_acc = 0.0
        self.waiting_for_hand = True
        self.countdown = COUNTDOWN
        self.fight_time = 0.0
        self.t = 0.0                   # animation clock
        self.result = None             # "WIN" / "LOSE"
        self.new_best = False
        self.casts = 0
        self.acc_sum = 0.0
        self.damage_taken = 0
        self.interrupts = 0
        self.parries = 0
        self.countered = 0
        self.player_shots = []
        self.enemy_shots = []
        self.particles = []
        self.texts = []
        self.pulses = []
        self.bolts = []
        self.toast = None
        self.flash_color = (255, 0, 0)
        self.flash_alpha = 0.0
        self.shake_time = 0.0
        self.shake_mag = 0.0

    # -- state helpers --
    @property
    def over(self):
        return self.result is not None

    @property
    def fighting(self):
        return not self.waiting_for_hand and self.countdown <= 0 and not self.over

    def phase(self):
        pct = self.enemy_hp / ENEMY_MAX_HP
        return 1 if pct > 0.5 else 2 if pct > 0.25 else 3

    def show_toast(self, title, sub="", color=GOLD, life=1.8):
        self.toast = {"title": title, "sub": sub, "color": color, "life": life, "max": life}

    def float_text(self, text, pos, color, life=0.8):
        self.texts.append({"text": text, "x": pos[0] + random.randint(-15, 15), "y": pos[1],
                           "color": color, "life": life})

    def burst(self, pos, color, n=14, speed=260, life=0.35):
        for _ in range(n):
            ang = random.uniform(0, math.tau)
            v = random.uniform(0.35, 1.0) * speed
            self.particles.append({"x": pos[0], "y": pos[1], "vx": math.cos(ang) * v,
                                   "vy": math.sin(ang) * v, "radius": random.randint(2, 5),
                                   "color": color, "life": random.uniform(0.6, 1.0) * life})

    def add_shake(self, duration, magnitude):
        current = self.shake_mag if self.shake_time > 0 else 0.0
        self.shake_time = max(self.shake_time, duration)
        self.shake_mag = max(current, magnitude)

    def clear_in_flight(self):
        self.player_shots.clear()
        self.enemy_shots.clear()
        self.particles.clear()
        self.pulses.clear()
        self.bolts.clear()

    # -- casting --
    def cast(self, points, cast_time, recognizer, spell_data, L):
        if len(points) < MIN_STROKE_POINTS:
            return  # accidental tap-pinch: ignore quietly
        if recognizer is None:
            play_sfx("fail", volume=0.6)
            self.show_toast("No spells recorded", "Run record_spells.py to add some", FAIL, 3.0)
            return

        name, score, runner_up = recognizer.recognize_ranked([Point(x, y) for x, y in points])
        pct = round(score * 100)
        if name is None or name not in spell_data or score < MIN_ACCURACY:
            play_sfx("fail", volume=0.6)
            self.show_toast("Fizzled", f"Closest match only {pct}%", FAIL)
            return
        if score - runner_up < MIN_MARGIN:
            play_sfx("fail", volume=0.6)
            self.show_toast("Fizzled", "Looked like two spells at once. Draw it more clearly", FAIL)
            return

        cfg = spell_data[name]
        cost = cfg.get("cost", 25)
        if self.mana < cost:
            play_sfx("fail", volume=0.6)
            self.show_toast("Not enough mana", f"{name} needs {cost}", MANA_BLUE)
            return

        self.mana -= cost
        self.casts += 1
        self.acc_sum += score
        effect = spell_effect(name, cfg)

        if effect == "parry":
            self.shield = SHIELD_DURATION
            play_sfx("shield_cast", volume=0.8)
            self.burst(L.player_pos, SHIELD_BLUE, n=18, speed=200)
            self.show_toast(name, f"{pct}% match · shield up", SHIELD_BLUE)
            return

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        lo, hi = SIZE_MULT_RANGE
        size_mult = max(lo, min(hi, diagonal / (L.arena.h * 0.3)))
        dmg = max(1, int(cfg["damage"] * score * size_mult))
        color = tuple(cfg["color"])
        sx, sy = points[-1]
        play_sfx("cast", volume=0.8)
        self.show_toast(name, f"{pct}% match · {size_mult:.2f}x size · {dmg} dmg", GOLD)

        if effect == "interrupt":
            # instant: a lightning bolt from the end of the stroke to the enemy
            self.bolts.append({"pts": self._bolt_path((sx, sy), L.enemy_center, L.scale),
                               "color": color, "life": 0.22})
            self._hit_enemy(dmg, color, "interrupt", L)
            return

        radius = int(14 * L.scale * max(0.8, size_mult))
        speed = PLAYER_PROJ_SPEED
        if effect == "counter":
            radius = int(radius * COUNTER_RADIUS_MULT)
            speed = COUNTER_SPEED
        self.player_shots.append({"x": float(sx), "y": float(sy), "dmg": dmg, "color": color,
                                  "radius": radius, "effect": effect, "speed": speed, "trail": []})

    @staticmethod
    def _bolt_path(start, end, s, segments=9):
        (x0, y0), (x1, y1) = start, end
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1
        nx, ny = -dy / length, dx / length
        pts = [start]
        for i in range(1, segments):
            t = i / segments
            off = random.uniform(-22, 22) * s
            pts.append((x0 + dx * t + nx * off, y0 + dy * t + ny * off))
        pts.append(end)
        return pts

    def _hit_enemy(self, dmg, color, effect, L):
        if self.over:
            return
        ex, ey = L.enemy_center
        if self.ward > 0:
            if effect == "interrupt":
                self.ward = 0.0
                self.interrupts += 1
                self.float_text("WARD SHATTERED!", (ex, ey - 70 * L.scale), WARD_VIOLET, 1.1)
                self.burst((ex, ey), WARD_VIOLET, n=26, speed=340, life=0.5)
            else:
                play_sfx("shield_block", volume=0.6)
                self.float_text("WARDED", (ex, ey - 40 * L.scale), WARD_VIOLET)
                self.burst((ex - 50 * L.scale, ey), WARD_VIOLET, n=8, speed=160)
                return

        if effect == "interrupt" and self.ai_state == "telegraphing":
            self.ai_state, self.ai_timer = "stunned", 0.0
            self.interrupts += 1
            self.float_text("INTERRUPTED!", (ex - 150 * L.scale, ey + 20 * L.scale), (255, 240, 120), 1.1)

        self.enemy_hp -= dmg
        self.flinch = FLINCH_DUR
        play_sfx("hit_enemy", volume=0.7)
        self.float_text(f"-{dmg}", (ex, ey - 40 * L.scale), (255, 80, 60))
        self.burst((ex, ey), color, n=16 if dmg < 150 else 26, speed=300)
        if dmg >= 150:
            self.add_shake(0.18, 5 * L.scale)
        if effect == "burn":
            self.burn_time = BURN_DURATION
            self.burn_dps = dmg * BURN_FRACTION / BURN_DURATION
            self.burn_acc = 0.0
        if self.enemy_hp <= 0:
            self._end("WIN")

    # -- per-frame simulation --
    def update(self, dt, L):
        s = L.scale
        self.t += dt
        self._update_effects(dt, s)
        self._update_player_shots(dt, L)
        self._update_enemy_shots(dt, L)

        if self.waiting_for_hand:
            return
        if self.countdown > 0:
            self.countdown -= dt
            if self.countdown <= 0:
                self.show_toast("Fight!", "", GOLD, 0.9)
            return
        if self.over:
            return

        self.fight_time += dt
        self.mana = min(MANA_MAX, self.mana + MANA_REGEN * dt)
        self.shield = max(0.0, self.shield - dt)
        self.ward = max(0.0, self.ward - dt)
        self._update_burn(dt, L)
        self._update_ai(dt, L)

    def _update_burn(self, dt, L):
        if self.burn_time <= 0:
            return
        self.burn_time -= dt
        self.burn_acc += dt
        r = L.enemy_rect
        if random.random() < 25 * dt:   # rising embers
            self.particles.append({"x": random.uniform(r.left, r.right), "y": random.uniform(r.centery, r.bottom),
                                   "vx": random.uniform(-15, 15), "vy": -random.uniform(60, 130) * L.scale,
                                   "radius": random.randint(2, 4), "color": EMBER, "life": 0.6})
        if self.burn_acc >= BURN_TICK:
            self.burn_acc -= BURN_TICK
            tick = max(1, round(self.burn_dps * BURN_TICK))
            self.enemy_hp -= tick
            self.float_text(f"-{tick}", (r.centerx + 30 * L.scale, r.y + 10 * L.scale), EMBER, 0.6)
            if self.enemy_hp <= 0:
                self._end("WIN")

    def _update_effects(self, dt, s):
        self.flinch = max(0.0, self.flinch - dt)
        self.shake_time = max(0.0, self.shake_time - dt)
        for p in self.particles[:]:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["life"] -= dt
            if p["life"] <= 0:
                self.particles.remove(p)
        for t in self.texts[:]:
            t["y"] -= 60 * s * dt
            t["life"] -= dt
            if t["life"] <= 0:
                self.texts.remove(t)
        for pl in self.pulses[:]:
            pl["radius"] += 120 * s * dt
            pl["alpha"] -= 720 * dt
            if pl["alpha"] <= 0:
                self.pulses.remove(pl)
        for b in self.bolts[:]:
            b["life"] -= dt
            if b["life"] <= 0:
                self.bolts.remove(b)
        if self.flash_alpha > 0:
            self.flash_alpha = max(0.0, self.flash_alpha - 300 * dt)
        if self.toast:
            self.toast["life"] -= dt
            if self.toast["life"] <= 0:
                self.toast = None

    def _update_player_shots(self, dt, L):
        ex, ey = L.enemy_center
        for shot in self.player_shots[:]:
            step = shot.get("speed", PLAYER_PROJ_SPEED) * L.scale * dt
            shot["trail"].append((shot["x"], shot["y"]))
            del shot["trail"][:-8]

            if shot.get("effect") == "counter":     # eat enemy shots on the way
                for ep in self.enemy_shots[:]:
                    if math.hypot(ep["x"] - shot["x"], ep["y"] - shot["y"]) < shot["radius"] + ep["radius"] * L.scale:
                        self.enemy_shots.remove(ep)
                        self.countered += 1
                        self.burst((ep["x"], ep["y"]), shot["color"], n=12, speed=220)
                        self.float_text("COUNTERED", (ep["x"], ep["y"] - 20 * L.scale), shot["color"], 0.7)

            dx, dy = ex - shot["x"], ey - shot["y"]
            dist = math.hypot(dx, dy)
            if dist <= max(step, 20 * L.scale):
                self.player_shots.remove(shot)
                self._hit_enemy(shot["dmg"], shot["color"], shot.get("effect", "none"), L)
            else:
                shot["x"] += dx / dist * step
                shot["y"] += dy / dist * step

    def _update_enemy_shots(self, dt, L):
        px, py = L.player_pos
        a = L.arena
        for ep in self.enemy_shots[:]:
            step = ep["speed"] * L.scale * dt
            if "vx" in ep:
                ep["x"] += ep["vx"] * step
                ep["y"] += ep["vy"] * step
            else:  # heavy shot homes in on the player
                dx, dy = px - ep["x"], py - ep["y"]
                dist = math.hypot(dx, dy)
                if dist > 0:
                    ep["x"] += dx / dist * min(step, dist)
                    ep["y"] += dy / dist * min(step, dist)

            if math.hypot(ep["x"] - px, ep["y"] - py) < PLAYER_HIT_RADIUS * L.scale:
                self.enemy_shots.remove(ep)
                if self.over:
                    continue
                if self.shield > 0 and self.shield >= SHIELD_DURATION - PARRY_WINDOW:
                    # perfect timing: send it back
                    self.parries += 1
                    play_sfx("shield_block", volume=0.9)
                    self.float_text("PARRY!", (px, py - 60 * L.scale), (140, 220, 255), 1.0)
                    self.burst((px, py), SHIELD_BLUE, n=22, speed=320)
                    self.flash_color, self.flash_alpha = (120, 200, 255), 90
                    self.player_shots.append({"x": float(px), "y": float(py), "dmg": ep["dmg"] * REFLECT_MULT,
                                              "color": SHIELD_BLUE, "radius": int(16 * L.scale),
                                              "effect": "none", "speed": PLAYER_PROJ_SPEED, "trail": []})
                elif self.shield > 0:
                    play_sfx("shield_block", volume=0.8)
                    self.float_text("BLOCKED!", (px, py - 50 * L.scale), SHIELD_BLUE)
                    self.burst((px, py), SHIELD_BLUE, n=10, speed=180)
                    self.flash_color, self.flash_alpha = (60, 140, 255), 110
                else:
                    self.player_hp -= ep["dmg"]
                    self.damage_taken += ep["dmg"]
                    play_sfx("hit_player", volume=0.8)
                    self.float_text(f"-{ep['dmg']}", (px, py - 50 * L.scale), (255, 50, 50))
                    self.burst((px, py), ep["color"], n=14, speed=240)
                    self.flash_color, self.flash_alpha = (255, 0, 0), 130
                    self.add_shake(0.3, 9 * L.scale)
                    if self.player_hp <= 0:
                        self._end("LOSE")
                continue

            if not a.inflate(100, 100).collidepoint(ep["x"], ep["y"]):
                self.enemy_shots.remove(ep)

    def _update_ai(self, dt, L):
        phase = self.phase()
        params = PHASE_PARAMS[phase]
        self.ai_timer += dt
        if self.ai_state == "stunned":
            if self.ai_timer >= STUN_DURATION:
                self.ai_state, self.ai_timer = "idle", 0.0
        elif self.ai_state == "idle" and self.ai_timer >= params["idle_dur"]:
            self.ai_state, self.ai_timer = "telegraphing", 0.0
        elif self.ai_state == "telegraphing" and self.ai_timer >= params["telegraph_dur"]:
            self._fire(params, L)
            self.ai_state, self.ai_timer = "cooldown", 0.0
        elif self.ai_state == "cooldown" and self.ai_timer >= params["cooldown"]:
            self.ai_state, self.ai_timer = "idle", 0.0
            self._maybe_raise_ward(phase)

    def _maybe_raise_ward(self, phase):
        if not self.interrupt_spell or self.ward > 0:
            return
        if random.random() < WARD_CHANCE[phase]:
            self.ward = WARD_DURATION
            play_sfx("shield_cast", volume=0.5)
            if not self.ward_hint_shown:
                self.ward_hint_shown = True
                self.show_toast("The enemy raised a ward!", f"{self.interrupt_spell} shatters it", WARD_VIOLET, 2.6)

    def _fire(self, params, L):
        ox, oy = L.staff_orb
        base = {"x": float(ox), "y": float(oy), "speed": params["speed"], "dmg": params["dmg"],
                "radius": params["radius"], "color": params["color"]}
        if params["attack"] == "heavy":
            self.enemy_shots.append(dict(base))
        else:
            px, py = L.player_pos
            aim = math.atan2(py - oy, px - ox)
            for off in (-VOLLEY_SPREAD_DEG, 0, VOLLEY_SPREAD_DEG):
                ang = aim + math.radians(off)
                self.enemy_shots.append(dict(base, vx=math.cos(ang), vy=math.sin(ang)))

    def _end(self, result):
        self.result = result
        self.enemy_hp = max(0, self.enemy_hp)
        self.player_hp = max(0, self.player_hp)
        self.burn_time = 0.0
        self.ward = 0.0
        stop_music()
        if result == "WIN":
            play_sfx("victory", volume=0.9)
            best = load_best_time()
            if best is None or self.fight_time < best:
                save_best_time(self.fight_time)
                self.new_best = True
        else:
            play_sfx("defeat", volume=0.9)


# ---------------------------------------------------------------------------
# Screens outside the fight
# ---------------------------------------------------------------------------
def _pause_music(paused):
    try:
        if pygame.mixer.get_init():
            if paused:
                pygame.mixer.music.pause()
            else:
                pygame.mixer.music.unpause()
    except pygame.error:
        pass


def _center_text(screen, font, text, color, y):
    surf = font.render(text, True, color)
    screen.blit(surf, (screen.get_width() // 2 - surf.get_width() // 2, y))
    return surf


def _wait_for_camera(screen, tracker, clock, fonts):
    """Loading screen until the first camera frame arrives. Returns None when ready,
    or "MENU"/"QUIT" if the player leaves or the camera fails."""
    t0 = time.monotonic()
    while not tracker.ready.is_set():
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "QUIT"
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return "MENU"
        screen = pygame.display.get_surface()
        screen.fill(BG)
        dots = "." * (1 + int((time.monotonic() - t0) * 3) % 3)
        h = screen.get_height()
        _center_text(screen, fonts["big"], f"Summoning the arena{dots}", GOLD, h // 2 - 30)
        _center_text(screen, fonts["small"], "Opening the camera and loading the hand model", SOFT, h // 2 + 10)
        pygame.display.flip()
        clock.tick(30)
    if tracker.error:
        return _show_error(tracker.error, clock, fonts)
    return None


def _show_error(message, clock, fonts):
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "QUIT"
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                return "MENU"
        screen = pygame.display.get_surface()
        screen.fill(BG)
        h = screen.get_height()
        _center_text(screen, fonts["big"], "The camera isn't working", FAIL, h // 2 - 50)
        _center_text(screen, fonts["small"], message[:110], SOFT, h // 2)
        _center_text(screen, fonts["small"], "Press any key to return to the menu", WHITE, h // 2 + 40)
        pygame.display.flip()
        clock.tick(30)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
class Renderer:
    def __init__(self, fonts, spell_data):
        self.f = fonts
        self._glow_cache = {}
        self._size = None
        self.dim = None
        self.flash = None
        self.fx = None
        self.bar_right = 0          # right edge of the spell list, so toasts can avoid it
        self.spells = [(n, e.get("cost", 25), tuple(e.get("color", (200, 200, 200))), spell_role_short(n, e))
                       for n, e in spell_data.items()]

    def _ensure_surfaces(self, screen, arena):
        key = (screen.get_size(), arena.size)
        if key == self._size:
            return
        self._size = key
        self.dim = pygame.Surface(arena.size)
        self.dim.fill((10, 12, 30))
        self.dim.set_alpha(95)          # darken the webcam so spells and UI stand out
        self.flash = pygame.Surface(screen.get_size())
        self.fx = pygame.Surface(screen.get_size(), pygame.SRCALPHA)

    def _glow(self, radius, color, alpha=60):
        radius = max(2, int(radius))
        key = (radius, color, alpha)
        if key not in self._glow_cache:
            if len(self._glow_cache) > 400:
                self._glow_cache.clear()
            g = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
            pygame.draw.circle(g, (*color, alpha), (radius * 2, radius * 2), radius * 2)
            pygame.draw.circle(g, (*color, min(255, alpha * 2)), (radius * 2, radius * 2), int(radius * 1.3))
            self._glow_cache[key] = g
        return self._glow_cache[key]

    def _blit_glow(self, screen, pos, radius, color, alpha=60):
        g = self._glow(radius, color, alpha)
        screen.blit(g, (int(pos[0]) - g.get_width() // 2, int(pos[1]) - g.get_height() // 2))

    def draw(self, screen, c, L, cam, stroke, lm, hand_visible, show_fps, fps, paused=False):
        a, s = L.arena, L.scale
        self._ensure_surfaces(screen, a)

        # ---- world (this part shakes) ----
        screen.fill(BG)
        if cam is not None:
            screen.blit(cam, a.topleft)
            screen.blit(self.dim, a.topleft)
        for p in c.particles:
            pygame.draw.circle(screen, p["color"], (int(p["x"]), int(p["y"])), p["radius"])
        self._draw_enemy(screen, c, L)
        self._draw_player(screen, c, L)
        self._draw_shots(screen, c, L)
        for t in c.texts:     # dark shadow keeps combat text readable on any background
            surf = self.f["dmg"].render(t["text"], True, t["color"])
            shadow = self.f["dmg"].render(t["text"], True, (10, 8, 20))
            x = t["x"] - surf.get_width() // 2
            screen.blit(shadow, (x + 2, t["y"] + 2))
            screen.blit(surf, (x, t["y"]))

        if c.shake_time > 0 and c.shake_mag > 0:
            m = c.shake_mag * min(1.0, c.shake_time / 0.15)
            offset = (int(random.uniform(-m, m)), int(random.uniform(-m, m)))
            world = screen.copy()
            screen.fill(BG)
            screen.blit(world, offset)

        # ---- overlay (steady) ----
        if len(stroke.points) > 1:
            self.fx.fill((0, 0, 0, 0))
            pygame.draw.lines(self.fx, (255, 190, 40, 90), False, stroke.points, max(8, int(16 * s)))
            screen.blit(self.fx, (0, 0))
            pygame.draw.lines(screen, (255, 236, 140), False, stroke.points, max(3, int(5 * s)))

        for pl in c.pulses:
            r = int(pl["radius"])
            surf = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(surf, (0, 255, 120, max(0, int(pl["alpha"]))), (r + 2, r + 2), r, 3)
            screen.blit(surf, (pl["x"] - r - 2, pl["y"] - r - 2))

        if lm is not None:
            cx, cy = pinch_point(lm, a)
            if stroke.active:
                pygame.draw.circle(screen, (0, 255, 100), (int(cx), int(cy)), 12)
                pygame.draw.circle(screen, WHITE, (int(cx), int(cy)), 5)
            else:
                pygame.draw.circle(screen, (255, 90, 90), (int(cx), int(cy)), 12, 2)

        self._draw_spell_bar(screen, c, L)
        self._draw_hud(screen, c, L)
        self._draw_toast(screen, c, L)
        if not c.over:
            self._draw_status(screen, c, L, stroke, hand_visible)

        if c.flash_alpha > 0:
            self.flash.fill(c.flash_color)
            self.flash.set_alpha(int(c.flash_alpha))
            screen.blit(self.flash, (0, 0))

        self._draw_overlays(screen, c, L, paused)
        if show_fps:
            fps_s = self.f["small"].render(f"FPS {fps:.0f}", True, WHITE)
            screen.blit(fps_s, (a.right - fps_s.get_width() - 10, a.y + 10))

    # -- world pieces --
    def _draw_shots(self, screen, c, L):
        s = L.scale
        for shot in c.player_shots:
            r = shot["radius"]
            trail = shot["trail"]
            for i, (tx, ty) in enumerate(trail):
                k = (i + 1) / (len(trail) + 1)
                pygame.draw.circle(screen, _lerp(BG, shot["color"], 0.35 + 0.5 * k), (int(tx), int(ty)),
                                   max(1, int(r * 0.8 * k)))
            pos = (int(shot["x"]), int(shot["y"]))
            self._blit_glow(screen, pos, r, shot["color"])
            if shot.get("effect") == "counter":
                spin = c.t * 8
                pygame.draw.circle(screen, shot["color"], pos, r, max(2, int(3 * s)))
                for k in range(3):
                    ang = spin + k * math.tau / 3
                    pygame.draw.circle(screen, WHITE, (int(pos[0] + math.cos(ang) * r * 0.6),
                                                       int(pos[1] + math.sin(ang) * r * 0.6)), max(2, r // 5))
            else:
                pygame.draw.circle(screen, shot["color"], pos, r)
                pygame.draw.circle(screen, WHITE, pos, max(3, r // 2))

        for b in c.bolts:
            k = max(0.0, b["life"] / 0.22)
            glow_col = _lerp(BG, b["color"], k)
            pygame.draw.lines(screen, glow_col, False, b["pts"], max(4, int(9 * s * k)))
            pygame.draw.lines(screen, WHITE, False, b["pts"], max(1, int(3 * s)))

        for ep in c.enemy_shots:
            r = max(4, int(ep["radius"] * s))
            pos = (int(ep["x"]), int(ep["y"]))
            self._blit_glow(screen, pos, r, ep["color"])
            pygame.draw.circle(screen, ep["color"], pos, r)
            pygame.draw.circle(screen, (30, 0, 0), pos, max(3, r // 2))

    def _draw_enemy(self, screen, c, L):
        """A hooded wizard drawn from shapes; the rect is still the hitbox."""
        r, s = L.enemy_rect, L.scale
        w, h = r.w, r.h
        stunned = c.ai_state == "stunned" and c.fighting
        charging = c.ai_state == "telegraphing" and c.fighting
        bob = 0 if stunned else math.sin(c.t * 2.2) * 4 * s
        cx, top = r.centerx, r.y + bob
        phase = c.phase()

        robe = (62, 28, 96) if phase < 3 else (100, 26, 50)
        if c.burn_time > 0:
            robe = _lerp(robe, (210, 90, 30), 0.25 + 0.15 * math.sin(c.t * 12))
        if c.flinch > 0:
            robe = _lerp(robe, (245, 245, 255), c.flinch / FLINCH_DUR)
        hood = _lerp(robe, (0, 0, 0), 0.35)
        trim = (205, 165, 70)

        # aura while charging / in phase III
        if charging:
            prog = min(1.0, c.ai_timer / PHASE_PARAMS[phase]["telegraph_dur"])
            self._blit_glow(screen, (cx, top + h * 0.55), w * (0.45 + 0.2 * prog), PHASE_PARAMS[phase]["color"], 35)

        # robe + sleeve
        bottom = top + h
        body = [(cx - w * 0.18, top + h * 0.33), (cx + w * 0.18, top + h * 0.33),
                (cx + w * 0.46, bottom), (cx - w * 0.46, bottom)]
        pygame.draw.polygon(screen, robe, body)
        pygame.draw.polygon(screen, hood, body, max(2, int(2 * s)))
        pygame.draw.line(screen, trim, (cx - w * 0.44, bottom - 3 * s), (cx + w * 0.44, bottom - 3 * s), max(2, int(4 * s)))
        pygame.draw.line(screen, trim, (cx - w * 0.26, top + h * 0.6), (cx + w * 0.26, top + h * 0.6), max(1, int(3 * s)))
        staff_x = r.x + w * 0.1
        hand = (staff_x + w * 0.02, top + h * 0.55)
        pygame.draw.polygon(screen, hood, [(cx - w * 0.12, top + h * 0.36), (cx - w * 0.02, top + h * 0.48),
                                           (hand[0] + w * 0.06, hand[1] + h * 0.05), (hand[0], hand[1] - h * 0.04)])

        # staff + orb
        orb = (staff_x, top + h * 0.06)
        pygame.draw.line(screen, (120, 84, 48), (staff_x, orb[1]), (staff_x, bottom), max(3, int(5 * s)))
        pygame.draw.circle(screen, (90, 60, 110), (int(hand[0]), int(hand[1])), max(3, int(w * 0.05)))
        orb_r = w * 0.07
        if stunned:
            orb_col = (120, 120, 135)
        elif charging:
            prog = min(1.0, c.ai_timer / PHASE_PARAMS[phase]["telegraph_dur"])
            orb_col = PHASE_PARAMS[phase]["color"]
            orb_r *= 1 + 1.3 * prog + 0.15 * math.sin(c.t * 30)
            self._blit_glow(screen, orb, orb_r * 1.3, orb_col, 70)
        else:
            orb_col = (175, 125, 255)
            self._blit_glow(screen, orb, orb_r, orb_col, 45)
        pygame.draw.circle(screen, orb_col, (int(orb[0]), int(orb[1])), max(3, int(orb_r)))
        pygame.draw.circle(screen, WHITE, (int(orb[0] - orb_r * 0.3), int(orb[1] - orb_r * 0.3)), max(1, int(orb_r * 0.3)))

        # hood + face
        head = (cx, top + h * 0.27)
        pygame.draw.polygon(screen, hood, [(cx - w * 0.21, top + h * 0.26), (cx + w * 0.21, top + h * 0.26),
                                           (cx + w * 0.07, top - h * 0.03)])
        pygame.draw.circle(screen, hood, (int(head[0]), int(head[1])), int(w * 0.21))
        pygame.draw.ellipse(screen, (8, 4, 14), (cx - w * 0.14, top + h * 0.2, w * 0.26, h * 0.16))
        eye_y = top + h * 0.28
        eyes = [(cx - w * 0.075, eye_y), (cx + w * 0.02, eye_y)]   # looking toward the player
        if stunned:
            for ex, ey in eyes:
                d = max(2, int(w * 0.022))
                pygame.draw.line(screen, (200, 200, 210), (ex - d, ey - d), (ex + d, ey + d), 2)
                pygame.draw.line(screen, (200, 200, 210), (ex - d, ey + d), (ex + d, ey - d), 2)
            for k in range(3):   # dizzy stars
                ang = c.t * 5 + k * math.tau / 3
                sx = head[0] + math.cos(ang) * w * 0.3
                sy = top - h * 0.02 + math.sin(ang) * h * 0.05
                pygame.draw.circle(screen, (255, 230, 90), (int(sx), int(sy)), max(2, int(4 * s)))
        else:
            eye_col = (255, 220, 90) if charging else (255, 70, 70)
            for ex, ey in eyes:
                self._blit_glow(screen, (ex, ey), max(2, w * 0.02), eye_col, 70)
                pygame.draw.circle(screen, eye_col, (int(ex), int(ey)), max(2, int(w * 0.022)))

        # ward: a slowly turning hexagon
        if c.ward > 0:
            R = int(h * 0.62)
            surf = pygame.Surface((R * 2 + 8, R * 2 + 8), pygame.SRCALPHA)
            pts = [(R + 4 + math.cos(c.t * 0.8 + k * math.tau / 6) * R,
                    R + 4 + math.sin(c.t * 0.8 + k * math.tau / 6) * R) for k in range(6)]
            fade = min(1.0, c.ward / 0.4)
            pygame.draw.polygon(surf, (*WARD_VIOLET, int(45 * fade)), pts)
            pygame.draw.polygon(surf, (*WARD_VIOLET, int(220 * fade)), pts, max(2, int(3 * s)))
            screen.blit(surf, (r.centerx - R - 4, r.centery - R - 4))

        # HP bar + labels
        bar = pygame.Rect(r.x, r.y - 25, r.w, 15)
        pygame.draw.rect(screen, (50, 50, 50), bar)
        pygame.draw.rect(screen, (50, 220, 50), (bar.x, bar.y, int(bar.w * max(0, c.enemy_hp) / ENEMY_MAX_HP), bar.h))
        label = ["", "I", "II", "III"][phase]
        col = [(0, 0, 0), (180, 255, 180), (255, 200, 60), (255, 80, 80)][phase]
        screen.blit(self.f["small"].render(f"ENEMY  Phase {label}", True, col), (r.x, r.y - 45))
        tag = None
        if charging:
            tag = ("!! CHARGING !!", (255, 200, 0))
        elif stunned:
            tag = ("STUNNED", (255, 240, 120))
        elif c.ward > 0 and c.fighting:
            tag = ("WARDED", WARD_VIOLET)
        if tag:
            t = self.f["small"].render(tag[0], True, tag[1])
            screen.blit(t, (r.centerx - t.get_width() // 2, r.y - 65))

    def _draw_player(self, screen, c, L):
        # a visible target, so enemy shots fly at something
        px, py = L.player_pos
        r = int(28 * L.scale)
        pygame.draw.circle(screen, (255, 215, 0), (px, py), r, 2)
        pygame.draw.circle(screen, (255, 215, 0), (px, py), max(3, r // 5))
        if c.shield > 0:
            sr = int(PLAYER_HIT_RADIUS * L.scale)
            perfect = c.shield >= SHIELD_DURATION - PARRY_WINDOW
            bubble = pygame.Surface((sr * 2 + 4, sr * 2 + 4), pygame.SRCALPHA)
            alpha = int(70 + 60 * abs(math.sin(c.t * 6)))
            col = (170, 230, 255) if perfect else SHIELD_BLUE
            pygame.draw.circle(bubble, (*col, alpha), (sr + 2, sr + 2), sr)
            pygame.draw.circle(bubble, (*col, 230), (sr + 2, sr + 2), sr, 4 if perfect else 3)
            screen.blit(bubble, (px - sr - 2, py - sr - 2))

    # -- HUD --
    def _draw_spell_bar(self, screen, c, L):
        """Your spells, what they do, and whether you can afford them right now."""
        a = L.arena
        self.bar_right = a.x
        if not self.spells:
            return
        f = self.f["tiny"]
        row_h = f.get_height() + 6
        widths = [f.size(f"{n}  {cost}  {role}")[0] for n, cost, _, role in self.spells]
        w = max(widths) + 40
        panel = pygame.Surface((w, row_h * len(self.spells) + 10), pygame.SRCALPHA)
        panel.fill((10, 10, 25, 150))
        screen.blit(panel, (a.x + 10, a.y + 10))
        self.bar_right = a.x + 10 + w
        y = a.y + 15
        for name, cost, color, role in self.spells:
            ok = c.mana >= cost
            pygame.draw.circle(screen, color if ok else (90, 90, 100), (a.x + 24, y + row_h // 2 - 2), 6)
            x = a.x + 36
            for text, col in ((name, WHITE if ok else (120, 120, 130)),
                              (str(cost), (120, 170, 255) if ok else (90, 100, 130)),
                              (role, SOFT if ok else (110, 110, 120))):
                if not text:
                    continue
                surf = f.render(text, True, col)
                screen.blit(surf, (x, y))
                x += surf.get_width() + 10
            y += row_h

    def _bar(self, screen, x, y, w, h, pct, color, label):
        pygame.draw.rect(screen, (50, 50, 50), (x, y, w, h), border_radius=6)
        if pct > 0:
            pygame.draw.rect(screen, color, (x, y, max(1, int(w * pct)), h), border_radius=6)
        pygame.draw.rect(screen, WHITE, (x, y, w, h), 2, border_radius=6)
        screen.blit(self.f["small"].render(label, True, WHITE), (x, y - 20))

    def _draw_hud(self, screen, c, L):
        a = L.arena
        x, w, h = a.x + 15, min(260, a.w // 3), 20
        mana_y = a.bottom - 35
        hp_y = mana_y - h - 28
        hp_pct = max(0, c.player_hp) / PLAYER_MAX_HP
        self._bar(screen, x, hp_y, w, h, hp_pct, (50, 220, 50) if hp_pct > 0.3 else (220, 60, 50),
                  f"YOU: {max(0, c.player_hp)}/{PLAYER_MAX_HP}")
        if c.shield > 0:
            txt = self.f["small"].render("SHIELD UP", True, SHIELD_BLUE)
            screen.blit(txt, (x + w - txt.get_width(), hp_y - 20))
        self._bar(screen, x, mana_y, w, h, c.mana / MANA_MAX, MANA_BLUE, f"MANA: {int(c.mana)}/{MANA_MAX}")

    def _draw_toast(self, screen, c, L):
        t = c.toast
        if not t:
            return
        alpha = int(255 * min(1.0, t["life"] / 0.3))
        title = self.f["big"].render(t["title"], True, t["color"])
        sub = self.f["small"].render(t["sub"], True, WHITE) if t["sub"] else None
        w = max(title.get_width(), sub.get_width() if sub else 0) + 32
        h = title.get_height() + (sub.get_height() + 4 if sub else 0) + 16
        box = pygame.Surface((w, h), pygame.SRCALPHA)
        box.fill((10, 10, 25, 190))
        box.blit(title, (w // 2 - title.get_width() // 2, 8))
        if sub:
            box.blit(sub, (w // 2 - sub.get_width() // 2, 12 + title.get_height()))
        box.set_alpha(alpha)
        x = max(L.arena.centerx - w // 2, self.bar_right + 10)   # never cover the spell list
        x = min(x, L.arena.right - w - 10) if L.arena.right - w - 10 > self.bar_right else x
        screen.blit(box, (x, L.arena.y + 14))

    def _draw_status(self, screen, c, L, stroke, hand_visible):
        if not hand_visible:
            text, color = "Show your hand to the camera", (255, 200, 80)
        elif stroke.active:
            text, color = "Drawing... release the pinch to cast", (120, 255, 150)
        else:
            text, color = "Pinch thumb + index finger, draw a spell, release to cast  ·  P to pause", SOFT
        surf = self.f["small"].render(text, True, color)
        a = L.arena
        x = max(a.x + 15 + min(260, a.w // 3) + 20, a.centerx - surf.get_width() // 2)
        screen.blit(surf, (x, a.bottom - 32))

    def _shade(self, screen, a, alpha):
        shade = pygame.Surface(a.size, pygame.SRCALPHA)
        shade.fill((0, 0, 0, alpha))
        screen.blit(shade, a.topleft)

    def _draw_overlays(self, screen, c, L, paused):
        a = L.arena
        if paused and not c.over:
            self._shade(screen, a, 150)
            t = self.f["end_title"].render("PAUSED", True, GOLD)
            screen.blit(t, (a.centerx - t.get_width() // 2, a.centery - 60))
            sub = self.f["end_sub"].render("P to resume  |  ESC for menu", True, WHITE)
            screen.blit(sub, (a.centerx - sub.get_width() // 2, a.centery + 20))
        elif c.waiting_for_hand:
            self._shade(screen, a, 120)
            t = self.f["end_sub"].render("Raise your hand to the camera to begin", True, GOLD)
            screen.blit(t, (a.centerx - t.get_width() // 2, a.centery - t.get_height() // 2))
        elif c.countdown > 0:
            n = self.f["huge"].render(str(math.ceil(c.countdown)), True, GOLD)
            screen.blit(n, (a.centerx - n.get_width() // 2, a.centery - n.get_height() // 2))
        elif c.over:
            self._shade(screen, a, 185)
            win = c.result == "WIN"
            title = self.f["end_title"].render("VICTORY!" if win else "DEFEAT...", True,
                                               GOLD if win else (220, 60, 60))
            y = a.centery - 130
            screen.blit(title, (a.centerx - title.get_width() // 2, y))
            y += title.get_height() + 10
            lines = []
            if win:
                best = load_best_time()
                line = f"Time {c.fight_time:.1f} s"
                if c.new_best:
                    line += "  ·  New best!"
                elif best is not None:
                    line += f"  ·  Best {best:.1f} s"
                lines.append((line, GOLD if c.new_best else WHITE))
            else:
                lines.append((f"Enemy HP left: {c.enemy_hp}", WHITE))
            avg = f"{c.acc_sum / c.casts * 100:.0f}%" if c.casts else "-"
            lines.append((f"Spells cast {c.casts}  ·  Avg accuracy {avg}  ·  Damage taken {c.damage_taken}", SOFT))
            lines.append((f"Interrupts {c.interrupts}  ·  Parries {c.parries}  ·  Shots countered {c.countered}", SOFT))
            lines.append(("Press R to rematch  |  ESC for menu", WHITE))
            for text, col in lines:
                surf = self.f["end_sub"].render(text, True, col)
                screen.blit(surf, (a.centerx - surf.get_width() // 2, y))
                y += surf.get_height() + 12


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_game(screen):
    fonts = {
        "tiny": load_font("MedievalSharp-Book.ttf", 15),
        "small": load_font("MedievalSharp-Book.ttf", 18),
        "big": load_font("MedievalSharp-Book.ttf", 28),
        "dmg": load_font("MedievalSharp-Book.ttf", 24),
        "end_title": load_font("MedievalSharp-Bold.ttf", 64),
        "end_sub": load_font("MedievalSharp-Book.ttf", 24),
        "huge": load_font("MedievalSharp-Bold.ttf", 120),
    }
    clock = pygame.time.Clock()
    recognizer, spell_data = setup_recognizer()
    tracker = HandTracker(camera_index=load_settings()["camera_index"])

    try:
        leave = _wait_for_camera(screen, tracker, clock, fonts)
        if leave:
            return leave

        combat = Combat(spell_data)
        pinch = PinchState()
        stroke = StrokeBuilder()
        renderer = Renderer(fonts, spell_data)
        show_fps = False
        paused = False
        layout = None
        last_id, last_t = None, None
        cam_src = cam_scaled = cam_rgb = None
        lm, hand_visible = None, False
        stroke_start = 0.0
        play_music("battle_theme", volume=0.35)

        while True:
            dt = min(clock.tick(60) / 1000.0, 0.05)   # clamp hitches so nothing teleports
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return "QUIT"
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return "MENU"
                    if event.key == pygame.K_F3:
                        show_fps = not show_fps
                    if event.key == pygame.K_p and not combat.over:
                        paused = not paused
                        stroke.cancel()
                        _pause_music(paused)
                    if event.key == pygame.K_r and combat.over:
                        combat = Combat(spell_data)
                        pinch.reset()
                        stroke.cancel()
                        play_music("battle_theme", volume=0.35)

            if tracker.error:
                return _show_error(tracker.error, clock, fonts)

            screen = pygame.display.get_surface()
            win_w, win_h = screen.get_size()

            # --- newest camera frame (never blocks) ---
            frame = tracker.latest()
            new_frame = frame is not None and frame[0] != last_id
            cam_dt = 1 / 30
            if new_frame:
                fid, cam_rgb, lm, t = frame
                cam_dt = (t - last_t) if last_t else 1 / 30
                last_id, last_t = fid, t
                fh, fw = cam_rgb.shape[:2]
                cam_src = pygame.image.frombuffer(cam_rgb, (fw, fh), "RGB")

            if cam_src is None:
                continue
            arena = cam_rect(win_w, win_h, *cam_src.get_size())
            if layout is None or layout.arena != arena:
                if layout is not None:          # window resized: in-flight things would be misplaced
                    combat.clear_in_flight()
                    stroke.cancel()
                layout = Layout(arena)
                cam_scaled = None
            if new_frame or cam_scaled is None:
                cam_scaled = pygame.transform.scale(cam_src, arena.size)

            # --- hand input (once per camera frame) ---
            if new_frame:
                hand_visible = lm is not None
                aspect = cam_src.get_width() / cam_src.get_height()
                event = pinch.update(lm, aspect)
                if combat.waiting_for_hand and hand_visible:
                    combat.waiting_for_hand = False

                if paused or not combat.fighting:
                    stroke.cancel()
                elif event == "start":
                    stroke.begin()
                    stroke_start = time.monotonic()
                    px, py = pinch_point(lm, arena)
                    combat.pulses.append({"x": px, "y": py, "radius": 12.0, "alpha": 255.0})
                elif event == "release" and stroke.active:
                    points = stroke.finish()
                    combat.cast(points, time.monotonic() - stroke_start, recognizer, spell_data, layout)
                elif event == "cancel" and stroke.active:
                    stroke.cancel()
                    play_sfx("fail", volume=0.5)
                    combat.show_toast("Spell cancelled", "Your hand left the camera while drawing", FAIL)

                if not paused and combat.fighting and stroke.active and pinch.pinching and lm is not None:
                    x, y = pinch_point(lm, arena)
                    kept = stroke.add(x, y, cam_dt, arena.h)
                    if kept:
                        combat.particles.append({
                            "x": kept[0], "y": kept[1],
                            "vx": random.uniform(-120, 120), "vy": random.uniform(-120, 120),
                            "radius": random.randint(3, 6), "color": GOLD, "life": 0.2,
                        })

            if not paused:
                combat.update(dt, layout)
            renderer.draw(screen, combat, layout, cam_scaled, stroke, lm, hand_visible,
                          show_fps, clock.get_fps(), paused)
            pygame.display.flip()
    finally:
        tracker.stop()
        stop_music()