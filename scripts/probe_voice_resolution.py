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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic_defense_simulator.panel import VOICE_PROFILES  # noqa: E402
from academic_defense_simulator.streamlit_app import _voice_component_html  # noqa: E402

# Prepended inside each iframe, before the component's own script. Stubs speak() rather
# than wrapping it: the point is to read the resolution result, not to hear nine voices
# at once. `getVoices` is untouched, so the resolution chain under test is the real one.
SHIM = """
<script>
window.__result = null;
speechSynthesis.speak = function (utterance) {
  window.__result = {
    voice: utterance.voice ? utterance.voice.name : null,
    pitch: utterance.pitch,
    rate: utterance.rate,
    text: utterance.text,
  };
};
</script>
"""

# A profile whose preferences cannot possibly resolve — DoD item 7's forcing mechanism.
# Decision 4 step 3 says fall through to the browser default; step 4 says apply pitch and
# rate anyway. Both are checked on this row.
NO_MATCH_PROBE = {
    "voice_prefs": ["Definitely Not An Installed Voice", "Nor Is This One"],
    "pitch": 1.42,
    "rate": 0.61,
}


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
            f'<iframe data-key="{key}" srcdoc="{(SHIM + doc).replace(chr(34), "&quot;")}"></iframe>'
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
window.addEventListener("load", () => setTimeout(() => {{
  const voices = speechSynthesis.getVoices().map(v => v.name);
  const rows = [...document.querySelectorAll("#frames iframe")].map(f => {{
    const r = f.contentWindow.__result;
    return {{ key: f.dataset.key, resolved: r ? (r.voice || "(browser default)") : "(did not speak)",
              pitch: r ? r.pitch : null, rate: r ? r.rate : null }};
  }});
  window.__ads_probe = {{ inventory: voices, rows }};

  const counts = {{}};
  rows.forEach(r => counts[r.resolved] = (counts[r.resolved] || 0) + 1);
  const body = rows.map(r => {{
    const cls = counts[r.resolved] > 1 ? ' class="collide"' : "";
    return `<tr${{cls}}><td>${{r.key}}</td><td>${{r.resolved}}</td>` +
           `<td>${{r.pitch}}</td><td>${{r.rate}}</td>` +
           `<td>${{PREFS[r.key].join(" &middot; ")}}</td></tr>`;
  }}).join("");
  document.getElementById("out").innerHTML =
    `<p>${{voices.length}} voices installed; ` +
    `${{new Set(rows.map(r => r.resolved)).size}} distinct voices resolved across ` +
    `${{rows.length}} profiles. Shaded rows share a resolved voice.</p>` +
    `<table><tr><th>archetype</th><th>resolved voice</th><th>pitch</th><th>rate</th>` +
    `<th>voice_prefs</th></tr>${{body}}</table>` +
    `<h2>installed</h2><pre>${{voices.join("\\n")}}</pre>`;
}}, 2500));
</script>
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    page = _build_page().encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *_args):
            pass

    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"probe at http://127.0.0.1:{args.port}/  (ctrl-c to stop)", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
