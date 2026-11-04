"""One "who is speaking" badge crop per second for a disputed fragment, in a 4-column grid.

Output: work/strip_<start>.jpg. The badge usually lags the speech by about a second.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video import check_crop  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("work")
ap.add_argument("start", type=float)
ap.add_argument("--duration", type=int, default=40)
ap.add_argument("--crop", default="360:76:1240:0")
args = ap.parse_args()
check_crop(args.crop, args.video)
out = Path(args.work) / f"strip_{int(args.start)}.jpg"
out.parent.mkdir(parents=True, exist_ok=True)
rows = (args.duration + 3) // 4
subprocess.run(["ffmpeg", "-v", "error", "-ss", str(args.start), "-t", str(args.duration), "-i", args.video,
                "-vf", f"fps=1,crop={args.crop},tile=4x{rows}", "-frames:v", "1", "-y", str(out)], check=True)
print(f"strip: {out} (seconds left to right, top to bottom, starting at {int(args.start)})")
