import os
import shutil
import sys

APP_NAME = "akuWizard"
LEGACY_APP_NAMES = ["WarOfWizards"]
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    return os.path.join(BASE_DIR, *parts)


# ---------------------------------------------------------------------------
# User data (%APPDATA% in the .exe, next to the code from source)
# ---------------------------------------------------------------------------
def _migrate_legacy(root, path):
    for old in LEGACY_APP_NAMES:
        old_path = os.path.join(root, old)
        if os.path.isdir(old_path):
            try:
                shutil.copytree(old_path, path)
                return
            except OSError as e:
                print(f"Couldn't copy old save data from {old_path}: {e}")


def user_data_dir():
    if not FROZEN:
        return BASE_DIR
    root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(root, APP_NAME)
    if not os.path.exists(path):
        _migrate_legacy(root, path)
    os.makedirs(path, exist_ok=True)
    return path


def user_data_path(name, seed_from_bundle=False):
    path = os.path.join(user_data_dir(), name)
    if seed_from_bundle and not os.path.exists(path):
        src = resource_path(name)
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(path):
            shutil.copyfile(src, path)
    return path