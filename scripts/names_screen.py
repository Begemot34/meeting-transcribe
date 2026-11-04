"""Speaker names from the screen: text that shows up while one speaker talks and not while the others do.

  names_screen.py <video> <work>   → {"SPEAKER_00": "Name"} on stdout, details in work/names_screen.log

Meeting clients show who is talking: a "Говорит: Name" / "Speaking: Name" badge, the name under the big video
of the active speaker. For each diarization speaker the script reads the text (OCR, on this machine) of frames
in the middle of their longest turns and looks for a name that is on almost all of their frames and on almost
none of the others'. Names that are always on screen — gallery tiles, the person sharing the screen — are on
everyone's frames and drop out by themselves. Nothing is assigned when the evidence is weak.

A name right under a "Говорит" / "Speaking" label is strong evidence and is enough on two frames; any other
text needs three frames, a stronger contrast and the look of a full name (2–3 words in one alphabet).

OCR: Apple Vision on macOS, EasyOCR elsewhere.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

PER_SPEAKER = 10      # frames per speaker
MIN_TURN = 2.5        # seconds: shorter turns are too often mislabeled by diarization
LAG = 1.0             # clients switch the badge about a second after the voice
MIN_FRAMES = 3        # fewer frames of a speaker: no decision…
MIN_BADGE_FRAMES = 2  # …unless the name stands under a "Говорит" label
SEEN = 0.5            # a badge name is on at least this share of the speaker's frames…
SEEN_TEXT = 0.7       # (any other text: this share, and it must look like "First Last")
ELSEWHERE = 0.25      # …and on at most this share of the other speakers' frames
LIFT = 0.4            # and the difference is at least this
MARGIN = 0.2          # the runner-up is at least this much weaker, or the speaker stays unnamed

LABEL = r"(?:сейчас\s+)?(?:говорит|говорят|speaking|talking|is speaking|presenting)"
PREFIX = re.compile(rf"^{LABEL}\s*[:\-–—]?\s*", re.I)
LABEL_ONLY = re.compile(rf"^{LABEL}\s*[:\-–—]?$", re.I)
WORD = re.compile(r"^[A-ZА-ЯЁ][a-zа-яё]+(?:-[A-ZА-ЯЁ]?[a-zа-яё]+)?\.?$")
NOT_NAMES = {"говорит", "speaking", "talking", "share", "layers", "pages", "chat", "участники", "participants",
             "mute", "unmute", "record", "recording", "запись", "чат"}


def log_to(path):
    f = open(path, "w", encoding="utf-8")

    def log(msg):
        f.write(msg + "\n")
        f.flush()
    return log


def name_like(text):
    text = PREFIX.sub("", " ".join(text.replace("•", " ").split())).strip(" :·|")
    words = text.split()
    if 1 <= len(words) <= 3 and len(text) <= 40 and all(WORD.match(w) for w in words) \
            and text.lower() not in NOT_NAMES and (len(words) > 1 or len(text) >= 3):
        return text
    return None


CYR = re.compile(r"^[А-ЯЁа-яё.-]+$")
LAT = re.compile(r"^[A-Za-z.-]+$")


def full_name(text):
    """2–3 words, all Cyrillic or all Latin: "Cосить Gemini" (a Latin C, a product name) is not a person."""
    words = text.split()
    return 2 <= len(words) <= 3 and (all(CYR.match(w) for w in words) or all(LAT.match(w) for w in words))


def candidates(lines):
    """lines: [(text, x, y, height)] with x, y of the top-left corner as shares of the frame.

    → (name-like strings on the frame, the ones of them that are a "Говорит" badge)."""
    out, badge = set(), set()
    labels = [(x, y, h) for text, x, y, h in lines if LABEL_ONLY.match(text.strip())]
    for text, x, y, h in lines:
        name = name_like(text)
        if not name:
            continue
        out.add(name)
        if PREFIX.match(text.strip()) and PREFIX.sub("", text.strip()):
            badge.add(name)  # "Говорит: Name" in one line
        elif any(abs(x - lx) < 0.03 and 0 < y - ly < 2.5 * max(lh, h) for lx, ly, lh in labels):
            badge.add(name)  # the label line, the name right under it
    return out, badge


def ocr_vision():
    import Vision
    from Foundation import NSURL

    def read(path):
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLanguages_(["ru-RU", "en-US", "uk-UA", "de-DE", "fr-FR", "es-ES"])
        req.setRecognitionLevel_(0)  # accurate
        req.setUsesLanguageCorrection_(False)  # names are not dictionary words
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
        handler.performRequests_error_([req], None)
        out = []
        for r in req.results() or []:
            box = r.boundingBox()  # shares of the frame, origin at the bottom left
            out.append((r.topCandidates_(1)[0].string(), box.origin.x, 1 - box.origin.y - box.size.height,
                        box.size.height))
        return out
    return read


def ocr_easyocr():
    import easyocr
    # the detector and the Cyrillic+Latin reader (~90 MB) come from github.com on the first run, into
    # ~/.EasyOCR or MT_EASYOCR_DIR
    reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False,
                            model_storage_directory=os.environ.get("MT_EASYOCR_DIR"))

    def read(path):
        import cv2
        hh, ww = cv2.imread(str(path)).shape[:2]
        out = []
        for box, text, conf in reader.readtext(str(path)):
            if conf >= 0.3:
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                out.append((text, min(xs) / ww, min(ys) / hh, (max(ys) - min(ys)) / hh))
        return out
    return read


def ocr_engine():
    if sys.platform == "darwin":
        try:
            return ocr_vision(), "Apple Vision"
        except ImportError:
            pass
    try:
        return ocr_easyocr(), "EasyOCR"
    except Exception as e:  # not installed, no network for the first download, a broken model file
        return None, f"no OCR ({e.__class__.__name__}: {e})"


def pick_times(turns):
    """Up to PER_SPEAKER moments per speaker: the middle of their longest turns, spread over the meeting."""
    by = defaultdict(list)
    for t in turns:
        s, e = t["start"], t["end"]
        if e - s < MIN_TURN:
            continue
        # skip turns overlapped by someone else: the badge may show either of them
        if any(o is not t and o["speaker"] != t["speaker"] and o["start"] < e and o["end"] > s for o in turns):
            continue
        by[t["speaker"]].append(t)
    times = {}
    for spk, ts in by.items():
        ts = sorted(ts, key=lambda t: t["end"] - t["start"], reverse=True)[:PER_SPEAKER * 3]
        ts = sorted(ts, key=lambda t: t["start"])
        step = max(1, len(ts) // PER_SPEAKER)
        times[spk] = [min(t["end"] - 0.3, (t["start"] + t["end"]) / 2 + LAG) for t in ts[::step][:PER_SPEAKER]]
    return times


def has_video(video):
    """A video stream that is not just cover art (an m4a or mp3 often carries an "attached pic")."""
    info = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
    return any("Video:" in line and "attached pic" not in line for line in info.splitlines())


def frame(video, t, path):
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-y", str(path)],
                   check=True)


def contrast(seen, s, kind):
    """[(lift, p, q, name)] for speaker s, best first: p — share of their frames with the name, q — of the others'."""
    counts = {o: Counter(c for f in frames for c in f[kind]) for o, frames in seen.items()}
    others = sum(len(seen[o]) for o in seen if o != s) or 1
    scored = []
    for c, n in counts[s].items():
        p = n / len(seen[s])
        q = sum(counts[o][c] for o in seen if o != s) / others
        scored.append((p - q, p, q, c))
    return sorted(scored, reverse=True)


