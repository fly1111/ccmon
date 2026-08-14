"""Retry the failed animals: crocodile (9 missing) + tiger (10 missing)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("D:/vscodepro/ccmon")


def mmx_image(prompt: str, subject_ref: str | None, seed: int, out: Path) -> None:
    cmd = f'mmx image generate --prompt "{prompt}" --aspect-ratio 1:1 --width 512 --height 512 --seed {seed} --out "{out}"'
    if subject_ref:
        cmd += f' --subject-ref "type=character,image={subject_ref}"'
    print(f"  {out.parent.name}/{out.name}", flush=True)
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


STYLES = [
    (
        "crocodile",
        "A cute kawaii 2D cartoon chibi crocodile, olive green scaly skin with lighter belly, big round golden eyes with vertical slit pupils, small rounded snout with two tiny visible white teeth, short stubby legs, long tail, simple chibi body proportions (chunky body, large head), flat soft cell-shading with simple scale pattern",
    ),
    (
        "tiger",
        "A cute kawaii 2D cartoon chibi tiger, bright orange fur with bold black stripes, white belly and inner ears, large round amber eyes, small pink nose, fluffy white cheeks, long striped tail with white tip, simple chibi body proportions, four short legs",
    ),
]

WALK_STEPS = [
    "walking forward, front right paw lifted mid-step, weight on back legs",
    "walking forward, all four paws grounded, body level neutral stride",
    "walking forward, front left paw lifted mid-step, weight on back legs",
    "walking forward, all four paws grounded, body slightly compressed mid-stride",
]

MOODS = [
    ("happy", "confident smirk, ears perked up, bright cheerful eyes, tongue slightly out"),
    ("anxious", "worried intense expression, ears pinned back, eyebrows raised, mouth slightly open"),
    ("sad", "sad droopy eyes, ears flat and drooping, slight frown, slumped posture"),
    ("sleepy", "sleepy half-closed eyes, gentle relaxed expression, ears drooped, calm"),
    ("alert", "very alert wide open eyes looking forward, ears upright and forward, focused"),
]

CONSTRAINT = " exactly the same character as reference, only this changes"


def gen_style(style: str, base_desc: str) -> None:
    style_dir = REPO / "assets" / "pet" / style
    style_dir.mkdir(parents=True, exist_ok=True)
    ref_path = style_dir / "reference.png"

    # Skip reference if it exists
    if not ref_path.exists():
        ref_prompt = (
            f"{base_desc}, {CONSTRAINT.replace('this changes', 'this is a neutral standing reference pose')}, "
            f"plain solid bright neon green background (#00FF00 chroma key green screen), "
            f"sticker style, single character, full body visible, centered, kawaii style, front view, standing pose"
        )
        mmx_image(ref_prompt, None, 7777, ref_path)

    for i, step in enumerate(WALK_STEPS, 1):
        out = style_dir / f"walk_{i}_raw.png"
        if out.exists():
            print(f"  skip {out.name} (exists)", flush=True)
            continue
        walk_prompt = (
            f"{base_desc}, {step}{CONSTRAINT}, "
            f"plain solid bright neon green background (#00FF00 chroma key green screen), "
            f"sticker style, single character, full body visible, centered, kawaii style, side view"
        )
        mmx_image(walk_prompt, str(ref_path), 100 + i, out)

    for state, expr in MOODS:
        out = style_dir / f"{state}_raw.png"
        if out.exists():
            print(f"  skip {out.name} (exists)", flush=True)
            continue
        mood_prompt = (
            f"{base_desc}, {expr}{CONSTRAINT}, "
            f"plain solid bright neon green background (#00FF00 chroma key green screen), "
            f"sticker style, single character, full body visible, centered, kawaii style, front view"
        )
        mmx_image(mood_prompt, str(ref_path), hash(state) % 1000 + 200, out)


for s in STYLES:
    print(f"=== {s[0]} ===", flush=True)
    try:
        gen_style(*s)
        print(f"  -> {s[0]} done", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"  -> {s[0]} FAILED: {e.stderr.decode()[:200]}", flush=True)

print("RETRY DONE", flush=True)
