"""M8 slice 1, offline: K2's note carries the round-2 rule, the invented-citation
check, round 2's own cache and ledger, and K3's scorer on fake label files (every
branch of the verdict, `?` counted as M, and sheets it must refuse)."""

from __future__ import annotations

import json
import re

import pytest

import scripts.probe_attribution as m7
from academic_defense_simulator.llm.provider import CallCounter
from academic_defense_simulator.models.panelist_output import PanelistQuestion
from backend.engine_adapter import ATTRIBUTION_NOTES
from backend.tests.test_probe_attribution import ANSWER, ScriptedGemini
from scripts.probe_attribution_r2 import invented_authors, named_authors
from scripts.score_attribution_judging import JudgingError
from scripts.score_attribution_judging_r2 import FLAG_ON, STOP, main, parse_labels, score

ROUND_TWO_RULE = "Never ask how the candidate built, ran, scaled, deployed or implemented"


@pytest.mark.parametrize("attribution", ["literature_review", "partly_literature_review"])
def test_both_notes_carry_the_round_two_rule(attribution):
    note = ATTRIBUTION_NOTES[attribution]
    assert ROUND_TWO_RULE in note
    assert "why they chose it" in note and "departed from it" in note
    assert note.startswith("[Note to the panelist:") and note.endswith("]")
    assert not re.search(r"\bpages?\b|\bp\.\s?\d", note)  # M7 J2: no page numbers


@pytest.mark.parametrize(
    ("question", "names"),
    [
        ("Regarding the technique proposed by Yarkan, how does it compare to the approach described by "
         "Yang and Song?", ["Yarkan", "Yang", "Song"]),
        ("How does Reyes et al.'s survey shape your sample?", ["Reyes"]),
        ("Why follow Cruz (2020) rather than Lim & Tan (2019)?", ["Cruz", "Lim", "Tan"]),
        ("Is J. P. Reyes's framework the right baseline?", ["Reyes"]),
        ("Why adopt the scheme proposed by Yarkan's team?", ["Yarkan"]),
        ("Why did you build the kiosk on a Raspberry Pi?", []),
        ("How is the data used by The system validated?", []),
    ],
)
def test_named_authors_finds_authors_by_position(question, names):
    assert named_authors(question) == names


def test_an_author_missing_from_the_passage_is_invented():
    passage = "Yang and Song (2019) proposed a geometric approach. Three-point estimation reduces skew."
    question = "Why prefer the method proposed by Yarkan over the one by Yang and Song?"
    assert invented_authors(question, passage) == ["Yarkan"]
    assert invented_authors("Why follow Yang's design over the one by song?", passage) == []


