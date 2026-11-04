"""Crops of the "who is speaking" badge per diarization speaker — to map SPEAKER_xx to names.

Output: work/badges/<SPEAKER>.jpg (crops at the middle of the speaker's longest turns).
--probe T saves the full frame work/badges/probe.png at second T: use it to pick --crop.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video import check_crop  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("work")
ap.add_argument("--crop", default="360:76:1240:0", help="W:H:X:Y of the badge area in the frame")
ap.add_argument("--share-start", type=float, default=70, help="second from which the screen is shared")
ap.add_argument("--per-speaker", type=int, default=6)
ap.add_argument("--probe", type=float, default=None, help="only save the full frame at this second")
args = ap.parse_args()
work = Path(args.work)
out = work / "badges"
out.mkdir(parents=True, exist_ok=True)

if args.probe is not None:
    p = out / "probe.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(args.probe), "-i", args.video, "-frames:v", "1", "-y", str(p)],
                   check=True)
    print(f"probe: {p} size={Image.open(p).size}")
    raise SystemExit

w, h, _, _ = check_crop(args.crop, args.video)
crop = f"crop={args.crop}"
if not (work / "diarization.json").exists():
    sys.exit(f"[transcribe] {work}/diarization.json not found — run asr first")
turns = json.loads((work / "diarization.json").read_text())

for spk in sorted({t["speaker"] for t in turns}):
    own = sorted((t for t in turns if t["speaker"] == spk and t["start"] > args.share_start),
                 key=lambda t: t["start"] - t["end"])[:args.per_speaker]
    tiles = []
    for n, t in enumerate(own):
        mid = (t["start"] + t["end"]) / 2
        p = out / f"{spk}_{n}.png"  # not by second: two turns can share the same int(mid)
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{mid:.2f}", "-i", args.video, "-frames:v", "1",
                        "-vf", crop, "-y", str(p)], check=True)
        tiles.append((p, mid, t["end"] - t["start"]))
    if not tiles:
        print(f"{spk}: no turns after {args.share_start}s")
        continue
    sheet = Image.new("RGB", (w, len(tiles) * (h + 18)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (p, mid, dur) in enumerate(tiles):
        sheet.paste(Image.open(p), (0, i * (h + 18) + 16))
        d.text((2, i * (h + 18) + 2), f"{spk} t={int(mid) // 60:02d}:{int(mid) % 60:02d} dur={dur:.0f}s", fill="black")
        p.unlink()
    sheet.save(out / f"{spk}.jpg", quality=90)
    total = sum(t["end"] - t["start"] for t in turns if t["speaker"] == spk)
    print(f"{spk}: turns={sum(1 for t in turns if t['speaker'] == spk)} speech={total / 60:.1f}min")
