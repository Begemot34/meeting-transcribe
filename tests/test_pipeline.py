"""merge → export on a synthetic meeting, run as ./transcribe runs them: `python -I`, from another folder, under a
path with a space and non-ASCII letters. The golden files were made by merge.py before its refactor into functions."""
import base64
import hashlib
import re

import pytest
from conftest import GOLDEN, run_script, write_json


def merge(work, out, *extra):
    r = run_script("merge", work, out, "--title", "Synthetic", "--date", "2026-11-04", *extra, cwd=work.parent.parent)
    assert r.returncode == 0, r.stderr
    return r


@pytest.mark.parametrize("variant", ["base", "names", "raw"])
def test_merge_matches_golden(meeting, tmp_path, variant):
    out = meeting.parent / variant
    extra = {"base": [], "raw": ["--raw-speakers"]}.get(variant)
    if variant == "names":
        write_json(tmp_path / "names.json", {"SPEAKER_00": "Anna"})
        extra = ["--speakers", tmp_path / "names.json"]
    r = merge(meeting, out, *extra)
    for name in ("transcript.md", "transcript_with_speakers.txt"):
        assert (out / name).read_text(encoding="utf-8") == (GOLDEN / variant / name).read_text(encoding="utf-8")
    assert "1 segment(s) without word timings" in r.stderr
    assert sorted(p.name for p in (out / "screenshots").iterdir()) == ["01_00-02.jpg", "02_00-12.jpg"]
    assert (out / "raw" / "audio.json").exists()


def test_two_runs_in_one_process_share_no_state(meeting, capsys):
    """merge.main twice in one interpreter: the language of the first run must not leak into the second."""
    import merge as merge_script
    common = ["--title", "Synthetic", "--date", "2026-11-04"]
    merge_script.main([str(meeting), str(meeting.parent / "en"), *common])
    merge_script.main([str(meeting), str(meeting.parent / "ru"), *common, "--language", "ru"])
    en = (meeting.parent / "en" / "transcript.md").read_text(encoding="utf-8")
    ru = (meeting.parent / "ru" / "transcript.md").read_text(encoding="utf-8")
    assert en == (GOLDEN / "base" / "transcript.md").read_text(encoding="utf-8")
    assert "## Транскрипт" in ru and "<!-- meeting-transcribe language=ru -->" in ru
    assert "## Transcript" not in ru


def test_merge_without_recognition_results(tmp_path):
    work = tmp_path / "empty"
    work.mkdir()
    r = run_script("merge", work, tmp_path / "out", "--title", "T", "--date", "D", cwd=tmp_path)
    assert r.returncode != 0
    assert "run asr first" in r.stderr


def test_export_html_and_pdf(meeting):
    out = meeting.parent / "out"
    merge(meeting, out)
    r = run_script("export", out, cwd=meeting.parent.parent)
    assert r.returncode == 0, r.stderr
    page = (out / "out.html").read_text(encoding="utf-8")
    assert page.count('src="data:image/jpeg;base64,') == 2  # self-contained: the screenshots are embedded
    assert "screenshots/" not in re.sub(r"<!--.*?-->", "", page)
    assert "Did he actually break it?" in page
    pdf = (out / "out.pdf").read_bytes()
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_export_escapes_html(meeting):
    out = meeting.parent / "out"
    merge(meeting, out)
    md = out / "transcript.md"
    md.write_text(md.read_text(encoding="utf-8").replace("Hello,", "<script>alert(1)</script>"), encoding="utf-8")
    assert run_script("export", out, "--no-pdf", cwd=out).returncode == 0
    page = (out / "out.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert not (out / "out.pdf").exists()


def test_export_allows_only_its_own_script(meeting):
    """The page's policy names its one script by hash: a script that slipped into the text would not run."""
    out = meeting.parent / "out"
    merge(meeting, out)
    assert run_script("export", out, "--no-pdf", cwd=out).returncode == 0
    page = (out / "out.html").read_text(encoding="utf-8")
    policy = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', page).group(1)
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1 and "default-src 'none'" in policy
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in policy
    assert page.index("Content-Security-Policy") < page.index("<body")


@pytest.mark.parametrize("link", ["../../secret.jpg", "/etc/hosts", "screenshots/link.jpg"])
def test_export_refuses_images_outside_the_folder(meeting, link):
    """The HTML gets shared: a hand-edited path or a symlink must not pull in a file from elsewhere on the disk."""
    out = meeting.parent / "out"
    merge(meeting, out)
    secret = meeting.parent.parent / "secret.jpg"
    secret.write_bytes((out / "screenshots" / "01_00-02.jpg").read_bytes())
    (out / "screenshots" / "link.jpg").symlink_to(secret)
    md = out / "transcript.md"
    md.write_text(md.read_text(encoding="utf-8").replace("screenshots/01_00-02.jpg", link), encoding="utf-8")
    r = run_script("export", out, "--no-pdf", cwd=out)
    assert r.returncode != 0
    assert "image outside" in r.stderr
