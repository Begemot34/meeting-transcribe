"""Diarization with pyannote/speaker-diarization-community-1, audio in RAM.

Audio is passed as a tensor, not a path: the torchcodec decoder cannot find libavutil on some machines.
Output: out_dir/diarization.rttm, out_dir/diarization.json ([{start, end, speaker}]).
"""
import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path

# pyannote warns at import when torchcodec cannot decode files (no system FFmpeg, or no torchcodec at all on Linux
# aarch64); the audio comes in as a tensor, so that decoder is never used
warnings.filterwarnings("ignore", message=r"\s*torchcodec is not installed correctly")

import torch  # noqa: E402
import whisperx  # noqa: E402
from pyannote.audio import Pipeline  # noqa: E402

MODEL = Path(__file__).resolve().parent.parent / "models" / "speaker-diarization-community-1"

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("out_dir")
ap.add_argument("--max-speakers", type=int, default=10)
ap.add_argument("--device", default="cpu",
                help="cpu or cuda; on Apple Silicon cpu means the Apple GPU (MPS) when it is there: ~30× faster, "
                     "same result. MT_DIARIZE_DEVICE=cpu forces the CPU")
args = ap.parse_args()
out_dir = Path(args.out_dir)
out_dir.mkdir(parents=True, exist_ok=True)

t0 = time.time()
waveform = torch.from_numpy(whisperx.load_audio(args.audio))[None]
pipeline = Pipeline.from_pretrained(MODEL)
if pipeline is None:
    sys.exit(f"could not load the diarization model from {MODEL}")
device = os.environ.get("MT_DIARIZE_DEVICE") or args.device
if device == "cpu" and "MT_DIARIZE_DEVICE" not in os.environ and torch.backends.mps.is_available():
    device = "mps"
pipeline.to(torch.device(device))
print(f"device={device}", flush=True)

# progress for the transcribe CLI: "Progress: <percent>" lines in the log.
# Speaker embeddings take most of the time, so they get the widest share.
SHARE = {"segmentation": (0, 20), "speaker_counting": (20, 21), "embeddings": (21, 97), "discrete_diarization": (97, 100)}
shown = -1


def hook(step_name, step_artifact, file=None, total=None, completed=None):
    global shown
    lo, hi = SHARE.get(step_name, (shown, shown))
    pct = int(lo + (hi - lo) * (completed / total if total else 1))
    if pct > shown:
        shown = pct
        print(f"Progress: {pct}", flush=True)


audio = {"waveform": waveform, "sample_rate": 16000}
try:
    result = pipeline(audio, max_speakers=args.max_speakers, hook=hook)
except RuntimeError as e:  # an op the Apple GPU does not support in some torch version: the CPU always works
    if device != "mps":
        raise
    print(f"MPS failed ({str(e)[:200]}), running on the CPU", flush=True)
    shown = -1
    pipeline.to(torch.device("cpu"))
    result = pipeline(audio, max_speakers=args.max_speakers, hook=hook)
annotation = getattr(result, "speaker_diarization", result)

turns = [
    {"start": round(seg.start, 3), "end": round(seg.end, 3), "speaker": speaker}
    for seg, _, speaker in annotation.itertracks(yield_label=True)
]
(out_dir / "diarization.json").write_text(json.dumps(turns, ensure_ascii=False, indent=1))
with open(out_dir / "diarization.rttm", "w") as f:
    annotation.write_rttm(f)
print(f"turns={len(turns)} speakers={sorted({t['speaker'] for t in turns})} time={time.time() - t0:.0f}s")
