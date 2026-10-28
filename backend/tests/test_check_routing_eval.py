"""M9 L4's offline verdict (scripts/check_routing_eval.py) on synthetic results files."""

from __future__ import annotations

import copy

from scripts.check_routing_eval import DOCUMENTS, UNTAGGED, check


def _document(name: str) -> dict:
    return {
        "file": name,
        "check_a": {"verdict": "differentiated"},
        "check_c": {"top1_navigation_picks": [], "session_navigation_picks": []},
        "check_e": {"max_displacement_rank": 2},
        "check_e_seeded": {"max_displacement_rank": 4, "turns_on_avoided_chunks": 0},
        "reference_filter": {"top1_reference_picks": 0, "session_reference_turns": 0},
    }


def _picks(top1=0, plain=0, seeded=0) -> dict:
    return {"top1": top1, "plain_turns": plain, "seeded_turns": seeded, "seeded_turns_total": 40}


def _files() -> tuple[dict, dict]:
    before = {"per_document": [_document(name) for name in DOCUMENTS]}
    after = copy.deepcopy(before)
    after["literature_routing"] = {
        name: {
            "access": "literature",
            "tagged_chunks": [3],
            "per_archetype": {
                "methodology_expert": {"before": _picks(1, 2, 30), "after": _picks()},
                "literature_theory_specialist": {"before": _picks(1, 2, 40), "after": _picks(1, 2, 40)},
            },
        }
        for name in DOCUMENTS
    }
    return before, after


def _document_in(results: dict, name: str) -> dict:
    return next(d for d in results["per_document"] if d["file"] == name)


def test_routing_that_meets_every_criterion_passes():
    lines, failures = check(*_files())
    assert failures == []
    assert "DAZSMA per-document results byte-identical to M8's: True" in lines


def test_a_barred_archetype_on_a_tagged_chunk_fails():
    before, after = _files()
    after["literature_routing"]["sample3.pdf"]["per_archetype"]["methodology_expert"]["after"] = _picks(seeded=1)
    assert check(before, after)[1] == ["sample3.pdf: methodology_expert still draws a tagged chunk"]


def test_a_worse_check_a_branch_or_a_far_displacement_fails():
    before, after = _files()
    _document_in(after, "sample3.pdf")["check_a"]["verdict"] = "partial"
    _document_in(after, "sample3.pdf")["check_e_seeded"]["max_displacement_rank"] = 6
    assert check(before, after)[1] == [
        "sample3.pdf: Check A moved from differentiated to partial",
        "sample3.pdf: Check E displacement 6 > 5",
    ]


def test_a_navigation_pick_fails():
    before, after = _files()
    _document_in(after, "sample-capstone-anicheck.pdf")["reference_filter"]["session_reference_turns"] = 1
    assert check(before, after)[1] == ["sample-capstone-anicheck.pdf: navigation or reference picks"]


def test_any_change_to_dazsma_fails():
    before, after = _files()
    _document_in(after, UNTAGGED)["check_e"]["turns_over_5"] = 0
    assert check(before, after)[1] == ["DAZSMA's results changed"]
