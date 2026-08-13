#!/usr/bin/env bash
# Regenerate (or create) a pet style with mmx.
#
# Usage:
#   ./scripts/gen_pet_sprites.sh                        # regenerate default (realistic)
#   ./scripts/gen_pet_sprites.sh <style> [prompt-add]   # write to <data_dir>/assets/pet/<style>/
#
# The "realistic" style is the default. To create a new style:
#   ./scripts/gen_pet_sprites.sh cyberpunk "neon cyberpunk dalmatian with glowing spots, dark background"
# It appears in the pet's menu automatically next launch.
#
# Output: <data_dir>/assets/pet/<style>/<state>.png and <state>_alpha.png.

set -euo pipefail

# Default: write under the repo's assets/pet/ so styles ship via git.
# Override with CCMON_PET_DIR=<path> if you want the old user-local behaviour.
if [ -n "${CCMON_PET_DIR:-}" ]; then
  DATA_DIR="${CCMON_PET_DIR}"
else
  REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
  DATA_DIR="$REPO_ROOT/assets/pet"
fi
mkdir -p "$DATA_DIR"

STYLE="${1:-peter}"

# Style-specific prompt fragments. When you want a different look, add a
# key here and the script will pick it up.
declare -A STYLE_PROMPTS=(
  [peter]="Strong male dalmatian dog, stocky muscular build, thick neck, broad chest, large head, big paws, masculine features, white fur with bold black spots, dark brown nose, "
  [peter2]="Cute chibi dalmatian puppy sitting facing forward, soft white fur with sparse black spots, big round black eyes with white highlights, brown nose, pink blush on cheeks, friendly neutral expression, "
  [luna]="Shandong lion cat (Chinese lion cat), female, lithe slender elegant body, long flowing pure white fur with thick lion-like mane ruff around neck and chest, regal imperious bearing, icy cold stare with half-lidded piercing ice-blue almond eyes, pink nose, long plumed fluffy tail, "
)
STYLE_PROMPT="${STYLE_PROMPTS[$STYLE]:-}"

# Style-specific state-image modifiers. Styles not listed fall through to
# the default (which includes "kawaii style"). Use this to override the
# hardcoded kawaii default for serious/realistic looks.
declare -A STATE_MODIFIERS=()
DEFAULT_STATE_MODIFIER="sticker style, single character, full body visible, centered, kawaii style"
STATE_MODIFIER="${STATE_MODIFIERS[$STYLE]:-$DEFAULT_STATE_MODIFIER}"

PROMPT_ADD="${2:-}"

if [ "$STYLE" = "builtin" ] || [ "$STYLE" = "_builtin" ]; then
  echo "builtin is the Pillow-drawn dalmatian -- nothing to generate." >&2
  exit 1
fi

STYLE_DIR="$DATA_DIR/$STYLE"
mkdir -p "$STYLE_DIR"

# reference.png is the character anchor -- one per style. Reuse if present.
REF="$STYLE_DIR/reference.png"

generate_reference() {
  echo "==> generating reference for style '$STYLE'"
  mmx image generate \
    --prompt "${STYLE_PROMPT}plain solid bright neon green background (#00FF00 chroma key green screen), sticker style, single character, full body visible, centered" \
    --aspect-ratio 1:1 --width 512 --height 512 --seed 7777 \
    --out "$REF" >/dev/null
}

generate_one() {
  local name="$1"
  local desc="$2"
  local out="$STYLE_DIR/${name}.png"
  echo "==> [$STYLE] $name"
  mmx image generate \
    --prompt "${STYLE_PROMPT}${desc}, plain solid bright neon green background (#00FF00 chroma key green screen), ${STATE_MODIFIER}" \
    --subject-ref "type=character,image=$REF" \
    --aspect-ratio 1:1 --width 512 --height 512 --seed "${SEED:-42}" \
    --out "$out" >/dev/null
}

strip_white() {
  local src="$1"
  local dst="$2"
  cd "${0%/*}/.."  # ensure .venv/Scripts/python.exe is reachable
  cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
  ./.venv/Scripts/python.exe -m scripts.remove_white_bg "$src" "$dst" >/dev/null
}

declare -A STATES=(
  [happy]="confident smirk, ears perked up, bright cheerful eyes, tongue slightly out"
  [anxious]="worried intense expression, ears pinned back, eyebrows raised, mouth slightly open"
  [sad]="sad droopy eyes, ears flat and drooping, slight frown, slumped posture"
  [sleepy]="sleepy half-closed eyes, gentle relaxed expression, ears drooped, calm"
  [alert]="very alert wide open eyes looking forward, ears upright and forward, focused"
)

if [ ! -f "$REF" ]; then
  generate_reference
fi

for name in "${!STATES[@]}"; do
  generate_one "$name" "${STATES[$name]}"
  strip_white "$STYLE_DIR/${name}.png" "$STYLE_DIR/${name}_alpha.png"
done

echo "Done. Style '$STYLE' at: $STYLE_DIR"
echo "Activate: pet menu -> 形象 -> $STYLE"
