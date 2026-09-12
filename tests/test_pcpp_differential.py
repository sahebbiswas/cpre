from __future__ import annotations

import difflib
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pcpp import Preprocessor
from pycparser import c_ast, c_parser

from cpre import preprocess_source
from cpre.expansion import tokenize


FIXTURES = Path(__file__).parent / "compatibility" / "fixtures"
_LINE_MARKER = re.compile(
    r'(?m)^[ \t]*#[ \t]*(?:line[ \t]+)?(?P<line>\d+)[^\r\n]*(?:\r\n|\r|\n|$)'
)
_LINE_MARKER_LINE = re.compile(
    r'^[ \t]*#[ \t]*(?:line[ \t]+)?(?P<line>\d+)[^\r\n]*(?:\r\n|\r|\n)?$'
)


@dataclass(frozen=True)
class DifferentialCase:
    fixture: str
    assumptions: tuple[tuple[str, bool], ...] = ()

    @property
    def id(self) -> str:
        if not self.assumptions:
            return self.fixture
        config = ",".join(f"{name}={int(value)}" for name, value in self.assumptions)
        return f"{self.fixture}[{config}]"


SUPPORTED_CASES = (
    DifferentialCase("offsetof_container.c"),
    DifferentialCase("nested_conditionals.c"),
    DifferentialCase("source_order.c"),
    DifferentialCase("multiline_nested_macros.c"),
    DifferentialCase("general_macro_operators.c"),
    DifferentialCase("incomplete_numeric_condition.c"),
    DifferentialCase("configured_conditional.c", (("FEATURE", True),)),
    DifferentialCase("configured_conditional.c", (("FEATURE", False),)),
)

KNOWN_DIFFERENCES = {
    "unsupported_include.c": (
        "cpre intentionally rejects reachable #include processing instead of "
        "returning partial source; downstream non-impact is unproven (#39)"
    ),
    "unsupported_va_opt.c": (
        "cpre rejects __VA_OPT__; downstream non-impact is unproven (#39)"
    ),
    "incomplete_unknown_condition.c": (
        "cpre requires explicit configuration; pcpp defaults unknown names to zero (#37)"
    ),
    "divergent_builtin_line.c": "__LINE__ remains unexpanded with complete=True (#38)",
    "divergent_pragma.c": "#pragma once remains in complete output (#38)",
}


def _load(fixture: str) -> str:
    return (FIXTURES / fixture).read_text(encoding="utf-8")


def _run_cpre(case: DifferentialCase, source: str) -> str:
    result = preprocess_source(
        source,
        filename=case.fixture,
        assumptions=dict(case.assumptions),
    )
    assert result.complete, f"{case.id}: cpre returned incomplete output: {result.incomplete}"
    assert result.source is not None
    return result.source


def _run_pcpp(case: DifferentialCase, source: str) -> str:
    preprocessor = Preprocessor()
    preprocessor.line_directive = "#line"
    for name, value in case.assumptions:
        preprocessor.define(f"{name} {1 if value else 0}")
    preprocessor.parse(source, source=case.fixture)
    output = io.StringIO()
    preprocessor.write(output)
    assert preprocessor.return_code == 0, f"{case.id}: pcpp reported an error"
    return output.getvalue()


def _without_line_markers(source: str) -> str:
    return _LINE_MARKER.sub(lambda match: "\n" if match.group(0).endswith("\n") else "", source)


def _semantic_tokens(source: str) -> tuple[str, ...]:
    normalized = _without_line_markers(source)
    return tuple(
        token.text for token in tokenize(normalized)
        if token.kind not in {"space", "comment"}
    )


def _semantic_positions(source: str) -> tuple[tuple[str, int], ...]:
    positions = []
    for token in tokenize(source):
        if token.kind in {"space", "comment"}:
            continue
        line = source.count("\n", 0, token.start) + 1
        positions.append((token.text, line))
    return tuple(positions)


def _pcpp_semantic_positions(source: str) -> tuple[tuple[str, int], ...]:
    logical_by_physical: dict[int, int | None] = {}
    masked_lines: list[str] = []
    logical_line = 1

    for physical_line, line in enumerate(source.splitlines(keepends=True), 1):
        marker = _LINE_MARKER_LINE.match(line)
        if marker:
            logical_line = int(marker.group("line"))
            logical_by_physical[physical_line] = None
            masked_lines.append("".join(char if char in "\r\n" else " " for char in line))
            continue
        logical_by_physical[physical_line] = logical_line
        masked_lines.append(line)
        logical_line += 1

    normalized = "".join(masked_lines)
    positions = []
    for token in tokenize(normalized):
        if token.kind in {"space", "comment"}:
            continue
        physical_line = normalized.count("\n", 0, token.start) + 1
        logical = logical_by_physical[physical_line]
        assert logical is not None
        positions.append((token.text, logical))
    return tuple(positions)


def _token_diff(left: tuple[str, ...], right: tuple[str, ...]) -> str:
    return "".join(difflib.unified_diff(
        [f"{index:04d}: {token}\n" for index, token in enumerate(left)],
        [f"{index:04d}: {token}\n" for index, token in enumerate(right)],
        fromfile="cpre",
        tofile="pcpp",
    ))


