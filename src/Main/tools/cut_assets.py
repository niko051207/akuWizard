"""
One-off: cut the downloaded packs into the PNGs the game loads.

    python tools/cut_assets.py <FreeUI.png> <Queen folder>

Writes assets/ui/*.png and assets/sprites/enemy_*.png. You only need to run
this again if you swap in a different version of the packs.
"""
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_OUT = os.path.join(ROOT, "assets", "ui")
SPRITE_OUT = os.path.join(ROOT, "assets", "sprites")

# (x, y, w, h) on MagicUiFree/FreeUI.png
PANEL = (4, 3, 88, 122)
BUTTON = (17 + 4, 77 + 3, 54, 17)          # the EXIT button, text removed below
ITEMS = {
    "hat_dark": (7, 136, 19, 17),
    "hat_purple": (7, 167, 19, 17),
    "bear": (6, 198, 20, 21),
    "skull": (3, 228, 26, 25),
}
# 14x14 purple buttons (normal state)
BUTTONS = {
    "btn_play": (97, 17), "btn_pause": (129, 17),
    "btn_plus": (97, 33), "btn_check": (129, 33),
    "btn_x": (97, 49), "btn_minus": (129, 49),
    "btn_help": (97, 65), "btn_coin": (129, 65),
}
# 16x16 icon cells, white set
ICONS = {
    "plus": (96, 192), "check": (112, 192), "help": (128, 192), "coin": (144, 192),
    "home": (96, 208), "x": (112, 208), "sound": (128, 208), "pause": (144, 208),
    "minus": (96, 224), "gear": (112, 224), "mute": (128, 224), "play": (144, 224),
}
# portrait -> game sprite name
QUEEN = {
    "Calm": ["enemy_idle"],
    "Aggression": ["enemy_charge"],
    "Special": ["enemy_stunned"],
    "Sadness": ["enemy_hurt", "enemy_dead"],
    "Smile": ["enemy_taunt"],
    "Talk": ["enemy_talk"],
}


def crop(img, box):
    x, y, w, h = box
    return img.crop((x, y, x + w, y + h))


def clean_panel(sheet):
    """The menu panel with its three baked-in buttons painted over with clean interior."""
    p = crop(sheet, PANEL).copy()
    px = p.load()
    for y in range(34, 96):                       # button rows
        sy = 20 + (y - 34) % 16                   # copy from the clean band under the gem
        for x in range(4, 84):
            px[x, y] = px[x, sy]
    return p


def clean_button(sheet):
    b = crop(sheet, BUTTON).copy()
    px = b.load()
    fill = (207, 181, 143, 255)
    for y in range(b.height):
        for x in range(b.width):
            r, g, bl, a = px[x, y]
            if a and r > 240 and g > 240 and bl > 240:    # the white label
                px[x, y] = fill
            elif a and bl > r:                               # panel purple showing in the corners
                px[x, y] = (0, 0, 0, 0)
    return b


def main(ui_png, queen_dir):
    os.makedirs(UI_OUT, exist_ok=True)
    os.makedirs(SPRITE_OUT, exist_ok=True)
    sheet = Image.open(ui_png).convert("RGBA")
    clean_panel(sheet).save(os.path.join(UI_OUT, "panel.png"))
    clean_button(sheet).save(os.path.join(UI_OUT, "button.png"))
    for name, box in ITEMS.items():
        crop(sheet, box).save(os.path.join(UI_OUT, name + ".png"))
    for name, (x, y) in BUTTONS.items():
        crop(sheet, (x, y, 14, 14)).save(os.path.join(UI_OUT, name + ".png"))
    for name, (x, y) in ICONS.items():
        icon = crop(sheet, (x, y, 16, 16))
        icon.crop(icon.getbbox()).save(os.path.join(UI_OUT, "icon_" + name + ".png"))
    for src, names in QUEEN.items():
        img = Image.open(os.path.join(queen_dir, src + ".png")).convert("RGBA")
        for n in names:
            img.save(os.path.join(SPRITE_OUT, n + ".png"))
    print("done ->", UI_OUT, "and", SPRITE_OUT)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
