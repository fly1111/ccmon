"""Re-run blackcat with constrained prompt to fix 4-frame pose drift."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path("D:/vscodepro/ccmon")
STYLE = "blackcat"
BASE = "A cute kawaii 2D cartoon chibi black cat, sleek black fur with subtle shimmer, piercing emerald green eyes, small pink nose, long plumed black tail, simple chibi body proportions, flat soft cell-shading, sticker style with clean outlines, no fur texture detail"
CONSTRAINT = " exactly the same character as reference, only this changes"

WALK_STEPS = [
    "walking forward, front right paw lifted mid-step, weight on back legs",
    "walking forward, all four paws grounded, body level neutral stride",
    "walking forward, front left paw lifted mid-step, weight on back legs",
    "walking forward, all four paws grounded, body slightly compressed mid-stride",
]
MOODS = [
    ("happy", "confident smirk, ears perked up, bright cheerful eyes"),
    ("anxious", "worried intense expression, ears pinned back, eyebrows raised"),
    ("sad", "sad droopy eyes, ears flat and drooping, slight frown"),
    ("sleepy", "sleepy half-closed eyes, gentle relaxed expression, ears drooped"),
    ("alert", "very alert wide open eyes looking forward, ears upright and forward"),
]


def mmx(prompt: str, subject_ref: str | None, seed: int, out: Path) -> None:
    cmd = f'mmx image generate --prompt "{prompt}" --aspect-ratio 1:1 --width 512 --height 512 --seed {seed} --out "{out}"'
    if subject_ref:
        cmd += f' --subject-ref "type=character,image={subject_ref}"'
    print(f"  {out.name}", flush=True)
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


style_dir = REPO / "assets" / "pet" / STYLE
ref = style_dir / "reference.png"

# 1. fresh reference with constrained prompt
ref_prompt = (
    f"{BASE}, {CONSTRAINT.replace('this changes', 'this is a neutral standing reference pose')}, "
    f"plain solid bright neon green background (#00FF00 chroma key green screen), "
    f"sticker style, single character, full body visible, centered, kawaii style, front view, standing pose"
)
mmx(ref_prompt, None, 7777, ref)

# 2. 4 walk
for i, step in enumerate(WALK_STEPS, 1):
    p = (
        f"{BASE}, {step}{CONSTRAINT}, "
        f"plain solid bright neon green background (#00FF00 chroma key green screen), "
        f"sticker style, single character, full body visible, centered, kawaii style, side view"
    )
    mmx(p, str(ref), 100 + i, style_dir / f"walk_{i}_raw.png")

# 3. 5 mood
for state, expr in MOODS:
    p = (
        f"{BASE}, {expr}{CONSTRAINT}, "
        f"plain solid bright neon green background (#00FF00 chroma key green screen), "
        f"sticker style, single character, full body visible, centered, kawaii style, front view"
    )
    mmx(p, str(ref), hash(state) % 1000 + 200, style_dir / f"{state}_raw.png")

print("BLACKCAT RERUN DONE", flush=True)
