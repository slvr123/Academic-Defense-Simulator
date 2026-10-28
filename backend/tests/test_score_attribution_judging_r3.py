"""M9 L5's scorer: the judge's `NN | LABEL | reason` lines against the key."""

from __future__ import annotations

import json

import pytest

from scripts.score_attribution_judging_r3 import JudgingError, main, parse_judge, score


def _key(n: int = 16) -> dict[str, dict]:
    return {
        f"{i:02d}": {"document": "sample3.pdf", "chunk": i, "pages": [i, i + 1], "grounded": True,
                     "note_only_grounding": False, "invented_authors": []}
        for i in range(1, n + 1)
    }


def _reply(labels: dict[int, str]) -> str:
    lines = ["Here are my labels.", "```"]
    lines += [f"{i:02d} | {labels.get(i, 'OK')} | reason {i}" for i in range(1, 17)]
    return "\n".join(lines + ["```"])


def test_parse_reads_label_and_reason_and_skips_other_lines():
    parsed = parse_judge(_reply({3: "M", 5: "?"}))
    assert len(parsed) == 16
    assert parsed["03"] == ("M", "reason 3") and parsed["05"] == ("?", "reason 5") and parsed["01"] == ("OK", "reason 1")


@pytest.mark.parametrize(("labels", "access"), [
    ({}, "literature"),
    ({4: "M"}, "literature"),
    ({4: "?"}, "literature"),
    ({4: "C"}, "literature"),
    ({4: "M", 9: "?"}, "none"),
    ({4: "M", 9: "M"}, "none"),
])
def test_the_verdict_keeps_access_only_at_one_m_or_fewer(labels, access):
    assert score(parse_judge(_reply(labels)), _key())[2] == access


def test_a_missing_or_doubled_item_refuses_to_score():
    with pytest.raises(JudgingError, match="unlabelled"):
        score(parse_judge(_reply({})), _key(17))
    with pytest.raises(JudgingError, match="twice"):
        parse_judge("01 | OK | a\n01 | M | b")


def test_main_prints_the_verdict(tmp_path, capsys):
    (tmp_path / "labels.md").write_text(_reply({2: "M", 7: "M"}), encoding="utf-8")
    (tmp_path / "key.json").write_text(json.dumps(_key()), encoding="utf-8")
    assert main(["--labels", str(tmp_path / "labels.md"), "--key", str(tmp_path / "key.json")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "labels: M 2, C 0, OK 14, ? 0" in out
    assert out[-2] == "L5 verdict: none"
