import pytest

from cpre import (
    AnalysisError,
    ErrorCode,
    IncompleteConfigurationError,
    MacroConfiguration,
    MacroDefinition,
    ParseError,
    PreprocessingContext,
    UnknownNamePolicy,
    preprocess_source,
)


def test_plain_defines_and_undefs():
    text = """
#define PRESENCE
#define ONE 1
#define ZERO 0
#undef UNDEFINED_MACRO
"""
    config = MacroConfiguration.from_source(text)
    expected = MacroConfiguration(
        presence={"PRESENCE"},
        integers={"ONE": 1, "ZERO": 0},
        undefined={"UNDEFINED_MACRO"},
    )
    assert config == expected


def test_conditional_selection_and_redefinition():
    text = """
#define FEATURE 1
#if FEATURE
#define SELECTED "yes"
#else
#define SELECTED "no"
#endif
#define FEATURE 2
"""
    config = MacroConfiguration.from_source(text)
    expected = MacroConfiguration(
        integers={"FEATURE": 2}, definitions=[MacroDefinition("SELECTED", '"yes"')]
    )
    assert config == expected


def test_define_inside_inactive_branch_is_ignored():
    text = """
#if 0
#define IGNORED 1
#endif
"""
    config = MacroConfiguration.from_source(text)
    expected = MacroConfiguration()
    assert config == expected


def test_function_like_and_variadic():
    text = """
#define EMPTY()
#define ADD(a, b) (a + b)
#define LOG(...) printf(__VA_ARGS__)
"""
    config = MacroConfiguration.from_source(text)
    expected = MacroConfiguration(
        definitions=[
            MacroDefinition("EMPTY", "", parameters=()),
            MacroDefinition("ADD", "(a + b)", parameters=("a", "b")),
            MacroDefinition("LOG", "printf(__VA_ARGS__)", parameters=(), variadic=True),
        ]
    )
    assert config == expected


def test_non_decimal_and_suffixed_integers_stay_definitions():
    text = """
#define PLUS_ONE +1
#define HEX 0x10
#define OCTAL 00
#define SUFFIX 1U
#define PAREN (1)
"""
    config = MacroConfiguration.from_source(text)
    expected = MacroConfiguration(
        definitions=[
            MacroDefinition("PLUS_ONE", "+1"),
            MacroDefinition("HEX", "0x10"),
            MacroDefinition("OCTAL", "00"),
            MacroDefinition("SUFFIX", "1U"),
            MacroDefinition("PAREN", "(1)"),
        ]
    )
    assert config == expected


def test_context_stripping():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    text = """
#define FEATURE 1
"""
    config = MacroConfiguration.from_source(text, context=context)
    expected = MacroConfiguration(integers={"FEATURE": 1})
    assert config == expected


@pytest.mark.parametrize(
    ("directive", "verb"),
    [
        ("#undef __STDC__", "undefines"),
        ("#define __STDC__ 0", "redefines"),
        ("#define __STDC__ 1", "redefines"),
        ("#define __STDC__(x) x", "redefines"),
    ],
)
def test_seed_changing_context_macro_is_rejected_at_directive(directive, verb):
    # A configuration carrying the change could never be used with the same
    # context, so the seed is rejected and the seed (not the target) is named.
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    text = f"#define FEATURE 1\n{directive}\n"
    with pytest.raises(AnalysisError) as excinfo:
        MacroConfiguration.from_source(text, filename="seed.h", context=context)
    error = excinfo.value
    assert error.code is ErrorCode.INVALID_CONFIGURATION
    assert error.filename == "seed.h"
    assert error.location is not None and error.location.line == 2
    assert f"seed {verb} __STDC__" in error.message
    assert not isinstance(error, IncompleteConfigurationError)


def test_seed_inactive_change_to_context_macro_is_allowed():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    text = "#if 0\n#undef __STDC__\n#endif\n#ifndef __STDC__\n#define __STDC__ 0\n#endif\n"
    config = MacroConfiguration.from_source(text, context=context)
    assert config == MacroConfiguration()
    result = preprocess_source("int x = __STDC__;\n", configuration=config, context=context)
    assert result.complete


