from __future__ import annotations

import difflib
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pcpp import Preprocessor
from pycparser import c_parser

from cpre import preprocess_source
from cpre.expansion import tokenize


FIXTURES = Path(__file__).parent / "compatibility" / "fixtures"
_LINE_MARKER = re.compile(
    r'(?m)^[ \t]*#[ \t]*(?:line[ \t]+)?\d+[^\r\n]*(?:\r\n|\r|\n|$)'
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
    DifferentialCase("configured_conditional.c", (("FEATURE", True),)),
    DifferentialCase("configured_conditional.c", (("FEATURE", False),)),
)

# These are compatibility gaps, not accepted output differences. A fixture may
# only be excluded from the pcpp equivalence gate when its reason is explicit.
KNOWN_DIFFERENCES = {
    "unsupported_include.c": (
        "cpre intentionally rejects reachable #include processing instead of "
        "returning partial source"
    ),
    "unsupported_va_opt.c": (
        "cpre intentionally rejects __VA_OPT__ until that expansion form is supported"
    ),
    "incomplete_unknown_condition.c": (
        "cpre requires an explicit macro configuration when branch selection is unknown"
    ),
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
    preprocessor.line_directive = None
    for name, value in case.assumptions:
        preprocessor.define(f"{name} {1 if value else 0}")
    preprocessor.parse(source, source=case.fixture)
    output = io.StringIO()
    preprocessor.write(output)
    return output.getvalue()


def _semantic_tokens(source: str) -> tuple[str, ...]:
    normalized = _LINE_MARKER.sub("", source)
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

    # pcpp line directives are disabled above, so physical token lines are a
    # meaningful cross-check that cpre preserves downstream source coordinates.
    cpre_positions = _semantic_positions(cpre_output)
    pcpp_positions = _semantic_positions(pcpp_output)
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
    parser.parse(first_pcpp, filename=f"pcpp:{case.fixture}")


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
