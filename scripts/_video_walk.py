"""Video walk pipeline: 1 mmx video call per style -> 12-frame cycle.

Same shape as the luna walk that we know works (mmx Hailuo-2.3,
5s, then ffmpeg fps=2, then per-frame bbox-centre + chroma key +
mirror + resize 192x192). The difference: this script accepts any
of the 6 batched-in styles and produces a 12-frame walk cycle
that loops smoothly (the image-route walks we have now are only
4 key frames in a 0.33s loop, which feels jumpy).

Quota: mmx video is rate-limited (3 calls/day on token plan).
Run one style per day, or run multiple over several days.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image

REPO = Path("D:/vscodepro/ccmon")

# Per-style mmx video prompt. Walk is a 5s side-view walk on green
# screen; subject description matches the style's reference image
# so the breed stays consistent. (Hailuo-2.3 doesn't accept
# --subject-ref, so the breed is locked in via the prose prompt.)
#
# IMPORTANT: the cat should fill only 60-70% of the frame, centred
# with breathing room on all four sides. If it fills 90%+, the bbox
# crop can't add headroom when the cat is drawn hugging the frame
# edge -- paws get clipped during stride.
COMMON_SUFFIX = (
    " Side view, full body visible, smooth consistent animation. "
    "Subject centred in frame and fills about 60% of the frame height "
    "and width, with generous empty space on all four sides for safe "
    "cropping. Plain solid bright neon green background (#00FF00 chroma "
    "key green screen) for background removal."
)
STYLES: dict[str, str] = {
    "luna": (
        "A cute kawaii 2D cartoon chibi female Chinese Shandong lion cat, "
        "long flowing pure white fur with thick lion-like mane ruff around "
        "neck and chest, big round ice-blue anime eyes, small pink nose, "
        "long plumed fluffy tail, simple chibi body proportions, flat soft "
        "cell-shading, sticker style with clean outlines, no fur texture "
        "detail. The cat walks naturally in a continuous four-step walk "
        "cycle that loops seamlessly: front-right paw lifted, both paws "
        "grounded, front-left paw lifted, both paws grounded, then repeats."
        + COMMON_SUFFIX
    ),
    "blackcat": (
        "A cute kawaii 2D cartoon chibi black cat, sleek black fur with "
        "subtle shimmer, piercing emerald green eyes, small pink nose, long "
        "plumed black tail, simple chibi body proportions, flat soft "
        "cell-shading, sticker style with clean outlines, no fur texture "
        "detail. The cat walks naturally in a continuous four-step walk "
        "cycle that loops seamlessly: front-right paw lifted, both paws "
        "grounded, front-left paw lifted, both paws grounded, then repeats."
        + COMMON_SUFFIX
    ),
    "orangecat": (
        "A cute kawaii 2D cartoon chibi orange tabby cat, soft orange tabby "
        "fur with subtle darker orange stripes, large round amber eyes, "
        "small pink nose, round chubby cheeks, fluffy orange tail, simple "
        "chibi body proportions (round chubby body, large head), flat soft "
        "cell-shading, sticker style with clean outlines, no fur texture "
        "detail. The cat walks naturally in a continuous four-step walk "
        "cycle that loops seamlessly: front-right paw lifted, both paws "
        "grounded, front-left paw lifted, both paws grounded, then repeats."
        + COMMON_SUFFIX
    ),
    "persian": (
        "A cute kawaii 2D cartoon chibi Persian cat, long flowing white-cream "
        "fur with soft fluffy texture, very flat squished face, large round "
        "deep blue eyes, small pink nose, short muzzle, very long fluffy "
        "plumed tail, simple chibi body proportions with thick fluffy body, "
        "flat soft cell-shading, sticker style with clean outlines, no fur "
        "texture detail. The cat walks naturally in a continuous four-step "
        "walk cycle that loops seamlessly: front-right paw lifted, both paws "
        "grounded, front-left paw lifted, both paws grounded, then repeats."
        + COMMON_SUFFIX
    ),
    "blackshiba": (
        "A cute kawaii 2D cartoon chibi black Shiba Inu dog, sleek "
        "black-tan fur with tan markings on cheeks and chest, large round "
        "dark brown eyes, small black nose, perky triangular ears, curled "
        "fluffy tail, simple chibi body proportions, four short legs, flat "
        "soft cell-shading, sticker style with clean outlines, no fur texture "
        "detail. The dog walks naturally in a continuous four-step walk cycle "
        "that loops seamlessly: front-right paw lifted, both paws grounded, "
        "front-left paw lifted, both paws grounded, then repeats."
        + COMMON_SUFFIX
    ),
    "crocodile": (
        "A cute kawaii 2D cartoon chibi crocodile, olive green scaly skin "
        "with lighter belly, big round golden eyes with vertical slit pupils, "
        "small rounded snout with two tiny visible white teeth, short stubby "
        "legs, long tail, simple chibi body proportions (chunky body, large "
        "head), flat soft cell-shading with simple scale pattern, sticker "
        "style with clean outlines. The crocodile walks naturally in a "
        "continuous four-step walk cycle that loops seamlessly: front-right "
        "paw lifted, both paws grounded, front-left paw lifted, both paws "
        "grounded, then repeats."
        + COMMON_SUFFIX
    ),
    "tiger": (
        "A cute kawaii 2D cartoon chibi tiger, bright orange fur with bold "
        "black stripes, white belly and inner ears, large round amber eyes, "
        "small pink nose, fluffy white cheeks, long striped tail with white "
        "tip, simple chibi body proportions, four short legs, flat soft "
        "cell-shading, sticker style with clean outlines, no fur texture "
        "detail. The tiger walks naturally in a continuous four-step walk "
        "cycle that loops seamlessly: front-right paw lifted, both paws "
        "grounded, front-left paw lifted, both paws grounded, then repeats."
        + COMMON_SUFFIX
    ),
}


def mmx_video_call(prompt: str, out_mp4: Path) -> None:
    cmd = (
        f'mmx video generate --model MiniMax-Hailuo-2.3 '
        f'--prompt "{prompt}" '
        # Note: --duration / --ratio are H3-only params. Hailuo-2.3
        # (the legacy / token-plan-friendly model) ignores them and
        # returns 5s by default. Passing --duration here makes the
        # server reject the whole call with code 2.
        f'--download "{out_mp4}" '
        f'--poll-interval 10 '
        f'--timeout 1800 '
        f'--non-interactive'
    )
    print(f"  mmx video generate -> {out_mp4.name}", flush=True)
    # capture_output=False so error messages from mmx show on the
    # console (was capture_output=True swallowing them).
    subprocess.run(cmd, shell=True, check=True)


def ffmpeg_extract(mp4: Path, frames_dir: Path) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    cmd = f'ffmpeg -y -i "{mp4}" -vf "fps=2" "{frames_dir}/frame_%02d.png"'
    print(f"  ffmpeg fps=2 -> {frames_dir}", flush=True)
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


def cat_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    w, h = img.size
    px = img.load()
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            if not (g > r + 25 and g > b + 25):  # not green
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    return (min_x, min_y, max_x, max_y)


def chroma_key(img: Image.Image) -> Image.Image:
    px = img.load()
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out_px = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            excess = min(g - r, g - b)
            if excess <= 8:
                a = 255
            elif excess >= 60:
                a = 0
            else:
                a = int(255 * (60 - excess) / 52)
            if a > 0:
                out_px[x, y] = (r, g, b, a)
    return out


def process_frames(frames_dir: Path, style_dir: Path) -> None:
    """Per-frame: bbox-centre + 25% padding + chroma key + resize 192.

    The mmx prompt asks for the subject to fill ~60% of the frame, so 25%
    padding gives ~5% empty space on each side after chroma key + resize.
    That keeps heads/tails from being clipped at the canvas edge.
    """
    raw_frames = sorted(frames_dir.glob("frame_*.png"))
    if len(raw_frames) != 12:
        print(f"  WARNING: expected 12 frames, got {len(raw_frames)}", flush=True)
    for idx, fp in enumerate(raw_frames, 1):
        img = Image.open(fp).convert("RGB")
        bb = cat_bbox(img)
        if bb[0] >= bb[2] or bb[1] >= bb[3]:
            print(f"  frame {idx}: empty bbox, skipping", flush=True)
            continue
        bw = bb[2] - bb[0]
        bh = bb[3] - bb[1]
        side = max(bw, bh)
        pad = int(side * 0.25)
        side_padded = min(768, side + 2 * pad)
        cx = (bb[0] + bb[2]) // 2
        cy = (bb[1] + bb[3]) // 2
        x0 = max(0, cx - side_padded // 2)
        y0 = max(0, cy - side_padded // 2)
        x1 = min(img.size[0], x0 + side_padded)
        y1 = min(img.size[1], y0 + side_padded)
        if x1 - x0 < side_padded:
            x0 = max(0, x1 - side_padded)
        if y1 - y0 < side_padded:
            y0 = max(0, y1 - side_padded)
        cropped = img.crop((x0, y0, x0 + side_padded, y0 + side_padded))
        # Chroma key the cropped RGB, then resize. mmx video already
        # renders the cat facing right, so we save it as-is. The
        # PetWindow runtime flips the frame when _walk_facing_left
        # is False (right-bound) to get the correct travel direction.
        keyed = chroma_key(cropped)
        keyed.resize((192, 192), Image.LANCZOS).save(
            style_dir / f"walk_{idx}_alpha.png"
        )
        print(f"  walk_{idx}_alpha.png", flush=True)
    # Cleanup
    for fp in raw_frames:
        fp.unlink()
    frames_dir.rmdir()


def gen_video_walk(style: str) -> None:
    if style not in STYLES:
        print(f"  unknown style: {style}", file=sys.stderr)
        print(f"  available: {', '.join(STYLES)}", file=sys.stderr)
        sys.exit(1)
    style_dir = REPO / "assets" / "pet" / style
    if not (style_dir / "reference.png").exists():
        print(f"  {style}: no reference.png, cannot lock style", file=sys.stderr)
        sys.exit(1)
    print(f"=== {style} video walk ===", flush=True)
    mp4 = style_dir / "walk_source.mp4"
    raw_dir = REPO / "assets" / "pet" / style / "_video_raw"

    try:
        mmx_video_call(STYLES[style], mp4)
        ffmpeg_extract(mp4, raw_dir)
        process_frames(raw_dir, style_dir)
    finally:
        # Only the ffmpeg-extracted intermediate PNGs are throwaway --
        # they can be regenerated from the mp4 in a second. The mp4
        # itself (walk_source.mp4) is kept so the user can re-derive
        # different crops without spending another mmx video quota.
        # To free disk: rm <style_dir>/walk_source.mp4 manually.
        if raw_dir.exists():
            for fp in raw_dir.iterdir():
                fp.unlink()
            raw_dir.rmdir()
    print(f"=== {style} done ===", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("style", choices=sorted(STYLES))
    args = p.parse_args()
    gen_video_walk(args.style)
