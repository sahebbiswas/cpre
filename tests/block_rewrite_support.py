"""Helpers for the tests of the suggested block rewrites (merge, #else, join, collapse)."""

from __future__ import annotations

import itertools
import re

import cpre


def apply_edit(source: str, edit: cpre.SuggestedEdit) -> str:
    """``source`` with the suggested edit applied (lines and columns are 1-based)."""
    # A line ends at a CR, an LF or a CR LF pair, as cpre counts physical lines.
    starts = [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", source)]

    def offset(location: cpre.SourceLocation) -> int:
        assert location.column is not None
        return starts[location.line - 1] + location.column - 1

    start, end = offset(edit.range.start), offset(edit.range.end)
    return source[:start] + edit.replacement + source[end:]


def findings_of(source: str, kind: cpre.FindingKind, **kwargs) -> list[cpre.Finding]:
    result = cpre.analyze_source(source, **kwargs)
    assert result.complete
    return [finding for finding in result.findings if finding.kind is kind]


def rewritten(source: str, kind: cpre.FindingKind) -> str:
    """``source`` after applying the edit of the only finding of ``kind``."""
    (finding,) = findings_of(source, kind)
    assert finding.edit is not None
    return apply_edit(source, finding.edit)


def outputs(source: str, names: tuple[str, ...]) -> list[str]:
    """The retained text of ``source`` under every closed-world setting of ``names``."""
    results = []
    for values in itertools.product([None, 0, 1], repeat=len(names)):
        configuration = cpre.MacroConfiguration(
            undefined=[name for name, value in zip(names, values, strict=True) if value is None],
            integers={
                name: value for name, value in zip(names, values, strict=True) if value is not None
            },
            unknown_names="undefined",
        )
        result = cpre.preprocess_source(source, configuration=configuration)
        assert result.complete
        assert result.source is not None
        results.append(" ".join(result.source.split()))
    return results
