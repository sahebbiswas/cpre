"""The parts of per-unit preprocessing work on their own (issue #165).

``_preprocess_unit`` used to hold the branch selector, the include handler and the
``__has_include`` answerer as closures, so none could run without the whole pipeline.
These tests build a unit context and call each part directly.
"""

from __future__ import annotations

import pytest

from cpre import _has_include
from cpre._types import AnalysisOptions
from cpre.errors import AnalysisError, ErrorCode
from cpre.includes import IncludeForm, IncludeOutcome, ResolvedInclude
from cpre.macros import MacroDefinition, MacroEnvironment
from cpre.parser import parse_source
from cpre.preprocessing import (
    _BranchSelector,
    _IncludeHandler,
    _IncludeState,
    _LogicalPreprocessingState,
    _QueryAnswerer,
    _Run,
    _UnitContext,
)
from cpre.robdd import AnalysisBudget

STATE = _LogicalPreprocessingState(1, None)


def make_context(
    source: str,
    *,
    macros: dict[str, str] | None = None,
    include_query=None,
    resolver=None,
    has_include_from_resolver: bool = False,
    skip_includes: bool = False,
    max_depth: int = 8,
    identity: str | None = None,
):
    """A unit context for ``source`` with ``__has_include`` placeholders already rendered."""
    environment = MacroEnvironment()
    for name, replacement in (macros or {}).items():
        environment.define(MacroDefinition(name, replacement))
    limits = AnalysisOptions()._resource_limits()
    includes = (
        _IncludeState(resolver, max_depth, [identity] if identity else [])
        if resolver is not None
        else None
    )
    run = _Run(
        environment,
        limits,
        AnalysisBudget(limits.max_work),
        skip_includes,
        includes,
        [],
        False,
        include_query,
        has_include_from_resolver,
    )
    queries = _has_include.scan(source) if "__has_include" in source else ()
    rendered = _has_include.render(source, queries) if queries else source
    context = _UnitContext(
        rendered, run, identity=identity, filename="unit.c", depth=0, queries=queries
    )
    return context, rendered


def branch_of(rendered: str, index: int = 0):
    return parse_source(rendered, distinguish_defined=True).groups[0].branches[index]


def select(source: str, **kwargs):
    context, rendered = make_context(source, **kwargs)
    selector = _BranchSelector(context, _QueryAnswerer(context))
    return selector.select(branch_of(rendered)), context


# --- branch selector -----------------------------------------------------------


def test_selector_decides_from_known_definedness():
    selected, context = select("#ifdef GONE\n#endif\n")
    assert selected is None  # GONE is unknown, not undefined
    assert context.diagnostics[0].code is ErrorCode.UNRESOLVED_CONDITION

    selected, context = select("#ifdef KNOWN\n#endif\n", macros={"KNOWN": "1"})
    assert selected is True and not context.diagnostics
    selected, _ = select("#ifndef KNOWN\n#endif\n", macros={"KNOWN": "1"})
    assert selected is False


def test_selector_falls_back_to_numeric_evaluation():
    selected, context = select("#if LEVEL >= 3 && LEVEL < 9\n#endif\n", macros={"LEVEL": "4"})
    assert selected is True and not context.diagnostics
    selected, _ = select("#if LEVEL >= 5\n#endif\n", macros={"LEVEL": "4"})
    assert selected is False


