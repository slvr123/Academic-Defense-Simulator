"""v1.2 panelist-voice tests (docs/v1.2-panelist-voice-decisions.md).

Scope is deliberately what a test can actually decide: the archetype -> profile map is
complete against the registry, the question text is escaped so it cannot break out of a
`<script>` block, and the generated component carries the right profile and guard key.

Decision 10, stated plainly rather than implied by an absent test: **no test here
asserts that audio was produced.** Whether sound came out, and whether two seated
panelists are distinguishable by ear, is a DoD item 5 listening pass, not something
`speechSynthesis` exposes to a headless assertion.

`streamlit_app.py` imports cleanly outside a running app ("bare mode": `st.session_state`
degrades to a working in-memory dict-like), the same property `test_streamlit_persist.py`
already relies on — so the component builders are called directly here.
"""

from __future__ import annotations

import re

import pytest

import academic_defense_simulator.streamlit_app as app
from academic_defense_simulator.panel import (
    DEVILS_ADVOCATE_KEY,
    PANEL_COMPOSITION,
    VOICE_PROFILES,
)
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG


# ---------------------------------------------------------------------------
# Completeness (Brief step 3) — the reason Decision 5's from-memory key table is
# safe to have been wrong. A tenth archetype, or a typo'd key, is a red test here
# instead of a KeyError inside `_voice_component_html` in front of a visitor.
# ---------------------------------------------------------------------------


def test_every_registry_archetype_has_a_voice_profile():
    missing = sorted(set(ARCHETYPE_CONFIG) - set(VOICE_PROFILES))
    assert missing == [], f"archetypes with no voice profile: {missing}"


def test_every_voice_profile_names_a_real_archetype():
    unknown = sorted(set(VOICE_PROFILES) - set(ARCHETYPE_CONFIG))
    assert unknown == [], f"voice profiles for non-existent archetypes: {unknown}"


@pytest.mark.parametrize("archetype_key", sorted(VOICE_PROFILES))
def test_voice_profile_shape_and_browser_ranges(archetype_key):
    """Decision 4: pitch is 0.0-2.0 and rate is 0.1-10.0 in the Web Speech API. Out of
    range is not an exception — the browser silently clamps, which would quietly undo
    the separation the values exist to create."""
    profile = VOICE_PROFILES[archetype_key]
    assert set(profile) == {"voice_prefs", "pitch", "rate"}
    assert profile["voice_prefs"], "an empty preference list is always the default voice"
    assert all(isinstance(name, str) and name for name in profile["voice_prefs"])
    assert 0.0 <= profile["pitch"] <= 2.0
    assert 0.1 <= profile["rate"] <= 10.0


def test_no_two_archetypes_share_an_identical_profile():
    """Two archetypes with byte-identical profiles are guaranteed indistinguishable if
    both are seated. This is the floor, not the target — Decision 5's real requirement
    is audible separation on the seated panel, which only the by-ear pass can settle."""
    seen: dict[tuple, str] = {}
    for key, profile in VOICE_PROFILES.items():
        signature = (tuple(profile["voice_prefs"]), profile["pitch"], profile["rate"])
        assert signature not in seen, f"{key} has the same profile as {seen.get(signature)}"
        seen[signature] = key


# ---------------------------------------------------------------------------
# Preference spread — the regression guard for what the v1.2 resolution probe found.
#
# The seed table named three English voices for nine archetypes, so every archetype
# resolved into a group of three sharing one voice, the closest co-seatable pair
# 0.07 pitch apart. Nothing in the completeness tests above catches that: the map was
# complete, well-formed, and in range the whole time. These assert the property that
# was actually violated. See scripts/probe_voice_resolution.py for the live evidence.
# ---------------------------------------------------------------------------

MIN_SHARED_VOICE_PITCH_GAP = 0.30


def _first_preference_groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for key, profile in VOICE_PROFILES.items():
        groups.setdefault(profile["voice_prefs"][0], []).append(key)
    return groups


def test_first_preferences_spread_over_enough_voices():
    """Every pair of domain archetypes can be seated together — `capstone` lists all
    eight — so there is no pair that is safe to collapse onto one voice for free."""
    groups = _first_preference_groups()
    oversubscribed = {voice: keys for voice, keys in groups.items() if len(keys) > 2}
    assert oversubscribed == {}, f"more than two archetypes on one voice: {oversubscribed}"


