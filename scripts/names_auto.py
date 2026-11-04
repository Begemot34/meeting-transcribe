"""Speaker names for `auto`, without an agent: from the screen and from introductions in the text.

  names_auto.py <video> <work> <out_dir> [--llm claude|codex|opencode|local|api] [--model M]
  → work/names.json {"SPEAKER_00": "Name"} (not written when no name is found)

1. The screen (names_screen.py, OCR on this machine): the "Говорит / Speaking" badge, the active speaker's tile.
2. With --llm, the summary model reads out_dir/transcript_with_speakers.txt (still SPEAKER_xx) for
   self-introductions and addressing (names.py checks every name against the text). It only fills labels the
   screen left empty, and only with names the screen did not give someone else.
A speaker with no clear evidence keeps SPEAKER_xx.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import names  # noqa: E402
import names_screen  # noqa: E402
import summarize  # noqa: E402

log = summarize.log


def text_names(out, work, llm, model):
    lines = (out / "transcript_with_speakers.txt").read_text(encoding="utf-8")
    summarize.RAW = work / "names_answer.md"  # a broken answer lands there
    log(f"looking for introductions in the text with {llm}: the meeting text "
        f"{summarize.where_data_goes(llm, model)}")
    try:
        answer = getattr(summarize, f"run_{llm}")(names.PROMPT, f"=== TRANSCRIPT ===\n{lines}", model)
    except SystemExit as e:  # the backends stop with die(): here that only means no names from the text
        log(f"names from the text skipped: {str(e).removeprefix('[transcribe] ')}")
        return {}
    summarize.save_raw(answer)
    kept, rejected = names.validate(answer, lines)
    for label, why in sorted(rejected.items()):
        log(f"  not taken for {label}: {why}")
    log("names from the text: " + (", ".join(f"{s} → {n}" for s, n in sorted(kept.items())) or "none"))
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("work")
    ap.add_argument("out_dir")
    ap.add_argument("--llm", choices=summarize.BACKENDS)
    ap.add_argument("--model")
    args = ap.parse_args()
    work, out = Path(args.work), Path(args.out_dir)
    dest = work / "names.json"
    dest.unlink(missing_ok=True)

    try:
        found = names_screen.screen_names(args.video, work)
    except Exception as e:  # an audio file has no frames; a broken frame or OCR must not stop the text search
        log(f"names from the screen skipped: {e.__class__.__name__}: {str(e)[-200:]}")
        found = {}
    if args.llm:
        taken = {n.lower() for n in found.values()}
        for label, name in text_names(out, work, args.llm, args.model).items():
            if label in found:
                if found[label].lower() != name.lower():
                    log(f"  {label}: the screen says {found[label]}, the text {name} — keeping the screen")
            elif name.lower() in taken:
                log(f"  {label}: {name} is someone else on the screen — not taken")
            else:
                found[label] = name
                taken.add(name.lower())
    if found:
        dest.write_text(json.dumps(dict(sorted(found.items())), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
