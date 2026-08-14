"""Rerun walk frames for all 6 animals with tightest possible constraints.

For each style:
  - 4 walk key frames, each with a different seed (100..103) so the
    walk step is visible, but with prompts that differ ONLY in the
    paw-position token -- everything else (lighting, angle, expression)
    is identical
  - subject-ref still locks the character so the breed/style stays
    consistent

Result: 4 frames per style should differ in paw position only, with
much tighter colour / pose / angle alignment than the earlier runs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path("D:/vscodepro/ccmon")

# Full base descriptions for each style (no variation across the 4
# walk frames except the paw position token).
STYLES = [
    (
        "blackcat",
        "A cute kawaii 2D cartoon chibi black cat, sleek black fur with subtle shimmer, piercing emerald green eyes, small pink nose, long plumed black tail, simple chibi body proportions, flat soft cell-shading, sticker style with clean outlines, no fur texture detail",
    ),
    (
        "orangecat",
        "A cute kawaii 2D cartoon chibi orange tabby cat, soft orange tabby fur with subtle darker orange stripes, large round amber eyes, small pink nose, round chubby cheeks, fluffy orange tail, simple chibi body proportions (round chubby body, large head), flat soft cell-shading, sticker style with clean outlines, no fur texture detail",
    ),
    (
        "persian",
        "A cute kawaii 2D cartoon chibi Persian cat, long flowing white-cream fur with soft fluffy texture, very flat squished face, large round deep blue eyes, small pink nose, short muzzle, very long fluffy plumed tail, simple chibi body proportions with thick fluffy body, flat soft cell-shading, sticker style with clean outlines, no fur texture detail",
    ),
    (
        "blackshiba",
        "A cute kawaii 2D cartoon chibi black Shiba Inu dog, sleek black-tan fur with tan markings on cheeks and chest, large round dark brown eyes, small black nose, perky triangular ears, curled fluffy tail, simple chibi body proportions, four short legs, flat soft cell-shading, sticker style with clean outlines, no fur texture detail",
    ),
    (
        "crocodile",
        "A cute kawaii 2D cartoon chibi crocodile, olive green scaly skin with lighter belly, big round golden eyes with vertical slit pupils, small rounded snout with two tiny visible white teeth, short stubby legs, long tail, simple chibi body proportions (chunky body, large head), flat soft cell-shading with simple scale pattern, sticker style with clean outlines",
    ),
    (
        "tiger",
        "A cute kawaii 2D cartoon chibi tiger, bright orange fur with bold black stripes, white belly and inner ears, large round amber eyes, small pink nose, fluffy white cheeks, long striped tail with white tip, simple chibi body proportions, four short legs, flat soft cell-shading, sticker style with clean outlines, no fur texture detail",
    ),
]

# The ONLY thing that varies between the 4 walk key frames.
WALK_STEPS = [
    "walking forward, front right paw lifted mid-step, weight on back legs",
    "walking forward, all four paws grounded, body level neutral stride",
    "walking forward, front left paw lifted mid-step, weight on back legs",
    "walking forward, all four paws grounded, body slightly compressed mid-stride",
]

# Fixed suffix to lock everything else.
COMMON_SUFFIX = (
    ", plain solid bright neon green background (#00FF00 chroma key green screen), "
    "sticker style, single character, full body visible, centered, kawaii style, side view"
)


def mmx(prompt: str, subject_ref: str, seed: int, out: Path) -> None:
    cmd = (
        f'mmx image generate --prompt "{prompt}" --aspect-ratio 1:1 '
        f'--width 512 --height 512 --seed {seed} --out "{out}" '
        f'--subject-ref "type=character,image={subject_ref}"'
    )
    print(f"  {out.parent.name}/{out.name}", flush=True)
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


for style, base in STYLES:
    style_dir = REPO / "assets" / "pet" / style
    ref = style_dir / "reference.png"
    if not ref.exists():
        print(f"  {style}: no reference, skip", flush=True)
        continue
    print(f"=== {style} ===", flush=True)
    for i, step in enumerate(WALK_STEPS, 1):
        out = style_dir / f"walk_{i}_raw.png"
        prompt = f"{base}, {step}{COMMON_SUFFIX}"
        try:
            mmx(prompt, str(ref), 100 + i, out)
        except subprocess.CalledProcessError as e:
            print(f"  walk_{i} FAILED: {e.stderr.decode()[:200]}", flush=True)
            break
    print(f"  -> {style} walks done", flush=True)

print("ALL WALKS RERUN DONE", flush=True)
