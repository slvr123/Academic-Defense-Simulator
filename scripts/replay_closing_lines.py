"""M15 E1 rerun: the revised closing prompt, replayed on E1's own 30 hand-offs.

Eval-only:

    backend/.venv/Scripts/python -u scripts/replay_closing_lines.py
        [--e1 evals/v2.0-m15-closing-lines.json] [--out evals/v2.0-m15-closing-lines-replay.json]
        [--chained]

For each hand-off recorded by scripts/measure_closing_lines.py, it makes the closing
call again with the current CLOSING_PROMPT, on the same question and the same answer,
so the only thing that changes is the prompt. The call goes through backend.closing's
own ClosingCall and its validation (problem()), with a 30 s grace, because latency was
measured in E1 and this rerun is about the words only.

E1 did not save the personas, so each replayed session gets a freshly generated panel
(setup_defense with the recorded domain and topic: one persona call per session), and
the outgoing panelist is that panel's member with the recorded archetype. The recorded
band and score are carried over unchanged. About 40 Gemini calls in all (10 persona
calls and 30 closing calls), no daily slot.

--chained (the M15 amendment's test) replays each session's hand-offs in order and
passes the closing lines already said in that replayed session into each call, as the
product now does (fallbacks included, since they were said). It writes
evals/v2.0-m15-closing-lines-chained.json unless --out says otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from academic_defense_simulator.llm.provider import CallCounter  # noqa: E402
from academic_defense_simulator.models.panelist import Panelist  # noqa: E402
from backend.closing import ClosingCall, closing_prompt  # noqa: E402
from backend.defense import setup_defense  # noqa: E402
from backend.defense_routes import gemini_providers_factory  # noqa: E402
from backend.embedder import OnnxEmbedder  # noqa: E402
from backend.schemas import PanelConfig  # noqa: E402
from backend.sessions import Session, SessionStatus  # noqa: E402
from backend.settings import Settings  # noqa: E402
from measure_opening_order import DOCUMENTS, THESIS_PANEL, ingest  # noqa: E402

ANICHECK = next(d for d in DOCUMENTS if d.label == "AniCheck")
REPLAY_GRACE_S = 30.0
# Decision 6: wait out 503s. A call that errored or timed out is Gemini's failure, not
# the prompt's, so it's made again after a wait; an invalid line stands as said.
BUSY_WAIT_S = 60
BUSY_RETRIES = 10


class PersonaFallback(logging.Handler):
    """Notices setup_defense's "persona_fallback=True" (the static panel)."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.used = False

    def emit(self, record: logging.LogRecord) -> None:
        self.used = self.used or "persona_fallback=True" in record.getMessage()


def panel_for(session: Session, settings: Settings, embedder, providers, calls, watch: PersonaFallback):
    for attempt in range(1, BUSY_RETRIES + 1):
        watch.used = False
        try:
            defense = setup_defense(session, providers, calls, embedder, settings.gemini_model, closing_lines="llm")
            if not watch.used:
                return defense
            print(f"    persona generation fell back to the static panel; waiting {BUSY_WAIT_S}s, attempt {attempt}", flush=True)
        except httpx.TransportError as exc:  # the machine's network, not Gemini
            print(f"    network error ({type(exc).__name__}); waiting {BUSY_WAIT_S}s, attempt {attempt}", flush=True)
        time.sleep(BUSY_WAIT_S)
    raise SystemExit("Gemini still unavailable for persona generation; stopping (output so far is saved)")


