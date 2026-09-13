from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pycparser import c_parser

from cpre import ErrorCode, preprocess_source


FIXTURES = Path(__file__).parent / "compatibility" / "fixtures"


@dataclass(frozen=True)
class CompatibilityCase:
    name: str
    status: str
    code: ErrorCode | None = None
    line: int | None = None
    expanded_lines: tuple[int, ...] = ()
    assumptions: tuple[tuple[str, bool], ...] = ()


CASES = (
    CompatibilityCase("offsetof_container.c", "supported", expanded_lines=(12,)),
    CompatibilityCase("nested_conditionals.c", "supported"),
    CompatibilityCase("source_order.c", "supported", expanded_lines=(2, 5)),
    CompatibilityCase("multiline_nested_macros.c", "supported", expanded_lines=(8,)),
    CompatibilityCase("general_macro_operators.c", "supported"),
    CompatibilityCase("incomplete_numeric_condition.c", "supported"),
    CompatibilityCase("divergent_builtin_line.c", "supported", expanded_lines=(1,)),
    CompatibilityCase(
        "configured_conditional.c", "supported",
        assumptions=(("FEATURE", True),),
    ),
    CompatibilityCase(
        "configured_conditional.c", "supported",
        assumptions=(("FEATURE", False),),
    ),
    CompatibilityCase(
        "unsupported_include.c", "unsupported",
        ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, 1,
    ),
    CompatibilityCase(
        "incomplete_unknown_condition.c", "incomplete",
        ErrorCode.UNRESOLVED_CONDITION, 1,
    ),
    CompatibilityCase(
        "divergent_pragma.c", "unsupported",
        ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, 1,
    ),
)


def load(case: CompatibilityCase) -> str:
    return (FIXTURES / case.name).read_text(encoding="utf-8")


def preprocess(case: CompatibilityCase, source: str):
    return preprocess_source(source, filename=case.name, assumptions=dict(case.assumptions))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_compatibility_corpus_has_explicit_stable_classification(case):
    source = load(case)
    first = preprocess(case, source)
    second = preprocess(case, source)

    if case.status == "supported":
        assert first.complete and second.complete
        assert first.source == second.source
        assert first.source_map == second.source_map
        assert first.macros == second.macros
        return

    assert case.status in {"unsupported", "incomplete"}
    assert not first.complete and not second.complete
    assert first.source is first.source_map is first.macros is None
    assert second.source is second.source_map is second.macros is None
    assert first.incomplete == second.incomplete
    diagnostic, = first.incomplete
    assert diagnostic.code is case.code
    assert diagnostic.location.line == case.line


@pytest.mark.parametrize(
    "case", [case for case in CASES if case.status == "supported"],
    ids=lambda case: case.name,
)
def test_supported_corpus_remains_parseable_by_pycparser(case):
    source = load(case)
    result = preprocess(case, source)
    assert result.complete, result.incomplete
    c_parser.CParser().parse(result.source, filename=case.name)


@pytest.mark.parametrize(
    "case", [case for case in CASES if case.expanded_lines],
    ids=lambda case: case.name,
)
def test_expanded_source_maps_recover_physical_invocation_lines(case):
    source = load(case)
    result = preprocess(case, source)
    assert result.complete, result.incomplete

    expanded = tuple(mapping for mapping in result.source_map if mapping.expanded)
    assert tuple(mapping.start.line for mapping in expanded) == case.expanded_lines
    for mapping in expanded:
        original = source[mapping.source_start:mapping.source_end]
        rendered = result.source[mapping.output_start:mapping.output_end]
        assert original.strip()
        assert rendered.strip()
        assert mapping.start.line == source.count("\n", 0, mapping.source_start) + 1


def test_container_fixture_exercises_offsetof_recovery_shape():
    source = load(next(case for case in CASES if case.name == "offsetof_container.c"))
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert "offsetof" not in result.source
    assert "container_of" not in result.source
    assert "struct item" in result.source
    assert "( ( size_t ) &" in " ".join(result.source.split())