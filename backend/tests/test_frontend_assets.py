"""The frontend's copies of what the backend can send (M3 decisions D11): every icon
name a panelist or /panel-options can carry has a file the static site serves, and
the frontend knows the Devil's Advocate by v1's key (M8 K6 retired the per-archetype
accents). Fails on drift in either direction."""

from __future__ import annotations

from pathlib import Path

from academic_defense_simulator.panel import ICON_ASSETS_DIR, archetype_default_icon
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
STATIC_ICONS = FRONTEND / "public" / "icons"


def test_every_icon_the_backend_can_send_has_a_file_on_the_static_site():
    # archetype_default_icon is what /start's PanelistOut.icon and /panel-options carry;
    # the API exposes no per-panelist icon override.
    missing = {
        key: icon
        for key in ARCHETYPE_CONFIG
        if not (STATIC_ICONS / f"{(icon := archetype_default_icon(key))}.png").is_file()
    }
    assert missing == {}


def test_the_static_icons_are_byte_identical_copies_of_v1s():
    v1 = {p.name: p.read_bytes() for p in ICON_ASSETS_DIR.glob("*.png")}
    static = {p.name: p.read_bytes() for p in STATIC_ICONS.glob("*.png")}
    assert v1 and static == v1


def test_the_frontend_knows_the_devils_advocate_by_v1s_key():
    # M8 K6 retired the per-archetype accents (only the active panelist is lit); what the
    # bench still needs per archetype is which plate is the Devil's Advocate's.
    from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY

    panel = (FRONTEND / "src" / "lib" / "panel.ts").read_text(encoding="utf-8")
    assert f"export const DEVILS_ADVOCATE_KEY = '{DEVILS_ADVOCATE_KEY}'" in panel


def test_the_frontend_voice_map_is_v1s_voice_profiles_exactly():
    # M3b E12: the browser voice is v1's, unchanged. The frontend's copy is JSON so
    # this compares values, not text; any edit on either side fails here.
    import json

    from academic_defense_simulator.panel import VOICE_PROFILES

    copy = json.loads((FRONTEND / "src" / "voice" / "voiceProfiles.json").read_text(encoding="utf-8"))
    assert copy == VOICE_PROFILES
