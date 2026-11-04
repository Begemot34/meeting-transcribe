"""Speech recognition on the Apple GPU with mlx-whisper — the fast alternative to whisperx (MT_WHISPER_MODEL=mlx-…).

  asr_mlx.py <audio.wav> <model_dir> <language> <out_dir>

Writes <out_dir>/audio.json in the whisperx format (segments with words), the same file merge.py reads after
whisperx. Word timestamps come from whisper's cross-attention, so there is no separate alignment step. Prints
"Progress: N" lines (percent of audio) for the progress bar of ./transcribe.
"""
import json
import sys
from pathlib import Path

import tqdm


class Progress(tqdm.tqdm):
    """mlx-whisper reports progress through a tqdm bar over audio frames; print whole percents instead."""

    def __init__(self, *args, **kwargs):
        kwargs["disable"] = True
        super().__init__(*args, **kwargs)
        self.frames, self.done, self.shown = kwargs.get("total") or 1, 0, -1

    def update(self, n=1):
        self.done += n
        pct = min(100, int(100 * self.done / self.frames))
        if pct != self.shown:
            self.shown = pct
            print(f"Progress: {pct}", flush=True)


def main():
    audio, model, language, out = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
    tqdm.tqdm = Progress  # mlx_whisper.transcribe looks tqdm.tqdm up at call time
    import mlx_whisper

    print("Transcribing with mlx-whisper", flush=True)
    # condition_on_previous_text=False: with it whisper sometimes repeats one phrase for minutes on long recordings
    r = mlx_whisper.transcribe(audio, path_or_hf_repo=model, language=language, word_timestamps=True,
                               condition_on_previous_text=False, verbose=False)
    segments = []
    for s in r["segments"]:
        words = [{"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3),
                  "score": round(w.get("probability", 0), 3)} for w in s.get("words", []) if w["word"].strip()]
        # a segment without word timings keeps its text: merge spreads the words over the segment
        if words or s["text"].strip():
            segments.append({"start": s["start"], "end": s["end"], "text": s["text"].strip(), "words": words})
    out.mkdir(parents=True, exist_ok=True)
    part = out / "audio.json.part"  # renamed when complete: an interrupted run leaves no half-written result
    part.write_text(json.dumps({"language": language, "segments": segments}, ensure_ascii=False), encoding="utf-8")
    part.replace(out / "audio.json")
    print(f"segments={len(segments)} words={sum(len(s['words']) for s in segments)}", flush=True)


if __name__ == "__main__":
    main()
