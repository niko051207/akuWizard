import pygame

from paths import resource_path

ASSETS_DIR = resource_path("assets")


def load_font(filename, size):
    path = resource_path("assets", filename)
    try:
        return pygame.font.Font(path, size)
    except (FileNotFoundError, OSError):
        print(f"Font not found: {path} (using default font)")
        return pygame.font.Font(None, size)
