"""v1.2.1 Phase 2 — the Decision 6/7/8 call-site tests for `_render_voice_component`.

`streamlit_app.py` imports cleanly outside a running app ("bare mode"), the same
property `test_voice.py` and `test_example_session.py` already rely on, so the
call site is exercised directly here rather than through a live browser session.

Decision 6's whole point is that this is the ONLY place the v1.2 browser path is
touched — one branch, fail-open on any failure. The first three tests below prove
the inverse of that: with the branch not engaged (browser, unset, or an unknown
provider value), the rendered output is byte-identical to calling
`_voice_component_html` directly, which is what "demonstrated, not asserted"
means for the Part 3 DoD line about `ADS_TTS_PROVIDER=browser`/unset.
"""

from __future__ import annotations

import academic_defense_simulator.streamlit_app as app
from academic_defense_simulator.mimo_provider import MimoTTSError

FAKE_AUDIO = b"RIFF\x00\x00\x00\x00WAVEfake-audio-bytes-not-a-real-wav-file"


def _real_gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")


def _reset_voice_session(monkeypatch):
    _real_gemini_key(monkeypatch)
    app.st.session_state.pop("session_id", None)
    app.st.session_state.pop("voice_session_id", None)
    app._cached_mimo_audio.clear()


def _capture_rendered_html(monkeypatch):
    """Stands in for "what actually got rendered", the same way
    `_voice_component_html`'s own tests in test_voice.py assert on its return
    value directly — `components.html` has no return value worth reading, so the
    call itself is what's captured."""
    captured: dict = {}

    def fake_html(html, **kwargs):
        captured["html"] = html
        captured["kwargs"] = kwargs

    monkeypatch.setattr(app.components, "html", fake_html)
    return captured


def _force_mimo(monkeypatch, api_key="test-mimo-key-not-real"):
    """v1.2.2 Decision 3 (retired 2026-07-31): Mimo no longer needs a separate
    session-level toggle acknowledged — resolving as the provider is enough."""
    monkeypatch.setenv("ADS_TTS_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", api_key)


# ---------------------------------------------------------------------------
# Part 3 DoD: "ADS_TTS_PROVIDER=browser and unset behave identically to v1.2 —
# demonstrated, not asserted."
# ---------------------------------------------------------------------------


def test_browser_provider_renders_the_unmodified_browser_component(monkeypatch):
    _reset_voice_session(monkeypatch)
    monkeypatch.setenv("ADS_TTS_PROVIDER", "browser")
    captured = _capture_rendered_html(monkeypatch)

    app._render_voice_component("A real question?", "methodology_expert", 1)

    expected = app._voice_component_html(
        "A real question?", "methodology_expert", app._voice_guard_key(1)
    )
    assert captured["html"] == expected
    assert captured["kwargs"] == {"height": 42}


def test_no_key_renders_the_unmodified_browser_component(monkeypatch):
    """v1.2.2 Task 4: this test used to assert on `ADS_TTS_PROVIDER` unset,
    back when unset always meant browser. It no longer does — v1.2.2 Decision 2
    makes the default key-presence-driven, so the actual "renders browser"
    guarantee is keyed on the absence of `MIMO_API_KEY`, not on the provider
    env var's literal unset-ness."""
    _reset_voice_session(monkeypatch)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("ADS_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    captured = _capture_rendered_html(monkeypatch)

    app._render_voice_component("A real question?", "methodology_expert", 2)

    expected = app._voice_component_html(
        "A real question?", "methodology_expert", app._voice_guard_key(2)
    )
    assert captured["html"] == expected


def test_no_key_never_attempts_a_mimo_network_call(monkeypatch):
    """v1.2.2 Decision 2's other half: "key absence must resolve to browser
    with no failed network call — do not default to mimo and rely on
    fail-open to catch it." Proven, not assumed: `synthesize_speech` is made
    to raise if it is ever called at all, and the render still has to
    succeed — with an EXPLICIT `ADS_TTS_PROVIDER=mimo`, not just unset, since
    that's the stricter of the two no-key paths.

    S.3-class fix (2026-07-31, caught here on 2026-07-31 during the Decision 3
    retirement): `delenv("MIMO_API_KEY")` alone is not "unset" on a machine
    whose real `.env` sets it — `load_dotenv()` inside `load_settings()`
    silently repopulates it, which is exactly what made this test's mock get
    called with a real key and print it into a pytest traceback the first
    time this test ran without the `dotenv.load_dotenv` no-op below."""
    _reset_voice_session(monkeypatch)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("ADS_TTS_PROVIDER", "mimo")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)

    def _must_not_be_called(text, archetype_key, api_key):
        raise AssertionError("synthesize_speech was called despite no MIMO_API_KEY being set")

    monkeypatch.setattr(app, "synthesize_speech", _must_not_be_called)
    captured = _capture_rendered_html(monkeypatch)

    app._render_voice_component("A real question?", "methodology_expert", 7)

    expected = app._voice_component_html(
        "A real question?", "methodology_expert", app._voice_guard_key(7)
    )
    assert captured["html"] == expected