def test_context_names_are_not_protected_without_context():
    config = MacroConfiguration.from_source("#undef __STDC__\n")
    assert config == MacroConfiguration(undefined={"__STDC__"})
    result = preprocess_source("#undef __STDC__\nint x;\n")
    assert result.complete


def test_unknown_names_policy():
    text = """
#if UNKNOWN
#define ACTIVE 1
#endif
"""
    with pytest.raises(IncompleteConfigurationError):
        MacroConfiguration.from_source(text)

    config = MacroConfiguration.from_source(text, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert config == MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED)


def test_incomplete_seed_raises_exception():
    text = """
#include "missing.h"
"""
    with pytest.raises(IncompleteConfigurationError) as exc_info:
        MacroConfiguration.from_source(text, filename="test.h")
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert exc_info.value.filename == "test.h"


def test_malformed_conditional_raises_parse_error():
    text = """
#if 1
"""
    with pytest.raises(ParseError):
        MacroConfiguration.from_source(text)


def test_target_override():
    config = MacroConfiguration.from_source("#define NAME 1\\n#define REMOVED 1")
    target = """
#define NAME 2
#undef REMOVED
"""
    result = preprocess_source(target, configuration=config)
    assert result.complete
    assert result.macros["NAME"].definition.replacement == "2"
    assert result.macros["REMOVED"].defined is False


# --- base= layering (#70) ---------------------------------------------------


@pytest.mark.parametrize("bad_base", [{}, "FEATURE", 1, MacroDefinition("FEATURE", "1")])
def test_base_rejects_non_configuration(bad_base, monkeypatch):
    import cpre.pragmas

    def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("preprocessing ran before base validation")

    monkeypatch.setattr(cpre.pragmas, "preprocess_source", fail)
    with pytest.raises(AnalysisError) as exc_info:
        MacroConfiguration.from_source("#define X 1\n", base=bad_base)
    assert exc_info.value.code == ErrorCode.INVALID_CONFIGURATION


def test_base_none_matches_single_seed():
    text = "#define FEATURE 1\n"
    assert MacroConfiguration.from_source(text, base=None) == MacroConfiguration.from_source(text)


def test_base_untouched_name_keeps_integer_category():
    base = MacroConfiguration(integers={"FEATURE": 1})
    config = MacroConfiguration.from_source("#define OTHER\n", base=base)
    assert config == MacroConfiguration(integers={"FEATURE": 1}, presence={"OTHER"})


def test_base_untouched_names_keep_every_category():
    base = MacroConfiguration(
        presence={"PRESENT"},
        integers={"NUMBER": 7},
        undefined={"ABSENT"},
        definitions=[MacroDefinition("FN", "(x)", parameters=("x",))],
    )
    config = MacroConfiguration.from_source("", base=base)
    assert config == base


def test_base_integer_then_seed_undef_is_undefined_only():
    base = MacroConfiguration(integers={"FEATURE": 1})
    config = MacroConfiguration.from_source("#undef FEATURE\n", base=base)
    assert config == MacroConfiguration(undefined={"FEATURE"})
    assert config.definitions == ()


def test_base_undefined_then_seed_define_is_presence_only():
    base = MacroConfiguration(undefined={"NAME"})
    config = MacroConfiguration.from_source("#define NAME\n", base=base)
    assert config == MacroConfiguration(presence={"NAME"})
    assert config.undefined == frozenset()


def test_base_function_like_then_seed_integer_is_integers_only():
    base = MacroConfiguration(definitions=[MacroDefinition("FOO", "(a)", parameters=("a",))])
    config = MacroConfiguration.from_source("#define FOO 3\n", base=base)
    assert config == MacroConfiguration(integers={"FOO": 3})
    assert config.definitions == (MacroDefinition("FOO", "3"),)


def test_base_integer_then_seed_function_like_replaces_category():
    base = MacroConfiguration(integers={"FOO": 3})
    config = MacroConfiguration.from_source("#define FOO(a) (a)\n", base=base)
    assert config == MacroConfiguration(
        definitions=[MacroDefinition("FOO", "(a)", parameters=("a",))]
    )


