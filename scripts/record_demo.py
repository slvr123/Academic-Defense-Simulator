"""v2.0 M10 N5: record the "Read a recorded defense" fixture from the real API, once.

One real defense on the bundled AniCheck sample against a running backend, driven a
step at a time so each answer can be written after reading its question:

    python scripts/record_demo.py start --base-url http://127.0.0.1:8010
    python scripts/record_demo.py start --session <id>   # resend /start after a 503 (no new reservation)
    python scripts/record_demo.py answer "..."      # once per question, until done
    python scripts/record_demo.py show              # the open question again

Every response is kept verbatim: the ready session status (with M10's ingest summary),
/start, each answer's response with the answer sent, and the report. When the defense is
done the fixture is written to frontend/public/demo/anicheck-defense.json, which the
frontend replays with no backend calls. This spends one of today's defense slots and the
Gemini calls of one defense (M10's cap: 41 gemini-3.1-flash-lite, 1 gemini-2.5-flash).
The working state lives in --state (default: the system temp dir), not in the repo.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf"
FIXTURE = REPO_ROOT / "frontend" / "public" / "demo" / "anicheck-defense.json"
DEFAULT_STATE = Path(tempfile.gettempdir()) / "ads-record-demo.json"

# The Recommended capstone preset (M10 N7), in its seating order; domain and topic are
# given, as the start form's optional details allow, so no extraction call is spent.
PANEL = {
    "defense_type": "capstone",
    "other_subtype": None,
    "domain": "Information Technology",
    "topic": "AniCheck: a mobile app for rice leaf disease identification",
    "archetype_keys": ["technical_implementation_reviewer", "methodology_expert", "results_conclusions_reviewer"],
    "starting_difficulty": 2,
}


def load(state_path: Path) -> dict:
    return json.loads(state_path.read_text(encoding="utf-8"))


def save(state_path: Path, state: dict) -> None:
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def show_question(question: dict | None) -> None:
    if question is None:
        print("(no open question)")
        return
    source = question["sources"][0]
    print(f"\nQuestion {question['turn']} - Dr. {question['panelist']['name']}, {question['panelist']['title']} "
          f"(difficulty {question['difficulty']}, {source['origin']}, pp. {source['page_start']}-{source['page_end']})")
    print(question["question"])


def checked(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        sys.exit(f"{response.request.method} {response.request.url.path} answered {response.status_code}: {response.text}")
    return response.json()


def start(args: argparse.Namespace) -> None:
    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        if args.session is None:
            with SAMPLE.open("rb") as handle:
                created = checked(client.post(
                    "/sessions",
                    files={"file": (SAMPLE.name, handle, "application/pdf")},
                    data={"panel": json.dumps(PANEL)},
                ))
            session_id = created["session_id"]
        else:
            session_id = args.session  # a defense whose first question failed resumes without a new reservation
        while True:
            status = checked(client.get(f"/sessions/{session_id}"))
            if status["status"] in ("ready", "failed"):
                break
            time.sleep(1)
        if status["status"] != "ready":
            sys.exit(f"ingest failed: {status}")
        started = checked(client.post(f"/sessions/{session_id}/start"))
    state = {
        "base_url": args.base_url,
        "session_id": session_id,
        "panel_config": PANEL,
        "session": status,
        "start": started,
        "turns": [],
        "open": started["question"],
    }
    save(args.state, state)
    print(f"session {session_id}: {status['ingest']}")
    show_question(started["question"])


def answer(args: argparse.Namespace) -> None:
    state = load(args.state)
    question = state["open"]
    if question is None:
        sys.exit("the defense is done")
    with httpx.Client(base_url=state["base_url"], timeout=180) as client:
        response = checked(client.post(
            f"/sessions/{state['session_id']}/answers", json={"text": args.text, "turn": question["turn"]}
        ))
        state["turns"].append({"answer": args.text, "response": response})
        state["open"] = response["next_question"]
        if response["done"]:
            state["report"] = checked(client.get(f"/sessions/{state['session_id']}/report"))
    save(args.state, state)
    if response["done"]:
        write_fixture(state)
        print(f"done after {len(state['turns'])} answers; fixture written to {FIXTURE.relative_to(REPO_ROOT)}")
    else:
        show_question(response["next_question"])


def write_fixture(state: dict) -> None:
    fixture = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document": SAMPLE.name,
        "panel_config": state["panel_config"],
        "session": state["session"],
        "start": state["start"],
        "turns": state["turns"],
        "report": state["report"],
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start")
    start_parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    start_parser.add_argument("--session", help="an uploaded session whose /start failed")
    answer_parser = commands.add_parser("answer")
    answer_parser.add_argument("text")
    commands.add_parser("show")
    args = parser.parse_args()
    if args.command == "start":
        start(args)
    elif args.command == "answer":
        answer(args)
    else:
        show_question(load(args.state)["open"])


if __name__ == "__main__":
    main()
