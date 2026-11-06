"""M16 Decision 2's gate: with no language line, every changed v1 question prompt renders
byte for byte as it did before M16, and so does M15's closing prompt (Decision 3: English
sessions stay identical). The golden files were captured on the parent commit (see
`prompt_golden_cases`)."""

from __future__ import annotations

import pytest

from backend.tests.prompt_golden_cases import CASES, golden_path


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_prompt_with_no_language_line_is_byte_identical_to_its_pre_m16_rendering(name):
    assert CASES[name]().encode("utf-8") == golden_path(name).read_bytes()


@pytest.mark.parametrize("name", sorted(CASES))
def test_passing_an_empty_language_line_explicitly_is_byte_identical_too(name):
    assert CASES[name](language_line="").encode("utf-8") == golden_path(name).read_bytes()


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_language_line_lands_once_in_the_brief_before_the_output_format(name):
    marker = "\n- Language: MARKER-LINE"
    prompt = CASES[name](language_line=marker)
    assert prompt.count("MARKER-LINE") == 1
    output_format = "Return only the closing line" if name.startswith("closing") else "Respond ONLY with JSON"
    assert prompt.index("MARKER-LINE") < prompt.index(output_format)
    # Taking the line out again gives back the golden rendering exactly.
    assert prompt.replace(marker, "", 1).encode("utf-8") == golden_path(name).read_bytes()