def test_base_definitions_drive_seed_conditionals():
    base = MacroConfiguration(integers={"FEATURE": 1}, undefined={"LEGACY"})
    text = """
#if FEATURE
#define FROM_FEATURE
#endif
#ifdef LEGACY
#define FROM_LEGACY
#endif
"""
    config = MacroConfiguration.from_source(text, base=base)
    assert config == MacroConfiguration(
        integers={"FEATURE": 1}, undefined={"LEGACY"}, presence={"FROM_FEATURE"}
    )


def test_base_policy_does_not_leak_into_evaluation():
    base = MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED)
    text = """
#if UNKNOWN
#define FEATURE 1
#endif
"""
    with pytest.raises(IncompleteConfigurationError):
        MacroConfiguration.from_source(text, base=base, unknown_names=UnknownNamePolicy.OPEN)


def test_base_policy_does_not_leak_into_result():
    base = MacroConfiguration(integers={"KEPT": 1}, unknown_names=UnknownNamePolicy.UNDEFINED)
    config = MacroConfiguration.from_source(
        "#define FEATURE 1\n", base=base, unknown_names=UnknownNamePolicy.OPEN
    )
    assert config.unknown_names is UnknownNamePolicy.OPEN
    assert config == MacroConfiguration(integers={"KEPT": 1, "FEATURE": 1})


def test_argument_policy_applies_when_base_is_open():
    base = MacroConfiguration(integers={"KEPT": 1})
    text = """
#if UNKNOWN
#define FEATURE 1
#endif
"""
    config = MacroConfiguration.from_source(
        text, base=base, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert config == MacroConfiguration(
        integers={"KEPT": 1}, unknown_names=UnknownNamePolicy.UNDEFINED
    )


def test_base_merge_has_no_cross_category_duplicates_and_sorted_order():
    base = MacroConfiguration(
        presence={"ZETA", "ALPHA"},
        integers={"MIDDLE": 2},
        undefined={"BETA"},
        definitions=[MacroDefinition("GAMMA", "x")],
    )
    text = """
#define BETA 5
#undef ALPHA
#define GAMMA
#define AAA 1
#define ZZZ(x) x
"""
    config = MacroConfiguration.from_source(text, base=base)
    expected = MacroConfiguration(
        presence={"GAMMA", "ZETA"},
        integers={"MIDDLE": 2, "BETA": 5, "AAA": 1},
        undefined={"ALPHA"},
        definitions=[MacroDefinition("ZZZ", "x", parameters=("x",))],
    )
    assert config == expected

    defined_names = [definition.name for definition in config.definitions]
    assert defined_names == sorted(defined_names)
    assert len(defined_names) == len(set(defined_names))
    assert not set(defined_names) & config.undefined


def test_base_with_context_still_strips_injected_names():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    base = MacroConfiguration(integers={"FEATURE": 1})
    config = MacroConfiguration.from_source("#define OTHER 2\n", base=base, context=context)
    assert config == MacroConfiguration(integers={"FEATURE": 1, "OTHER": 2})


def test_base_with_context_rejects_seed_changes_to_injected_names():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    base = MacroConfiguration(integers={"FEATURE": 1})
    with pytest.raises(AnalysisError) as excinfo:
        MacroConfiguration.from_source("#undef __STDC__\n", base=base, context=context)
    assert excinfo.value.code is ErrorCode.INVALID_CONFIGURATION
    assert excinfo.value.location is not None and excinfo.value.location.line == 1


def test_base_is_not_mutated():
    base = MacroConfiguration(integers={"FEATURE": 1})
    snapshot = (base.definitions, base.undefined, base.unknown_names)
    MacroConfiguration.from_source("#undef FEATURE\n#define NEW\n", base=base)
    assert (base.definitions, base.undefined, base.unknown_names) == snapshot


def test_base_defining_context_name_is_rejected_not_silently_stripped():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    base = MacroConfiguration(integers={"__STDC__": 1, "FEATURE": 1})
    with pytest.raises(AnalysisError) as exc_info:
        MacroConfiguration.from_source("#define OTHER 2\n", base=base, context=context)
    assert exc_info.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    "base",
    [
        MacroConfiguration(integers={"__STDC__": 1, "FEATURE": 1}),
        MacroConfiguration(undefined={"__STDC__"}, integers={"FEATURE": 1}),
    ],
)
def test_context_stripping_never_drops_untouched_base_names(base, monkeypatch):
    # Disable the context/configuration conflict check so the stripping guard
    # in from_source() is exercised on its own: base names must survive even
    # though base definitions and context definitions both have location=None.
    import cpre.preprocessing

    monkeypatch.setattr(cpre.preprocessing, "_configured_preprocessing_context", lambda *_: None)
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    config = MacroConfiguration.from_source("#define OTHER 2\n", base=base, context=context)
    assert config == MacroConfiguration(
        definitions=[*base.definitions, MacroDefinition("OTHER", "2")],
        undefined=base.undefined,
    )


