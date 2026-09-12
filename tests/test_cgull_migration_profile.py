from __future__ import annotations

import json
from pathlib import Path

from pycparser import c_parser

from cpre import ErrorCode, preprocess_source


COMPATIBILITY = Path(__file__).parent / "compatibility"
FIXTURES = COMPATIBILITY / "fixtures"
PROFILE_FIXTURES = COMPATIBILITY / "cgull_profile"
PROFILE_PATH = COMPATIBILITY / "cgull-profile.json"


def _profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _assert_atomic_diagnostic(source: str, code: ErrorCode, line: int) -> None:
    result = preprocess_source(source, filename="outside-profile.c")
    assert not result.complete
    assert result.source is None
    assert result.source_map is None
    assert result.macros is None
    diagnostic, = result.incomplete
    assert diagnostic.code is code
    assert diagnostic.location.line == line


def test_cgull_profile_is_pinned_and_keeps_boundary_narrow():
    profile = _profile()

    assert profile["schema_version"] == 1
    assert profile["downstream"] == {
        "repository": "sahebbiswas/cgull",
        "commit": "c61b27520624c074661afa0a615160944b31ab3d",
    }
    assert profile["boundary"]["resolved_project_headers"] == "expanded_before_cpre"
    assert profile["boundary"]["unresolved_includes"] == (
        "mask_active_directive_lines_preserve_line_endings"
    )
    assert profile["boundary"]["raw_reachable_includes_supported"] is False
    assert profile["boundary"]["typedef_prelude_stage"] == "after_cpre"
    assert profile["boundary"]["configuration_api"] == "MacroConfiguration"
    assert profile["boundary"]["unknown_name_policy"] == "closed"
    assert profile["boundary"]["source_definitions_override_configuration"] is True

    va_opt = profile["exclusions"]["__VA_OPT__"]
    assert va_opt["supported"] is False
    assert va_opt["occurrences_in_pinned_downstream_corpus"] == 0
    assert va_opt["diagnostic"] == ErrorCode.UNSUPPORTED_MACRO_EXPANSION.value

    include = profile["exclusions"]["raw_reachable_include"]
    assert include["supported"] is False
    assert include["diagnostic"] == ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE.value


def test_prepared_cgull_translation_unit_is_supported_and_parseable():
    source = (PROFILE_FIXTURES / "prepared_translation_unit.c").read_text(
        encoding="utf-8"
    )

    # Physical line 1 models an unresolved external include already masked by the
    # C-GULL adapter. Resolved project-header content follows inline.
    assert source.startswith("\n")
    assert "#include" not in source

    result = preprocess_source(source, filename="prepared_translation_unit.c")
    assert result.complete, result.incomplete
    assert result.source is not None
    assert result.source_map is not None
    assert result.source.count("\n") == source.count("\n")
    assert "header_value" in result.source
    assert "PROJECT_LIMIT" not in result.source

    c_parser.CParser().parse(result.source, filename="prepared_translation_unit.c")


def test_raw_reachable_include_remains_atomic_outside_profile():
    source = (FIXTURES / "unsupported_include.c").read_text(encoding="utf-8")
    _assert_atomic_diagnostic(
        source,
        ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
        line=1,
    )


def test_va_opt_remains_atomic_outside_profile():
    source = (FIXTURES / "unsupported_va_opt.c").read_text(encoding="utf-8")
    _assert_atomic_diagnostic(
        source,
        ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
        line=2,
    )
