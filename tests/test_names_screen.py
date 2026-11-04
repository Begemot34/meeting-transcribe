"""Names from the "who is speaking" badge: OCR lines → candidates → a decision per speaker."""
from names_screen import candidates, decide, full_name, name_like


def test_name_like():
    assert name_like("Говорит: Анна Петрова") == "Анна Петрова"
    assert name_like("Speaking: Oleg") == "Oleg"
    assert name_like("Al") is None  # one short word
    assert name_like("Quarterly results for the whole team") is None


def test_full_name_is_one_alphabet():
    assert full_name("Анна Петрова") and full_name("Oleg Ivanov")
    assert not full_name("Oleg") and not full_name("Cосить Gemini")  # a Latin C in a Cyrillic word


def test_badge_in_one_line_or_under_the_label():
    lines = [("Speaking: Anna Petrova", 0.1, 0.0, 0.03), ("Speaking", 0.6, 0.0, 0.03), ("Oleg Ivanov", 0.6, 0.04, 0.03),
             ("Roadmap", 0.3, 0.5, 0.05)]
    found, badge = candidates(lines)
    assert {"Anna Petrova", "Oleg Ivanov", "Roadmap"} <= found
    assert badge == {"Anna Petrova", "Oleg Ivanov"}


def frames(n, names=(), badge=()):
    return [(set(names), set(badge)) for _ in range(n)]


def test_badge_seen_only_while_they_speak():
    seen = {"SPEAKER_00": frames(6, ["Anna Petrova"], ["Anna Petrova"]),
            "SPEAKER_01": frames(6, ["Oleg Ivanov"], ["Oleg Ivanov"])}
    names, _ = decide(seen)
    assert names == {"SPEAKER_00": "Anna Petrova", "SPEAKER_01": "Oleg Ivanov"}


def test_a_title_always_on_screen_is_not_a_name():
    seen = {"SPEAKER_00": frames(6, ["Quarterly Review"]), "SPEAKER_01": frames(6, ["Quarterly Review"])}
    names, _ = decide(seen)
    assert names == {}


def test_too_few_frames():
    names, report = decide({"SPEAKER_00": frames(1, ["Anna Petrova"], ["Anna Petrova"])})
    assert names == {} and "too few" in report[0]


def test_close_runner_up_leaves_the_speaker_unnamed():
    seen = {"SPEAKER_00": frames(3, ["Anna Petrova", "Oleg Ivanov"], ["Anna Petrova", "Oleg Ivanov"]) +
            frames(3, ["Anna Petrova"], ["Anna Petrova"]) + frames(2, ["Oleg Ivanov"], ["Oleg Ivanov"]),
            "SPEAKER_01": frames(6)}
    names, report = decide(seen)
    assert names == {} and any("unclear" in r for r in report)
