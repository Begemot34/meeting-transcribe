"""Speaker assignment, grouping and the two diarization corrections in merge.py."""
from conftest import utt
from merge import (Speakers, absorb_flickers, assign_speakers, build_utterances, fix_boundaries, group_utterances,
                   segment_words, transcript_md)

TURNS = [{"start": 0, "end": 5, "speaker": "SPEAKER_00"}, {"start": 5, "end": 10, "speaker": "SPEAKER_01"}]


def texts(utts):
    return [(u["speaker"], " ".join(w["word"] for w in u["words"])) for u in utts]


def test_speaker_with_the_largest_overlap():
    sp = Speakers(TURNS)
    assert sp.at(4.0, 5.5) == "SPEAKER_00"
    assert sp.at(4.8, 6.0) == "SPEAKER_01"


def test_speaker_of_the_nearest_turn_in_silence():
    sp = Speakers([{"start": 0, "end": 1, "speaker": "A"}, {"start": 10, "end": 11, "speaker": "B"}])
    assert sp.at(2.0, 2.5) == "A"
    assert sp.at(8.5, 9.0) == "B"


def test_no_turns_gives_one_speaker():
    assert Speakers([]).at(1, 2) == "SPEAKER_00"


def test_unsorted_turns():
    assert Speakers(list(reversed(TURNS))).at(6, 7) == "SPEAKER_01"


def test_failed_alignment_spreads_the_text():
    words, approx = segment_words({"start": 10.0, "end": 12.0, "text": " one two ", "words": []})
    assert approx
    assert [(w["word"], w["start"], w["end"]) for w in words] == [("one", 10.0, 11.0), ("two", 11.0, 12.0)]


def test_aligned_segment_keeps_its_words():
    seg = {"start": 0, "end": 1, "text": "hi", "words": [{"word": "hi", "start": 0, "end": 1}]}
    assert segment_words(seg) == (seg["words"], False)


def test_untimed_token_goes_to_the_previous_word():
    seg = {"start": 0, "end": 7, "text": "a 2026",
           "words": [{"word": "a", "start": 6, "end": 6.5}, {"word": "2026"}]}
    words, unaligned = assign_speakers([seg], Speakers(TURNS))
    assert unaligned == 0
    assert [(w["word"], w["speaker"], w["start"]) for w in words] == [("a", "SPEAKER_01", 6), ("2026", "SPEAKER_01", 6.5)]


def test_grouping_splits_on_a_long_pause_and_a_new_speaker():
    words = [{"word": "a", "start": 0, "end": 1, "speaker": "A"}, {"word": "b", "start": 2, "end": 3, "speaker": "A"},
             {"word": "c", "start": 6, "end": 7, "speaker": "A"}, {"word": "d", "start": 7, "end": 8, "speaker": "B"}]
    assert texts(group_utterances(words, 1.5)) == [("A", "a b"), ("A", "c"), ("B", "d")]


def test_glued_short_insert_is_absorbed():
    utts = [utt("A", 0, 2, "so", "we"), utt("B", 2.1, 2.5, "yes"), utt("A", 2.6, 4, "agreed", "then")]
    absorb_flickers(utts)
    assert texts(utts) == [("A", "so we yes agreed then")]
    assert utts[0]["end"] == 4


def test_short_answer_after_a_pause_stays():
    utts = [utt("A", 0, 2, "ready?"), utt("B", 3, 3.4, "no"), utt("A", 4, 5, "ok")]
    absorb_flickers(utts)
    assert texts(utts) == [("A", "ready?"), ("B", "no"), ("A", "ok")]


def test_sentence_start_moves_to_the_next_speaker():
    utts = [utt("A", 0, 3, "a", "bone.", "Did", "he"), utt("B", 3.1, 5, "actually", "break", "it?")]
    fix_boundaries(utts)
    assert texts(utts) == [("A", "a bone."), ("B", "Did he actually break it?")]
    assert utts[0]["end"] == utts[0]["words"][-1]["end"]
    assert utts[1]["start"] == utts[1]["words"][0]["start"]


def test_capitalized_continuation_is_a_new_sentence():
    utts = [utt("A", 0, 3, "a", "bone.", "Did", "he"), utt("B", 3.1, 5, "Actually", "no.")]
    fix_boundaries(utts)
    assert texts(utts) == [("A", "a bone. Did he"), ("B", "Actually no.")]


def test_raw_speakers_turns_off_both_corrections():
    timed = [("so", 0.0), ("we", 0.3), ("yes", 0.6), ("agreed.", 0.9), ("Did", 1.2), ("he", 1.5), ("go?", 1.8)]
    segs = [{"start": 0, "end": 2, "text": "", "words": [{"word": w, "start": s, "end": s + 0.2} for w, s in timed]}]
    turns = [{"start": 0, "end": 0.55, "speaker": "A"}, {"start": 0.55, "end": 0.85, "speaker": "B"},
             {"start": 0.85, "end": 1.45, "speaker": "A"}, {"start": 1.45, "end": 3, "speaker": "B"}]
    raw, _ = build_utterances(segs, turns, {}, raw_speakers=True)
    fixed, _ = build_utterances(segs, turns, {})
    assert [(u["speaker"], u["text"]) for u in raw] == [("A", "so we"), ("B", "yes"), ("A", "agreed. Did"),
                                                        ("B", "he go?")]
    assert [(u["speaker"], u["text"]) for u in fixed] == [("A", "so we yes agreed."), ("B", "Did he go?")]


def test_names_and_punctuation_in_the_text():
    segs = [{"start": 0, "end": 2, "text": "", "words": [{"word": "Hi", "start": 0, "end": 0.5},
                                                         {"word": ",", "start": 0.5, "end": 0.6},
                                                         {"word": "all", "start": 0.7, "end": 1}]}]
    utts, _ = build_utterances(segs, TURNS, {"SPEAKER_00": "Anna"})
    assert [(u["name"], u["text"]) for u in utts] == [("Anna", "Hi, all")]


def test_screenshots_stand_before_the_next_utterance():
    utts = [{**utt("A", 0, 2, "a"), "name": "A", "text": "a"}, {**utt("B", 5, 6, "b"), "name": "B", "text": "b"}]
    frames = [{"t": 3, "name": "01.jpg"}, {"t": 9, "name": "02.jpg"}]
    md = transcript_md(utts, frames, "T", "2026-01-01", "ru")
    assert md[0] == "# T"
    assert "## Транскрипт" in md
    body = [ln for ln in md if ln.startswith(("###", "!["))]
    assert body == ["### A · [00:00 — 00:02]", "![00:03](screenshots/01.jpg)", "### B · [00:05 — 00:06]",
                    "![00:09](screenshots/02.jpg)"]