# --- include guard detection and exclude= (#71) -----------------------------


def test_guard_detected_enters_body_on_second_preprocess():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H
#define FEATURE 1
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert derived == MacroConfiguration(
        integers={"FEATURE": 1}, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" not in {d.name for d in derived.definitions}
    assert "FLAGS_H" not in derived.undefined

    # Second preprocess enters the body because FLAGS_H is not in derived configuration
    result = preprocess_source(seed, configuration=derived)
    assert result.complete
    assert result.macros["FEATURE"].definition.replacement == "1"
    assert result.macros["FLAGS_H"].defined is True


def test_guard_not_detected_and_not_excluded_skips_body_on_second_preprocess():
    # Code after #endif causes include guard detection not to match
    seed = """
#ifndef FLAGS_H
#define FLAGS_H
#define FEATURE 1
#endif
int after_guard = 1;
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "FLAGS_H" in {d.name for d in derived.definitions}
    assert derived == MacroConfiguration(
        presence={"FLAGS_H"},
        integers={"FEATURE": 1},
        unknown_names=UnknownNamePolicy.UNDEFINED,
    )

    # Second preprocess of a seed expecting FEATURE 2 skips body because FLAGS_H is present
    target = """
#ifndef FLAGS_H
#define FLAGS_H
#define FEATURE 2
#endif
"""
    result = preprocess_source(target, configuration=derived)
    assert result.complete
    # FEATURE remains the value from derived configuration (1), body was skipped
    assert result.macros["FEATURE"].definition.replacement == "1"


def test_exclude_removes_guard_when_detector_does_not_match():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H
#define FEATURE 1
#endif
int after_guard = 1;
"""
    # Detector does not match because of trailing code, but exclude overrides it
    derived = MacroConfiguration.from_source(
        seed, unknown_names=UnknownNamePolicy.UNDEFINED, exclude=("FLAGS_H",)
    )
    assert derived == MacroConfiguration(
        integers={"FEATURE": 1}, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" not in {d.name for d in derived.definitions}

    target = """
#ifndef FLAGS_H
#define FLAGS_H
#define FEATURE 2
#endif
"""
    result = preprocess_source(target, configuration=derived)
    assert result.complete
    # Enters body because FLAGS_H was excluded, updating FEATURE to 2
    assert result.macros["FEATURE"].definition.replacement == "2"


@pytest.mark.parametrize(
    "condition",
    [
        "#if !defined(FLAGS_H)",
        "#if !defined FLAGS_H",
        "#if !(defined(FLAGS_H))",
        "#if (!defined(FLAGS_H))",
        "#if ! defined ( FLAGS_H )",
    ],
)
def test_guard_detected_across_if_not_defined_variants(condition):
    seed = f"""
{condition}
#define FLAGS_H
#define FEATURE 1
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert derived == MacroConfiguration(
        integers={"FEATURE": 1}, unknown_names=UnknownNamePolicy.UNDEFINED
    )


def test_guard_detected_with_comments_and_whitespace():
    seed = """
/* Leading file comment */
// Another comment line

#ifndef FLAGS_H
/* Internal comment */
#define FLAGS_H
#define FEATURE 1
#endif /* FLAGS_H */
// Trailing file comment
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert derived == MacroConfiguration(
        integers={"FEATURE": 1}, unknown_names=UnknownNamePolicy.UNDEFINED
    )


def test_guard_with_integer_definition_stripped():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H 1
#define FEATURE 2
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert derived == MacroConfiguration(
        integers={"FEATURE": 2}, unknown_names=UnknownNamePolicy.UNDEFINED
    )


def test_guard_macro_used_as_later_condition_stays():
    seed_if = """
#ifndef FLAGS_H
#define FLAGS_H 1
#if FLAGS_H
#define FEATURE 1
#endif
#endif
"""
    derived_if = MacroConfiguration.from_source(seed_if, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "FLAGS_H" in {d.name for d in derived_if.definitions}

    seed_ifdef = """
#ifndef FLAGS_H
#define FLAGS_H
#ifdef FLAGS_H
#define FEATURE 1
#endif
#endif
"""
    derived_ifdef = MacroConfiguration.from_source(
        seed_ifdef, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" in {d.name for d in derived_ifdef.definitions}

    seed_defined = """
#ifndef FLAGS_H
#define FLAGS_H
#if defined(FLAGS_H)
#define FEATURE 1
#endif
#endif
"""
    derived_defined = MacroConfiguration.from_source(
        seed_defined, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" in {d.name for d in derived_defined.definitions}


def test_guard_macro_used_as_later_replacement_stays():
    seed_macro_rep = """
#ifndef FLAGS_H
#define FLAGS_H
#define FEATURE FLAGS_H
#endif
"""
    derived = MacroConfiguration.from_source(
        seed_macro_rep, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" in {d.name for d in derived.definitions}

    seed_source_rep = """
#ifndef FLAGS_H
#define FLAGS_H
int x = FLAGS_H;
#endif
"""
    derived_source = MacroConfiguration.from_source(
        seed_source_rep, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" in {d.name for d in derived_source.definitions}


def test_function_like_guard_with_line_splice_not_stripped():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H\\
(x) 1
int body = 1;
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert any(d.name == "FLAGS_H" and d.parameters == ("x",) for d in derived.definitions)

    # Second preprocess of the same source must skip the body
    result = preprocess_source(seed, configuration=derived)
    assert "int body = 1;" not in result.source


def test_function_like_guard_standard_not_stripped():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H(x) 1
int body = 1;
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert any(d.name == "FLAGS_H" and d.parameters == ("x",) for d in derived.definitions)

    result = preprocess_source(seed, configuration=derived)
    assert "int body = 1;" not in result.source


def test_object_like_guard_with_line_splice_and_space_detected():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H\\
 (x) 1
int body = 1;
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "FLAGS_H" not in {d.name for d in derived.definitions}
    result = preprocess_source(seed, configuration=derived)
    assert "int body = 1;" in result.source


def test_object_like_guard_with_space_before_line_splice_detected():
    seed = """
#ifndef FLAGS_H
#define FLAGS_H \\
(x) 1
int body = 1;
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "FLAGS_H" not in {d.name for d in derived.definitions}
    result = preprocess_source(seed, configuration=derived)
    assert "int body = 1;" in result.source


def test_nested_ifndef_not_stripped():
    seed = """
#if 1
#ifndef NESTED_H
#define NESTED_H
#define FEATURE 1
#endif
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "NESTED_H" in {d.name for d in derived.definitions}


def test_pragma_once_not_treated_as_guard():
    seed = """
#pragma once
#define FEATURE 1
"""
    with pytest.raises(IncompleteConfigurationError) as exc_info:
        MacroConfiguration.from_source(seed)
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE


def test_exclude_removes_name_that_came_only_from_base():
    base = MacroConfiguration(
        integers={"BASE_KEPT": 1, "BASE_REMOVED": 2},
        undefined={"BASE_UNDEF"},
    )
    seed = "#define SEED_KEPT 3\n"
    derived = MacroConfiguration.from_source(
        seed, base=base, exclude=("BASE_REMOVED", "BASE_UNDEF")
    )
    assert derived == MacroConfiguration(integers={"BASE_KEPT": 1, "SEED_KEPT": 3})


def test_exclude_removes_from_all_categories():
    base = MacroConfiguration(
        presence={"PRES"},
        integers={"INT": 42},
        undefined={"UNDEF"},
        definitions=[MacroDefinition("FN", "(a)", parameters=("a",))],
    )
    derived = MacroConfiguration.from_source("", base=base, exclude=("PRES", "INT", "UNDEF", "FN"))
    assert derived == MacroConfiguration()


def test_exclude_does_not_hide_macro_during_seed_derivation():
    seed = """
#define SEED_GUARD 1
#if SEED_GUARD
#define CHOSEN 42
#else
#define CHOSEN 0
#endif
"""
    derived = MacroConfiguration.from_source(seed, exclude=("SEED_GUARD",))
    assert derived == MacroConfiguration(integers={"CHOSEN": 42})
    assert "SEED_GUARD" not in {d.name for d in derived.definitions}


@pytest.mark.parametrize(
    "bad_name",
    ["123BAD", "FOO-BAR", "WITH SPACE", "", None, 42],
)
def test_exclude_rejects_invalid_names_before_preprocessing(bad_name, monkeypatch):
    import cpre.pragmas

    def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("preprocessing ran before exclude validation")

    monkeypatch.setattr(cpre.pragmas, "preprocess_source", fail)
    with pytest.raises(AnalysisError) as exc_info:
        MacroConfiguration.from_source("#define X 1\n", exclude=[bad_name])
    assert exc_info.value.code == ErrorCode.INVALID_CONFIGURATION


def test_exclude_rejects_non_iterable(monkeypatch):
    import cpre.pragmas

    def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("preprocessing ran before exclude validation")

    monkeypatch.setattr(cpre.pragmas, "preprocess_source", fail)
    with pytest.raises(AnalysisError) as exc_info:
        MacroConfiguration.from_source("#define X 1\n", exclude=123)
    assert exc_info.value.code == ErrorCode.INVALID_CONFIGURATION


def test_exclude_ignores_duplicates_and_unmentioned_names():
    seed = "#define KEPT 1\n#define REMOVED 2\n"
    derived = MacroConfiguration.from_source(seed, exclude=("REMOVED", "REMOVED", "NOT_IN_RESULT"))
    assert derived == MacroConfiguration(integers={"KEPT": 1})


def test_guard_with_other_directive_before_define_not_stripped():
    seed = """
#ifndef FLAGS_H
#define OTHER 1
#define FLAGS_H
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "FLAGS_H" in {d.name for d in derived.definitions}


def test_guard_with_code_before_ifndef_not_stripped():
    seed = """
int prefix = 1;
#ifndef FLAGS_H
#define FLAGS_H
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "FLAGS_H" in {d.name for d in derived.definitions}


def test_guard_with_else_or_elif_branch_not_stripped():
    seed_else = """
#ifndef FLAGS_H
#define FLAGS_H
#else
#define OTHER 1
#endif
"""
    derived_else = MacroConfiguration.from_source(
        seed_else, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" in {d.name for d in derived_else.definitions}

    seed_elif = """
#ifndef FLAGS_H
#define FLAGS_H
#elif 1
#define OTHER 1
#endif
"""
    derived_elif = MacroConfiguration.from_source(
        seed_elif, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    assert "FLAGS_H" in {d.name for d in derived_elif.definitions}


def test_multiple_top_level_groups_not_stripped():
    seed = """
#ifndef A_H
#define A_H
#endif
#ifndef B_H
#define B_H
#endif
"""
    derived = MacroConfiguration.from_source(seed, unknown_names=UnknownNamePolicy.UNDEFINED)
    assert "A_H" in {d.name for d in derived.definitions}
    assert "B_H" in {d.name for d in derived.definitions}