def test_every_compatibility_fixture_is_gated_or_explicitly_allowlisted():
    corpus = {path.name for path in FIXTURES.glob("*.c")}
    supported = {case.fixture for case in SUPPORTED_CASES}
    known = set(KNOWN_DIFFERENCES)

    assert supported.isdisjoint(known)
    assert corpus == supported | known, (
        "Every compatibility fixture must participate in the pcpp differential gate "
        "or have an explicit KNOWN_DIFFERENCES reason"
    )
    assert all(reason.strip() for reason in KNOWN_DIFFERENCES.values())


def test_classifications_and_differential_configurations_agree():
    from test_downstream_compatibility import CASES

    classified = {(case.name, case.assumptions) for case in CASES
                  if case.status == "supported"}
    gated = {(case.fixture, case.assumptions) for case in SUPPORTED_CASES}
    assert classified == gated
    assert {case.name for case in CASES if case.status != "supported"} == set(KNOWN_DIFFERENCES)


@pytest.mark.parametrize("case", SUPPORTED_CASES, ids=lambda case: case.id)
def test_cpre_matches_pcpp_preprocessing_tokens_and_coordinates(case):
    source = _load(case.fixture)
    cpre_output = _run_cpre(case, source)
    pcpp_output = _run_pcpp(case, source)

    cpre_tokens = _semantic_tokens(cpre_output)
    pcpp_tokens = _semantic_tokens(pcpp_output)
    assert cpre_tokens == pcpp_tokens, (
        f"{case.id}: macro-expanded token stream differs from pcpp\n"
        f"{_token_diff(cpre_tokens, pcpp_tokens)}"
    )

    cpre_positions = _semantic_positions(cpre_output)
    pcpp_positions = _pcpp_semantic_positions(pcpp_output)
    assert cpre_positions == pcpp_positions, (
        f"{case.id}: token source lines differ from pcpp\n"
        f"cpre={cpre_positions!r}\npcpp={pcpp_positions!r}"
    )


@pytest.mark.parametrize("case", SUPPORTED_CASES, ids=lambda case: case.id)
def test_differential_outputs_are_parseable_and_deterministic(case):
    source = _load(case.fixture)
    first_cpre = _run_cpre(case, source)
    second_cpre = _run_cpre(case, source)
    first_pcpp = _run_pcpp(case, source)
    second_pcpp = _run_pcpp(case, source)

    assert first_cpre == second_cpre, f"{case.id}: cpre output is nondeterministic"
    assert first_pcpp == second_pcpp, f"{case.id}: pcpp output is nondeterministic"

    parser = c_parser.CParser()
    parser.parse(first_cpre, filename=f"cpre:{case.fixture}")
    parser.parse(_without_line_markers(first_pcpp), filename=f"pcpp:{case.fixture}")


def test_offsetof_container_semantics_match_pcpp():
    case = DifferentialCase("offsetof_container.c")
    source = _load(case.fixture)
    cpre_output = _run_cpre(case, source)
    pcpp_output = _run_pcpp(case, source)

    for output in (cpre_output, pcpp_output):
        tokens = _semantic_tokens(output)
        assert "offsetof" not in tokens
        assert "container_of" not in tokens
        assert ("struct", "item") in tuple(zip(tokens, tokens[1:]))
        assert "->" in tokens

        tree = c_parser.CParser().parse(_without_line_markers(output))
        function = next(node for node in tree.ext if isinstance(node, c_ast.FuncDef))
        recovery = function.body.block_items[0].expr
        assert isinstance(recovery, c_ast.Cast)
        assert recovery.to_type.type.type.type.name == "item"
        subtraction = recovery.expr
        assert isinstance(subtraction, c_ast.BinaryOp) and subtraction.op == "-"
        assert subtraction.left.to_type.type.type.type.names == ["char"]
        assert subtraction.left.expr.name == "member"
        offset = subtraction.right
        assert isinstance(offset, c_ast.Cast)
        assert offset.to_type.type.type.names == ["size_t"]
        assert isinstance(offset.expr, c_ast.UnaryOp) and offset.expr.op == "&"
        access = offset.expr.expr
        assert isinstance(access, c_ast.StructRef) and access.type == "->"
        assert access.field.name == "value"
        assert access.name.to_type.type.type.type.name == "item"
        assert access.name.expr.value == "0"


def test_unknown_condition_has_concrete_pcpp_outcome_but_remains_incomplete():
    fixture = "incomplete_unknown_condition.c"
    case = DifferentialCase(fixture)
    source = _load(fixture)
    result = preprocess_source(source, filename=fixture)
    assert not result.complete
    assert result.source is result.source_map is result.macros is None
    output = _run_pcpp(case, source)
    assert _semantic_tokens(output) == ()
    c_parser.CParser().parse(_without_line_markers(output))


@pytest.mark.parametrize("fixture,expected", [
    ("divergent_builtin_line.c", ("int", "physical_line", "=", "1", ";")),
    ("divergent_pragma.c", ("int", "value", ";")),
])
def test_complete_but_divergent_output_is_an_explicit_migration_blocker(fixture, expected):
    case = DifferentialCase(fixture)
    source = _load(fixture)
    output = _run_cpre(case, source)
    reference = _run_pcpp(case, source)
    assert output == source
    assert _semantic_tokens(reference) == expected
    assert _semantic_tokens(output) != _semantic_tokens(reference)
    c_parser.CParser().parse(_without_line_markers(reference))
