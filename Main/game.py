import json
import math
import random
import re
import time

import pygame
from dollarpy import Point

from fonts import load_font
from hand_input import HandTracker, PinchState, StrokeBuilder, cam_rect, pinch_point
from paths import user_data_path
from settings import load_settings
from sounds import play as play_sfx, play_music, stop_music
from spell_recognizer import setup_recognizer, spell_effect, spell_role_short
from sprites import SpriteBank, flashed, tinted
from ui_kit import UIKit, INK, CREAM, PARCHMENT, starfield

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

# The enemy: the Dark Queen. Lines she says during the fight (one is picked at random).
ENEMY_NAME = "DARK QUEEN"
TAUNT_DUR = 1.1            # seconds she gloats (enemy_taunt) after hitting you
DIALOG_LIFE = 2.6
QUEEN_LINES = {
    "start":   ["Another hedge-wizard? How quaint.", "Kneel, and I may let you keep your hands.",
                "You dare draw sigils in MY court?"],
    "phase2":  ["Enough games.", "You've earned my full attention. Pity.", "Now you've made me angry."],
    "phase3":  ["I will NOT fall to a mortal!", "My crown... you will pay for every scratch!"],
    "stunned": ["Wh- how dare you!", "A cheap trick!", "My spell...!"],
    "parried": ["Clever little mage.", "My own spell? Insolent!"],
    "hit":     ["Too slow.", "Is that all?", "Ha! Feel that?", "Bow your head."],
    "low":     ["Your light is fading.", "Beg, and it ends quickly."],
    "win":     ["Bow before your queen.", "Another trinket for my collection."],
    "lose":    ["This... isn't... over...", "Impossible..."],
}

# Sprite sizes (at a 600 px tall arena) when drawn from assets/sprites/
PLAYER_SPRITE_H = 150
PROJ_SPRITE_SCALE = 2.6    # projectile sprite size (longest side) = radius * this

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


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


# ---------------------------------------------------------------------------
# Layout: everything positioned relative to the letterboxed camera arena
# ---------------------------------------------------------------------------
class Layout:
    def __init__(self, arena, orb=(0.1, 0.06)):
        self.arena = arena
        self.scale = arena.h / 600
        s = self.scale
        self.enemy_rect = pygame.Rect(
            arena.x + int(arena.w * 0.72), arena.y + int(arena.h * 0.3), int(130 * s), int(160 * s)
        )
        self.enemy_center = self.enemy_rect.center
        r = self.enemy_rect
        self.staff_orb = (r.x + int(r.w * orb[0]), r.y + int(r.h * orb[1]))   # enemy spells come from here
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
        self.player_flinch = 0.0
        self.since_cast = 99.0         # seconds since the player's last successful cast (sprite anim)
        self.over_t = 0.0              # seconds since the fight ended (death anims)
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
        self.taunt = 0.0               # enemy gloating after a hit
        self.dialog = None             # {"text", "life", "max"} while the enemy is talking
        self.last_phase = 1
        self.low_hp_said = False

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

    def say(self, key, chance=1.0, interrupt=True):
        """The enemy says a random line from QUEEN_LINES[key]."""
        if random.random() > chance or (self.dialog and not interrupt):
            return
        lines = QUEEN_LINES.get(key)
        if lines:
            self.dialog = {"text": random.choice(lines), "life": DIALOG_LIFE, "max": DIALOG_LIFE}

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
        self.since_cast = 0.0
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
                                  "radius": radius, "effect": effect, "speed": speed, "trail": [],
                                  "name": name})

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
            self.say("stunned")
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
                self.say("start")
            return
        if self.over:
            self.over_t += dt
            return

        self.fight_time += dt
        phase = self.phase()
        if phase != self.last_phase:
            self.last_phase = phase
            self.say(f"phase{phase}")
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
        self.player_flinch = max(0.0, self.player_flinch - dt)
        self.taunt = max(0.0, self.taunt - dt)
        self.since_cast += dt
        if self.dialog:
            self.dialog["life"] -= dt
            if self.dialog["life"] <= 0:
                self.dialog = None
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
                    self.say("parried", 0.6)
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
                    self.player_flinch = FLINCH_DUR
                    play_sfx("hit_player", volume=0.8)
                    self.float_text(f"-{ep['dmg']}", (px, py - 50 * L.scale), (255, 50, 50))
                    self.burst((px, py), ep["color"], n=14, speed=240)
                    self.flash_color, self.flash_alpha = (255, 0, 0), 130
                    self.add_shake(0.3, 9 * L.scale)
                    self.taunt = TAUNT_DUR
                    if self.player_hp <= 0:
                        self._end("LOSE")
                    elif not self.low_hp_said and self.player_hp < PLAYER_MAX_HP * 0.3:
                        self.low_hp_said = True
                        self.say("low")
                    else:
                        self.say("hit", 0.35, interrupt=False)
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
        self.over_t = 0.0
        self.enemy_hp = max(0, self.enemy_hp)
        self.player_hp = max(0, self.player_hp)
        self.burn_time = 0.0
        self.ward = 0.0
        stop_music()
        self.say("lose" if result == "WIN" else "win")
        self.dialog["life"] = self.dialog["max"] = 99.0      # stays up on the end screen
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


