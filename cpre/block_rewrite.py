"""Apply the suggested edits of conditional-block findings, and verify the result.

``rewrite_blocks()`` is what ``cpre simplify-blocks --rewrite`` runs: it analyzes the
source, applies every ``EXACT`` edit of the selected finding kinds that does not overlap
another, analyzes the result, and repeats until nothing more applies (a rewrite can expose
the next one: two merged blocks may now be adjacent to a third). The result of every round
is checked before the next one starts; a rewrite that fails the check raises an
:class:`~cpre.AnalysisError` and nothing is returned.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from ._types import (
    AnalysisIncomplete,
    AnalysisOptions,
    Finding,
    FindingKind,
    FixConfidence,
    SourceLocation,
)
from .api import analyze_source
from .configuration import MacroConfiguration
from .errors import AnalysisError, ErrorCode

# The kinds that rewrite the structure of the blocks. A collapsible nesting is a style
# suggestion, so it is applied only when it is asked for.
DEFAULT_REWRITE_KINDS = frozenset(
    {
        FindingKind.MERGEABLE_BLOCKS,
        FindingKind.COMPLEMENTARY_BLOCKS,
        FindingKind.JOINABLE_BRANCHES,
        FindingKind.EMPTY_BLOCK,
        FindingKind.EMPTY_BRANCH,
    }
)
REWRITE_KINDS = DEFAULT_REWRITE_KINDS | {FindingKind.COLLAPSIBLE_NESTING}

_MAX_ROUNDS = 16
_LINE_ENDING = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class BlockRewriteResult:
    """What :func:`rewrite_blocks` did to one source.

    ``rewritten_source`` equals ``source`` when nothing applied or the analysis was
    incomplete. ``applied`` lists the findings whose edits were applied, in the order they
    were applied; each finding's location refers to the text as it was in that round
    (``rounds`` is how many rounds changed the text). ``remaining`` lists the selected
    findings still reported in ``rewritten_source``: those without an exact edit, and those
    that overlapped another edit until the rounds ran out.
    """

    source: str
    rewritten_source: str
    applied: tuple[Finding, ...]
    remaining: tuple[Finding, ...]
    rounds: int
    incomplete: tuple[AnalysisIncomplete, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every analysis finished; an incomplete one rewrites nothing."""
        return not self.incomplete

    @property
    def changed(self) -> bool:
        return self.rewritten_source != self.source


def _offsets(source: str) -> list[int]:
    """The offset at which each physical line starts, as cpre counts lines."""
    return [0] + [match.end() for match in _LINE_ENDING.finditer(source)]


def _span(source: str, starts: list[int], finding: Finding) -> tuple[int, int] | None:
    edit = finding.edit
    if edit is None:
        return None

    def offset(location: SourceLocation) -> int | None:
        if location.column is None or not 1 <= location.line <= len(starts):
            return None
        return starts[location.line - 1] + location.column - 1

    start, end = offset(edit.range.start), offset(edit.range.end)
    if start is None or end is None or not 0 <= start <= end <= len(source):
        return None
    return start, end


def _apply_round(source: str, findings: list[Finding]) -> tuple[str, list[Finding]]:
    """``source`` with the findings' edits applied, skipping any that overlaps an earlier one."""
    starts = _offsets(source)
    spans = []
    for finding in findings:
        span = _span(source, starts, finding)
        if span is not None:
            spans.append((span, finding))
    spans.sort(key=lambda item: item[0])
    chosen: list[tuple[tuple[int, int], Finding]] = []
    limit = -1
    for span, finding in spans:
        # An empty range at the end of the previous edit does not overlap it.
        if span[0] < limit or (chosen and span == chosen[-1][0]):
            continue
        chosen.append((span, finding))
        limit = span[1]
    result = source
    for (start, end), finding in reversed(chosen):
        assert finding.edit is not None
        result = result[:start] + finding.edit.replacement + result[end:]
    return result, [finding for _, finding in chosen]


def _key(finding: Finding) -> tuple[str, str | None, str | None]:
    return (
        finding.kind.value,
        finding.original_condition,
        finding.edit.replacement if finding.edit is not None else None,
    )


