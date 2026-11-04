"""Joins whisperx word timings with pyannote diarization and builds transcript.md with screenshots.

Usage:
  python merge.py <work_dir> <out_dir> --title "..." --date YYYY-MM-DD [--speakers speakers.json] [--language ru]
Expects in work_dir: whisperx/audio.json, diarization.json, frames.json, frames/, [frames_drop.txt].
Writes to out_dir: transcript.md, transcript_with_speakers.txt, screenshots/, raw/.
Headings are in the meeting language (--language, by default the one whisperx recognized).
"""
import argparse
import bisect
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labels import fmt_time as fmt, labels, marker  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("work")
ap.add_argument("out")
ap.add_argument("--title", required=True)
ap.add_argument("--date", required=True)
ap.add_argument("--speakers", default=None)
ap.add_argument("--merge-gap", type=float, default=1.5)
ap.add_argument("--language", default=None)
ap.add_argument("--raw-speakers", action="store_true",
                help="keep diarization labels as they are: no smoothing of short inserts, no boundary fix")
args = ap.parse_args()
work, out = Path(args.work), Path(args.out)


def die(msg):
    sys.exit(f"[transcribe] {msg}")


wx_json = work / "whisperx" / "audio.json"  # the name asr gives it (audio.wav → audio.json)
wx_json = wx_json if wx_json.exists() else None
if wx_json is None or not (work / "diarization.json").exists():
    die(f"{work} has no recognition results (whisperx/*.json, diarization.json) — run asr first")
wx = json.loads(wx_json.read_text(encoding="utf-8"))
turns = sorted(json.loads((work / "diarization.json").read_text(encoding="utf-8")), key=lambda t: t["start"])
names = json.loads(Path(args.speakers).read_text(encoding="utf-8")) if args.speakers else {}
language = args.language or wx.get("language") or "en"
L = labels(language)
turn_starts = [t["start"] for t in turns]
longest = max((t["end"] - t["start"] for t in turns), default=0)


def speaker_at(start, end):
    """The speaker whose turn overlaps [start, end] most; the nearest turn if none does."""
    if not turns:
        return "SPEAKER_00"  # diarization found no speech, but recognition found words
    best, best_ov = None, 0.0
    # only turns that start before `end` and could still reach `start` can overlap
    hi = bisect.bisect_right(turn_starts, end)
    lo = bisect.bisect_left(turn_starts, start - longest)
    for tr in turns[lo:hi]:
        ov = min(end, tr["end"]) - max(start, tr["start"])
        if ov > best_ov:
            best, best_ov = tr["speaker"], ov
    if best is None:
        mid = (start + end) / 2
        k = bisect.bisect_left(turn_starts, mid)
        near = turns[max(0, k - 2):k + 2]
        best = min(near, key=lambda tr: min(abs(tr["start"] - mid), abs(tr["end"] - mid)))["speaker"]
    return best


def segment_words(seg):
    """Words of a segment. When word alignment failed, whisperx keeps the text but no words: spread the words of
    the text evenly over the segment so nothing recognized is lost (their timing is approximate)."""
    ws = seg.get("words") or []
    if any("start" in w for w in ws) or not seg.get("text", "").strip():
        return ws, False
    tokens = seg["text"].split()
    step = (seg["end"] - seg["start"]) / max(len(tokens), 1)
    return [{"word": t, "start": seg["start"] + k * step, "end": seg["start"] + (k + 1) * step}
            for k, t in enumerate(tokens)], True


# 1. A speaker for every word
words = []
prev_spk = None
unaligned = 0
for seg in wx.get("segments", []):
    seg_words, approx = segment_words(seg)
    unaligned += approx
    for w in seg_words:
        if "start" in w:
            spk = speaker_at(w["start"], w["end"])
            prev_spk = spk
        else:  # tokens/numbers without timing go to the previous word
            spk = prev_spk or speaker_at(seg["start"], seg["end"])
            w = {**w, "start": words[-1]["end"] if words else seg["start"], "end": words[-1]["end"] if words else seg["start"]}
        words.append({"start": w["start"], "end": w["end"], "word": w["word"].strip(), "speaker": spk})
words = [w for w in words if w["word"]]
if not words:
    die("no speech was recognized in the recording — check that it has an audio track with voices "
        f"(ffmpeg -i <video>), and the language (--language). Logs: {work}/whisperx.log")
if unaligned:
    print(f"warning: {unaligned} segment(s) without word timings (alignment failed), "
          "their words got approximate times", file=sys.stderr)

# 2. Group words into utterances
utts = []
for w in words:
    if utts and utts[-1]["speaker"] == w["speaker"] and w["start"] - utts[-1]["end"] <= args.merge_gap:
        utts[-1]["end"] = max(utts[-1]["end"], w["end"])
        utts[-1]["words"].append(w)
    else:
        utts.append({"start": w["start"], "end": w["end"], "speaker": w["speaker"], "words": [w]})