def test_selector_reports_unevaluable_expression():
    selected, context = select("#if 1 +\n#endif\n")
    assert selected is None
    assert context.diagnostics[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION
    assert context.diagnostics[0].location.line == 1


def test_selector_names_unresolved_macros():
    selected, context = select("#if FEATURE_X > 1\n#endif\n")
    assert selected is None
    (diagnostic,) = context.diagnostics
    assert diagnostic.code is ErrorCode.UNRESOLVED_CONDITION
    assert [item.name for item in diagnostic.unresolved] == ["FEATURE_X"]


def test_selector_refuses_unsupported_predefined_macro():
    selected, context = select("#if __COUNTER__ > 0\n#endif\n")
    assert selected is None
    assert context.diagnostics[0].code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION


def test_selector_asks_only_the_query_it_needs():
    asked: list[str] = []

    def provider(query):
        asked.append(query.header)
        return True

    selected, context = select(
        "#if __has_include(<a.h>) || __has_include(<b.h>)\n#endif\n", include_query=provider
    )
    assert selected is True and asked == ["a.h"]  # the second query is irrelevant
    assert not context.diagnostics


def test_selector_reports_unanswered_query():
    selected, context = select(
        "#if __has_include(<a.h>)\n#endif\n", include_query=lambda query: None
    )
    assert selected is None
    assert context.diagnostics[0].code is ErrorCode.UNRESOLVED_CONDITION
    assert "<a.h>" in context.diagnostics[0].message


# --- __has_include answerer ----------------------------------------------------


def occurrence_in(source: str, **kwargs):
    context, rendered = make_context(source, **kwargs)
    (occurrence,) = _has_include.scan(source)
    return context, _QueryAnswerer(context), occurrence


def test_answerer_uses_the_provider_answer():
    context, answerer, occurrence = occurrence_in(
        '#if __has_include("x/y.h")\n#endif\n', include_query=lambda query: query.header == "x/y.h"
    )
    assert answerer.answer(occurrence) is True
    assert context.view.answers == {occurrence.placeholder: True}


def test_answerer_without_a_source_of_answers_reports_it():
    context, answerer, occurrence = occurrence_in("#if __has_include(<a.h>)\n#endif\n")
    assert answerer.answer(occurrence) is False
    assert context.diagnostics[0].message.startswith("__has_include requires")


def test_answerer_rejects_has_include_next():
    context, answerer, occurrence = occurrence_in(
        "#if __has_include_next(<a.h>)\n#endif\n", include_query=lambda query: True
    )
    assert answerer.answer(occurrence) is False
    assert "__has_include_next" in context.diagnostics[0].message


def test_answerer_rejects_a_non_boolean_answer():
    _, answerer, occurrence = occurrence_in(
        "#if __has_include(<a.h>)\n#endif\n", include_query=lambda query: "yes"
    )
    with pytest.raises(AnalysisError) as error:
        answerer.answer(occurrence)
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION


def test_answerer_falls_back_to_the_resolver():
    seen = []

    def resolver(request):
        seen.append((request.header, request.form, request.directive))
        return ResolvedInclude("/inc/a.h", "") if request.header == "a.h" else None

    for header, expected in (("a.h", True), ("missing.h", False)):
        source = f"#if __has_include(<{header}>)\n#endif\n"
        context, answerer, occurrence = occurrence_in(
            source,
            include_query=lambda query: None,
            resolver=resolver,
            has_include_from_resolver=True,
        )
        assert answerer.answer(occurrence) is True
        assert context.view.answers == {occurrence.placeholder: expected}
    assert seen == [
        ("a.h", IncludeForm.ANGLE, "include"),
        ("missing.h", IncludeForm.ANGLE, "include"),
    ]


# --- include handler -----------------------------------------------------------

HEADERS = {
    "a.h": "#ifndef A_H\n#define A_H\nint a;\n#endif\n",
    "once.h": "#pragma once\nint once;\n",
    "loop.h": '#include "loop.h"\n',
    "imp.h": "int imp;\n",
}


def resolver(request):
    source = HEADERS.get(request.header)
    return ResolvedInclude("/inc/" + request.header, source) if source is not None else None


def handler(directive: str, **kwargs):
    context, _ = make_context(directive + "\n", resolver=resolver, **kwargs)
    return context, _IncludeHandler(context)


def test_include_handler_enters_and_splices_a_header():
    context, include = handler('#include "a.h"')
    assert include.handle("include", '"a.h"', 1, STATE) is True
    assert context.retained == [False]
    ((offset, line, child),) = context.insertions
    assert (offset, line) == (len(context.physical[0]), 1)
    assert "int a;" in child.source
    (record,) = context.run.includes.records
    assert record.outcome is IncludeOutcome.ENTERED and record.identity == "/inc/a.h"


def test_include_handler_does_not_enter_a_guarded_header_twice():
    context, include = handler('#include "a.h"')
    assert include.handle("include", '"a.h"', 1, STATE)
    assert include.handle("include", '"a.h"', 1, STATE)
    outcomes = [record.outcome for record in context.run.includes.records]
    assert outcomes == [IncludeOutcome.ENTERED, IncludeOutcome.GUARDED]
    assert len(context.insertions) == 1


def test_include_handler_import_makes_a_header_include_once():
    context, include = handler('#import "imp.h"')
    assert include.handle("import", '"imp.h"', 1, STATE)
    assert include.handle("import", '"imp.h"', 1, STATE)
    outcomes = [record.outcome for record in context.run.includes.records]
    assert outcomes == [IncludeOutcome.ENTERED, IncludeOutcome.IMPORTED]


def test_include_handler_reports_an_unresolved_header():
    context, include = handler('#include "nope.h"')
    assert include.handle("include", '"nope.h"', 1, STATE) is False
    assert context.diagnostics[0].code is ErrorCode.UNRESOLVED_INCLUDE


def test_include_handler_masks_an_unresolved_header_in_skip_mode():
    context, include = handler('#include "nope.h"', skip_includes=True)
    assert include.handle("include", '"nope.h"', 1, STATE) is True
    assert context.retained == [False] and not context.diagnostics
    (skipped,) = context.run.skipped
    assert (skipped.directive, skipped.operand) == ("include", '"nope.h"')


def test_include_handler_without_a_resolver_skips():
    context, _ = make_context('#include "a.h"\n', skip_includes=True)
    include = _IncludeHandler(context)
    assert include.handle("include", '"a.h"', 1, STATE) is True
    assert context.retained == [False] and len(context.run.skipped) == 1


def test_include_handler_detects_a_cycle():
    context, include = handler('#include "loop.h"')
    context.run.includes.stack.append("/inc/loop.h")
    assert include.handle("include", '"loop.h"', 1, STATE) is False
    assert context.diagnostics[0].code is ErrorCode.INCLUDE_CYCLE


def test_include_handler_enforces_the_depth_limit():
    context, include = handler('#include "once.h"', max_depth=1)
    context.depth = 1  # as if this unit were itself an include
    assert include.handle("include", '"once.h"', 1, STATE) is False
    assert context.diagnostics[0].code is ErrorCode.INCLUDE_DEPTH_EXCEEDED


def test_include_handler_requires_an_operand():
    context, include = handler("#include")
    assert include.handle("include", "", 1, STATE) is False
    assert "expects a header name" in context.diagnostics[0].message


def test_include_handler_rejects_a_bad_resolver_result():
    context, _ = make_context('#include "a.h"\n', resolver=lambda request: "text")
    with pytest.raises(AnalysisError) as error:
        _IncludeHandler(context).handle("include", '"a.h"', 1, STATE)
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION


# --- structure (issue #165) ----------------------------------------------------


def _preprocessing_tree():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "cpre" / "preprocessing.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def test_preprocess_unit_has_no_nested_functions():
    import ast

    (unit,) = [
        node
        for node in _preprocessing_tree().body
        if isinstance(node, ast.FunctionDef) and node.name == "_preprocess_unit"
    ]
    nested = [
        node
        for child in ast.walk(unit)
        for node in ([child] if child is not unit else [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]
    assert nested == []


def test_no_function_in_preprocessing_is_longer_than_150_lines():
    import ast

    too_long = [
        (node.name, node.end_lineno - node.lineno + 1)
        for node in ast.walk(_preprocessing_tree())
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 150
    ]
    assert too_long == []


def test_preprocessing_is_type_checked_without_blanket_ignores():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"cpre.preprocessing"' not in config
    source = (root / "cpre" / "preprocessing.py").read_text(encoding="utf-8")
    assert not re.search(r"#\s*type:\s*ignore", source)
