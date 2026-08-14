"""Batch-generate 5 new pet styles with constrained prompts.

Each style gets 1 reference + 4 walk key frames + 5 mood frames = 10
mmx image calls. Prompts include 'exactly the same character as
reference, only X changes' to keep walk key frames visually
consistent (the morphing ghosting we saw with blackcat was caused
by per-frame pose drift; tighter prompts reduce that drift).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("D:/vscodepro/ccmon")


def mmx_image(prompt: str, subject_ref: str | None, seed: int, out: Path) -> None:
    # Use shell so the user's PATH is searched (mmx is on npm PATH which
    # Python subprocess doesn't inherit on Windows by default).
    cmd = f'mmx image generate --prompt "{prompt}" --aspect-ratio 1:1 --width 512 --height 512 --seed {seed} --out "{out}"'
    if subject_ref:
        cmd += f' --subject-ref "type=character,image={subject_ref}"'
    print(f"  {out.parent.name}/{out.name}", flush=True)
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


# (style_name, base_description, view_for_walk, view_for_mood, view_for_reference)
STYLES = [
    (
        "orangecat",
        "A cute kawaii 2D cartoon chibi orange tabby cat, soft orange tabby fur with subtle darker orange stripes, large round amber eyes, small pink nose, round chubby cheeks, fluffy orange tail, simple chibi body proportions (round chubby body, large head)",
        "side", "front", "front",
    ),
    (
        "persian",
        "A cute kawaii 2D cartoon chibi Persian cat, long flowing white-cream fur with soft fluffy texture, very flat squished face, large round deep blue eyes, small pink nose, short muzzle, very long fluffy plumed tail, simple chibi body proportions with thick fluffy body",
        "side", "front", "front",
    ),
    (
        "blackshiba",
        "A cute kawaii 2D cartoon chibi black Shiba Inu dog, sleek black-tan fur with tan markings on cheeks and chest, large round dark brown eyes, small black nose, perky triangular ears, curled fluffy tail, simple chibi body proportions, four short legs",
        "side", "front", "front",
    ),
    (
        "crocodile",
        "A cute kawaii 2D cartoon chibi crocodile, olive green scaly skin with lighter belly, big round golden eyes with vertical slit pupils, small rounded snout with two tiny visible white teeth, short stubby legs, long tail, simple chibi body proportions (chunky body, large head), flat soft cell-shading with simple scale pattern",
        "side", "front", "front",
    ),
    (
        "tiger",
        "A cute kawaii 2D cartoon chibi tiger, bright orange fur with bold black stripes, white belly and inner ears, large round amber eyes, small pink nose, fluffy white cheeks, long striped tail with white tip, simple chibi body proportions, four short legs",
        "side", "front", "front",
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


def gen_style(style: str, base_desc: str, walk_view: str, mood_view: str, ref_view: str) -> None:
    style_dir = REPO / "assets" / "pet" / style
    style_dir.mkdir(parents=True, exist_ok=True)
    ref_path = style_dir / "reference.png"

    # 1. reference
    ref_prompt = (
        f"{base_desc}, {CONSTRAINT.replace('this changes', 'this is a neutral standing reference pose')}, "
        f"plain solid bright neon green background (#00FF00 chroma key green screen), "
        f"sticker style, single character, full body visible, centered, kawaii style, {ref_view} view, standing pose"
    )
    mmx_image(ref_prompt, None, 7777, ref_path)

    # 2. 4 walk key frames
    for i, step in enumerate(WALK_STEPS, 1):
        walk_prompt = (
            f"{base_desc}, {step}{CONSTRAINT}, "
            f"plain solid bright neon green background (#00FF00 chroma key green screen), "
            f"sticker style, single character, full body visible, centered, kawaii style, {walk_view} view"
        )
        mmx_image(walk_prompt, str(ref_path), 100 + i, style_dir / f"walk_{i}_raw.png")

    # 3. 5 mood frames
    for state, expr in MOODS:
        mood_prompt = (
            f"{base_desc}, {expr}{CONSTRAINT}, "
            f"plain solid bright neon green background (#00FF00 chroma key green screen), "
            f"sticker style, single character, full body visible, centered, kawaii style, {mood_view} view"
        )
        mmx_image(mood_prompt, str(ref_path), hash(state) % 1000 + 200, style_dir / f"{state}_raw.png")


for s in STYLES:
    print(f"=== {s[0]} ===", flush=True)
    try:
        gen_style(*s)
        print(f"  -> {s[0]} done", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"  -> {s[0]} FAILED: {e.stderr.decode()[:200]}", flush=True)
        sys.exit(1)

print("ALL 5 STYLES DONE", flush=True)