def test_archetypes_sharing_a_first_preference_are_far_apart_in_pitch():
    """Where a voice is doubled up, pitch is the only thing separating the two, so the
    gap has to be wide. 0.07 — the seed table's worst pair — is not a gap."""
    for voice, keys in _first_preference_groups().items():
        if len(keys) < 2:
            continue
        ordered = sorted(keys, key=lambda k: VOICE_PROFILES[k]["pitch"])
        for lower, upper in zip(ordered, ordered[1:]):
            gap = VOICE_PROFILES[upper]["pitch"] - VOICE_PROFILES[lower]["pitch"]
            assert gap >= MIN_SHARED_VOICE_PITCH_GAP, (
                f"{lower} and {upper} share {voice!r} but are only {gap:.2f} apart in pitch"
            )


def test_devils_advocate_does_not_share_a_first_preference():
    """`compose_full_roster` appends Devil's Advocate to every session, so it is the one
    archetype guaranteed to be in the room with whatever else got seated. Sharing a voice
    with any domain archetype means a guaranteed collision rather than a possible one."""
    da_voice = VOICE_PROFILES[DEVILS_ADVOCATE_KEY]["voice_prefs"][0]
    others = [k for k in VOICE_PROFILES if k != DEVILS_ADVOCATE_KEY]
    clashes = [k for k in others if VOICE_PROFILES[k]["voice_prefs"][0] == da_voice]
    assert clashes == [], f"Devil's Advocate shares its voice with {clashes}"


def _default_panels() -> dict[str, list[str]]:
    """The panel the UI pre-selects for each defense type: the first three of the
    composition roster, plus Devil's Advocate, which `compose_full_roster` appends to
    every roster unconditionally."""
    return {ck: roster[:3] + [DEVILS_ADVOCATE_KEY] for ck, roster in PANEL_COMPOSITION.items()}


def test_default_panels_have_no_repeated_first_preference():
    """The strict form of the per-panel property, and it is only assertable because the
    inventory happens to allow it.

    Edge offers three male English voices, and the avatar set is five male to four
    female, so male reuse across the nine archetypes is forced. Whether that reuse can
    be kept *out of* every default panel is a separate question, and it comes down to
    the worst panel: `capstone` and `other/certification_interview` each seat three
    male archetypes. Three seats, three male voices, so the strict property is exactly
    satisfiable — with no margin. Add a fourth male archetype to either panel and this
    test becomes unsatisfiable and must be weakened to "at most one shared pair per
    panel, shared pairs at least 0.30 apart in pitch"; see the Decision 5 amendment."""
    for comp_key, members in _default_panels().items():
        firsts = [VOICE_PROFILES[m]["voice_prefs"][0] for m in members]
        dupes = {v for v in firsts if firsts.count(v) > 1}
        assert not dupes, (
            f"default panel {comp_key!r} seats two archetypes wanting the same voice "
            f"{dupes}: " + ", ".join(f"{m}->{VOICE_PROFILES[m]['voice_prefs'][0]}" for m in members)
        )


def test_fallback_tiers_are_spread_too():
    """Every "Google ..." voice is network-only, so an offline visitor falls straight
    through to the local tier. If that tier is one name, the panel is one voice."""
    for tier in (1, 2):
        names = [p["voice_prefs"][tier] for p in VOICE_PROFILES.values() if len(p["voice_prefs"]) > tier]
        counts = {name: names.count(name) for name in set(names)}
        crowded = {name: n for name, n in counts.items() if n > 2}
        assert crowded == {}, f"preference tier {tier} crowds onto {crowded}"


# ---------------------------------------------------------------------------
# Escaping (Brief step 4) — Decision 7 part 4
# ---------------------------------------------------------------------------


def test_escaping_double_quote():
    assert app._js_string_literal('He said "no".') == '"He said \\"no\\"."'


def test_escaping_single_quote():
    """An apostrophe needs no escaping inside a double-quoted JS literal, and json.dumps
    correctly leaves it alone — asserted so a future switch to single quotes is caught."""
    assert app._js_string_literal("the study's scope") == '"the study\'s scope"'


def test_escaping_newline():
    assert app._js_string_literal("line one\nline two") == '"line one\\nline two"'


