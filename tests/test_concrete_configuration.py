from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pytest
from pcpp import Preprocessor

from cpre import (
    AnalysisError,
    ErrorCode,
    MacroConfiguration,
    MacroDefinition,
    UnknownNamePolicy,
    preprocess_source,
)
from cpre.expansion import tokenize

_LINE_MARKER = re.compile(
    r'(?m)^[ \t]*#[ \t]*(?:line[ \t]+)?\d+[^\r\n]*(?:\r\n|\r|\n|$)'
)


@dataclass(frozen=True)
class ConcreteCase:
    name: str
    source: str
    configuration: MacroConfiguration
    pcpp_definitions: tuple[str, ...] = ()


CASES = (
    ConcreteCase(
        "missing-name-closed",
        "#if EXTERNAL_FEATURE\nint enabled;\n#else\nint disabled;\n#endif\n",
        MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED),
    ),
    ConcreteCase(
        "explicit-undefined",
        "#if defined(DISABLED) || DISABLED\nint enabled;\n#else\nint disabled;\n#endif\n",
        MacroConfiguration(undefined={"DISABLED"}),
    ),
    ConcreteCase(
        "integer-zero",
        "#if FEATURE_VALUE\nint enabled;\n#else\nint disabled;\n#endif\n",
        MacroConfiguration(integers={"FEATURE_VALUE": 0}),
        ("FEATURE_VALUE 0",),
    ),
    ConcreteCase(
        "integer-42",
        "#if FEATURE_VALUE == 42\nint selected;\n#else\nint other;\n#endif\n",
        MacroConfiguration(integers={"FEATURE_VALUE": 42}),
        ("FEATURE_VALUE 42",),
    ),
    ConcreteCase(
        "presence-flag",
        "#ifdef FEATURE\nint enabled;\n#else\nint disabled;\n#endif\n",
        MacroConfiguration(presence={"FEATURE"}),
        ("FEATURE",),
    ),
    ConcreteCase(
        "injected-offsetof",
        "struct item { int value; };\n"
        "unsigned long n = offsetof(struct item, value);\n",
        MacroConfiguration(definitions=(
            MacroDefinition(
                "offsetof",
                "((unsigned long)&(((TYPE*)0)->MEMBER))",
                parameters=("TYPE", "MEMBER"),
            ),
        )),
        ("offsetof(TYPE, MEMBER) ((unsigned long)&(((TYPE*)0)->MEMBER))",),
    ),
)


def _semantic_tokens(source: str) -> tuple[str, ...]:
    source = _LINE_MARKER.sub("", source)
    return tuple(
        token.text for token in tokenize(source)
        if token.kind not in {"space", "comment"}
    )


def _pcpp(source: str, definitions: tuple[str, ...]) -> str:
    preprocessor = Preprocessor()
    preprocessor.line_directive = "#line"
    for definition in definitions:
        preprocessor.define(definition)
    preprocessor.parse(source, source="configured.c")
    output = io.StringIO()
    preprocessor.write(output)
    assert preprocessor.return_code == 0
    return output.getvalue()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_concrete_configuration_matches_pcpp(case):
    result = preprocess_source(
        case.source,
        filename="configured.c",
        configuration=case.configuration,
    )
    assert result.complete, result.incomplete
    assert _semantic_tokens(result.source) == _semantic_tokens(
        _pcpp(case.source, case.pcpp_definitions)
    )


def test_closed_world_is_opt_in_and_does_not_change_symbolic_default():
    source = "#if MISSING\nint value;\n#endif\n"
    assert not preprocess_source(source).complete

    result = preprocess_source(
        source,
        configuration=MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED),
    )
    assert result.complete
    assert "int value" not in result.source
    assert "MISSING" not in result.macros


def test_configured_definition_maps_expansion_to_source_invocation():
    source = "int values[COUNT];\n"
    result = preprocess_source(
        source,
        configuration=MacroConfiguration(integers={"COUNT": 42}),
    )
    assert result.complete
    assert "42" in result.source
    expanded = [mapping for mapping in result.source_map if mapping.expanded]
    assert len(expanded) == 1
    mapping = expanded[0]
    assert source[mapping.source_start:mapping.source_end] == "COUNT"
    assert mapping.start.line == 1 and mapping.start.column == 12
    assert result.macros["COUNT"].definition.location is None


def test_source_redefinition_and_undef_override_external_configuration():
    source = (
        "int before = VALUE;\n"
        "#define VALUE 7\n"
        "int after = VALUE;\n"
        "#undef VALUE\n"
        "#if VALUE\n"
        "int unreachable;\n"
        "#endif\n"
        "int literal_name = VALUE;\n"
    )
    result = preprocess_source(
        source,
        configuration=MacroConfiguration(integers={"VALUE": 42}),
    )
    assert result.complete, result.incomplete
    tokens = _semantic_tokens(result.source)
    assert tokens[:5] == ("int", "before", "=", "42", ";")
    assert ("int", "after", "=", "7", ";") == tokens[5:10]
    assert "unreachable" not in tokens
    assert tokens[-5:] == ("int", "literal_name", "=", "VALUE", ";")
    assert result.macros["VALUE"].defined is False
    assert result.macros["VALUE"].definition is None


def test_assumptions_still_do_not_supply_replacement_text():
    result = preprocess_source("VALUE\n", assumptions={"VALUE": True})
    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION


def test_assumptions_and_concrete_configuration_cannot_be_mixed():
    with pytest.raises(AnalysisError) as caught:
        preprocess_source(
            "",
            assumptions={"VALUE": True},
            configuration=MacroConfiguration(integers={"VALUE": 1}),
        )
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    "kwargs",
    [
        {"presence": {"BAD-NAME"}},
        {"integers": {"VALUE": True}},
        {"presence": {"VALUE"}, "undefined": {"VALUE"}},
        {"definitions": (MacroDefinition("F", "x", parameters=("x", "x")),)},
        {"unknown_names": "not-a-policy"},
    ],
)
def test_invalid_configuration_is_structured(kwargs):
    with pytest.raises(AnalysisError) as caught:
        MacroConfiguration(**kwargs)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
