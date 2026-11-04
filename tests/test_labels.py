"""Headings in the meeting language and time formats shared by merge, export and summarize."""
import pytest
from labels import detect, fmt_time, labels, marker, meeting_language, to_sec


@pytest.mark.parametrize(("sec", "text"), [(0, "00:00"), (59.9, "00:59"), (61, "01:01"), (3599, "59:59"),
                                           (3600, "1:00:00"), (3725, "1:02:05")])
def test_time_round_trip(sec, text):
    assert fmt_time(sec) == text
    assert to_sec(text) == int(sec)


def test_unknown_language_gets_english_headings():
    assert labels("de") is labels("en")


def test_marker_tells_the_meeting_language():
    md = f"# T\n{marker('de')}\n\n## Transcript\n"
    assert meeting_language(md) == "de"
    assert detect(md) == "en"  # German meeting, English headings


def test_older_files_without_marker():
    assert detect("# T\n\n## Транскрипт\n") == "ru"
    assert meeting_language("# T\n\n## Транскрипт\n") == "ru"
    assert detect("no headings") == "en"
