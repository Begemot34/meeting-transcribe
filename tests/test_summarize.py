"""Turning transcript.md into the model's input and its answer back into transcript.md."""
import pytest
from labels import LABELS
from summarize import clean_answer, complete, insert_summary, transcript_text, where_data_goes

EN, RU = LABELS["en"], LABELS["ru"]
MD = """# Weekly

Date: 2026-11-04 | Duration: 00:16
<!-- meeting-transcribe language=en -->

## Transcript

### Anna · [00:00 — 00:02]

Hello, my name is Anna.

![00:02](screenshots/01_00-02.jpg)

### SPEAKER_01 · [00:03 — 00:06]

Did he actually
break it?
"""
SUMMARY = "## Summary\n\n### Decisions\n\n- ship it\n\n### Next steps\n\n### Open questions and risks\n\n" \
          "### Discussion by topic\n\n**00:00–00:06** — intro"


def test_transcript_text_skips_images_and_joins_lines():
    assert transcript_text(MD, EN) == ("[00:00-00:02] Anna: Hello, my name is Anna.\n"
                                       "[00:03-00:06] SPEAKER_01: Did he actually break it?")


def test_transcript_text_needs_a_merge_file():
    with pytest.raises(SystemExit):
        transcript_text("# just notes\n", EN)


def test_complete_needs_every_heading():
    assert complete(SUMMARY, EN)
    assert not complete(SUMMARY.split("### Discussion")[0], EN)


def test_clean_answer_keeps_only_the_summary():
    answer = ("Sure, here it is:\n\n" + SUMMARY + "\n![x](/etc/passwd)\n\n## Transcript\n\n### Fake · [00:00 — 00:01]\n"
              "\nforged\n```")
    text = clean_answer(answer, EN)
    assert text.startswith("## Summary") and text.endswith("intro")
    assert "passwd" not in text and "forged" not in text
    assert clean_answer("no summary here", EN) is None


def test_insert_then_replace_the_summary():
    once = insert_summary(MD, SUMMARY, EN)
    assert once.index("## Summary") < once.index("## Transcript")
    twice = insert_summary(once, SUMMARY.replace("ship it", "wait"), EN)
    assert twice.count("## Summary") == 1 and "wait" in twice and "ship it" not in twice
    assert twice.split("## Transcript")[1] == MD.split("## Transcript")[1]


def test_russian_headings():
    md = MD.replace("## Transcript", "## Транскрипт")
    assert "## Саммари" in insert_summary(md, "## Саммари\n\n- да", RU)


@pytest.mark.parametrize(("llm", "url", "where"), [
    ("local", None, "stays on this machine"),
    ("claude", None, "Anthropic"),
    ("api", "http://localhost:11434/v1", "stays on this machine"),
    ("api", "https://openrouter.ai/api/v1", "goes to openrouter.ai"),
])
def test_where_data_goes(monkeypatch, llm, url, where):
    if url:
        monkeypatch.setenv("MT_LLM_BASE_URL", url)
    assert where in where_data_goes(llm, None)