# 3. Smoothing: a short insert (≤2 words, <1 s) glued between two utterances of the same speaker is absorbed —
# usually a diarization flicker. A real short answer ("no") comes after a pause, so only gap-free inserts count.
changed = not args.raw_speakers
while changed:
    changed = False
    for i in range(1, len(utts) - 1):
        u, a, b = utts[i], utts[i - 1], utts[i + 1]
        glued = u["start"] - a["end"] < 0.3 and b["start"] - u["end"] < 0.3
        if (len(u["words"]) <= 2 and u["end"] - u["start"] < 1.0 and glued
                and a["speaker"] == b["speaker"] != u["speaker"]):
            a["words"] += u["words"] + b["words"]
            a["end"] = b["end"]
            del utts[i:i + 2]
            changed = True
            break
# 3b. Boundary fix: diarization often switches speakers a little late, so the first words of the next
# speaker's sentence stick to the end of the previous utterance: "...a bone. Did he" | "actually break it?".
# If an utterance ends with 1–3 words after its last sentence end and the next speaker continues in lowercase,
# those words move to the next speaker.
SENT_END = re.compile(r"[.?!…]$")
for a, b in zip(utts, utts[1:]) if not args.raw_speakers else []:
    if a["speaker"] == b["speaker"] or not b["words"][0]["word"][:1].islower() or b["start"] - a["end"] > 1.5:
        continue
    ends = [k for k, w in enumerate(a["words"]) if SENT_END.search(w["word"])]
    if ends and 1 <= len(a["words"]) - 1 - ends[-1] <= 3:
        tail = a["words"][ends[-1] + 1:]
        a["words"] = a["words"][:ends[-1] + 1]
        b["words"] = tail + b["words"]
        a["end"], b["start"] = a["words"][-1]["end"], tail[0]["start"]

for u in utts:
    u["text"] = " ".join(w["word"] for w in u["words"]).replace(" ,", ",").replace(" .", ".")
    u["name"] = names.get(u["speaker"], u["speaker"])

# 4. Screenshots
drop = set((work / "frames_drop.txt").read_text().split()) if (work / "frames_drop.txt").exists() else set()
if not (work / "frames.json").exists():
    print(f"note: no {work}/frames.json — the transcript gets no screenshots (run frames first to add them)",
          file=sys.stderr)
frames = [f for f in json.loads((work / "frames.json").read_text()) if f["file"] not in drop] \
    if (work / "frames.json").exists() else []
shots_dir = out / "screenshots"
shots_dir.mkdir(parents=True, exist_ok=True)
for i, f in enumerate(frames, 1):
    f["name"] = f"{i:02d}_{fmt(f['t']).replace(':', '-')}.jpg"
    Image.open(work / "frames" / f["file"]).convert("RGB").save(shots_dir / f["name"], quality=85)

# 5. Output
duration = max(u["end"] for u in utts)
participants = sorted({u["name"] for u in utts})
md = [f"# {args.title}", "",
      f"{L['date']}: {args.date} | {L['duration']}: {fmt(duration)} | {L['utterances']}: {len(utts)} | "
      f"{L['screenshots']}: {len(frames)}",
      f"{L['speakers']}: {', '.join(participants)}", marker(language), "", f"## {L['transcript']}", ""]
fi = 0
for u in utts:
    while fi < len(frames) and frames[fi]["t"] <= u["start"]:
        md += [f"![{fmt(frames[fi]['t'])}](screenshots/{frames[fi]['name']})", ""]
        fi += 1
    md += [f"### {u['name']} · [{fmt(u['start'])} — {fmt(u['end'])}]", "", u["text"], ""]
while fi < len(frames):
    md += [f"![{fmt(frames[fi]['t'])}](screenshots/{frames[fi]['name']})", ""]
    fi += 1
(out / "transcript.md").write_text("\n".join(md), encoding="utf-8")
(out / "transcript_with_speakers.txt").write_text(
    "\n".join(f"[{fmt(u['start'])}-{fmt(u['end'])}] {u['name']}: {u['text']}" for u in utts) + "\n", encoding="utf-8")

raw = out / "raw"
raw.mkdir(exist_ok=True)
for src in [wx_json, work / "diarization.json", work / "diarization.rttm"]:
    if src.exists():
        shutil.copy(src, raw / src.name)
if args.speakers:
    shutil.copy(args.speakers, raw / "speakers.json")
by_spk = {}
for u in utts:
    by_spk[u["name"]] = by_spk.get(u["name"], 0) + u["end"] - u["start"]
print(f"utterances={len(utts)} frames={len(frames)} duration={fmt(duration)}")
for n, s in sorted(by_spk.items(), key=lambda x: -x[1]):
    print(f"  {n}: {s / 60:.1f} min")
