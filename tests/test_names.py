"""Speaker names from the text: a name is kept only with a quote that proves it."""
import json

from names import parse, plausible, validate

LINES = "\n".join([
    "[00:00-00:05] SPEAKER_00: Hello everyone, my name is Anna Petrova.",
    "[00:05-00:09] SPEAKER_01: Thank you, Anna. Joanna will join later.",
    "[00:09-00:12] SPEAKER_02: Oleg, your turn.",
    "[00:12-00:20] SPEAKER_01: Sure, I will start.",
])


def answer(**items):
    return "Here you go:\n```json\n" + json.dumps(items) + "\n```"


def test_parse_finds_the_object_in_prose():
    assert parse('Sure! {"SPEAKER_00": {"name": "A"}} done') == {"SPEAKER_00": {"name": "A"}}
    assert parse("no json") == {}
    assert parse("{broken") == {}
    assert parse("[1, 2]") == {}


def test_plausible_names():
    assert plausible("Anna") and plausible("Анна Петрова") and plausible("O'Brien") and plausible("J. R. R. Tolkien")
    assert not plausible("") and not plausible("the speaker from the sales team") and not plausible("Agent 007")


def test_self_introduction_is_kept():
    kept, rejected = validate(answer(SPEAKER_00={"name": "Anna Petrova", "quote": "my name is Anna Petrova"}), LINES)
    assert kept == {"SPEAKER_00": "Anna Petrova"} and not rejected


def test_neighbour_addressing_them_is_kept():
    kept, _ = validate(answer(SPEAKER_01={"name": "Oleg", "quote": "Oleg, your turn"}), LINES)
    assert kept == {"SPEAKER_01": "Oleg"}


def test_whole_words_only():
    kept, rejected = validate(answer(SPEAKER_02={"name": "Anna", "quote": "Joanna"}), LINES)
    assert not kept and "SPEAKER_02" in rejected


def test_quote_must_contain_the_name_and_be_in_the_transcript():
    kept, rejected = validate(answer(SPEAKER_00={"name": "Maria", "quote": "my name is Anna Petrova"},
                                     SPEAKER_02={"name": "Oleg", "quote": "Oleg is great"}), LINES)
    assert not kept and set(rejected) == {"SPEAKER_00", "SPEAKER_02"}


def test_someone_elses_introduction_does_not_name_the_neighbour():
    kept, _ = validate(answer(SPEAKER_01={"name": "Anna Petrova", "quote": "my name is Anna Petrova"}), LINES)
    assert not kept


def test_one_name_for_two_labels_names_nobody():
    kept, rejected = validate(answer(SPEAKER_00={"name": "Anna", "quote": "Anna"},
                                     SPEAKER_01={"name": "Anna", "quote": "Thank you, Anna"}), LINES)
    assert not kept and "claimed for" in rejected["SPEAKER_00"]


def test_unknown_labels_and_junk_are_ignored():
    kept, rejected = validate(answer(SPEAKER_09={"name": "X", "quote": "X"}, SPEAKER_00="Anna"), LINES)
    assert kept == {} and rejected == {}
