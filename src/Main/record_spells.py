"""
Spell recorder: draw a gesture several times and save each one to spells.json.

Usage:
    python record_spells.py Fireball
    python record_spells.py Fireball --damage 150 --color 255,60,60
    python record_spells.py            (it will ask for the spell name)
    python record_spells.py Fireball --camera 2   (use the second webcam)
    python record_spells.py Frostbite --effect counter --damage 90 --cost 20

Controls (same pipeline as the game, so recordings match how spells are cast):
    - Pinch (thumb + index) to start drawing, move your hand to draw.
    - Release the pinch to finish a stroke.
    ENTER / SPACE : save the stroke as a sample
    BACKSPACE / R : discard the stroke and redraw
    D             : delete the last saved sample of this spell
    ESC           : quit
"""
import argparse

import pygame

from hand_input import HandTracker, PinchState, StrokeBuilder, cam_rect, pinch_point
from settings import load_settings
from spell_recognizer import EFFECTS, load_spell_data, save_spell_data, new_spell_entry

MIN_POINTS = 15      # strokes shorter than this are ignored
WIDTH, HEIGHT = 960, 720


def main():
    parser = argparse.ArgumentParser(description="Record spell gestures into spells.json")
    parser.add_argument("name", nargs="?", help="spell name, e.g. Fireball")
    parser.add_argument("--damage", type=int, help="base damage for this spell")
    parser.add_argument("--cost", type=int, help="mana cost to cast this spell")
    parser.add_argument("--type", choices=["attack", "defense"],
                        help="attack deals damage; defense blocks an incoming enemy attack")
    parser.add_argument("--color", help="projectile color as R,G,B e.g. 255,60,60")
    parser.add_argument("--effect", choices=sorted(EFFECTS),
                        help="what an attack spell does besides damage (burn, interrupt, counter, none)")
    parser.add_argument("--camera", type=int,
                        help="camera number to use (1 = first). Defaults to the one chosen in the game's CAMERA menu")
    args = parser.parse_args()

    spell_name = args.name or input("Spell name: ").strip()
    if not spell_name:
        print("Spell name can't be empty.")
        return

    spells = load_spell_data()
    entry = spells.setdefault(spell_name, new_spell_entry())
    if args.damage is not None:
        entry["damage"] = args.damage
    if args.cost is not None:
        entry["cost"] = args.cost
    if args.type is not None:
        entry["type"] = args.type
    if args.effect:
        entry["effect"] = args.effect
    if args.color:
        entry["color"] = [int(c) for c in args.color.split(",")]
    save_spell_data(spells)

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(f"Recording: {spell_name}")
    font = pygame.font.SysFont(None, 32)
    font_small = pygame.font.SysFont(None, 24)
    clock = pygame.time.Clock()

    camera_index = args.camera - 1 if args.camera else load_settings()["camera_index"]
    tracker = HandTracker(camera_index=camera_index)
    pinch = PinchState()
    stroke = StrokeBuilder()
    pending = None       # finished stroke waiting for save/discard
    message = "Pinch to start drawing, release to finish."
    last_id, last_t = None, None
    cam_src = cam_rgb = None
    lm = None

    try:
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE) and pending:
                        entry["gestures"].append([{"x": x, "y": y} for x, y in pending])
                        save_spell_data(spells)
                        message = f"Saved! {len(entry['gestures'])} samples for {spell_name}."
                        pending = None
                    elif event.key in (pygame.K_BACKSPACE, pygame.K_r) and pending:
                        pending = None
                        message = "Discarded. Draw again."
                    elif event.key == pygame.K_d and entry["gestures"]:
                        entry["gestures"].pop()
                        save_spell_data(spells)
                        message = f"Deleted last sample. {len(entry['gestures'])} left."

            if tracker.error:
                print("Camera error:", tracker.error)
                break

            screen.fill((8, 8, 18))
            frame = tracker.latest()
            if frame is not None and frame[0] != last_id:
                fid, cam_rgb, lm, t = frame
                cam_dt = (t - last_t) if last_t else 1 / 30
                last_id, last_t = fid, t
                fh, fw = cam_rgb.shape[:2]
                cam_src = pygame.image.frombuffer(cam_rgb, (fw, fh), "RGB")
                arena = cam_rect(WIDTH, HEIGHT, fw, fh)

                ev = pinch.update(lm, fw / fh)
                if pending is None:
                    if ev == "start":
                        stroke.begin()
                    elif ev == "release" and stroke.active:
                        pts = stroke.finish()
                        if len(pts) >= MIN_POINTS:
                            pending = pts
                            message = "ENTER = save, BACKSPACE = redraw"
                        else:
                            message = "Stroke too short, try again."
                    elif ev == "cancel" and stroke.active:
                        stroke.cancel()
                        message = "Hand left the camera. Draw again."
                    if stroke.active and pinch.pinching and lm is not None:
                        x, y = pinch_point(lm, arena)
                        stroke.add(x, y, cam_dt, arena.h)

            if cam_src is not None:
                arena = cam_rect(WIDTH, HEIGHT, *cam_src.get_size())
                screen.blit(pygame.transform.scale(cam_src, arena.size), arena.topleft)
                if lm is not None:
                    cx, cy = pinch_point(lm, arena)
                    color = (0, 255, 100) if pinch.pinching else (255, 90, 90)
                    pygame.draw.circle(screen, color, (int(cx), int(cy)), 10, 0 if pinch.pinching else 2)

            if len(stroke.points) > 1:
                pygame.draw.lines(screen, (255, 215, 0), False, stroke.points, 6)
            if pending and len(pending) > 1:
                pygame.draw.lines(screen, (0, 255, 120), False, pending, 6)

            panel = pygame.Surface((WIDTH, 90))
            panel.set_alpha(190)
            panel.fill((10, 10, 25))
            screen.blit(panel, (0, 0))
            screen.blit(font.render(f"Recording: {spell_name}   Samples: {len(entry['gestures'])}",
                                    True, (255, 215, 0)), (15, 8))
            screen.blit(font_small.render(
                f"Damage: {entry['damage']}   Cost: {entry['cost']}   Type: {entry['type']}",
                True, (200, 200, 255)), (15, 40))
            status = message if cam_src is not None else "Opening the camera..."
            screen.blit(font_small.render(status, True, (255, 255, 255)), (15, 60))

            pygame.display.flip()
            clock.tick(60)
    finally:
        tracker.stop()
        pygame.quit()
    print(f"Done. '{spell_name}' now has {len(entry['gestures'])} samples in spells.json.")


if __name__ == "__main__":
    main()