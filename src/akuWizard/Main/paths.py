"""
Where files live, both when running from source and inside a PyInstaller build.

- resource_path(): read-only files shipped with the game (model, fonts, sounds,
  the default spells.json). In a one-file .exe these are unpacked to a temp
  folder (sys._MEIPASS) that is deleted when the game closes.
- user_data_path(): files the game writes (recorded spells, best times).
  From source they stay next to the code; in the .exe they go to
  %APPDATA%/WarOfWizards so they survive restarts.
"""
import os
import shutil
import sys

APP_NAME = "WarOfWizards"
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    return os.path.join(BASE_DIR, *parts)


def user_data_dir():
    if not FROZEN:
        return BASE_DIR
    root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(root, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def user_data_path(name, seed_from_bundle=False):
    """Path to a writable file. With seed_from_bundle, copy the shipped default on first run."""
    path = os.path.join(user_data_dir(), name)
    if seed_from_bundle and not os.path.exists(path):
        src = resource_path(name)
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(path):
            shutil.copyfile(src, path)
    return path