def test_round_two_reads_m7s_cache_but_writes_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(m7.time, "sleep", lambda s: None)
    m7_cache = tmp_path / "m7-cache.jsonl"
    m7_cache.write_text(json.dumps({"key": m7._key("PanelistQuestion", "setup"),
                                    "response": ANSWER.model_dump_json()}) + "\n", encoding="utf-8")
    m7_before = m7_cache.read_text(encoding="utf-8")
    calls = CallCounter()
    inner = ScriptedGemini(calls)
    provider = m7.CachedProvider(inner, calls, 10, cache=tmp_path / "r2.jsonl", ledger=tmp_path / "r2-ledger.json",
                                 read_only_caches=(m7_cache,))
    assert provider.generate_structured("setup", PanelistQuestion) == ANSWER  # M7's cache: no call
    assert provider.generate_structured("new", PanelistQuestion) == ANSWER
    assert inner.prompts == ["new"]
    assert m7_cache.read_text(encoding="utf-8") == m7_before
    assert len((tmp_path / "r2.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert json.loads((tmp_path / "r2-ledger.json").read_text(encoding="utf-8")) == {"calls": 1}


# --- the scorer -----------------------------------------------------------------------

CHUNKS = list(range(7, 21))


def _key(grounded: int = 28, note_only: int = 0, invented: tuple[int, ...] = ()) -> dict[str, dict]:
    """28 items: chunks 7-20 x TIR then ME, ids in that order."""
    key = {}
    for n in range(1, 29):
        chunk = CHUNKS[(n - 1) % 14]
        archetype = "technical_implementation_reviewer" if n <= 14 else "methodology_expert"
        key[f"{n:02d}"] = {
            "chunk": chunk, "archetype": archetype, "pages": [chunk * 2, chunk * 2 + 1],
            "grounded": n <= grounded, "note_only_grounding": n > 28 - note_only,
            "invented_authors": ["Yarkan"] if n in invented else [],
        }
    return key


def _sheet(labels: dict[int, str]) -> str:
    """A sheet in the probe's format; items not in `labels` are OK."""
    items = [f"\n---\n\n## Item {n:02d}\n\n**Question:** Why?\n\n**Passage:**\n\n> Label: not this one\n\n"
             f"Label: {labels.get(n, 'OK')}\n" for n in range(1, 29)]
    return "# Sheet\n" + "".join(items)


@pytest.mark.parametrize(
    ("labels", "key", "verdict"),
    [
        ({}, _key(), FLAG_ON),
        ({6: "M"}, _key(grounded=25), FLAG_ON),  # every condition exactly at its limit
        ({6: "M", 20: "C"}, _key(), FLAG_ON),  # C isn't M
        ({6: "M", 8: "M"}, _key(), STOP),  # two M
        ({6: "M", 8: "?"}, _key(), STOP),  # ? counts as M
        ({6: "?"}, _key(), FLAG_ON),  # one ? is one M
        ({}, _key(grounded=24), STOP),  # grounding below 25
        ({}, _key(note_only=1), STOP),  # note-only grounding
        ({}, _key(invented=(3, 4, 5)), FLAG_ON),  # invented citations are report only
    ],
)
def test_each_branch(labels, key, verdict):
    assert score(parse_labels(_sheet(labels)), key).verdict == verdict


def test_per_chunk_labels_and_the_invented_count():
    result = score(parse_labels(_sheet({8: "M", 25: "?"})), _key(invented=(2, 9)))
    assert result.per_chunk[14] == {"TIR": "M", "ME": "OK"}
    assert result.per_chunk[17] == {"TIR": "OK", "ME": "?"}
    assert result.m == 2 and result.invented_items == ["02", "09"]


@pytest.mark.parametrize(
    ("sheet", "message"),
    [
        (_sheet({3: ""}), "isn't one of"),
        (_sheet({3: "maybe"}), "isn't one of"),
        (_sheet({}).replace("Label: OK\n", "Label: OK\nLabel: M\n", 1), "two Label"),
        (_sheet({}).split("\n---\n\n## Item 28")[0], "unlabelled"),
    ],
    ids=["blank", "invalid", "twice", "missing"],
)
def test_sheets_it_refuses(sheet, message):
    with pytest.raises(JudgingError, match=message):
        score(parse_labels(sheet), _key())


def test_a_key_of_the_wrong_size_is_refused():
    key = _key()
    key.pop("28")
    with pytest.raises(JudgingError, match="defined for 28"):
        score(parse_labels(_sheet({}).split("\n---\n\n## Item 28")[0]), key)


def test_the_command_prints_the_per_chunk_table_and_verdict(tmp_path, capsys):
    (tmp_path / "sheet.md").write_text(_sheet({8: "M"}), encoding="utf-8")
    (tmp_path / "key.json").write_text(json.dumps(_key(invented=(5,))), encoding="utf-8")
    assert main(["--sheet", str(tmp_path / "sheet.md"), "--key", str(tmp_path / "key.json")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "   14  28-29    M    OK   (round 1 miss)" in out
    assert "invented citations (report only): 1 items (05)" in out
    assert out[-2:] == ["K3 verdict: flag_on", "Turn the flag on: set ADS_ATTRIBUTION_NOTES=true on Render."]


def test_the_command_refuses_an_unfinished_sheet(tmp_path, capsys):
    (tmp_path / "sheet.md").write_text(_sheet({4: ""}), encoding="utf-8")
    (tmp_path / "key.json").write_text(json.dumps(_key()), encoding="utf-8")
    assert main(["--sheet", str(tmp_path / "sheet.md"), "--key", str(tmp_path / "key.json")]) == 1
    assert "Can't score yet" in capsys.readouterr().err
