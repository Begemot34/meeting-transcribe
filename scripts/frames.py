"""Screenshot candidates: ffmpeg scene changes → full frame → dhash dedup → contact sheets to review.

Output: work/frames/<secs>.png (kept), work/frames.json ([{t, file}]), work/contact_NN.jpg.
On a static share (scrolling a document) threshold 0.3 yields few frames — use 0.08.
"""
import argparse
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path

from PIL import Image, ImageDraw

warnings.simplefilter("ignore", DeprecationWarning)

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("work")
ap.add_argument("--threshold", type=float, default=0.3, help="ffmpeg scene change threshold")
ap.add_argument("--min-gap", type=float, default=15, help="minimum seconds between frames")
args = ap.parse_args()
video, out = args.video, Path(args.work)
frames_dir = out / "frames"
frames_dir.mkdir(parents=True, exist_ok=True)


def done(kept):
    (out / "frames.json").write_text(json.dumps(kept, indent=1))
    for old in out.glob("contact_*.jpg"):  # sheets of a previous run
        old.unlink()


probe = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-map", "0:v:0?", "-frames:v", "1", "-f", "null", "-"],
                       capture_output=True, text=True)
NO_VIDEO = ("does not contain any stream", "matches no streams")
if any(m in probe.stderr for m in NO_VIDEO):
    print("no video track: no screenshots")
    done([])
    sys.exit(0)
if probe.returncode:
    sys.exit(f"[transcribe] ffmpeg cannot read {video}: {probe.stderr.strip()[-300:]}")
r = subprocess.run(
    ["ffmpeg", "-v", "info", "-i", video, "-map", "0:v:0?", "-vf",
     f"fps=1,select='gt(scene,{args.threshold})',showinfo", "-an", "-f", "null", "-"],
    capture_output=True, text=True)
if any(m in r.stderr for m in NO_VIDEO):
    print("no video track: no screenshots")
    done([])
    sys.exit(0)
if r.returncode:
    sys.exit(f"[transcribe] ffmpeg failed on {video}: {r.stderr.strip()[-300:]}")
times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
if not times:  # one static screen the whole meeting: keep one frame of it
    times = [5.0]
print(f"candidates={len(times)}")


def dhash(img, size=16):
    g = img.convert("L").resize((size + 1, size))
    px = list(g.getdata())
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (px[r * (size + 1) + c] > px[r * (size + 1) + c + 1])
    return bits


def grab(t):
    path = frames_dir / f"{int(t):04d}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", video, "-frames:v", "1", "-y", str(path)],
                   capture_output=True)  # past the end of a short clip there is simply no frame
    return path


kept, last_t, hashes = [], -1e9, []
for t in times:
    if t - last_t < args.min_gap:  # too close to the previous kept frame: not even extracted
        continue
    path = grab(t)
    if not path.exists():
        continue
    h = dhash(Image.open(path))
    if any(bin(h ^ h2).count("1") <= 10 for h2 in hashes[-5:]):
        path.unlink()
        continue
    hashes.append(h)
    last_t = t
    kept.append({"t": t, "file": path.name})
print(f"kept={len(kept)}")
done(kept)

cols, tw, th, per_sheet = 4, 400, 225, 20
for s in range(0, len(kept), per_sheet):
    chunk = kept[s:s + per_sheet]
    rows = (len(chunk) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 18)), "white")
    d = ImageDraw.Draw(sheet)
    for i, k in enumerate(chunk):
        x, y = (i % cols) * tw, (i // cols) * (th + 18)
        sheet.paste(Image.open(frames_dir / k["file"]).resize((tw, th)), (x, y + 18))
        d.text((x + 4, y + 2), f"{k['file']}  {int(k['t']) // 60:02d}:{int(k['t']) % 60:02d}", fill="black")
    sheet.save(out / f"contact_{s // per_sheet:02d}.jpg", quality=80)
print(f"sheets={(len(kept) + per_sheet - 1) // per_sheet}")
