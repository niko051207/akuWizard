import os
import pygame

from paths import resource_path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SFX_DIR = resource_path("assets", "sfx")
MUSIC_DIR = resource_path("assets", "music")

_cache = {}
_warned = set()

_vol = {"master": 1.0, "music": 1.0, "sfx": 1.0}
_music_base = 0.4


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------
def configure(settings):
    set_volumes(settings.get("master_volume"), settings.get("music_volume"), settings.get("sfx_volume"))


def set_volumes(master=None, music=None, sfx=None):
    for key, val in (("master", master), ("music", music), ("sfx", sfx)):
        if val is not None:
            _vol[key] = max(0.0, min(1.0, float(val)))
    try:
        if _mixer_ready():
            pygame.mixer.music.set_volume(_music_base * _vol["master"] * _vol["music"])
    except pygame.error:
        pass


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------
def _mixer_ready():
    try:
        return pygame.mixer.get_init() is not None
    except pygame.error:
        return False


def load_sound(name):
    if name in _cache:
        return _cache[name]
    if not _mixer_ready():
        return None

    for ext in (".wav", ".ogg", ".mp3"):
        path = os.path.join(SFX_DIR, name + ext)
        if os.path.exists(path):
            try:
                snd = pygame.mixer.Sound(path)
                _cache[name] = snd
                return snd
            except pygame.error as e:
                print(f"[sounds] Failed to load {path}: {e}")
                break

    if name not in _warned:
        print(f"[sounds] No sfx file found for '{name}' in {SFX_DIR} (looked for .wav/.ogg/.mp3) - skipping sound")
        _warned.add(name)
    _cache[name] = None
    return None


def play(name, volume=1.0):
    snd = load_sound(name)
    if snd:
        snd.set_volume(volume * _vol["master"] * _vol["sfx"])
        snd.play()


def play_music(name, volume=0.4, loops=-1):
    global _music_base
    _music_base = volume
    if not _mixer_ready():
        return
    for ext in (".ogg", ".mp3", ".wav"):
        path = os.path.join(MUSIC_DIR, name + ext)
        if os.path.exists(path):
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(volume * _vol["master"] * _vol["music"])
                pygame.mixer.music.play(loops)
                return
            except pygame.error as e:
                print(f"[sounds] Failed to load music {path}: {e}")
                return

    key = f"music:{name}"
    if key not in _warned:
        print(f"[sounds] No music file found for '{name}' in {MUSIC_DIR} - skipping music")
        _warned.add(key)


def stop_music():
    try:
        if _mixer_ready():
            pygame.mixer.music.stop()
    except pygame.error:
        pass