"""Text to video: a vertical 6-second 2K clip from a prompt alone."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from minimax_h3 import MiniMaxH3, estimate_cost  # noqa: E402

PROMPT = (
    "Handheld close-up of a street vendor flipping a scallion pancake on a hot griddle at night. "
    "Steam rises through the neon light, oil crackles, the vendor glances at the camera and smiles. "
    "Shallow depth of field, warm tungsten light, natural market noise."
)

print(f"estimated cost: ${estimate_cost(6, '2k'):.3f}")  # 6 s x $0.064
job = MiniMaxH3().generate(PROMPT, "pancake.mp4", duration=6, resolution="2k", aspect_ratio="9:16")
print(job["usage"])
