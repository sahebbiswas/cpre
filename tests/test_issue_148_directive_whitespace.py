"""Regression tests for issue #148: directive-leading whitespace."""

import cpre


def test_non_ascii_and_non_whitespace_prefixes_do_not_start_directives() -> None:
    for prefix in ("\x85", "\x1c"):
        source = f"{prefix}#if 0\nhidden\n"

        assert cpre.parse_conditionals(source).directives == ()
        analysis = cpre.analyze_source(source)
        assert analysis.complete
        assert analysis.findings == ()
        output = cpre.preprocess_source(source).source
        assert "#if 0" in output
        assert "hidden" in output
        assert cpre.unknown_macros_in_source(source) == ()


def test_allowed_directive_leading_whitespace_is_consistent() -> None:
    for prefix in ("", "\f", "\v"):
        source = f"{prefix}#if 0\nhidden\n#endif\n"

        assert [item.kind for item in cpre.parse_conditionals(source).directives] == [
            "if",
            "endif",
        ]
        assert "hidden" not in cpre.preprocess_source(source).source


def test_newline_before_directive_still_starts_a_new_line() -> None:
    source = "\n#if 0\nhidden\n#endif\n"
    assert [item.kind for item in cpre.parse_conditionals(source).directives] == [
        "if",
        "endif",
    ]
    assert "hidden" not in cpre.preprocess_source(source).source
