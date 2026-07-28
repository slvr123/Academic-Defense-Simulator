"""v1.2 DoD item 5, mechanical half: which voice does each archetype's profile actually
resolve to in a real browser?

Whether two panelists are *distinguishable* is a listening judgement and stays a by-ear
item (Decision 10). Which voice each profile *resolves to* is not — it is a deterministic
walk of `voice_prefs` against `speechSynthesis.getVoices()`, and it is the thing that
decides whether the by-ear pass is even starting from a sane table. Running this first
turns "these nine sound samey" into "these nine resolved to three voices".

Serves a page that renders the real `_voice_component_html` output for every archetype
inside sandboxed iframes, with `speechSynthesis.speak` stubbed so nothing is audible and
nine panelists do not talk over each other. The stub records the utterance the component
actually built — voice name, pitch, rate — which is exactly what the resolution chain
produced.

Two modes, same server:

    /              resolution mode (default). speak() is stubbed, nothing is audible,
                   and the page reports what each profile resolved to. This is the
                   mode the committed evidence was captured from, so its behaviour
                   must not drift.
    /?audible=1    listening mode. speak() is wrapped rather than stubbed, so sound
                   comes out, and the page plays co-seatable panels with their
                   members alternating the way they would in a real session.

Usage:
    python scripts/probe_voice_resolution.py            # serve on :8765, print the URL
    python scripts/probe_voice_resolution.py --port N

Open the URL in the browser you care about and read the table it prints. `window.
__ads_probe` holds the same data for programmatic collection.
"""

from __future__ import annotations

import argparse
import http.server
import json
import re
import socketserver
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic_defense_simulator.panel import (  # noqa: E402
    DEVILS_ADVOCATE_KEY,
    PANEL_COMPOSITION,
    VOICE_PROFILES,
)
from academic_defense_simulator.streamlit_app import _voice_component_html  # noqa: E402

# One line, fourteen words, spoken by every archetype. Varying the text would confound
# voice discrimination with content discrimination — if two panelists say different
# sentences you can tell them apart without hearing any difference in the voices.
CONTROLLED_LINE = (
    "The panel has reviewed your document and would like to discuss your methods now."
)

# How many times the group cycles. One pass already alternates; a second gives the ear
# the repeat comparison it needs, because discrimination is much easier on re-hearing.
GROUP_ROUNDS = 2

# A profile whose preferences cannot possibly resolve — DoD item 7's forcing mechanism.
# Decision 4 step 3 says fall through to the browser default; step 4 says apply pitch and
# rate anyway. Both are checked on this row.
NO_MATCH_PROBE = {
    "voice_prefs": ["Definitely Not An Installed Voice", "Nor Is This One"],
    "pitch": 1.42,
    "rate": 0.61,
}


def _shared_first_preference_pairs() -> list[tuple[str, str]]:
    """Archetype pairs whose profiles name the same voice first, so they are separated
    by pitch and rate alone wherever that voice resolves. These are the pairs the
    listening pass actually has to adjudicate."""
    by_voice: dict[str, list[str]] = {}
    for key, profile in VOICE_PROFILES.items():
        by_voice.setdefault(profile["voice_prefs"][0], []).append(key)
    pairs = []
    for members in by_voice.values():
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                pairs.append((a, b))
    return pairs


def coseatable_groups() -> list[dict]:
    """Panels that can genuinely be seated, derived from `PANEL_COMPOSITION` and the
    `compose_full_roster` rule rather than from a hardcoded list.

    Two kinds, because they answer different questions:

    - **default**: `PANEL_COMPOSITION[key][:3]`, which is the panel the UI pre-selects
      for each defense type, so it is what most visitors actually hear.
    - **stress**: a pair sharing a first-preference voice, seated together. Legal
      because `DefenseProfile.selected_archetypes` is `min_length=1, max_length=3`, so
      a two-domain panel is a real configuration, and this is the hardest separation
      the composition table permits.

    Devil's Advocate is appended to every group, not because this script says so but
    because `compose_full_roster` is `compose_panel(profile) + [DEVILS_ADVOCATE_KEY]`
    with no condition on it.
    """
    groups: list[dict] = []
    seen: set[tuple[str, ...]] = set()

    def add(kind: str, label: str, domain: list[str]) -> None:
        members = tuple(domain + [DEVILS_ADVOCATE_KEY])
        if members in seen:
            return
        seen.add(members)
        groups.append({"kind": kind, "label": label, "members": list(members)})

    for comp_key, roster in PANEL_COMPOSITION.items():
        add("default", f"{comp_key} (UI default: first 3 of the roster)", roster[:3])

    for a, b in _shared_first_preference_pairs():
        for comp_key, roster in PANEL_COMPOSITION.items():
            if a in roster and b in roster:
                voice = VOICE_PROFILES[a]["voice_prefs"][0]
                add("stress", f"{comp_key}: {a} + {b} both want {voice!r}", [a, b])
                break

    return groups


