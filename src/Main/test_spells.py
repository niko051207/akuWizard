"""
Checks how well your recorded spells can be told apart.

Each saved sample is matched against all the OTHER samples (leave-one-out),
the same way the game matches a cast. Run it after recording:

    python test_spells.py

Look for: samples recognized as the wrong spell (re-record or delete them with
D in record_spells.py) and spells whose margin is often below MIN_MARGIN.
"""
from collections import Counter, defaultdict

from dollarpy import Point, Template

from game import MIN_ACCURACY, MIN_MARGIN
from spell_recognizer import FastRecognizer, load_spell_data


def main():
    data = load_spell_data()
    samples = [(name, i, [Point(p["x"], p["y"]) for p in g])
               for name, e in data.items() for i, g in enumerate(e["gestures"]) if len(g) >= 2]
    if len(samples) < 2:
        print("Record at least two samples first.")
        return

    confusion = defaultdict(Counter)
    margins = defaultdict(list)
    for k, (truth, idx, pts) in enumerate(samples):
        rest = [Template(n, p) for j, (n, _, p) in enumerate(samples) if j != k]
        name, score, runner = FastRecognizer(rest).recognize_ranked(pts)
        if score < MIN_ACCURACY:
            verdict = "REJECT (low)"
        elif score - runner < MIN_MARGIN:
            verdict = "REJECT (close)"
        else:
            verdict = name
        confusion[truth][verdict] += 1
        margins[truth].append(score - runner)
        if verdict != truth:
            print(f"  {truth} sample #{idx}: {verdict}  score {score:.0%}, runner-up {runner:.0%}")

    print("\nResults (how each spell's samples were recognized):")
    for truth, row in confusion.items():
        total = sum(row.values())
        ok = row.get(truth, 0)
        m = sorted(margins[truth])
        print(f"  {truth:16} {ok}/{total} correct   median margin {m[len(m) // 2]:.2f}   {dict(row)}")
    print(f"\nThresholds in game.py: MIN_ACCURACY={MIN_ACCURACY}, MIN_MARGIN={MIN_MARGIN}")


if __name__ == "__main__":
    main()
