"""Skipped includes are assumed not to change names that are already settled (#120, documented)."""

import pytest

from cpre import MacroConfiguration, preprocess_source


@pytest.mark.parametrize("policy", ["open", "undefined"])
def test_source_defined_name_keeps_its_state_across_a_skipped_include(policy):
    source = '#define FEATURE 1\n#include "config.h"\n#if FEATURE\nint on;\n#endif\n'
    result = preprocess_source(
        source,
        skip_includes=True,
        configuration=MacroConfiguration(unknown_names=policy),
    )
    assert result.complete
    assert "int on;" in result.source


@pytest.mark.parametrize("policy", ["open", "undefined"])
def test_configured_name_keeps_its_state_across_a_skipped_include(policy):
    source = '#include "config.h"\n#if FEATURE\nint on;\n#endif\n'
    result = preprocess_source(
        source,
        skip_includes=True,
        configuration=MacroConfiguration(integers={"FEATURE": 1}, unknown_names=policy),
    )
    assert result.complete
    assert "int on;" in result.source


def test_an_unsettled_name_becomes_unknown_after_a_skipped_include():
    source = '#include "config.h"\n#if OTHER\nint on;\n#endif\n'
    result = preprocess_source(
        source,
        skip_includes=True,
        configuration=MacroConfiguration(unknown_names="undefined"),
    )
    assert not result.complete