def test_escaping_script_terminator():
    """The case json.dumps alone does not cover. An HTML parser ends the script element
    at `</script>` regardless of JS string context, so the literal must not contain it.
    `<\\/` is the same string to JS and invisible to the HTML parser."""
    escaped = app._js_string_literal("a naive <script>alert(1)</script> tag")
    assert "</" not in escaped
    assert "<\\/script>" in escaped
    assert escaped == '"a naive <script>alert(1)<\\/script> tag"'


def test_escaping_list_produces_a_js_array():
    assert app._js_string_literal_list(["Daniel", 'Say "Hi"']) == '["Daniel", "Say \\"Hi\\""]'


# ---------------------------------------------------------------------------
# Component generation
# ---------------------------------------------------------------------------


def test_component_binds_the_archetype_profile():
    html = app._voice_component_html("Why this sample?", "devils_advocate", "ads-spoken:s:1")
    profile = VOICE_PROFILES["devils_advocate"]
    assert f"var PITCH = {profile['pitch']};" in html
    assert f"var RATE  = {profile['rate']};" in html
    for name in profile["voice_prefs"]:
        assert f'"{name}"' in html
    assert 'var GUARD = "ads-spoken:s:1";' in html


def test_component_survives_a_script_terminator_in_the_question():
    """End to end for the escaping helper: an LLM-generated question containing a
    closing script tag must not terminate the component's own script block early. The
    only `</script>` left in the document is the one this function wrote."""
    html = app._voice_component_html(
        'Your appendix literally contains </script> — why?', "methodology_expert", "ads-spoken:s:2"
    )
    assert html.count("</script>") == 1
    assert "<\\/script>" in html


def test_component_applies_pitch_and_rate_outside_the_voice_match_branch():
    """Decision 4 step 4 is the load-bearing one: pitch and rate must be set on the
    utterance unconditionally, not inside the `if (voice)` block that only runs when a
    preferred voice resolved. Asserted structurally because the no-match path is what
    most visitors will actually get."""
    html = app._voice_component_html("q", "methodology_expert", "g")
    body = html[html.index("function speak()") :]
    match_branch = re.search(r"if \(voice\) \{\{?(.*?)\}\}?\n", body)
    assert match_branch is not None
    assert "pitch" not in match_branch.group(1)
    assert "rate" not in match_branch.group(1)
    assert "utterance.pitch = PITCH;" in body
    assert "utterance.rate  = RATE;" in body


def test_component_waits_for_voiceschanged():
    """Decision 4 step 1 — the single most common way this feature ships silent."""
    html = app._voice_component_html("q", "methodology_expert", "g")
    assert 'addEventListener("voiceschanged"' in html


def test_component_guards_auto_speak_on_session_storage():
    """Decision 7 part 1: the rerun re-speak guard reads and writes the iframe's own
    sessionStorage, not st.session_state."""
    html = app._voice_component_html("q", "methodology_expert", "g")
    assert "window.sessionStorage.getItem(GUARD)" in html
    assert 'window.sessionStorage.setItem(GUARD, "1")' in html


def test_component_renders_both_controls():
    html = app._voice_component_html("q", "methodology_expert", "g")
    assert 'id="ads-voice-play"' in html
    assert 'id="ads-voice-stop"' in html
    assert "synth.cancel();" in html


def test_unknown_archetype_is_a_keyerror_not_a_silent_default():
    """The completeness tests above are what stop this ever firing in the app."""
    with pytest.raises(KeyError):
        app._voice_component_html("q", "not_an_archetype", "g")


# ---------------------------------------------------------------------------
# Guard key
# ---------------------------------------------------------------------------


def test_guard_key_uses_session_id_when_persistence_minted_one():
    app.st.session_state.session_id = "abc-123"
    try:
        assert app._voice_guard_key(3) == "ads-spoken:abc-123:3"
    finally:
        del app.st.session_state.session_id


def test_guard_key_falls_back_to_a_stable_browser_session_uuid():
    """`session_id` only exists when persistence is enabled (v0.4b Decision 2). The
    fallback must be stable across reruns or the guard never matches and every rerun
    re-speaks — the exact failure Decision 7 exists to prevent."""
    app.st.session_state.pop("session_id", None)
    app.st.session_state.pop("voice_session_id", None)

    first = app._voice_guard_key(1)
    second = app._voice_guard_key(1)
    assert first == second
    assert first.startswith("ads-spoken:")
    assert first.endswith(":1")

    assert app._voice_guard_key(2) != first
