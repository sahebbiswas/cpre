"""Adjacent conditional blocks that could be written as fewer blocks.

``#if A ... #endif`` followed by ``#if A ... #endif`` with nothing but blank lines
between them is one block written twice, and ``#if A ... #endif`` followed by
``#if !A ... #endif`` is one ``#if``/``#else`` block. This module finds both. It is
deliberately conservative: a pair is only reported when rewriting it provably keeps the
meaning, and anything it cannot prove (an opaque condition, a limit, a body that may
change the condition) is left alone.

Two sibling blocks are a candidate pair when all of these hold:

* each is a plain ``#if``/``#ifdef``/``#ifndef`` block, with no ``#elif`` or ``#else``;
* both were analyzed and their opening branch is reachable (a dead or redundant block is
  reported as such, not as mergeable);
* their conditions are built only from names, ``defined`` tests and ``!``/``&&``/``||``
  (no opaque predicate), with ``X`` and ``defined(X)`` kept apart because they are
  different tests, and are either Boolean-equivalent (the pair can be merged) or exact
  complements (the pair can be one ``#if``/``#else``);
* only blank lines separate the first block's ``#endif`` from the second block's opening
  directive, and neither of those two directives carries a comment;
* the first block's body cannot change the condition: it holds no ``#include``,
  ``#pragma``, ``_Pragma``, digraph directive, or ``#define``/``#undef`` of a name the
  condition depends on (a nested block counts as part of the body);
* nothing the condition depends on is volatile (``__LINE__``, ``__COUNTER__``, a name built
  by token pasting, or a ``_Pragma``), no condition after the rewrite depends on
  ``__LINE__`` (the edit moves it), and when
  the two conditions are written differently (always, for complements), no name they use
  is defined in this file as anything but a single token or a parenthesized expression,
  because a replacement list such as ``X || Y`` would change how a negated or regrouped
  condition reads.

"Depends on" follows the ``#define`` directives of this file: a name is replaced by the
names its replacement list mentions, repeatedly. Macros defined elsewhere (a header, a
command-line ``-D``) are not visible, so for those only the names the condition itself
uses are checked.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

from ._types import FindingKind
from .expressions import expression_atoms_in_order
from .lexer import lex, macro_name, strip_comments
from .model import (
    TRUE,
    ConditionalBranch,
    ConditionalGroup,
    ConditionalTree,
    Conjunction,
    DefinedVariable,
    Disjunction,
    Expression,
    Negation,
    Variable,
)
from .robdd import BDD, AnalysisBudget, AnalysisLimitExceeded, ResourceLimits
from .structure import (
    ConditionalBlock,
    ConditionalStructureTree,
    StructuralSourceLocation,
    parse_conditionals,
)

# Whitespace that may fill the gap between two blocks: what the lexer treats as horizontal
# whitespace, plus the line breaks.
_BLANK = frozenset(" \t\f\v\r\n")

# Built-in macros whose value differs from one use to the next, so two tests of the same
# text are not the same test.
_VOLATILE_NAMES = frozenset({"__COUNTER__", "__LINE__"})

# Directives that may define or remove macros we cannot see, or otherwise have effects
# beyond the text of the block (``#pragma push_macro`` and ``pop_macro`` change macros).
_OPAQUE_DIRECTIVES = frozenset({"include", "include_next", "import", "pragma"})

# The operator form of ``#pragma``, which can do the same from inside a body.
_PRAGMA_OPERATOR = "_Pragma"

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_SINGLE_TOKEN = re.compile(r"(?:[A-Za-z_]\w*|[0-9][\w']*)?\Z")


@dataclass(frozen=True)
class BlockRewrite:
    """A group of adjacent sibling blocks that can be written with fewer blocks.

    ``kind`` says which rewrite applies. ``first`` is the block the rewrite is anchored at;
    ``related`` are the other blocks involved, in source order. The rewrite replaces the
    physical range from ``edit_start`` to ``edit_end`` with ``replacement``:

    * ``MERGEABLE_BLOCKS``: from the first block's ``#endif`` through the last block's
      opening directive, with the bodies of the blocks in between;
    * ``COMPLEMENTARY_BLOCKS``: from the first block's ``#endif`` through the second
      block's opening directive (and its line break), with ``#else`` and that line break.
    """

    kind: FindingKind
    first: ConditionalGroup
    related: tuple[ConditionalGroup, ...]
    edit_start: StructuralSourceLocation
    edit_end: StructuralSourceLocation
    replacement: str


@dataclass(frozen=True)
class _Mutation:
    """A directive that can change what a later condition means."""

    offset: int
    name: str
    macro: str | None


def _boolean_names(expression: Expression) -> frozenset[str] | None:
    """The names ``expression`` tests, or ``None`` when it is not purely Boolean over names.

    Opaque predicates, symbolic literals and constants all return ``None``: a constant
    condition is never a reachable, non-redundant block, and the rest cannot be compared.
    """
    if type(expression) in (Variable, DefinedVariable):
        assert isinstance(expression, Variable)
        return frozenset({expression.name})
    if isinstance(expression, Negation):
        return _boolean_names(expression.operand)
    if isinstance(expression, (Conjunction, Disjunction)):
        names: set[str] = set()
        for operand in expression.operands:
            found = _boolean_names(operand)
            if found is None:
                return None
            names |= found
        return frozenset(names)
    return None


def _is_atomic(replacement: str) -> bool:
    """Whether a replacement list is nothing, one token, or one parenthesized expression."""
    text = replacement.strip()
    if _SINGLE_TOKEN.match(text):
        return True
    if not text.startswith("("):
        return False
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            return index == len(text) - 1
    return False


def _scan_directives(
    source: str,
) -> tuple[list[_Mutation], dict[str, list[str]], set[str]]:
    """What the directives of ``source`` can do to macros.

    Returns the directives that may change a macro, in source order; each name's
    replacement lists from the ``#define`` lines of this file; and the names defined as
    something other than a single token or a parenthesized expression (a function-like
    macro, or a list such as ``X || Y`` that regroups when the text around it changes).
    """
    found: list[_Mutation] = []
    definitions: dict[str, list[str]] = {}
    unsafe_to_regroup: set[str] = set()
    for line in lex(source).lines:
        directive = line.directive
        if directive is None:
            if line.masked.lstrip().startswith(("%:", "??=")):
                # A digraph or trigraph directive: the lexer does not read these, so
                # nothing about it is known.
                found.append(_Mutation(line.start_offset, "digraph", None))
            continue
        if directive.name is None:
            continue
        if directive.name in {"define", "undef"}:
            operand = line.text[directive.name_end :]
            name = macro_name(operand)
            found.append(_Mutation(line.start_offset, directive.name, name))
            if directive.name == "define" and name is not None:
                replacement = operand.lstrip()[len(name) :]
                definitions.setdefault(name, []).append(replacement)
                if replacement.startswith("(") or not _is_atomic(replacement):
                    unsafe_to_regroup.add(name)
        elif directive.name in _OPAQUE_DIRECTIVES:
            found.append(_Mutation(line.start_offset, directive.name, None))
    return found, definitions, unsafe_to_regroup


class _Finder:
    def __init__(
        self,
        source: str,
        limits: ResourceLimits,
        budget: AnalysisBudget,
    ) -> None:
        self._source = source
        self._line_sensitive: int | None = None
        self._limits = limits
        self._budget = budget
        self.rewrites: list[BlockRewrite] = []

    # These are needed only once a pair has passed the cheaper checks, and most sources have
    # no such pair, so they are computed on first use.
    @cached_property
    def _plain(self) -> str:
        return strip_comments(self._source)

    @cached_property
    def _directives(self) -> tuple[list[_Mutation], dict[str, list[str]], set[str]]:
        return _scan_directives(self._source)

    @cached_property
    def _mutation_offsets(self) -> list[int]:
        return [item.offset for item in self._directives[0]]

    @cached_property
    def _pragma_macros(self) -> frozenset[str]:
        """Names of this file's macros that expand, directly or not, to ``_Pragma``."""
        _, definitions, _ = self._directives
        found = {
            name
            for name, replacements in definitions.items()
            if any(_PRAGMA_OPERATOR in _IDENTIFIER.findall(text) for text in replacements)
        }
        changed = True
        while changed:
            changed = False
            for name, replacements in definitions.items():
                if name not in found and any(
                    identifier in found
                    for text in replacements
                    for identifier in _IDENTIFIER.findall(text)
                ):
                    found.add(name)
                    changed = True
        return frozenset(found)

    @cached_property
    def _closures(self) -> dict[str, frozenset[str]]:
        return {}

    def _closure(self, name: str) -> frozenset[str]:
        """``name`` plus every name its definitions in this file mention, repeatedly."""
        cached = self._closures.get(name)
        if cached is not None:
            return cached
        _, definitions, _ = self._directives
        seen = {name}
        pending = [name]
        while pending:
            for text in definitions.get(pending.pop(), ()):
                for identifier in _IDENTIFIER.findall(text):
                    if identifier not in seen:
                        seen.add(identifier)
                        pending.append(identifier)
        result = self._closures[name] = frozenset(seen)
        return result

    def _dependencies(self, names: frozenset[str]) -> frozenset[str]:
        """The union of each name's closure; each closure is computed once per file."""
        return frozenset().union(*(self._closure(name) for name in names))

    def _inert(self, depends_on: frozenset[str]) -> bool:
        """Whether testing these names once, twice or on another line cannot matter.

        That fails for the built-ins whose value changes from use to use, for a name that a
        macro of this file builds by token pasting (it could form one of them), and for a
        ``_Pragma`` that expanding the names would run.
        """
        if not depends_on.isdisjoint(_VOLATILE_NAMES):
            return False
        if _PRAGMA_OPERATOR in depends_on or not depends_on.isdisjoint(self._pragma_macros):
            return False
        _, definitions, _ = self._directives
        return not any("##" in text for name in depends_on for text in definitions.get(name, ()))

    def _last_line_sensitive_offset(self, structure: ConditionalStructureTree) -> int:
        """The offset of the last condition whose value could depend on its line number.

        That is a condition reaching ``__LINE__`` through the macros of this file, or one
        reaching a macro this file builds by token pasting (it could form ``__LINE__``).
        Computed once per file; -1 when there is none.
        """
        if self._line_sensitive is None:
            _, definitions, _ = self._directives
            last = -1
            for directive in structure.directives:
                names = frozenset(_IDENTIFIER.findall(directive.logical_condition or ""))
                depends_on = self._dependencies(names)
                pastes = any(
                    "##" in text for name in depends_on for text in definitions.get(name, ())
                )
                if "__LINE__" in depends_on or pastes:
                    last = max(last, directive.source_range.start.offset)
            self._line_sensitive = last
        return self._line_sensitive

    def shifts_a_line_test(
        self, rewrite: BlockRewrite, structure: ConditionalStructureTree
    ) -> bool:
        """Whether a condition after the rewrite could depend on its own line number.

        A rewrite that deletes lines moves everything after it up, so a later ``#if`` that
        tests ``__LINE__`` could select another branch. The check also stops at a later
        condition that reaches a token-pasting macro of this file, since that could form
        ``__LINE__``. (Other uses of ``__LINE__`` change only the numbers in the output.)
        """
        return self._last_line_sensitive_offset(structure) > rewrite.edit_start.offset

    # --- evidence about one pair -----------------------------------------------------

    def _gap_is_blank(self, earlier: ConditionalBlock, later: ConditionalBlock) -> bool:
        start = earlier.source_range.end.offset
        end = later.source_range.start.offset
        return start <= end and all(char in _BLANK for char in self._source[start:end])

    def _junction_is_comment_free(self, earlier: ConditionalBlock, later: ConditionalBlock) -> bool:
        """Whether the ``#endif`` and the next opening directive carry no comment.

        These are the two directives a merge removes. A comment on the first block's
        opening directive or the last block's ``#endif`` survives the merge, so it is fine.
        """
        assert earlier.endif is not None
        for item in (earlier.endif.source_range, later.branches[0].directive.source_range):
            start, end = item.start.offset, item.end.offset
            if self._source[start:end] != self._plain[start:end]:
                return False
        return True

    def _body_is_safe(self, block: ConditionalBlock, depends_on: frozenset[str]) -> bool:
        """Whether nothing in the block's body can change what ``depends_on`` mean."""
        start = block.branches[0].directive.source_range.end.offset
        assert block.endif is not None
        end = block.endif.source_range.start.offset
        mutations = self._directives[0]
        offsets = self._mutation_offsets
        index = bisect_left(offsets, start)
        while index < len(offsets) and offsets[index] < end:
            item = mutations[index]
            if item.name in _OPAQUE_DIRECTIVES or item.name == "digraph":
                return False
            if item.macro is None or item.macro in depends_on:
                return False
            index += 1
        # ``_Pragma`` can do what ``#pragma`` does, directly or through a macro of this file.
        pragma = self._pragma_macros | {_PRAGMA_OPERATOR}
        return not any(
            identifier in pragma for identifier in _IDENTIFIER.findall(self._plain[start:end])
        )

    def _relation(self, left: Expression, right: Expression) -> FindingKind | None:
        """How ``right`` relates to ``left``: the same test, its complement, or neither."""
        if left == right:
            return FindingKind.MERGEABLE_BLOCKS
        if Negation(left) == right or left == Negation(right):
            return FindingKind.COMPLEMENTARY_BLOCKS
        left_atoms = list(dict.fromkeys(expression_atoms_in_order(left)))
        right_atoms = list(dict.fromkeys(expression_atoms_in_order(right)))
        if set(left_atoms).isdisjoint(right_atoms):
            # Neither condition is constant (a constant block is dead or redundant), so two
            # conditions that share no test cannot be equal or complementary. This also
            # keeps unrelated neighbours from spending the shared analysis work.
            return None
        atoms = list(dict.fromkeys([*left_atoms, *right_atoms]))
        try:
            bdd = BDD(atoms, limits=self._limits, budget=self._budget)
            if bdd.equivalent_under(TRUE, left, right):
                return FindingKind.MERGEABLE_BLOCKS
            if bdd.equivalent_under(TRUE, left, Negation(right)):
                return FindingKind.COMPLEMENTARY_BLOCKS
        except AnalysisLimitExceeded as error:
            if error.resource == "work":
                raise
        return None

    # --- one sibling list --------------------------------------------------------------

    @staticmethod
    def _opener(group: ConditionalGroup) -> ConditionalBranch | None:
        if len(group.branches) != 1:
            return None
        opener = group.branches[0]
        if opener.analysis is None or opener.analysis.status != "reachable":
            return None
        return opener

    def _pair_relation(
        self,
        first: tuple[ConditionalGroup, ConditionalBlock],
        second: tuple[ConditionalGroup, ConditionalBlock],
    ) -> FindingKind | None:
        """Which rewrite, if any, the two adjacent blocks allow."""
        (first_group, first_block), (second_group, second_block) = first, second
        if first_block.endif is None or second_block.endif is None:
            return None
        if len(first_block.branches) != 1 or len(second_block.branches) != 1:
            return None
        if self._opener(first_group) is None or self._opener(second_group) is None:
            return None
        first_directive = first_block.branches[0].directive
        second_directive = second_block.branches[0].directive
        left, right = first_directive.condition, second_directive.condition
        if left is None or right is None:
            return None
        left_names, right_names = _boolean_names(left), _boolean_names(right)
        if left_names is None or right_names is None:
            return None
        if not self._gap_is_blank(first_block, second_block):
            return None
        if not self._junction_is_comment_free(first_block, second_block):
            return None
        depends_on = self._dependencies(left_names | right_names)
        if not self._inert(depends_on):
            return None
        if not self._body_is_safe(first_block, depends_on):
            return None
        same_text = " ".join((first_directive.logical_condition or "").split()) == " ".join(
            (second_directive.logical_condition or "").split()
        )
        # Two different conditions are regrouped by a rewrite (equal text cannot be a
        # complement), so none may depend on a name that is unsafe to regroup. Deciding that
        # first spares the equivalence check for a pair that could never be reported.
        if not same_text and not depends_on.isdisjoint(self._directives[2]):
            return None
        relation = self._relation(left, right)
        return relation

    def _merge_rewrite(
        self, groups: Sequence[ConditionalGroup], blocks: Sequence[ConditionalBlock]
    ) -> BlockRewrite:
        """The run of ``groups`` and the single edit that merges it."""
        first, last = blocks[0], blocks[-1]
        assert first.endif is not None
        start = first.endif.source_range.start
        end = last.branches[0].directive.source_range.end
        replacement = "".join(
            self._source[
                block.branches[
                    0
                ].directive.source_range.end.offset : block.endif.source_range.start.offset
            ]
            for block in blocks[1:-1]
            if block.endif is not None
        )
        return BlockRewrite(
            FindingKind.MERGEABLE_BLOCKS, groups[0], tuple(groups[1:]), start, end, replacement
        )

    def _else_rewrite(
        self, groups: Sequence[ConditionalGroup], blocks: Sequence[ConditionalBlock]
    ) -> BlockRewrite:
        """The pair of ``groups`` and the edit that turns it into one ``#if``/``#else``."""
        first, second = blocks
        assert first.endif is not None
        # The edit starts at the line start of the first ``#endif`` and keeps that line's
        # indentation and line break for the ``#else`` that stands in its place.
        text = first.endif.source_range.text(self._source)
        newline = next((mark for mark in ("\r\n", "\n", "\r") if text.endswith(mark)), "")
        indent = text[: len(text) - len(text.lstrip())]
        return BlockRewrite(
            FindingKind.COMPLEMENTARY_BLOCKS,
            groups[0],
            (groups[1],),
            first.endif.source_range.start,
            second.branches[0].directive.source_range.end,
            f"{indent}#else{newline}",
        )

    def _scan_siblings(
        self,
        groups: Sequence[ConditionalGroup],
        blocks: Sequence[ConditionalBlock],
    ) -> None:
        if len(groups) != len(blocks):
            return  # The two parsers disagree about the shape; report nothing here.
        start = 0
        while start < len(groups):
            relation = (
                self._pair_relation(
                    (groups[start], blocks[start]), (groups[start + 1], blocks[start + 1])
                )
                if start + 1 < len(groups)
                else None
            )
            if relation is FindingKind.COMPLEMENTARY_BLOCKS:
                self.rewrites.append(
                    self._else_rewrite(groups[start : start + 2], blocks[start : start + 2])
                )
                start += 2
            elif relation is FindingKind.MERGEABLE_BLOCKS:
                end = start + 1  # The first pair was just proved; only the next ones are new.
                while end + 1 < len(groups) and (
                    self._pair_relation(
                        (groups[end], blocks[end]), (groups[end + 1], blocks[end + 1])
                    )
                    is FindingKind.MERGEABLE_BLOCKS
                ):
                    end += 1
                self.rewrites.append(
                    self._merge_rewrite(groups[start : end + 1], blocks[start : end + 1])
                )
                start = end + 1
            else:
                start += 1
        for group, block in zip(groups, blocks, strict=True):
            self._scan_branches(group, block)

    def _scan_branches(self, group: ConditionalGroup, block: ConditionalBlock) -> None:
        if len(group.branches) != len(block.branches):
            return
        for branch, structural in zip(group.branches, block.branches, strict=True):
            self._scan_siblings(branch.children, structural.children)


def find_block_rewrites(
    source: str,
    tree: ConditionalTree,
    *,
    limits: ResourceLimits,
    budget: AnalysisBudget,
    structure: ConditionalStructureTree | None = None,
) -> list[BlockRewrite]:
    """The adjacent sibling blocks in ``tree`` that could be written with fewer blocks.

    ``tree`` must be the analyzed tree of ``source``. Blocks that were not analyzed (a
    resource limit stopped their component) are never part of a run. A rewrite is dropped
    when a condition after it depends on ``__LINE__``. Exhausting the shared
    work budget raises :class:`AnalysisLimitExceeded`; any other limit only means that one
    pair is not reported.
    """
    structure = structure or parse_conditionals(source)
    if structure.diagnostics:
        return []
    finder = _Finder(source, limits, budget)
    finder._scan_siblings(tree.groups, structure.blocks)
    return [item for item in finder.rewrites if not finder.shifts_a_line_test(item, structure)]


__all__ = ["BlockRewrite", "find_block_rewrites"]
