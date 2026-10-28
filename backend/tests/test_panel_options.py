"""GET /panel-options mirrors v1's rosters, titles and icons, and calls no model."""

from __future__ import annotations

import json
from pathlib import Path

from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY, PANEL_COMPOSITION, image_icon_path
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG
from backend.schemas import MAX_DOMAIN_PANELISTS, PanelConfig


def _composition_key(roster: dict) -> str:
    return PanelConfig(defense_type=roster["defense_type"], other_subtype=roster["other_subtype"]).composition_key


def test_every_composition_key_has_its_roster_in_v1_order(client_factory, gemini):
    with client_factory() as client:
        body = client.get("/panel-options").json()
    rosters = {_composition_key(r): [a["archetype_key"] for a in r["archetypes"]] for r in body["rosters"]}
    assert rosters == PANEL_COMPOSITION
    assert body["max_domain_panelists"] == MAX_DOMAIN_PANELISTS == 3
    assert sum(gemini.calls_by_role.values()) == 0


def test_titles_and_icons_come_from_v1(client_factory):
    with client_factory() as client:
        body = client.get("/panel-options").json()
    options = [a for r in body["rosters"] for a in r["archetypes"]] + [body["devils_advocate"]]
    for option in options:
        assert option["title"] == ARCHETYPE_CONFIG[option["archetype_key"]]["archetype_title"]
        assert image_icon_path(option["icon"]) is not None, option  # an image, not the emoji fallback
    assert body["devils_advocate"]["archetype_key"] == DEVILS_ADVOCATE_KEY


def test_the_other_type_is_split_by_subtype_and_the_rest_have_none(client_factory):
    with client_factory() as client:
        rosters = client.get("/panel-options").json()["rosters"]
    pairs = {(r["defense_type"], r["other_subtype"]) for r in rosters}
    assert ("thesis", None) in pairs and ("capstone", None) in pairs
    assert {s for t, s in pairs if t == "other"} == {
        "oral_comps", "scholarship_panel", "grant_defense", "certification_interview"
    }


def test_the_openapi_schema_carries_panel_config_and_the_upload_body(client_factory):
    """The frontend generates its types from /openapi.json; POST /sessions reads its form
    by hand, so PanelConfig and the multipart body are added to the schema explicitly."""
    with client_factory() as client:
        schema = client.get("/openapi.json").json()
    panel = schema["components"]["schemas"]["PanelConfig"]
    assert set(panel["properties"]) == set(PanelConfig.model_fields)
    assert panel["properties"]["domain"]["anyOf"][0]["maxLength"] == 200
    body = schema["paths"]["/sessions"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["properties"]["panel"]["contentSchema"] == {"$ref": "#/components/schemas/PanelConfig"}
    assert body["required"] == ["file"]


def test_the_frontend_preset_fixture_is_the_live_response(client_factory):
    """M10 N7: the frontend checks its panel presets against this copy of /panel-options
    (frontend/src/lib/presets.test.ts); it must be what the endpoint returns today."""
    fixture = Path(__file__).resolve().parents[2] / "frontend" / "src" / "test" / "panelOptions.real.json"
    with client_factory() as client:
        assert client.get("/panel-options").json() == json.loads(fixture.read_text(encoding="utf-8"))
