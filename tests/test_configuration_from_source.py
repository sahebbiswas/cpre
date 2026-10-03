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


def test_context_undef():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    text = """
#undef __STDC__
"""
    config = MacroConfiguration.from_source(text, context=context)
    expected = MacroConfiguration(undefined={"__STDC__"})
    assert config == expected


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


def test_base_with_context_keeps_seed_changes_to_injected_names():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    base = MacroConfiguration(integers={"FEATURE": 1})
    config = MacroConfiguration.from_source("#undef __STDC__\n", base=base, context=context)
    assert config == MacroConfiguration(integers={"FEATURE": 1}, undefined={"__STDC__"})


def test_base_is_not_mutated():
    base = MacroConfiguration(integers={"FEATURE": 1})
    snapshot = (base.definitions, base.undefined, base.unknown_names)
    MacroConfiguration.from_source("#undef FEATURE\n#define NEW\n", base=base)
    assert (base.definitions, base.undefined, base.unknown_names) == snapshot
