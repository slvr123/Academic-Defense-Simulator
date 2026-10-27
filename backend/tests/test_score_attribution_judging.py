"""M7 J4's scorer on fake label files: every branch, the conservative handling of
"?", and sheets it must refuse to score."""

from __future__ import annotations

import json
import re

import pytest

from scripts.score_attribution_judging import (
    BACK_TO_DESIGN,
    FLAG_ON,
    RARE,
    JudgingError,
    main,
    parse_labels,
    score,
)


def _key(before_grounded: int = 20, after_grounded: int = 20) -> dict[str, dict]:
    """20 before, 20 after and 12 control items, ids in that order."""
    arms = ["before"] * 20 + ["after"] * 20 + ["control"] * 12
    grounded = {"before": before_grounded, "after": after_grounded, "control": 12}
    seen = dict.fromkeys(grounded, 0)
    key = {}
    for n, arm in enumerate(arms, start=1):
        key[f"{n:02d}"] = {"arm": arm, "grounded": seen[arm] < grounded[arm]}
        seen[arm] += 1
    return key


def _sheet(before: list[str], after: list[str], control: list[str]) -> str:
    """A sheet in the probe's format, each arm padded to its size with OK."""
    labels = before + ["OK"] * (20 - len(before)) + after + ["OK"] * (20 - len(after)) + control + ["OK"] * (12 - len(control))
    items = [f"\n---\n\n## Item {n:02d}\n\n**Question:** Why?\n\n**Passage:**\n\n> Label: not this one\n\nLabel: {label}\n"
             for n, label in enumerate(labels, start=1)]
    return "# Sheet\n\nType a label after `Label:`.\n" + "".join(items)


def _branch(before, after, control, **grounding) -> str:
    return score(parse_labels(_sheet(before, after, control)), _key(**grounding)).branch


@pytest.mark.parametrize(
    ("before", "after", "control", "grounding", "branch"),
    [
        # Rare: at most 3 of 20 before, whatever the rest says.
        ([], [], [], {}, RARE),
        (["M"] * 3, ["M"] * 9, ["C"] * 9, {}, RARE),
        (["M"] * 3 + ["?"] * 5, [], [], {}, RARE),  # unsure counts for the before arm
        # Flag on: every condition at its limit.
        (["M"] * 4, ["M"], ["C"], {"before_grounded": 20, "after_grounded": 18}, FLAG_ON),
        (["M"] * 12, [], [], {}, FLAG_ON),
        # Back to design: each condition failing alone.
        (["M"] * 4, ["M"] * 2, [], {}, BACK_TO_DESIGN),
        (["M"] * 4, ["M", "?"], [], {}, BACK_TO_DESIGN),  # unsure counts against the after arm
        (["M"] * 4, [], ["C"] * 2, {}, BACK_TO_DESIGN),
        (["M"] * 4, [], ["C", "?"], {}, BACK_TO_DESIGN),  # and against the control
        (["M"] * 4, [], [], {"before_grounded": 20, "after_grounded": 17}, BACK_TO_DESIGN),
    ],
)
def test_each_branch(before, after, control, grounding, branch):
    assert _branch(before, after, control, **grounding) == branch


def test_labels_are_case_insensitive_and_unsure_is_counted_conservatively():
    result = score(parse_labels(_sheet(["m", "?"], ["ok", "?"], ["c", "?"])), _key())
    assert (result.before_m, result.after_m, result.control_c) == (1, 1, 2)
    assert result.labels["before"] == {"M": 1, "?": 1, "OK": 18}
    assert result.items == {"before": 20, "after": 20, "control": 12}


@pytest.mark.parametrize(
    ("sheet", "message"),
    [
        (_sheet([], [], []).replace("Label: OK\n", "Label:\n", 1), "label '(blank)'"),
        (_sheet([], [], []).replace("Label: OK\n", "Label: maybe\n", 1), "label 'maybe'"),
        (_sheet([], [], []).replace("Label: OK\n", "Label: OK\nLabel: M\n", 1), "two Label: lines"),
        (_sheet([], [], []).split("\n---\n\n## Item 52")[0], "unlabelled ['52']"),
    ],
    ids=["blank", "invalid", "twice", "missing"],
)
def test_sheets_it_refuses(sheet, message):
    with pytest.raises(JudgingError, match=re.escape(message)):
        score(parse_labels(sheet), _key())


def test_the_command_prints_the_branch(tmp_path, capsys):
    (tmp_path / "sheet.md").write_text(_sheet(["M"] * 4, [], []), encoding="utf-8")
    (tmp_path / "key.json").write_text(json.dumps(_key()), encoding="utf-8")
    assert main(["--sheet", str(tmp_path / "sheet.md"), "--key", str(tmp_path / "key.json")]) == 0
    out = capsys.readouterr().out
    assert "J4 branch: flag_on" in out and "ADS_ATTRIBUTION_NOTES=true" in out


def test_the_command_refuses_an_unfinished_sheet(tmp_path, capsys):
    (tmp_path / "sheet.md").write_text(_sheet([], [], []).replace("Label: OK\n", "Label:\n", 1), encoding="utf-8")
    (tmp_path / "key.json").write_text(json.dumps(_key()), encoding="utf-8")
    assert main(["--sheet", str(tmp_path / "sheet.md"), "--key", str(tmp_path / "key.json")]) == 1
    assert "Can't score yet" in capsys.readouterr().err
