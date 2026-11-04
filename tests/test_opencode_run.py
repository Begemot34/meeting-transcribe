"""Reading `opencode run --format json` events: only a normally finished text answer counts."""
import json

import pytest
from opencode_run import Failed, answer, parse

M = "openrouter/some-model"


def events(*items):
    return parse("\n".join(json.dumps(e) for e in items) + "\nnot json\n")


def text(msg, part_id, s):
    return {"type": "text", "part": {"messageID": msg, "id": part_id, "text": s}}


def finish(msg, reason="stop"):
    return {"type": "step_finish", "part": {"messageID": msg, "reason": reason}}


def test_text_of_the_last_step():
    evs = events(text("m1", "p0", "thinking"), finish("m1", "tool-calls"),
                 text("m2", "p1", "## Sum"), text("m2", "p1", "## Summary"), text("m2", "p2", "more"), finish("m2"))
    assert answer(evs, 0, [], M) == "## Summary\nmore"


@pytest.mark.parametrize(("evs", "code", "says"), [
    ([{"type": "error", "error": {"name": "APIError", "data": {"message": "quota"}}}], 0, "quota"),
    ([{"type": "tool_use"}], 0, "tried to use a tool"),
    ([text("m", "p", "x"), finish("m")], 1, "exit code 1"),
    ([text("m", "p", "x")], 0, "gave no answer"),
    ([text("m", "p", "x"), finish("m", "length")], 0, "stopped with 'length'"),
    ([finish("m")], 0, "gave no answer"),
])
def test_failures(evs, code, says):
    with pytest.raises(Failed, match=says):
        answer(evs, code, ["tail"], M)