def _shim(audible: bool) -> str:
    """Injected inside each iframe ahead of the component's own script.

    Resolution mode stubs `speak` so nothing is audible and nine panelists do not talk
    over each other. Listening mode wraps it instead and calls through, so the same
    recording happens but the sound is real. `getVoices` is untouched in both, so the
    resolution chain under test is always the shipped one."""
    call_through = "  return real.call(speechSynthesis, utterance);" if audible else ""
    return f"""
<script>
window.__result = null;
var real = speechSynthesis.speak;
speechSynthesis.speak = function (utterance) {{
  window.__result = {{
    voice: utterance.voice ? utterance.voice.name : null,
    pitch: utterance.pitch,
    rate: utterance.rate,
    text: utterance.text,
  }};
{call_through}
}};
</script>
"""


def _build_audible_page() -> str:
    """Listening mode. Renders one real component per (group, archetype) and plays each
    group by clicking the components' own in-iframe play controls in turn, so the code
    path exercised is identical to the app's."""
    groups = coseatable_groups()

    frames, guard_keys = [], []
    for gi, group in enumerate(groups):
        cells = []
        for member in group["members"]:
            guard = f"probe-audible:{gi}:{member}"
            guard_keys.append(guard)
            doc = _shim(audible=True) + _voice_component_html(CONTROLLED_LINE, member, guard)
            cells.append(
                f'<div class="slot" data-key="{member}">'
                f'<div class="slot-key">{member}</div>'
                f'<div class="slot-what" id="what-{gi}-{member}">not played yet</div>'
                f'<iframe data-group="{gi}" data-key="{member}" '
                f'srcdoc="{doc.replace(chr(34), "&quot;")}"></iframe>'
                f"</div>"
            )
        frames.append(
            f'<section class="group" data-group="{gi}">'
            f'<h3>{group["kind"].upper()} &middot; {group["label"]}</h3>'
            f'<button class="play" data-group="{gi}">Play this panel '
            f'({GROUP_ROUNDS} rounds, alternating)</button>'
            f'<span class="status" id="status-{gi}"></span>'
            f'<div class="slots">{"".join(cells)}</div>'
            f"</section>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ADS v1.2 voice listening pass</title>
<script>
// Seeded BEFORE any component iframe is parsed. Each component's Decision 7 guard
// finds its key already present and therefore does not auto-speak on load, which is
// what stops every panel on the page talking at once. Playback is driven explicitly
// below, through each component's own play control.
{json.dumps(guard_keys)}.forEach(function (k) {{
  try {{ sessionStorage.setItem(k, "1"); }} catch (e) {{}}
}});
</script>
<style>
 body {{ font: 14px/1.6 ui-monospace, Consolas, monospace; background:#12100E;
        color:#ECE7DD; padding: 1.5rem; max-width: 1100px; }}
 h2, h3 {{ color:#D2A24C; font-weight:600; }}
 .group {{ border:1px solid #2E2A25; border-radius:10px; padding:0.8rem 1rem;
           margin-bottom:1rem; background:#1A1714; }}
 .slots {{ display:flex; flex-wrap:wrap; gap:0.75rem; margin-top:0.6rem; }}
 .slot {{ border:1px solid #2E2A25; border-radius:8px; padding:0.5rem; min-width:250px; }}
 .slot-key {{ color:#D2A24C; }}
 .slot-what {{ color:#8A8378; font-size:0.85em; min-height:2.6em; }}
 .slot.live {{ border-color:#8C3A3F; box-shadow:0 0 0 2px rgba(140,58,63,0.25); }}
 iframe {{ width:230px; height:42px; border:0; }}
 button.play {{ background:#8C3A3F; color:#F2E9E4; border:0; border-radius:8px;
                padding:0.35rem 0.9rem; cursor:pointer; font:inherit; }}
 .status {{ margin-left:0.8rem; color:#8A8378; }}
 .line {{ color:#8A8378; }}
</style></head>
<body>
<h2>ADS v1.2 &mdash; listening pass (DoD item 5, by-ear half)</h2>
<p class="line">Every archetype speaks the same line, so any difference you hear is
the voice and not the words:<br><em>&ldquo;{CONTROLLED_LINE}&rdquo;</em></p>
<p class="line">Panels below are derived from <code>PANEL_COMPOSITION</code> and the
<code>compose_full_roster</code> rule, not hardcoded. STRESS panels seat two
archetypes that want the same voice &mdash; those are the pairs to judge hardest.
Note any pair you cannot tell apart.</p>
{"".join(frames)}
<script>
function frameFor(gi, key) {{
  return document.querySelector('iframe[data-group="' + gi + '"][data-key="' + key + '"]');
}}
function describe(gi, key) {{
  const f = frameFor(gi, key);
  const r = f.contentWindow.__result;
  const el = document.getElementById("what-" + gi + "-" + key);
  if (!r) {{ el.textContent = "did not speak"; return; }}
  el.innerHTML = "resolved: <b>" + (r.voice || "(browser default)") + "</b><br>" +
                 "pitch " + Number(r.pitch).toFixed(2) + " &middot; rate " + Number(r.rate).toFixed(2);
}}
function speakOne(gi, key) {{
  return new Promise(function (resolve) {{
    const f = frameFor(gi, key);
    const slot = f.closest(".slot");
    slot.classList.add("live");
    // Click the component's OWN play control. Nothing here reimplements the
    // resolution chain; this is the same path a visitor's click takes.
    f.contentWindow.document.getElementById("ads-voice-play").click();
    setTimeout(function () {{
      describe(gi, key);
      const synth = f.contentWindow.speechSynthesis;
      const poll = setInterval(function () {{
        if (!synth.speaking && !synth.pending) {{
          clearInterval(poll);
          slot.classList.remove("live");
          setTimeout(resolve, 350);
        }}
      }}, 200);
    }}, 250);
  }});
}}
document.querySelectorAll("button.play").forEach(function (btn) {{
  btn.addEventListener("click", async function () {{
    const gi = btn.dataset.group;
    const keys = [...document.querySelectorAll('.group[data-group="' + gi + '"] .slot')]
                   .map(function (s) {{ return s.dataset.key; }});
    const status = document.getElementById("status-" + gi);
    btn.disabled = true;
    // Alternating, not blocked: the whole panel cycles, then cycles again. Playing
    // one voice repeatedly before moving on would be an easier task than the real one.
    for (let round = 1; round <= {GROUP_ROUNDS}; round++) {{
      for (const key of keys) {{
        status.textContent = "round " + round + "/{GROUP_ROUNDS} — " + key;
        await speakOne(gi, key);
      }}
    }}
    status.textContent = "done";
    btn.disabled = false;
  }});
}});
window.__ads_groups = {json.dumps(groups)};
</script>
</body></html>
"""


def _build_page() -> str:
    rows = list(VOICE_PROFILES.items()) + [("__forced_no_match__", NO_MATCH_PROBE)]
    frames = []
    for key, profile in rows:
        if key == "__forced_no_match__":
            # Reuse a real archetype's component, then override the two constants the
            # resolution chain reads. Same code path, unmatchable preferences.
            doc = _voice_component_html("Forced no-match probe.", "methodology_expert", f"probe:{key}")
            doc = re.sub(r"var PREFS = .*;", f'var PREFS = {json.dumps(profile["voice_prefs"])};', doc)
            doc = re.sub(r"var PITCH = .*;", f'var PITCH = {profile["pitch"]};', doc)
            doc = re.sub(r"var RATE  = .*;", f'var RATE  = {profile["rate"]};', doc)
        else:
            doc = _voice_component_html(f"Probe utterance for {key}.", key, f"probe:{key}")
        frames.append(
            f'<iframe data-key="{key}" '
            f'srcdoc="{(_shim(audible=False) + doc).replace(chr(34), "&quot;")}"></iframe>'
        )

    expected = json.dumps({k: v["voice_prefs"] for k, v in rows}, indent=2)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ADS v1.2 voice resolution probe</title>
<style>
 body {{ font: 14px/1.5 ui-monospace, Consolas, monospace; background:#12100E; color:#ECE7DD;
        padding: 1.5rem; }}
 iframe {{ width: 220px; height: 44px; border: 0; }}
 table {{ border-collapse: collapse; margin-top: 1rem; }}
 th, td {{ border: 1px solid #2E2A25; padding: 0.35rem 0.6rem; text-align: left; }}
 th {{ color: #D2A24C; }}
 .collide {{ background: #3A1B1D; }}
 h2 {{ color:#D2A24C; font-weight:600; }}
</style></head>
<body>
<h2>ADS v1.2 - archetype voice resolution</h2>
<div id="frames">{"".join(frames)}</div>
<div id="out">resolving...</div>
<script>
const PREFS = {expected};

// The probe used to read getVoices() straight off, and on Edge that returned an empty
// list on a machine with 26 voices installed — the same async quirk the shipped
// component handles via `voiceschanged` (Decision 4, step 1). The probe not handling it
// was a measurement bug, not a product bug, but it produced an all-"(browser default)"
// table that reads exactly like a broken map. Same wait as the component, so the probe
// cannot understate the inventory it exists to report.
function whenVoicesReady(callback) {{
  if (speechSynthesis.getVoices().length > 0) {{ callback(); return; }}
  var fired = false;
  function fire() {{ if (fired) {{ return; }} fired = true; callback(); }}
  speechSynthesis.addEventListener("voiceschanged", fire, {{ once: true }});
  setTimeout(fire, 3000);
}}

window.addEventListener("load", () => whenVoicesReady(() => setTimeout(() => {{
  const inventory = speechSynthesis.getVoices().map(v => ({{
    name: v.name, lang: v.lang, localService: v.localService, default: v.default,
  }}));
  const voices = inventory.map(v => v.name);
  const english = inventory.filter(v => /^en\\b|^en-/i.test(v.lang));
  const rows = [...document.querySelectorAll("#frames iframe")].map(f => {{
    const r = f.contentWindow.__result;
    return {{ key: f.dataset.key, resolved: r ? (r.voice || "(browser default)") : "(did not speak)",
              pitch: r ? r.pitch : null, rate: r ? r.rate : null }};
  }});
  window.__ads_probe = {{ inventory: voices, voices: inventory, english, rows }};

  const counts = {{}};
  rows.forEach(r => counts[r.resolved] = (counts[r.resolved] || 0) + 1);
  const body = rows.map(r => {{
    const cls = counts[r.resolved] > 1 ? ' class="collide"' : "";
    return `<tr${{cls}}><td>${{r.key}}</td><td>${{r.resolved}}</td>` +
           `<td>${{r.pitch}}</td><td>${{r.rate}}</td>` +
           `<td>${{PREFS[r.key].join(" &middot; ")}}</td></tr>`;
  }}).join("");
  // Full inventory, not just what resolved: name, lang, localService. A profile can
  // only resolve to something in this list, so when a table looks wrong this is the
  // first thing to read.
  const invRows = inventory.map(v =>
    `<tr><td>${{v.name}}</td><td>${{v.lang}}</td>` +
    `<td>${{v.localService ? "local" : "network"}}</td>` +
    `<td>${{v.default ? "DEFAULT" : ""}}</td></tr>`).join("");

  document.getElementById("out").innerHTML =
    `<p>${{voices.length}} voices installed (${{english.length}} English); ` +
    `${{new Set(rows.map(r => r.resolved)).size}} distinct voices resolved across ` +
    `${{rows.length}} profiles. Shaded rows share a resolved voice.</p>` +
    `<table><tr><th>archetype</th><th>resolved voice</th><th>pitch</th><th>rate</th>` +
    `<th>voice_prefs</th></tr>${{body}}</table>` +
    `<h2>full inventory (${{voices.length}})</h2>` +
    `<table><tr><th>name</th><th>lang</th><th>service</th><th></th></tr>${{invRows}}</table>`;
}}, 2500)));
</script>
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # Both built once at startup so a reload cannot silently pick up a half-edited map.
    resolution_page = _build_page().encode("utf-8")
    audible_page = _build_audible_page().encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            # Resolution mode is the default and stays the default: the committed
            # evidence was captured from it, so it has to remain reproducible.
            query = parse_qs(urlparse(self.path).query)
            page = audible_page if query.get("audible") == ["1"] else resolution_page
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *_args):
            pass

    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"resolution probe at http://127.0.0.1:{args.port}/", flush=True)
        print(f"listening pass  at http://127.0.0.1:{args.port}/?audible=1", flush=True)
        print("(ctrl-c to stop)", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