def rewrite_blocks(
    source: str,
    *,
    filename: str | None = None,
    kinds: Iterable[FindingKind] | None = None,
    options: AnalysisOptions | None = None,
    configuration: MacroConfiguration | None = None,
    strict_merge: bool = False,
) -> BlockRewriteResult:
    """Apply the exact edits of the block findings of ``kinds`` and verify the outcome.

    ``kinds`` defaults to :data:`DEFAULT_REWRITE_KINDS`; add
    :attr:`FindingKind.COLLAPSIBLE_NESTING` to apply the style suggestion too. The other
    arguments are those of :func:`analyze_source`. Line endings, a missing final newline
    and everything outside the edited ranges are kept as they are.

    Raises :class:`AnalysisError` (``ErrorCode.ANALYSIS_FAILURE``) when a round's result
    fails verification: the rewritten text must analyze completely, an applied finding
    must not be reported again, and no other kind of finding may become more frequent.
    """
    selected = frozenset(kinds) if kinds is not None else DEFAULT_REWRITE_KINDS
    unknown = selected - REWRITE_KINDS
    if unknown:
        names = ", ".join(sorted(kind.value for kind in unknown))
        raise AnalysisError(f"not a block rewrite kind: {names}", code=ErrorCode.ANALYSIS_FAILURE)

    def analyze(text: str):
        return analyze_source(
            text,
            filename=filename,
            options=options,
            configuration=configuration,
            strict_merge=strict_merge,
        )

    current = source
    result = analyze(current)
    applied: list[Finding] = []
    rounds = 0
    while result.complete:
        candidates = [
            finding
            for finding in result.findings
            if finding.kind in selected
            and finding.edit is not None
            and finding.edit.confidence is FixConfidence.EXACT
        ]
        updated, chosen = _apply_round(current, candidates)
        if not chosen or updated == current:
            break
        after = analyze(updated)
        _verify(filename, result.findings, chosen, after, selected)
        applied.extend(chosen)
        current, result = updated, after
        rounds += 1
        if rounds >= _MAX_ROUNDS:
            break
    if not result.complete:
        return BlockRewriteResult(source, source, (), (), 0, tuple(result.incomplete))
    remaining = tuple(finding for finding in result.findings if finding.kind in selected)
    return BlockRewriteResult(source, current, tuple(applied), remaining, rounds)


def _verify(filename, before, chosen, after, selected) -> None:
    """Raise unless ``after`` is a sound outcome of applying ``chosen`` to what ``before`` found."""
    name = filename or "<source>"
    if not after.complete:
        raise AnalysisError(
            f"{name}: rewrite verification failed: the rewritten source could not be analyzed",
            code=ErrorCode.ANALYSIS_FAILURE,
        )
    # An applied finding must be gone. Compare by what it said, not where it was.
    gone = Counter(_key(item) for item in before if item.kind in selected)
    gone.subtract(Counter(_key(item) for item in chosen))
    seen = Counter(_key(item) for item in after.findings if item.kind in selected)
    for key in {_key(item) for item in chosen}:
        if seen[key] > max(gone[key], 0):
            raise AnalysisError(
                f"{name}: rewrite verification failed: {key[0]} is still reported after "
                "its edit was applied",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
    # Nothing may get worse: no new dead, redundant or simplifiable condition. A new block
    # suggestion is not damage (a merge often leaves a block holding one block), so the
    # block rewrite kinds are not compared here.
    others_before = Counter(item.kind for item in before if item.kind not in REWRITE_KINDS)
    others_after = Counter(item.kind for item in after.findings if item.kind not in REWRITE_KINDS)
    for kind, count in others_after.items():
        if count > others_before[kind]:
            raise AnalysisError(
                f"{name}: rewrite verification failed: the edit added a {kind.value} finding",
                code=ErrorCode.ANALYSIS_FAILURE,
            )


__all__ = ["DEFAULT_REWRITE_KINDS", "REWRITE_KINDS", "BlockRewriteResult", "rewrite_blocks"]