def closing_for(providers, prompt: str):
    for attempt in range(1, BUSY_RETRIES + 1):
        call = ClosingCall(providers.defense, prompt).start()
        closing, _ = call.take(grace_s=REPLAY_GRACE_S)
        infra = closing.reason is not None and (closing.reason == "timeout" or closing.reason.startswith("error:"))
        if not infra:
            return closing, call.call_ms, attempt - 1
        print(f"    closing call failed ({closing.reason}); waiting {BUSY_WAIT_S}s, attempt {attempt}", flush=True)
        time.sleep(BUSY_WAIT_S)
    raise SystemExit("Gemini still unavailable for closing calls; stopping (output so far is saved)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e1", type=Path, default=REPO_ROOT / "evals" / "v2.0-m15-closing-lines.json")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--chained", action="store_true", help="pass each session's earlier closing lines into the call")
    args = parser.parse_args()
    args.out = args.out or REPO_ROOT / "evals" / f"v2.0-m15-closing-lines-{'chained' if args.chained else 'replay'}.json"
    e1 = json.loads(args.e1.read_text(encoding="utf-8"))
    # Resumable after every hand-off: the replayed lines and each session's regenerated
    # panel are saved as they come, so a killed run continues with the same personas.
    previous = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    replayed: list[dict] = previous.get("replayed", [])
    panels: dict[str, list[dict]] = previous.get("panels", {})
    base_calls = previous.get("gemini_calls", 0)
    settings = Settings()
    embedder = OnnxEmbedder.load(settings.model_dir, threads=settings.ort_threads)
    matrix, chunks, starts = ingest(ANICHECK.path, embedder)
    watch = PersonaFallback()
    logging.getLogger("backend.defense").addHandler(watch)
    logging.getLogger("backend.defense").setLevel(logging.INFO)
    calls = CallCounter()
    providers = gemini_providers_factory(settings)(calls)
    config = PanelConfig(defense_type="thesis", domain=ANICHECK.domain, topic=ANICHECK.topic, archetype_keys=THESIS_PANEL)

    def save() -> None:
        args.out.write_text(json.dumps({
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": settings.gemini_model, "replay_of": args.e1.name, "personas": "regenerated", "chained": args.chained,
            "hand_offs": len(replayed), "gemini_calls": base_calls + calls.total, "panels": panels, "replayed": replayed,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"model={settings.gemini_model} hand_offs={e1['hand_offs']} chained={args.chained} resumed={len(replayed)}", flush=True)
    for recorded in e1["sessions"]:
        number = recorded["session"]
        done = [r for r in replayed if r["session"] == number]
        if len(done) == len(recorded["hand_offs"]):
            continue
        if str(number) not in panels:
            session = Session(
                id=str(uuid.uuid4()), panel=config, filename=ANICHECK.path.name, last_access=time.monotonic(),
                status=SessionStatus.READY, matrix=matrix, chunks=chunks, chapter_starts=starts,
            )
            defense = panel_for(session, settings, embedder, providers, calls, watch)
            panels[str(number)] = [p.model_dump(mode="json") for p in defense.session.panel]
            save()
        by_key = {p["archetype_key"]: Panelist.model_validate(p) for p in panels[str(number)]}
        profile = config.to_profile(domain=ANICHECK.domain, topic=ANICHECK.topic, document_id=f"replay-{number}")
        said = [r["closing"] for r in done]
        for h in recorded["hand_offs"][len(done):]:
            panelist = by_key[h["outgoing"]]
            prompt = closing_prompt(panelist, profile, h["question"], h["answer"], said if args.chained else ())
            closing, call_ms, retries = closing_for(providers, prompt)
            earlier = list(said)
            said.append(closing.line)
            replayed.append({
                "session": number, "turn": h["turn"], "outgoing": h["outgoing"],
                "outgoing_name": panelist.panelist_name, "band": h["band"], "total": h["total"],
                "e1_closing": h["closing"], "closing": closing.line, "source": closing.source,
                "reason": closing.reason, "call_ms": call_ms, "retries_after_gemini_failure": retries,
                **({"earlier": earlier} if args.chained else {}),
            })
            save()
            print(f"  s{number} t{h['turn']} {h['band']:6} [{closing.source}/{closing.reason or '-'}] {closing.line}", flush=True)
    total_calls = base_calls + calls.total
    print(f"{len(replayed)} hand-offs replayed, {total_calls} Gemini calls -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
