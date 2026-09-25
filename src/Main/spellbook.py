import pygame

from sounds import play as play_sfx
from spell_recognizer import DEFENSE_ROLE, EFFECTS, load_spell_data, new_spell_entry, save_spell_data, spell_effect
from spell_recorder import (RECOMMENDED_SAMPLES, draw_button, draw_gesture, fit_text, font,
                            run_recorder, ui_scale)
from ui_kit import UIKit, starfield, CREAM, INK, PLUM

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GOLD = (255, 215, 0)
SOFT = (200, 190, 220)
DIM = (150, 140, 175)
OK = (110, 235, 140)
WARN = (255, 190, 90)
FAIL = (235, 120, 110)
ATTACK_COL = (255, 160, 80)
DEFENSE_COL = (120, 195, 255)

NAME_MAX = 18
LIMITS = {"damage": (0, 999, 10), "cost": (0, 100, 5)}
EFFECT_ORDER = ["none", "burn", "interrupt", "counter"]
EFFECT_LABELS = {"none": "NONE", "burn": "BURN", "interrupt": "STUN", "counter": "COUNTER"}
COLOR_PRESETS = [
    [255, 60, 60], [255, 150, 40], [255, 255, 0], [110, 235, 90],
    [0, 200, 255], [80, 160, 255], [180, 90, 255], [255, 110, 200],
]
ROW_KEYS = ("name", "type", "effect", "damage", "cost", "color")
ROW_LABELS = {"name": "Name", "type": "Type", "effect": "Effect", "damage": "Damage",
              "cost": "Mana Cost", "color": "Color"}