def test_unknown_provider_value_also_renders_the_unmodified_browser_component(monkeypatch):
    _reset_voice_session(monkeypatch)
    monkeypatch.setenv("ADS_TTS_PROVIDER", "some-typo'd-value")
    captured = _capture_rendered_html(monkeypatch)

    app._render_voice_component("A real question?", "methodology_expert", 3)

    expected = app._voice_component_html(
        "A real question?", "methodology_expert", app._voice_guard_key(3)
    )
    assert captured["html"] == expected


# ---------------------------------------------------------------------------
# Decision 8's fourth new test: a forced synthesis failure falls back to
# browser and the turn completes. Fault injected by monkeypatching
# `synthesize_speech` (as imported into streamlit_app's namespace) to raise
# `MimoTTSError` unconditionally — the exact exception type mimo_provider.py's
# own except clause wraps every real failure mode (network, auth, quota,
# malformed response, timeout) into, so this exercises the real catch path,
# not a stand-in for it.
# ---------------------------------------------------------------------------


def test_forced_mimo_failure_falls_back_to_browser_and_the_turn_completes(monkeypatch):
    _reset_voice_session(monkeypatch)
    _force_mimo(monkeypatch)

    def _always_fails(text, archetype_key, api_key):
        raise MimoTTSError("forced failure: simulated network timeout")

    monkeypatch.setattr(app, "synthesize_speech", _always_fails)
    captured = _capture_rendered_html(monkeypatch)

    # No exception escapes _render_voice_component — the turn completes.
    app._render_voice_component("A real question?", "methodology_expert", 4)

    expected = app._voice_component_html(
        "A real question?", "methodology_expert", app._voice_guard_key(4)
    )
    assert captured["html"] == expected, "forced Mimo failure did not fall back to the browser component"


def test_successful_mimo_synthesis_renders_audio_not_the_browser_component(monkeypatch):
    """The positive case beside the forced-failure one above: when synthesis
    succeeds, `st.audio` is used and the browser iframe is never rendered."""
    _reset_voice_session(monkeypatch)
    _force_mimo(monkeypatch)
    monkeypatch.setattr(app, "synthesize_speech", lambda text, archetype_key, api_key: FAKE_AUDIO)
    audio_calls = []
    monkeypatch.setattr(app.st, "audio", lambda data, **kwargs: audio_calls.append((data, kwargs)))
    html_calls = _capture_rendered_html(monkeypatch)

    app._render_voice_component("A real question?", "methodology_expert", 5)

    assert html_calls == {}, "browser component rendered even though Mimo synthesis succeeded"
    assert len(audio_calls) == 1
    assert audio_calls[0][0] == FAKE_AUDIO
    assert audio_calls[0][1]["autoplay"] is True


# ---------------------------------------------------------------------------
# Part 3 DoD: "Cache verified; synthesis call count on rerun shown" (Decision 7).
# ---------------------------------------------------------------------------


def test_rerun_with_the_same_question_issues_zero_additional_synthesis_calls(monkeypatch):
    """A Streamlit rerun re-executes the whole script, so `_render_voice_component`
    is called again with the exact same (question, archetype, turn_num). Simulated
    here by calling it twice and counting real invocations of the underlying
    `synthesize_speech` — Decision 7 requires this count to stay at 1."""
    _reset_voice_session(monkeypatch)
    _force_mimo(monkeypatch)
    call_count = {"n": 0}

    def _counting_success(text, archetype_key, api_key):
        call_count["n"] += 1
        return FAKE_AUDIO

    monkeypatch.setattr(app, "synthesize_speech", _counting_success)
    monkeypatch.setattr(app.st, "audio", lambda *args, **kwargs: None)

    app._render_voice_component("Same question both times.", "methodology_expert", 6)
    app._render_voice_component("Same question both times.", "methodology_expert", 6)

    assert call_count["n"] == 1, f"expected exactly 1 real synthesis call across two renders, got {call_count['n']}"