_BACKDROP = {}


def _backdrop(size):
    if size not in _BACKDROP:
        _BACKDROP.clear()
        _BACKDROP[size] = starfield(size)
    return _BACKDROP[size]


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
        screen.blit(_backdrop(screen.get_size()), (0, 0))
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
        screen.blit(_backdrop(screen.get_size()), (0, 0))
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
        self.sprites = SpriteBank()
        self.kit = UIKit()
        self.enemy_box = None       # (left, top, right, bottom) of the drawn enemy, for the speech bubble
        self.hud_right = 0          # right edge of the player HUD panel
        self._trail = {"enemy": 1.0, "player": 1.0}   # lagging HP shown as a pale bar segment
        self._glow_cache = {}
        self._tint_cache = {}
        self._size = None
        self.dim = None
        self.flash = None
        self.fx = None
        self.bar_right = 0          # right edge of the spell list, so toasts can avoid it
        self.spells = [(n, e.get("cost", 25), tuple(e.get("color", (200, 200, 200))), spell_role_short(n, e))
                       for n, e in spell_data.items()]

    def enemy_orb(self):
        """Where enemy shots spawn, as fractions of the enemy hitbox (tunable in sprites.json)."""
        orb = self.sprites.opts("enemy").get("orb")
        if self.sprites.has("enemy_idle") and isinstance(orb, (list, tuple)) and len(orb) == 2:
            return tuple(orb)
        return (0.1, 0.06)

    def _ensure_surfaces(self, screen, arena):
        key = (screen.get_size(), arena.size)
        if key == self._size:
            return
        self._size = key
        self.dim = pygame.Surface(arena.size)
        self.dim.fill((22, 8, 38))
        self.dim.set_alpha(110)         # darken + tint the webcam purple so spells and UI stand out
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

    # -- sprite helpers --
    def _char_frame(self, name, t, height, flash=0.0, burn=0.0):
        """A character sprite frame, scaled, with optional hit flash / burn glow."""
        anim = self.sprites.get(name)
        if anim is None:
            return None
        img = anim.frame(t, height)
        if flash > 0:
            img = flashed(img, flash * 0.8)
        if burn > 0:
            img = img.copy()
            img.fill((int(90 * burn), int(30 * burn), 0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        return img

    def _blit_char(self, screen, img, anchor_x, bottom_y):
        screen.blit(img, (int(anchor_x - img.get_width() / 2), int(bottom_y - img.get_height())))

    def _proj_frame(self, names, t, size, color, angle_deg, spin=0.0):
        """Projectile sprite frame: first sprite found in names, tinted/rotated per sprites.json."""
        for name in names:
            anim = self.sprites.get(name, fallback=False)
            if anim is not None:
                break
        else:
            return None
        o = self.sprites.opts(name)
        fw, fh = anim.frames[0].get_size()
        height = max(2, int(size * fh / max(fw, fh)))    # longest side = size
        img = anim.frame(t, height)
        if o.get("tint", name == "proj_player"):
            key = (name, anim.index(t), int(height), color)
            cached = self._tint_cache.get(key)
            if cached is None:
                if len(self._tint_cache) > 300:
                    self._tint_cache.clear()
                cached = self._tint_cache[key] = tinted(img, color)
            img = cached
        rot = spin if spin else (angle_deg if o.get("rotate", True) else 0.0)
        if rot:
            img = pygame.transform.rotate(img, -rot)
        return img

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
        self._update_trails(c)
        self._draw_hud(screen, c, L)
        if not c.over:
            self._draw_dialog(screen, c, L)
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
            pos = (int(shot["x"]), int(shot["y"]))
            names = [f"proj_{_slug(shot['name'])}"] if shot.get("name") else []
            names.append("proj_player")
            ang = 0.0
            if trail:
                tx, ty = trail[0]
                ang = math.degrees(math.atan2(shot["y"] - ty, shot["x"] - tx))
            spin = c.t * 480 if shot.get("effect") == "counter" else 0.0
            img = self._proj_frame(names, c.t, r * PROJ_SPRITE_SCALE, shot["color"], ang, spin)

            for i, (tx, ty) in enumerate(trail):
                k = (i + 1) / (len(trail) + 1)
                pygame.draw.circle(screen, _lerp(BG, shot["color"], 0.35 + 0.5 * k), (int(tx), int(ty)),
                                   max(1, int(r * 0.8 * k)))
            self._blit_glow(screen, pos, r, shot["color"])
            if img is not None:
                screen.blit(img, img.get_rect(center=pos))
            elif shot.get("effect") == "counter":
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

        px, py = L.player_pos
        for ep in c.enemy_shots:
            r = max(4, int(ep["radius"] * s))
            pos = (int(ep["x"]), int(ep["y"]))
            if "vx" in ep:
                ang = math.degrees(math.atan2(ep["vy"], ep["vx"]))
            else:
                ang = math.degrees(math.atan2(py - ep["y"], px - ep["x"]))
            img = self._proj_frame(["proj_enemy"], c.t, r * PROJ_SPRITE_SCALE, ep["color"], ang)
            self._blit_glow(screen, pos, r, ep["color"])
            if img is not None:
                screen.blit(img, img.get_rect(center=pos))
            else:
                pygame.draw.circle(screen, ep["color"], pos, r)
                pygame.draw.circle(screen, (30, 0, 0), pos, max(3, r // 2))

    def _draw_enemy(self, screen, c, L):
        """The enemy wizard: a sprite from assets/sprites if present, else drawn from shapes.
        The rect is the hitbox either way."""
        r, s = L.enemy_rect, L.scale
        h = r.h
        stunned = c.ai_state == "stunned" and c.fighting
        charging = c.ai_state == "telegraphing" and c.fighting
        phase = c.phase()

        top = r.y
        self._enemy_half_w = r.w // 2
        if self.sprites.has("enemy_idle"):
            top = min(top, self._draw_enemy_sprite(screen, c, L, stunned, charging, phase))
        else:
            self._draw_enemy_shapes(screen, c, L, stunned, charging, phase)

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

        # state tag under the enemy (the HP bar lives in the HUD, top-right)
        tag = None
        if charging:
            tag = ("!! CHARGING !!", (255, 200, 0))
        elif stunned:
            tag = ("STUNNED", (255, 240, 120))
        elif c.ward > 0 and c.fighting:
            tag = ("WARDED", WARD_VIOLET)
        if tag:
            t = self.kit.text(self.f["small"], tag[0], tag[1])
            screen.blit(t, (r.centerx - t.get_width() // 2, r.bottom + int(6 * s)))
        self.enemy_box = (min(r.x, r.centerx - self._enemy_half_w), top, r.right, r.bottom)

    def _draw_enemy_sprite(self, screen, c, L, stunned, charging, phase):
        r, s = L.enemy_rect, L.scale
        o = self.sprites.opts("enemy")
        off = o.get("offset", (0, 0))
        height = r.h * float(o.get("height", 1.0))
        bob = 0 if (stunned or c.over) else math.sin(c.t * 2.2) * 4 * s
        cx = r.centerx + off[0] * s
        bottom = r.bottom + bob + off[1] * s
        if c.flinch > 0:        # recoil shake when hit
            cx += random.uniform(-1, 1) * 6 * s * c.flinch / FLINCH_DUR

        if c.result == "WIN":
            name, t = "enemy_dead", c.over_t
        elif c.flinch > 0 and self.sprites.has("enemy_hurt"):
            name, t = "enemy_hurt", FLINCH_DUR - c.flinch
        elif stunned:
            name, t = "enemy_stunned", c.ai_timer
        elif charging:
            name, t = "enemy_charge", c.ai_timer
        elif c.result == "LOSE" or c.taunt > 0:
            name, t = "enemy_taunt", c.t
        elif c.dialog and c.fighting:
            name, t = "enemy_talk", c.t
        elif phase == 3 and c.fighting:
            name, t = "enemy_charge", c.t      # desperate: stays furious in phase III
        else:
            name, t = "enemy_idle", c.t

        # aura behind the sprite: a soft violet haze, flaring in the attack colour while charging
        if charging:
            prog = min(1.0, c.ai_timer / PHASE_PARAMS[phase]["telegraph_dur"])
            self._blit_glow(screen, (cx, bottom - height * 0.45), r.w * (0.45 + 0.2 * prog),
                            PHASE_PARAMS[phase]["color"], 35)
        elif not c.result == "WIN":
            pulse = 0.5 + 0.5 * math.sin(c.t * 1.7)
            self._blit_glow(screen, (cx, bottom - height * 0.5), r.w * (0.42 + 0.04 * pulse),
                            (120, 60, 200) if phase < 3 else (170, 40, 80), 16)

        burn = (0.6 + 0.4 * math.sin(c.t * 12)) if c.burn_time > 0 else 0.0
        img = self._char_frame(name, t, height, flash=c.flinch / FLINCH_DUR, burn=burn)
        self._blit_char(screen, img, cx, bottom)
        self._enemy_half_w = img.get_width() // 2

        # keep the attack readable: a charging orb where shots spawn
        if charging:
            prog = min(1.0, c.ai_timer / PHASE_PARAMS[phase]["telegraph_dur"])
            orb_r = r.w * 0.07 * (1 + 1.3 * prog + 0.15 * math.sin(c.t * 30))
            col = PHASE_PARAMS[phase]["color"]
            self._blit_glow(screen, L.staff_orb, orb_r * 1.3, col, 70)
            pygame.draw.circle(screen, col, L.staff_orb, max(3, int(orb_r)))
        if stunned and not self.sprites.has("enemy_stunned"):
            for k in range(3):   # dizzy stars
                ang = c.t * 5 + k * math.tau / 3
                sx = cx + math.cos(ang) * r.w * 0.3
                sy = bottom - height * 1.02 + math.sin(ang) * r.h * 0.05
                pygame.draw.circle(screen, (255, 230, 90), (int(sx), int(sy)), max(2, int(4 * s)))
        return bottom - img.get_height()

    def _draw_enemy_shapes(self, screen, c, L, stunned, charging, phase):
        """A hooded wizard drawn from shapes (used when there's no enemy sprite)."""
        r, s = L.enemy_rect, L.scale
        w, h = r.w, r.h
        bob = 0 if stunned else math.sin(c.t * 2.2) * 4 * s
        cx, top = r.centerx, r.y + bob

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

    def _draw_player(self, screen, c, L):
        px, py = L.player_pos
        s = L.scale
        if self.sprites.has("player_idle"):
            o = self.sprites.opts("player")
            off = o.get("offset", (0, 0))
            height = PLAYER_SPRITE_H * s * float(o.get("height", 1.0))
            cast = self.sprites.get("player_cast")
            if c.result == "LOSE":
                name, t = "player_dead", c.over_t
            elif c.player_flinch > 0 and self.sprites.has("player_hurt"):
                name, t = "player_hurt", FLINCH_DUR - c.player_flinch
            elif self.sprites.has("player_cast") and c.since_cast < max(0.3, cast.duration):
                name, t = "player_cast", c.since_cast
            else:
                name, t = "player_idle", c.t
            img = self._char_frame(name, t, height, flash=c.player_flinch / FLINCH_DUR)
            # the sprite's middle sits on player_pos, where enemy shots aim
            self._blit_char(screen, img, px + off[0] * s, py + height / 2 + off[1] * s)
        elif self.kit.raw("hat_purple") is not None:
            # your wizard's hat floats where enemy shots aim
            hat = self.kit.image("hat_purple", max(2, self.kit.px_for(s, 3)))
            if c.player_flinch > 0:
                hat = flashed(hat, 0.8 * c.player_flinch / FLINCH_DUR)
            bob = math.sin(c.t * 2.6) * 5 * s
            self._blit_glow(screen, (px, py + 4 * s), hat.get_width() * 0.45, (170, 110, 255), 30)
            screen.blit(hat, hat.get_rect(center=(px, int(py + bob))))
        else:
            # a visible target, so enemy shots fly at something
            r = int(28 * s)
            pygame.draw.circle(screen, (255, 215, 0), (px, py), r, 2)
            pygame.draw.circle(screen, (255, 215, 0), (px, py), max(3, r // 5))
        if c.shield > 0:
            sr = int(PLAYER_HIT_RADIUS * s)
            perfect = c.shield >= SHIELD_DURATION - PARRY_WINDOW
            bubble = pygame.Surface((sr * 2 + 4, sr * 2 + 4), pygame.SRCALPHA)
            alpha = int(70 + 60 * abs(math.sin(c.t * 6)))
            col = (170, 230, 255) if perfect else SHIELD_BLUE
            pygame.draw.circle(bubble, (*col, alpha), (sr + 2, sr + 2), sr)
            pygame.draw.circle(bubble, (*col, 230), (sr + 2, sr + 2), sr, 4 if perfect else 3)
            screen.blit(bubble, (px - sr - 2, py - sr - 2))

    # -- HUD --
    def _px(self, L):
        """Screen pixels per UI-kit pixel (2 at a 600 px tall arena)."""
        return self.kit.px_for(L.scale)

    def _update_trails(self, c):
        """Pale 'recent damage' segment on the HP bars that drains after a hit."""
        dt = min(0.1, max(0.0, c.t - getattr(self, "_trail_t", c.t)))
        self._trail_t = c.t
        for key, pct in (("enemy", c.enemy_hp / ENEMY_MAX_HP), ("player", c.player_hp / PLAYER_MAX_HP)):
            pct = max(0.0, pct)
            tr = self._trail[key]
            self._trail[key] = pct if pct >= tr else max(pct, tr - max(0.08, (tr - pct) * 2.5) * dt)

    def _diamond(self, screen, color, center, r):
        x, y = center
        pts = [(x, y - r), (x + r, y), (x, y + r), (x - r, y)]
        pygame.draw.polygon(screen, color, pts)
        pygame.draw.polygon(screen, INK, pts, max(1, r // 3))

    def _draw_spell_bar(self, screen, c, L):
        """Your spells, what they do, and whether you can afford them right now."""
        a = L.arena
        self.bar_right = a.x
        self.bar_bottom = a.y
        if not self.spells:
            return
        px = self._px(L)
        f = self.f["tiny"]
        row_h = f.get_height() + 6
        widths = [f.size(f"{n}  {cost}  {role}")[0] for n, cost, _, role in self.spells]
        pad = 6 * px
        panel = self.kit.panel(max(widths) + 30 + 2 * pad, row_h * len(self.spells) + 2 * pad, px, alpha=230)
        x0, y0 = a.x + 10, a.y + 10
        screen.blit(panel, (x0, y0))
        self.bar_right = x0 + panel.get_width()
        self.bar_bottom = y0 + panel.get_height()
        y = y0 + (panel.get_height() - row_h * len(self.spells)) // 2 + 2
        for name, cost, color, role in self.spells:
            ok = c.mana >= cost
            self._diamond(screen, color if ok else (90, 80, 105), (x0 + pad + 6, y + row_h // 2 - 2), 6)
            x = x0 + pad + 20
            for text, col in ((name, WHITE if ok else (140, 125, 155)),
                              (str(cost), (150, 195, 255) if ok else (100, 105, 140)),
                              (role, SOFT if ok else (125, 115, 135))):
                if not text:
                    continue
                surf = self.kit.text(f, text, col)
                screen.blit(surf, (x, y))
                x += surf.get_width() + 10
            y += row_h

    def _draw_hud(self, screen, c, L):
        self._draw_player_hud(screen, c, L)
        self._draw_boss_bar(screen, c, L)

    def _draw_player_hud(self, screen, c, L):
        a, s = L.arena, L.scale
        px = self._px(L)
        f = self.f["small"]
        pad = 5 * px
        bar_w = int(min(230 * s, a.w * 0.28))
        hp_h, mana_h = 9 * px, 7 * px
        icon = self.kit.image("hat_purple", px)
        slot = self.kit.panel(icon.get_width() + 8 * px, icon.get_height() + 8 * px, px) if icon else None
        slot_w = slot.get_width() + pad if slot else 0
        lh = f.get_height()
        col_h = lh + hp_h + 6 + lh + mana_h
        panel = self.kit.panel(slot_w + bar_w + 2 * pad, col_h + 2 * pad, px, alpha=230)
        x0, y0 = a.x + 10, a.bottom - 10 - panel.get_height()
        screen.blit(panel, (x0, y0))
        self.hud_right = x0 + panel.get_width()
        if slot:
            sy = y0 + (panel.get_height() - slot.get_height()) // 2
            screen.blit(slot, (x0 + pad, sy))
            ic = icon if c.player_flinch <= 0 else flashed(icon, 0.8 * c.player_flinch / FLINCH_DUR)
            screen.blit(ic, ic.get_rect(center=(x0 + pad + slot.get_width() // 2, sy + slot.get_height() // 2)))

        x = x0 + pad + slot_w
        y = y0 + (panel.get_height() - col_h) // 2
        hp_pct = max(0, c.player_hp) / PLAYER_MAX_HP
        hp_col = (70, 215, 90) if hp_pct > 0.3 else _lerp((220, 50, 50), (255, 120, 90), 0.5 + 0.5 * math.sin(c.t * 8))
        screen.blit(self.kit.text(f, f"YOU  {max(0, c.player_hp)}/{PLAYER_MAX_HP}", WHITE), (x, y))
        if c.shield > 0:
            t = self.kit.text(f, "SHIELD UP", SHIELD_BLUE)
            screen.blit(t, (x + bar_w - t.get_width(), y))
        y += lh
        screen.blit(self.kit.bar(bar_w, hp_h, hp_pct, hp_col, px, self._trail["player"]), (x, y))
        y += hp_h + 6
        screen.blit(self.kit.text(f, f"MANA  {int(c.mana)}/{MANA_MAX}", (170, 205, 255)), (x, y))
        y += lh
        screen.blit(self.kit.bar(bar_w, mana_h, c.mana / MANA_MAX, MANA_BLUE, px), (x, y))

    def _mini_portrait(self, size):
        """The enemy's face, cropped from enemy_idle, for the boss bar."""
        key = ("mini", size)
        if key not in self._tint_cache:
            anim = self.sprites.get("enemy_idle")
            if anim is None:
                return None
            src = anim.frames[0]
            w, h = src.get_size()
            face = src.subsurface((int(w * 0.08), 0, int(w * 0.72), int(h * 0.72)))
            self._tint_cache[key] = pygame.transform.smoothscale(face, (size, size))
        return self._tint_cache[key]

    def _draw_boss_bar(self, screen, c, L):
        """Enemy name, portrait, phase pips and HP, top-right."""
        a, s = L.arena, L.scale
        px = self._px(L)
        f = self.f["small"]
        pad = 5 * px
        bar_w = int(min(250 * s, a.w * 0.3))
        bar_h = 10 * px
        lh = f.get_height()
        col_h = lh + 2 + bar_h
        face = self._mini_portrait(max(16, col_h + 2 * px))
        slot = self.kit.panel(face.get_width() + 6 * px, face.get_height() + 6 * px, px) if face else None
        slot_w = slot.get_width() + pad if slot else 0
        panel = self.kit.panel(bar_w + slot_w + 2 * pad, max(col_h, slot.get_height() if slot else 0) + 2 * pad,
                               px, alpha=230)
        x0, y0 = a.right - 10 - panel.get_width(), a.y + 10
        screen.blit(panel, (x0, y0))
        self.boss_left = x0
        self.boss_bottom = y0 + panel.get_height()

        x = x0 + pad
        if slot:
            sy = y0 + (panel.get_height() - slot.get_height()) // 2
            screen.blit(slot, (x, sy))
            img = face
            if c.flinch > 0:
                img = flashed(face, 0.8 * c.flinch / FLINCH_DUR)
            screen.blit(img, img.get_rect(center=(x + slot.get_width() // 2, sy + slot.get_height() // 2)))
            x += slot_w

        phase = c.phase()
        y = y0 + (panel.get_height() - col_h) // 2
        screen.blit(self.kit.text(f, ENEMY_NAME, CREAM), (x, y))
        pip_r = max(3, int(5 * s))
        for k in range(3):     # phase pips: filled up to the current phase
            col = [(180, 255, 180), (255, 200, 60), (255, 80, 80)][k] if k < phase else (70, 50, 85)
            self._diamond(screen, col, (x + bar_w - pip_r - (2 - k) * (pip_r * 3), y + lh // 2), pip_r)
        y += lh + 2
        pct = max(0, c.enemy_hp) / ENEMY_MAX_HP
        col = [(0, 0, 0), (200, 60, 170), (230, 90, 60), (255, 60, 60)][phase]
        screen.blit(self.kit.bar(bar_w, bar_h, pct, col, px, self._trail["enemy"]), (x, y))

    def _draw_dialog(self, screen, c, L):
        """The enemy's speech bubble, beside her face."""
        d = c.dialog
        if not d or not self.enemy_box or c.waiting_for_hand:
            return
        px = self._px(L)
        f = self.f["small"]
        lines = self.kit.wrap(f, d["text"], int(220 * L.scale))
        lh = f.get_linesize()
        tw = max(f.size(t)[0] for t in lines)
        pad = 5 * px
        panel = self.kit.panel(tw + 2 * pad + 2, lh * len(lines) + 2 * pad, px)
        tail = 5 * px
        bubble = pygame.Surface((panel.get_width() + tail, panel.get_height()), pygame.SRCALPHA)
        bubble.blit(panel, (0, 0))
        ty = panel.get_height() // 2
        pts = [(panel.get_width() - px * 2, ty - tail), (panel.get_width() + tail - px, ty),
               (panel.get_width() - px * 2, ty + tail)]
        pygame.draw.polygon(bubble, (99, 39, 121), pts)
        pygame.draw.lines(bubble, PARCHMENT, False, pts, px)
        y = (panel.get_height() - lh * len(lines)) // 2
        for t in lines:
            surf = self.kit.text(f, t, WHITE)
            bubble.blit(surf, ((panel.get_width() - surf.get_width()) // 2, y))
            y += lh
        age = d["max"] - d["life"]
        bubble.set_alpha(int(255 * min(1.0, age / 0.12, d["life"] / 0.3)))

        left, top, right, bottom = self.enemy_box
        x = int(left + (right - left) * 0.12) - bubble.get_width()
        y = int(top + (bottom - top) * 0.22) - bubble.get_height() // 2
        x = max(x, L.arena.x + 10)
        y = max(y, getattr(self, "boss_bottom", L.arena.y) + 6)
        screen.blit(bubble, (x, y))

    def _draw_toast(self, screen, c, L):
        t = c.toast
        if not t:
            return
        px = self._px(L)
        alpha = int(255 * min(1.0, t["life"] / 0.3))
        title = self.kit.text(self.f["big"], t["title"], t["color"])
        sub = self.kit.text(self.f["small"], t["sub"], WHITE) if t["sub"] else None
        pad = 6 * px
        w = max(title.get_width(), sub.get_width() if sub else 0) + 2 * pad
        h = title.get_height() + (sub.get_height() + 2 if sub else 0) + 2 * pad
        panel = self.kit.panel(w, h, px)
        box = pygame.Surface(panel.get_size(), pygame.SRCALPHA)
        box.blit(panel, (0, 0))
        y = (panel.get_height() - (h - 2 * pad)) // 2
        box.blit(title, (box.get_width() // 2 - title.get_width() // 2, y))
        if sub:
            box.blit(sub, (box.get_width() // 2 - sub.get_width() // 2, y + title.get_height() + 2))
        box.set_alpha(alpha)
        a = L.arena
        lo = self.bar_right + 10                      # never cover the spell list...
        hi = getattr(self, "boss_left", a.right) - 10  # ...or the enemy's HP
        bw = box.get_width()
        if hi - lo >= bw:
            x, y = min(max(a.centerx - bw // 2, lo), hi - bw), a.y + 14
        else:                                          # no room up top: bottom middle, above the hint line
            lo = self.hud_right + 10
            x = min(max(a.centerx - bw // 2, lo), a.right - 10 - bw)
            y = a.bottom - 44 - box.get_height()
        screen.blit(box, (x, y))

    def _draw_status(self, screen, c, L, stroke, hand_visible):
        if not hand_visible:
            text, color = "Show your hand to the camera", (255, 200, 80)
        elif stroke.active:
            text, color = "Drawing... release the pinch to cast", (120, 255, 150)
        else:
            text, color = "Pinch thumb + index finger, draw a spell, release to cast  ·  P to pause", SOFT
        a = L.arena
        surf = self.kit.text(self.f["small"], text, color)
        if self.hud_right + 16 + surf.get_width() > a.right - 8:     # narrow window: smaller text
            surf = self.kit.text(self.f["tiny"], text, color)
        if self.hud_right + 16 + surf.get_width() > a.right - 8:     # still too wide: drop the pause hint
            surf = self.kit.text(self.f["tiny"], text.split("  ·  ")[0], color)
        x = max(self.hud_right + 16, a.centerx - surf.get_width() // 2)
        screen.blit(surf, (x, a.bottom - 32))

    def _shade(self, screen, a, alpha):
        shade = pygame.Surface(a.size, pygame.SRCALPHA)
        shade.fill((12, 4, 22, alpha))
        screen.blit(shade, a.topleft)

    def _hints(self, screen, items, cx, y, px):
        """A row of key hints: [(ui button image, key, label), ...] centred on cx."""
        f = self.f["small"]
        parts = []
        for icon, key, label in items:
            img = self.kit.image(icon, px)
            k = self.kit.text(f, key, GOLD)
            t = self.kit.text(f, label, WHITE)
            parts.append((img, k, t))
        gap = 8
        widths = [(i.get_width() + gap if i else 0) + k.get_width() + 6 + t.get_width() for i, k, t in parts]
        total = sum(widths) + 28 * (len(parts) - 1)
        x = cx - total // 2
        for (img, k, t), w in zip(parts, widths):
            h = max(img.get_height() if img else 0, k.get_height())
            if img:
                screen.blit(img, (x, y + (h - img.get_height()) // 2))
                x += img.get_width() + gap
            screen.blit(k, (x, y + (h - k.get_height()) // 2))
            x += k.get_width() + 6
            screen.blit(t, (x, y + (h - t.get_height()) // 2))
            x += t.get_width() + 28

    def _draw_overlays(self, screen, c, L, paused):
        a, s = L.arena, L.scale
        px = self._px(L)
        if paused and not c.over:
            self._shade(screen, a, 150)
            panel = self.kit.panel(int(380 * s), int(210 * s), px, gem=True)
            r = panel.get_rect(center=a.center)
            screen.blit(panel, r)
            t = self.kit.text(self.f["end_title"], "PAUSED", GOLD)
            screen.blit(t, (r.centerx - t.get_width() // 2, r.y + int(55 * s)))
            self._hints(screen, [("btn_pause", "P", "resume"), ("btn_x", "ESC", "menu")],
                        r.centerx, r.bottom - int(60 * s), px)
        elif c.waiting_for_hand:
            self._shade(screen, a, 120)
            t = self.kit.text(self.f["end_sub"], "Raise your hand to the camera to begin", GOLD)
            hat = self.kit.image("hat_purple", px)
            hat_w = hat.get_width() + 12 if hat else 0
            panel = self.kit.panel(t.get_width() + hat_w + 24 * px, max(t.get_height(), hat.get_height() if hat else 0) + 12 * px, px)
            r = panel.get_rect(center=a.center)
            screen.blit(panel, r)
            x = r.centerx - (t.get_width() + hat_w) // 2
            if hat:
                screen.blit(hat, (x, r.centery - hat.get_height() // 2))
            screen.blit(t, (x + hat_w, r.centery - t.get_height() // 2))
        elif c.countdown > 0:
            n = self.kit.text(self.f["huge"], str(math.ceil(c.countdown)), GOLD, offset=max(3, px * 2))
            screen.blit(n, (a.centerx - n.get_width() // 2, a.centery - n.get_height() // 2))
        elif c.over:
            self._draw_end_screen(screen, c, L, px)

    def _draw_end_screen(self, screen, c, L, px):
        a, s = L.arena, L.scale
        self._shade(screen, a, 185)
        win = c.result == "WIN"
        pw, ph = int(min(a.w - 30, 640 * s)), int(min(a.h - 30, 420 * s))
        panel = self.kit.panel(pw, ph, px, gem=True)
        r = panel.get_rect(center=a.center)
        screen.blit(panel, r)

        title = self.kit.text(self.f["end_title"], "VICTORY!" if win else "DEFEAT...", GOLD if win else (225, 70, 70),
                              offset=max(2, px))
        ty = r.y + int(38 * s)
        screen.blit(title, (r.centerx - title.get_width() // 2, ty))
        skull = None if win else self.kit.image("skull", px)
        if skull:
            for sx in (r.centerx - title.get_width() // 2 - skull.get_width() - 14, r.centerx + title.get_width() // 2 + 14):
                screen.blit(skull, (sx, ty + (title.get_height() - skull.get_height()) // 2))

        # the queen's reaction, framed, with her last words
        y = ty + title.get_height() + int(10 * s)
        x = r.x + int(28 * s)
        anim = self.sprites.get("enemy_dead" if win else "enemy_taunt")
        if anim is not None:
            k = 2 if s >= 1.3 else 1
            img = pygame.transform.scale(anim.frames[0], (anim.frames[0].get_width() * k, anim.frames[0].get_height() * k))
            frame = self.kit.panel(img.get_width() + 8 * px, img.get_height() + 8 * px, px)
            screen.blit(frame, (x, y))
            screen.blit(img, img.get_rect(center=(x + frame.get_width() // 2, y + frame.get_height() // 2)))
            x += frame.get_width() + int(22 * s)
        f = self.f["small"]
        lines = []
        if c.dialog:
            for t in self.kit.wrap(self.f["end_sub"], f'"{c.dialog["text"]}"', r.right - x - int(28 * s)):
                lines.append((self.f["end_sub"], t, CREAM))
            lines.append((f, f"- {ENEMY_NAME.title()}", (190, 160, 210)))
            lines.append((f, "", WHITE))
        if win:
            best = load_best_time()
            line = f"Time {c.fight_time:.1f} s"
            if c.new_best:
                line += "  ·  New best!"
            elif best is not None:
                line += f"  ·  Best {best:.1f} s"
            lines.append((f, line, GOLD if c.new_best else WHITE))
        else:
            lines.append((f, f"{ENEMY_NAME.title()} HP left: {c.enemy_hp}", WHITE))
        avg = f"{c.acc_sum / c.casts * 100:.0f}%" if c.casts else "-"
        lines.append((f, f"Spells cast {c.casts}  ·  Avg accuracy {avg}", SOFT))
        lines.append((f, f"Damage taken {c.damage_taken}", SOFT))
        lines.append((f, f"Interrupts {c.interrupts}  ·  Parries {c.parries}  ·  Countered {c.countered}", SOFT))
        for font, text, col in lines:
            if text:
                screen.blit(self.kit.text(font, text, col), (x, y))
            y += font.get_linesize() + 2

        self._hints(screen, [("btn_play", "R", "rematch"), ("btn_x", "ESC", "menu")],
                    r.centerx, r.bottom - int(52 * s), px)


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
                layout = Layout(arena, renderer.enemy_orb())
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