def top3(scored):
    """The three best candidates for the report: "name seen-here/seen-elsewhere"."""
    return ", ".join(f"{c} {p:.0%}/{q:.0%}" for _, p, q, c in scored[:3]) or "—"


def decide(seen):
    """seen: {speaker: [(names, badge names) per frame]} → {speaker: name}, plus a report line per speaker."""
    best = {}
    report = []
    for s in sorted(seen):
        n = len(seen[s])
        if n < MIN_BADGE_FRAMES:
            report.append(f"{s}: {n} frames — too few")
            continue
        badge = contrast(seen, s, 1)
        others = sum(len(seen[o]) for o in seen if o != s)
        # text that is not a badge needs counterexamples: with no other speaker's frames, a title that is always on
        # screen would look like a name
        text = contrast(seen, s, 0) if n >= MIN_FRAMES and others >= MIN_FRAMES else []
        report.append(f"{s} ({n} frames): badge {top3(badge)}; text {top3(text)}")
        text = [x for x in text if full_name(x[3])]
        for scored, seen_min in ((badge, SEEN), (text, SEEN_TEXT)):
            if scored and scored[0][1] >= seen_min and scored[0][2] <= ELSEWHERE and scored[0][0] >= LIFT:
                if len(scored) > 1 and scored[0][0] - scored[1][0] < MARGIN:
                    report.append(f"{s}: {scored[0][3]} or {scored[1][3]} — unclear, left unnamed")
                    break
                best[s] = (scored[0][3], scored[0][0])
                break
    # one name — one speaker: the clearly strongest claim keeps it, a close call names nobody
    names = {}
    by_name = {}
    for s, (c, lift) in best.items():
        by_name.setdefault(c, []).append((lift, s))
    for c, claims in by_name.items():
        claims.sort(reverse=True)
        if len(claims) > 1 and claims[0][0] - claims[1][0] < MARGIN:
            report.append(f"{c}: claimed by {', '.join(s for _, s in claims)} — left unnamed")
            continue
        names[claims[0][1]] = c
        for _, s in claims[1:]:
            report.append(f"{s}: {c} is {claims[0][1]}")
    return names, report


def screen_names(video, work):
    """{speaker: name} found on the screen; the details go to work/names_screen.log."""
    work = Path(work)
    log = log_to(work / "names_screen.log")
    turns = json.loads((work / "diarization.json").read_text())
    if not has_video(video):
        print("[transcribe] names from the screen skipped: the recording has no video", file=sys.stderr)
        return {}
    read, engine = ocr_engine()
    log(f"OCR: {engine}")
    if read is None:
        print(f"[transcribe] names from the screen skipped: {engine}", file=sys.stderr)
        return {}
    times = pick_times(turns)
    seen = {}
    with tempfile.TemporaryDirectory() as tmp:
        for spk, ts in times.items():
            seen[spk] = []
            for i, t in enumerate(ts):
                p = Path(tmp) / f"{spk}_{i}.png"
                frame(video, t, p)
                found, badge = candidates(read(p))
                seen[spk].append((found, badge))
                log(f"{spk} {t:7.1f}s: badge {sorted(badge)}; {sorted(found - badge)}")
    names, report = decide(seen)
    for line in report:
        log(line)
    shown = ", ".join(f"{s} → {n}" for s, n in sorted(names.items())) or "none"
    print(f"[transcribe] names from the screen ({engine}): {shown}", file=sys.stderr)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("work")
    args = ap.parse_args()
    print(json.dumps(screen_names(args.video, args.work), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
