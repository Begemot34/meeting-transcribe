"""Helpers for the badge crops: frame size of a video and a checked --crop W:H:X:Y."""
import re
import subprocess
import sys


def frame_size(video):
    """(width, height) of the first video stream, or None."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", video], capture_output=True, text=True)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr)
    return (int(m.group(1)), int(m.group(2))) if m else None


def check_crop(crop, video):
    """W:H:X:Y as ints; exits with a hint when it is malformed or does not fit the frame."""
    m = re.fullmatch(r"(\d+):(\d+):(\d+):(\d+)", crop)
    if not m:
        sys.exit(f"[transcribe] --crop must be W:H:X:Y in pixels, e.g. 360:76:1240:0 (got {crop!r})")
    w, h, x, y = map(int, m.groups())
    size = frame_size(video)
    if size is None:
        sys.exit(f"[transcribe] {video} has no video track: there is no badge to crop")
    if w == 0 or h == 0 or x + w > size[0] or y + h > size[1]:
        sys.exit(f"[transcribe] --crop {crop} does not fit the {size[0]}x{size[1]} frame of {video}: "
                 "save a frame with `badges <video> <work> --probe <second>` and pick the badge area on it")
    return w, h, x, y
