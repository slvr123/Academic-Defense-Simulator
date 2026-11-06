"""M16 Decision 4: does the scorer mark a Taglish answer lower than the same answer in English?

Eval-only:

    backend/.venv/Scripts/python -u scripts/measure_taglish_parity.py
        [--pairs evals/v2.0-m16-parity-pairs.json] [--out evals/v2.0-m16-scoring-parity.json]

The pairs file holds 10 answers to real AniCheck questions, each written twice with the
same content, in English and in Taglish (by Claude, Decision 4), with the question, the
panelist who asked it and the exact chunk text the scorer reads. Every version is scored
REPS times by v1's own `_score_answer`, on the backend's configured defense model and its
real provider settings: the product's scorer, unchanged. The calls interleave (rep by rep,
and English and Taglish alternate which goes first), so drift over the run can't favour
one language.

The rule is Decision 4's, fixed before the data: the mean signed difference (Taglish minus
English, over the 10 pairs, each pair's means of three) is within +-0.75 points of 15, and
no single pair differs by more than 2.0 points. A failed call waits and retries the same
call; the model never changes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from academic_defense_simulator.engine import _score_answer  # noqa: E402
from academic_defense_simulator.llm.provider import CallCounter, LLMProviderError  # noqa: E402
from academic_defense_simulator.models.panelist import Panelist  # noqa: E402
from academic_defense_simulator.models.session import ConversationTurn  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.llm_errors import is_panel_busy  # noqa: E402
from backend.settings import Settings  # noqa: E402

REPS = 3
MEAN_LIMIT = 0.75
PAIR_LIMIT = 2.0
LANGUAGES = ("english", "taglish")
BUSY_WAIT_S = 60
RETRIES = 10


def score(provider, pair: dict, language: str) -> dict:
    turn = ConversationTurn(
        panelist_archetype_key=pair["archetype_key"], panelist_name=pair["panelist"], question=pair["question"],
        grounding_reference="", chunk_index=0, chunk_text=pair["chunk_text"], difficulty_level=pair.get("difficulty", 2),
        answer=pair[language],
    )
    panelist = Panelist(archetype_key=pair["archetype_key"], panelist_name=pair["panelist"], persona_framing="", icon="")
    for attempt in range(1, RETRIES + 1):
        try:
            result = _score_answer(provider, turn, panelist, "thesis")
            return {"clarity": result.clarity, "depth": result.depth, "grounding": result.grounding,
                    "total": result.clarity + result.depth + result.grounding, "attempts": attempt}
        except LLMProviderError as exc:
            if attempt == RETRIES:
                raise
            wait = BUSY_WAIT_S if is_panel_busy(exc) else 10
            print(f"    scoring failed ({type(exc).__name__}); waiting {wait}s, attempt {attempt}", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def summarise(pairs: list[dict], scores: dict[str, dict[str, list[dict]]]) -> dict:
    rows = []
    for pair in pairs:
        by_language = {lang: [s["total"] for s in scores[pair["id"]][lang]] for lang in LANGUAGES}
        means = {lang: statistics.mean(t) for lang, t in by_language.items()}
        rows.append({
            "id": pair["id"], "strength": pair["strength"], "question_language": pair["question_language"],
            "english": by_language["english"], "taglish": by_language["taglish"],
            "english_mean": round(means["english"], 3), "taglish_mean": round(means["taglish"], 3),
            "english_spread": max(by_language["english"]) - min(by_language["english"]),
            "taglish_spread": max(by_language["taglish"]) - min(by_language["taglish"]),
            "difference": round(means["taglish"] - means["english"], 3),
        })
    mean_difference = statistics.mean(r["difference"] for r in rows)
    largest = max(rows, key=lambda r: abs(r["difference"]))
    passed = abs(mean_difference) <= MEAN_LIMIT and abs(largest["difference"]) <= PAIR_LIMIT
    return {"rows": rows, "mean_difference": round(mean_difference, 3),
            "largest_pair_difference": {"id": largest["id"], "difference": largest["difference"]},
            "rule": f"|mean difference| <= {MEAN_LIMIT} and every |pair difference| <= {PAIR_LIMIT}",
            "passed": passed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=Path, default=REPO_ROOT / "evals" / "v2.0-m16-parity-pairs.json")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "v2.0-m16-scoring-parity.json")
    args = parser.parse_args()
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))["pairs"]
    assert len(pairs) == 10 and len({p["id"] for p in pairs}) == 10
    settings = Settings()
    calls = CallCounter()
    provider = gemini_providers_factory(settings)(calls).defense
    print(f"model={settings.gemini_model} pairs={len(pairs)} reps={REPS} calls planned={len(pairs) * 2 * REPS}", flush=True)
    scores: dict[str, dict[str, list[dict]]] = {p["id"]: {lang: [] for lang in LANGUAGES} for p in pairs}
    for rep in range(1, REPS + 1):
        for i, pair in enumerate(pairs):
            order = LANGUAGES if (i + rep) % 2 == 0 else LANGUAGES[::-1]
            for language in order:
                result = score(provider, pair, language)
                scores[pair["id"]][language].append(result)
                print(f"  rep {rep} {pair['id']} {language}: {result['total']} ({result['clarity']}/{result['depth']}/{result['grounding']})", flush=True)
    summary = summarise(pairs, scores)
    args.out.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.gemini_model,
        "reps": REPS,
        "scores": scores,
        **summary,
        "gemini_calls": calls.total,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"mean difference {summary['mean_difference']:+.3f}, largest pair {summary['largest_pair_difference']}, "
          f"passed={summary['passed']}, {calls.total} Gemini calls -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
