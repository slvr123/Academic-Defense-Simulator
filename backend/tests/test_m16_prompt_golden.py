"""M16 Decision 2's gate: with no language line, every changed v1 question prompt renders
byte for byte as it did before M16. The golden files were captured on the parent commit
(see `prompt_golden_cases`)."""

from __future__ import annotations

import pytest

from backend.tests.prompt_golden_cases import CASES, golden_path


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_prompt_with_no_language_line_is_byte_identical_to_its_pre_m16_rendering(name):
    assert CASES[name]().encode("utf-8") == golden_path(name).read_bytes()
