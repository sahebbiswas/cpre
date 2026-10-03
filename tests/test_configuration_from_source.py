import pytest
from cpre import (
    MacroConfiguration,
    MacroDefinition,
    UnknownNamePolicy,
    PreprocessingContext,
    ParseError,
    IncompleteConfigurationError,
    ErrorCode,
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
        integers={"FEATURE": 2},
        definitions=[MacroDefinition("SELECTED", '"yes"')]
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
