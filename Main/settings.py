"""
Player settings saved between sessions (settings.json in the user data folder).
"""
import json

from paths import user_data_path

SETTINGS_FILE = user_data_path("settings.json")

DEFAULTS = {
    "camera_index": 0,       # which webcam to use (0 = the system's first camera)
    "camera_name": "",       # remembered so the menu can show it
}


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update({k: saved[k] for k in DEFAULTS if k in saved})
    except (OSError, ValueError):
        pass
    return data


def save_settings(data):
    merged = dict(DEFAULTS)
    merged.update(data)
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(merged, f, indent=2)
    except OSError as e:
        print("Couldn't save settings:", e)