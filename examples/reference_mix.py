"""Reference mode: a character image, a motion clip and a beat, in one request.

Local paths are uploaded through /media/uploads first; https URLs are passed as they are.
The reference clip's length is billed like output: an 8 s video with a 5 s reference
clip bills 13 s (13 x $0.039 = $0.507 at 768p).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from minimax_h3 import MiniMaxH3  # noqa: E402

client = MiniMaxH3()
job = client.generate(
    "The skateboarder in @Image 1 rides down the empty plaza with the moves of @Video 1, "
    "landing each trick on the kick drum of @Audio 1. Low wide angle, late afternoon sun.",
    "plaza.mp4",
    duration=8,
    resolution="768p",
    aspect_ratio="16:9",
    reference_images=["skater.png"],                            # @Image 1
    reference_videos=["https://example.com/tricks-5s.mp4"],     # @Video 1, 2-15 s
    reference_audios=["https://example.com/drum-loop.mp3"],     # @Audio 1, needs an image or video beside it
)
usage = job["usage"]
print(f"billed {usage['billed_seconds']} s = {usage['output_seconds']} output + {usage['reference_video_seconds']} reference")