_bg = {}


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
        s = self.s = ui_scale(H)
        self.px = px = 3 if H >= 900 else 2
        self.f_title = font(True, int(34 * s))
        self.f_label = font(True, int(17 * s))
        self.f_value = font(False, int(16 * s))
        self.f_small = font(False, int(13 * s))
        self.f_button = font(True, int(16 * s))

        margin = int(20 * s)
        self.title_y = int(10 * s)
        top = self.title_y + self.f_title.get_height() + int(8 * s)
        bar_h = int(40 * s)
        bar_y = H - margin - bar_h
        bottom = bar_y - int(12 * s)
        pad = int(12 * s) + 3 * px

        # --- panels ---
        list_w = int(min(250 * s, W * 0.33))
        self.list_panel = pygame.Rect(margin, top, list_w, bottom - top)
        ex0 = self.list_panel.right + int(14 * s)
        self.edit_panel = pygame.Rect(ex0, top, W - margin - ex0, bottom - top)

        # --- spell list ---
        self.item_h = int(46 * s)
        lp = self.list_panel
        self.btn_new = pygame.Rect(lp.x + pad, lp.bottom - pad - int(34 * s), lp.w - 2 * pad, int(34 * s))
        self.list_area = pygame.Rect(lp.x + pad, lp.y + pad, lp.w - 2 * pad, self.btn_new.y - int(8 * s) - lp.y - pad)

        # --- editor rows ---
        ep = self.edit_panel
        ex, ew = ep.x + pad, ep.w - 2 * pad
        self.label_x = ex
        self.ctrl_x = ex + int(105 * s)
        self.ctrl_w = max(int(160 * s), min(ew - int(105 * s), int(300 * s)))
        row_h = int(36 * s)
        fh = int(28 * s)
        aw = int(30 * s)
        y = ep.y + pad
        self.rows = {}
        self.ctrl = {}
        for key in ROW_KEYS:
            row = pygame.Rect(ex, y, ew, row_h)
            self.rows[key] = row
            cy = row.centery - fh // 2
            if key == "name":
                self.ctrl["name"] = pygame.Rect(self.ctrl_x, cy, self.ctrl_w, fh)
            elif key in ("type", "effect"):
                self.ctrl[key + "_prev"] = pygame.Rect(self.ctrl_x, cy, aw, fh)
                self.ctrl[key + "_next"] = pygame.Rect(self.ctrl_x + self.ctrl_w - aw, cy, aw, fh)
                self.ctrl[key] = pygame.Rect(self.ctrl_x + aw, cy, self.ctrl_w - 2 * aw, fh)
            elif key in ("damage", "cost"):
                self.ctrl[key + "_minus"] = pygame.Rect(self.ctrl_x, cy, aw, fh)
                self.ctrl[key] = pygame.Rect(self.ctrl_x + aw + int(6 * s), cy, int(80 * s), fh)
                self.ctrl[key + "_plus"] = pygame.Rect(self.ctrl[key].right + int(6 * s), cy, aw, fh)
            y += row_h
            if key == "effect":
                self.hint_y = y
                y += int(18 * s)

        sw, gap = int(24 * s), int(7 * s)
        crow = self.rows["color"]
        self.swatches = [pygame.Rect(self.ctrl_x + i * (sw + gap), crow.centery - sw // 2, sw, sw)
                         for i in range(len(COLOR_PRESETS))]

        # --- samples ---
        self.samples_label_y = y + int(10 * s)
        st = self.samples_label_y + self.f_label.get_height() + int(6 * s)
        self.samples_area = pygame.Rect(ex, st, ew, ep.bottom - pad - st)
        self.thumb = int(62 * s)
        self.thumb_gap = int(8 * s)

        # --- bottom bar ---
        self.btn_back = pygame.Rect(margin, bar_y, int(150 * s), bar_h)
        self.btn_record = pygame.Rect(W - margin - int(200 * s), bar_y, int(200 * s), bar_h)
        self.btn_delete = pygame.Rect(self.btn_record.x - int(12 * s) - int(170 * s), bar_y, int(170 * s), bar_h)
        self.status_y = bar_y + bar_h // 2

    def thumb_rects(self, n):
        a, t, g = self.samples_area, self.thumb, self.thumb_gap
        cols = max(1, (a.w + g) // (t + g))
        rows = max(1, (a.h + g) // (t + g))
        cap = cols * rows
        shown = n if n <= cap else cap - 1
        rects = [pygame.Rect(a.x + (i % cols) * (t + g), a.y + (i // cols) * (t + g), t, t) for i in range(shown)]
        more = None
        if n > cap:
            i = cap - 1
            more = pygame.Rect(a.x + (i % cols) * (t + g), a.y + (i // cols) * (t + g), t, t)
        return rects, more

    def delete_x(self, thumb):
        d = int(16 * self.s)
        return pygame.Rect(thumb.right - d - 2, thumb.y + 2, d, d)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _field(screen, rect, text, f, px, active, color=CREAM, caret=False):
    pygame.draw.rect(screen, PLUM, rect)
    pygame.draw.rect(screen, GOLD if active else INK, rect, px)
    surf = fit_text(f, text, color, rect.w - 12)
    ty = rect.centery - surf.get_height() // 2
    screen.blit(surf, (rect.x + 8, ty))
    if caret and (pygame.time.get_ticks() // 500) % 2 == 0:
        cx = rect.x + 8 + surf.get_width() + 2
        pygame.draw.line(screen, GOLD, (cx, ty + 2), (cx, ty + surf.get_height() - 2), 2)


def _draw_list(screen, kit, L, names, spells, sel, scroll, mouse):
    screen.blit(kit.panel(L.list_panel.w, L.list_panel.h, L.px), L.list_panel.topleft)
    a = L.list_area
    clip = screen.get_clip()
    screen.set_clip(a)
    for i, name in enumerate(names):
        r = pygame.Rect(a.x, a.y + i * L.item_h - scroll, a.w, L.item_h - int(4 * L.s))
        if r.bottom < a.y or r.y > a.bottom:
            continue
        entry = spells[name]
        if i == sel or r.collidepoint(mouse):
            hl = pygame.Surface(r.size, pygame.SRCALPHA)
            hl.fill((255, 255, 255, 34 if i == sel else 16))
            screen.blit(hl, r.topleft)
        if i == sel:
            pygame.draw.rect(screen, GOLD, (r.x, r.y + 3, max(2, L.px), r.h - 6))
        dot = (r.x + int(16 * L.s), r.centery)
        pygame.draw.circle(screen, INK, dot, int(7 * L.s) + 1)
        pygame.draw.circle(screen, tuple(entry["color"]), dot, int(7 * L.s))
        tx = r.x + int(30 * L.s)
        screen.blit(fit_text(L.f_label, name, CREAM if i == sel else SOFT, r.right - tx - 6),
                    (tx, r.y + int(3 * L.s)))
        n = len(entry["gestures"])
        kind = "DEFENSE" if entry["type"] == "defense" else "ATTACK"
        sub_col = DIM if n >= RECOMMENDED_SAMPLES else WARN
        sub = L.f_small.render(f"{kind}  ·  {n} sample{'s' if n != 1 else ''}", True, sub_col)
        screen.blit(sub, (tx, r.y + int(24 * L.s)))
    screen.set_clip(clip)
    if not names:
        msg = L.f_value.render("No spells yet", True, DIM)
        screen.blit(msg, (a.centerx - msg.get_width() // 2, a.y + int(10 * L.s)))
    draw_button(screen, kit, L.btn_new, "+ NEW SPELL", L.f_button, L.px, mouse)


def _draw_editor(screen, kit, L, name, entry, edit, mouse):
    screen.blit(kit.panel(L.edit_panel.w, L.edit_panel.h, L.px), L.edit_panel.topleft)
    if entry is None:
        msg = L.f_value.render("Click + NEW SPELL to create your first spell.", True, DIM)
        screen.blit(msg, (L.edit_panel.centerx - msg.get_width() // 2, L.edit_panel.centery))
        return

    defense = entry["type"] == "defense"
    eff = spell_effect(name, entry)
    for key in ROW_KEYS:
        row = L.rows[key]
        disabled = defense and key in ("effect", "damage")
        lab = kit.text(L.f_label, ROW_LABELS[key], DIM if disabled else SOFT)
        screen.blit(lab, (L.label_x, row.centery - lab.get_height() // 2))
        editing = edit is not None and edit["field"] == key

        if key == "name":
            text = edit["text"] if editing else name
            _field(screen, L.ctrl[key], text, L.f_value, L.px, editing, caret=editing)
        elif key in ("type", "effect"):
            if key == "type":
                val, col = ("DEFENSE", DEFENSE_COL) if defense else ("ATTACK", ATTACK_COL)
            else:
                val, col = ("PARRY", DIM) if defense else (EFFECT_LABELS[eff], GOLD)
            if not disabled:
                draw_button(screen, kit, L.ctrl[key + "_prev"], "<", L.f_button, L.px, mouse)
                draw_button(screen, kit, L.ctrl[key + "_next"], ">", L.f_button, L.px, mouse)
            v = kit.text(L.f_label, val, col)
            c = L.ctrl[key]
            screen.blit(v, (c.centerx - v.get_width() // 2, c.centery - v.get_height() // 2))
        elif key in ("damage", "cost"):
            if disabled:
                v = L.f_value.render("-  (defense spells don't deal damage)", True, DIM)
                screen.blit(v, (L.ctrl_x, row.centery - v.get_height() // 2))
                continue
            draw_button(screen, kit, L.ctrl[key + "_minus"], "-", L.f_button, L.px, mouse)
            draw_button(screen, kit, L.ctrl[key + "_plus"], "+", L.f_button, L.px, mouse)
            text = edit["text"] if editing else str(entry[key])
            _field(screen, L.ctrl[key], text, L.f_value, L.px, editing, caret=editing)
        else:
            for rect, preset in zip(L.swatches, COLOR_PRESETS):
                pygame.draw.rect(screen, INK, rect.inflate(4, 4))
                pygame.draw.rect(screen, tuple(preset), rect)
                if list(entry["color"]) == preset:
                    pygame.draw.rect(screen, GOLD, rect.inflate(8, 8), L.px)

    hint = DEFENSE_ROLE if defense else EFFECTS[eff]
    h = fit_text(L.f_small, hint, DIM, L.edit_panel.right - L.ctrl_x - 20)
    screen.blit(h, (L.ctrl_x, L.hint_y))

    # --- samples ---
    n = len(entry["gestures"])
    lab = kit.text(L.f_label, f"Gesture Samples ({n})", SOFT)
    screen.blit(lab, (L.label_x, L.samples_label_y))
    if n < RECOMMENDED_SAMPLES:
        warn = L.f_small.render(f"record {RECOMMENDED_SAMPLES}+ for reliable casting", True, WARN)
        screen.blit(warn, (L.label_x + lab.get_width() + int(10 * L.s),
                           L.samples_label_y + lab.get_height() - warn.get_height()))
    if n == 0:
        screen.blit(fit_text(L.f_value, "No samples yet. Press RECORD SAMPLES and draw it a few times.",
                             DIM, L.samples_area.w), (L.samples_area.x, L.samples_area.y + int(6 * L.s)))
        return
    rects, more = L.thumb_rects(n)
    for i, r in enumerate(rects):
        pygame.draw.rect(screen, PLUM, r)
        pygame.draw.rect(screen, INK, r, L.px)
        draw_gesture(screen, entry["gestures"][i], r, tuple(entry["color"]), max(2, L.px), pad=int(9 * L.s),
                     arrow=False)
        num = L.f_small.render(str(i + 1), True, DIM)
        screen.blit(num, (r.x + 4, r.bottom - num.get_height() - 2))
        if r.collidepoint(mouse):
            x = L.delete_x(r)
            pygame.draw.rect(screen, FAIL, x)
            pygame.draw.rect(screen, INK, x, 1)
            m = x.inflate(-x.w // 2, -x.h // 2)
            pygame.draw.line(screen, INK, m.topleft, m.bottomright, 2)
            pygame.draw.line(screen, INK, m.topright, m.bottomleft, 2)
    if more:
        pygame.draw.rect(screen, PLUM, more)
        pygame.draw.rect(screen, INK, more, L.px)
        t = L.f_label.render(f"+{n - len(rects)}", True, SOFT)
        screen.blit(t, (more.centerx - t.get_width() // 2, more.centery - t.get_height() // 2))


def _draw(screen, kit, L, names, spells, sel, scroll, edit, confirm_delete, status, mouse):
    W, H = screen.get_size()
    screen.blit(_background((W, H)), (0, 0))
    title = kit.text(L.f_title, "SPELLBOOK", GOLD, offset=3)
    screen.blit(title, (W // 2 - title.get_width() // 2, L.title_y))

    name = names[sel] if names else None
    _draw_list(screen, kit, L, names, spells, sel, scroll, mouse)
    _draw_editor(screen, kit, L, name, spells[name] if name else None, edit, mouse)

    # --- bottom bar ---
    draw_button(screen, kit, L.btn_back, "< BACK (ESC)", L.f_button, L.px, mouse)
    draw_button(screen, kit, L.btn_delete, "CONFIRM?" if confirm_delete else "DELETE SPELL", L.f_button, L.px,
                mouse, enabled=bool(name), color=(150, 30, 30) if confirm_delete else (48, 20, 60))
    draw_button(screen, kit, L.btn_record, "RECORD SAMPLES", L.f_button, L.px, mouse, enabled=bool(name),
                color=(20, 110, 60))
    if status:
        st = fit_text(L.f_value, status[0], status[1], L.btn_delete.x - L.btn_back.right - 20)
        screen.blit(st, ((L.btn_back.right + L.btn_delete.x) // 2 - st.get_width() // 2,
                         L.status_y - st.get_height() // 2))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_spellbook(screen):
    kit = UIKit()
    clock = pygame.time.Clock()
    spells = load_spell_data()
    sel = 0
    scroll = 0
    edit = None
    confirm_until = 0
    status = None

    # --- data helpers ---
    def names():
        return list(spells.keys())

    def current():
        n = names()
        return n[sel] if n else None

    def say(text, color=SOFT, secs=2.5):
        nonlocal status
        status = (text, color, pygame.time.get_ticks() + int(secs * 1000))

    def persist():
        try:
            save_spell_data(spells)
        except OSError as e:
            say(f"Couldn't save spells.json: {e}", FAIL, 4)

    def rename(old, new):
        nonlocal spells
        entry = spells[old]
        if entry["type"] == "attack" and "effect" not in entry:
            entry["effect"] = spell_effect(old, entry)
        spells = {(new if k == old else k): v for k, v in spells.items()}

    def new_spell():
        nonlocal sel, edit
        base, i = "New Spell", 1
        name = base
        taken = {k.lower() for k in spells}
        while name.lower() in taken:
            i += 1
            name = f"{base} {i}"
        entry = new_spell_entry()
        entry["effect"] = "none"
        spells[name] = entry
        sel = len(spells) - 1
        persist()
        begin_edit("name")

    # --- text editing ---
    def begin_edit(field):
        nonlocal edit
        name = current()
        value = name if field == "name" else str(spells[name][field])
        edit = {"field": field, "text": value, "fresh": True}
        pygame.key.start_text_input()

    def end_edit(apply=True):
        nonlocal edit
        if edit is None:
            return
        field, text = edit["field"], edit["text"].strip()
        edit = None
        pygame.key.stop_text_input()
        if not apply:
            return
        name = current()
        if field == "name":
            if not text:
                say("Name can't be empty.", FAIL)
            elif text != name and text.lower() in {k.lower() for k in spells if k != name}:
                say(f"There's already a spell called {text}.", FAIL)
            elif text != name:
                rename(name, text)
                persist()
                say(f"Renamed to {text}.", OK)
        else:
            lo, hi, _ = LIMITS[field]
            if text.isdigit():
                spells[name][field] = max(lo, min(hi, int(text)))
                persist()

    def type_char(ch):
        if edit["fresh"]:
            edit["text"] = ""
            edit["fresh"] = False
        if edit["field"] == "name":
            if len(edit["text"]) < NAME_MAX and ch.isprintable():
                edit["text"] += ch
        elif ch.isdigit() and len(edit["text"]) < 3:
            edit["text"] += ch

    # --- value changes ---
    def step(field, direction):
        entry = spells[current()]
        lo, hi, inc = LIMITS[field]
        entry[field] = max(lo, min(hi, entry[field] + inc * direction))
        persist()

    def cycle(field, direction):
        name = current()
        entry = spells[name]
        if field == "type":
            entry["type"] = "defense" if entry["type"] == "attack" else "attack"
            if entry["type"] == "attack" and "effect" not in entry:
                entry["effect"] = "none"
        else:
            cur = spell_effect(name, entry)
            i = EFFECT_ORDER.index(cur) if cur in EFFECT_ORDER else 0
            entry["effect"] = EFFECT_ORDER[(i + direction) % len(EFFECT_ORDER)]
        persist()

    def delete_spell():
        nonlocal sel
        name = current()
        del spells[name]
        sel = max(0, min(sel, len(spells) - 1))
        persist()
        say(f"Deleted {name}.", WARN)
        play_sfx("fail", volume=0.5)

    def click(pos, L):
        nonlocal sel, confirm_until, scroll
        name = current()
        if L.btn_back.collidepoint(pos):
            return "MENU"
        if L.btn_new.collidepoint(pos):
            new_spell()
            return None
        for i in range(len(spells)):
            r = pygame.Rect(L.list_area.x, L.list_area.y + i * L.item_h - scroll, L.list_area.w, L.item_h)
            if L.list_area.collidepoint(pos) and r.collidepoint(pos):
                sel = i
                confirm_until = 0
                return None
        if name is None:
            return None
        entry = spells[name]
        defense = entry["type"] == "defense"

        if L.btn_record.collidepoint(pos):
            return "RECORD"
        if L.btn_delete.collidepoint(pos):
            if pygame.time.get_ticks() < confirm_until:
                confirm_until = 0
                delete_spell()
            else:
                confirm_until = pygame.time.get_ticks() + 2500
            return None

        c = L.ctrl
        if c["name"].collidepoint(pos):
            begin_edit("name")
        elif c["type_prev"].collidepoint(pos) or c["type_next"].collidepoint(pos):
            cycle("type", 1)
        elif not defense and c["effect_prev"].collidepoint(pos):
            cycle("effect", -1)
        elif not defense and c["effect_next"].collidepoint(pos):
            cycle("effect", 1)
        for field in ("damage", "cost"):
            if field == "damage" and defense:
                continue
            if c[field].collidepoint(pos):
                begin_edit(field)
            elif c[field + "_minus"].collidepoint(pos):
                step(field, -1)
            elif c[field + "_plus"].collidepoint(pos):
                step(field, 1)
        for rect, preset in zip(L.swatches, COLOR_PRESETS):
            if rect.collidepoint(pos):
                entry["color"] = list(preset)
                persist()
        rects, _ = L.thumb_rects(len(entry["gestures"]))
        for i, r in enumerate(rects):
            if L.delete_x(r).collidepoint(pos):
                entry["gestures"].pop(i)
                persist()
                say(f"Removed sample {i + 1}.", WARN)
                play_sfx("fail", volume=0.5)
                break
        return None

    while True:
        clock.tick(60)
        screen = pygame.display.get_surface()
        W, H = screen.get_size()
        L = _Layout(W, H)
        mouse = pygame.mouse.get_pos()
        max_scroll = max(0, len(spells) * L.item_h - L.list_area.h)

        # --- events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                end_edit()
                return "QUIT"
            if event.type == pygame.TEXTINPUT and edit is not None:
                for ch in event.text:
                    type_char(ch)
            elif event.type == pygame.KEYDOWN and edit is not None:
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
                    end_edit()
                elif event.key == pygame.K_ESCAPE:
                    end_edit(apply=False)
                elif event.key == pygame.K_BACKSPACE:
                    edit["text"] = "" if edit["fresh"] else edit["text"][:-1]
                    edit["fresh"] = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return "MENU"
                if spells and event.key in (pygame.K_UP, pygame.K_DOWN):
                    sel = (sel + (1 if event.key == pygame.K_DOWN else -1)) % len(spells)
                    confirm_until = 0
                    top = sel * L.item_h
                    scroll = min(max(scroll, top + L.item_h - L.list_area.h), top)
            elif event.type == pygame.MOUSEWHEEL and L.list_area.collidepoint(mouse):
                scroll = max(0, min(max_scroll, scroll - event.y * L.item_h // 2))
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if edit is not None:
                    field_rect = L.ctrl.get(edit["field"])
                    if field_rect and field_rect.collidepoint(event.pos):
                        continue
                    end_edit()
                result = click(event.pos, L)
                if result == "MENU":
                    return "MENU"
                if result == "RECORD":
                    if run_recorder(screen, spells, current()) == "QUIT":
                        return "QUIT"
                    pygame.event.clear()

        scroll = max(0, min(max_scroll, scroll))
        if status and pygame.time.get_ticks() > status[2]:
            status = None
        confirm = pygame.time.get_ticks() < confirm_until

        # --- render ---
        screen = pygame.display.get_surface()
        L = _Layout(*screen.get_size())
        _draw(screen, kit, L, names(), spells, sel, scroll, edit, confirm, status, mouse)
        pygame.display.flip()
