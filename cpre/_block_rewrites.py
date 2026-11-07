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

"Depends on" follows the ``#define`` directives of this file and, when one is supplied,
the definitions of a :class:`~cpre.MacroConfiguration`: a name is replaced by the names
its replacement list mentions, repeatedly. Macros defined anywhere else (a header that was
not turned into a configuration) are not visible, so for those only the names the
condition itself uses are checked, unless ``strict_merge`` is set: then a name nothing
defines is assumed to depend on whatever a body between two blocks changes.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

from ._types import FindingKind
from .configuration import MacroConfiguration, UnknownNamePolicy
from .expressions import expression_atoms_in_order, expressions_differ
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
    ConditionalDirective,
    ConditionalStructureTree,
    StructuralSourceLocation,
    parse_conditionals,
)
from .structure import ConditionalBranch as _StructuralBranch

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

_DEFINED_OPERAND = re.compile(r"\bdefined\s*(?:\(\s*[A-Za-z_]\w*\s*\)|[A-Za-z_]\w*)")
# Not preceded by a word character or a dot, so the ``U`` of ``1U`` or ``f`` of ``1.5f`` is
# part of a number, not a name.
_IDENTIFIER = re.compile(r"(?<![\w.])[A-Za-z_]\w*")
_WHOLE_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")
_CALL = re.compile(r"([A-Za-z_]\w*)\s*\(")
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<number>(?:0[xX][0-9A-Fa-f]+|\d+)[uUlL]*)
        |(?P<char>(?:[uUL]|u8)?'(?:[^'\\\n]|\\.)+')
        |(?P<name>[A-Za-z_]\w*)
        |(?P<operator><<|>>|<=|>=|==|!=|&&|\|\||[-+*/%<>&|^?:,])
        |(?P<unary>[!~])
        |(?P<open>\()
        |(?P<close>\))
    )""",
    re.VERBOSE,
)
_HAS_INCLUDE = re.compile(r"\b__has_include(?:_next)?\s*\([^()]*\)")
# A test that needs no parentheses when it is joined to another with ``||``: a name, a
# ``defined`` test, or the negation of either.
_SIMPLE_TEST = re.compile(
    r"!?\s*(?:defined\s*\(\s*[A-Za-z_]\w*\s*\)|defined\s+[A-Za-z_]\w*|[A-Za-z_]\w*)\Z"
)
_DIRECTIVE_START = re.compile(r"[ \t\f\v]*#")
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
      block's opening directive (and its line break), with ``#else`` and that line break;
    * ``JOINABLE_BRANCHES``: from the first joined branch's directive through the end of
      the last joined branch's body, with one directive that tests the conditions joined
      by ``||`` and the body once. Here ``branches`` holds the joined branches of
      ``first``, and ``related`` is empty;
    * ``COLLAPSIBLE_NESTING``: the whole outermost block, replaced by one block that tests
      the conditions of the whole chain joined by ``&&`` around the innermost body.
      ``related`` holds the blocks nested inside ``first``;
    * ``EMPTY_BLOCK``: the whole block, deleted. ``related`` holds the empty blocks inside it;
    * ``EMPTY_BRANCH``: from the first empty trailing branch's directive through the end of
      the block's last body, deleted. ``branches`` holds the deleted branches of ``first``.
    """

    kind: FindingKind
    first: ConditionalGroup
    related: tuple[ConditionalGroup, ...]
    edit_start: StructuralSourceLocation
    edit_end: StructuralSourceLocation
    replacement: str
    branches: tuple[ConditionalBranch, ...] = ()


def _is_parenthesized(text: str) -> bool:
    """Whether ``text`` is one parenthesized group, such as ``(A || B)``.

    Parentheses inside a character or string literal, such as ``'('``, do not count.
    """
    if not (text.startswith("(") and text.endswith(")")):
        return False
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        depth += (char == "(") - (char == ")")
        if depth == 0 and index < len(text) - 1:
            return False
    return depth == 0 and not quote


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


def _macro_body(text: str) -> str:
    """The replacement list of a stored definition, without a function-like parameter list."""
    if not text.startswith("("):
        return text
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            return text[index + 1 :]
    return text


def _scan_directives(
    source: str, configuration: MacroConfiguration | None = None
) -> tuple[list[_Mutation], dict[str, list[str]], set[str], set[str]]:
    """What the directives of ``source`` can do to macros.

    Returns the directives that may change a macro, in source order; each name's
    replacement lists from the ``#define`` lines of this file; and the names defined as
    something other than a single token or a parenthesized expression (a function-like
    macro, or a list such as ``X || Y`` that regroups when the text around it changes);
    and the names defined as function-like macros.
    The definitions of ``configuration`` come first: they are in force before the file.
    """
    found: list[_Mutation] = []
    definitions: dict[str, list[str]] = {}
    unsafe_to_regroup: set[str] = set()
    function_like: set[str] = set()
    for definition in configuration.definitions if configuration is not None else ():
        # Stored the way a ``#define`` line gives it: a parameter list right after the name
        # for a function-like macro, a blank first for an object-like one.
        if definition.parameters is not None:
            text = f"({', '.join(definition.parameters)}) {definition.replacement}"
            function_like.add(definition.name)
        else:
            text = f" {definition.replacement}"
        definitions.setdefault(definition.name, []).append(text)
        if definition.parameters is not None or not _is_atomic(definition.replacement):
            unsafe_to_regroup.add(definition.name)
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
                if replacement.startswith("("):
                    function_like.add(name)
                if replacement.startswith("(") or not _is_atomic(replacement):
                    unsafe_to_regroup.add(name)
        elif directive.name in _OPAQUE_DIRECTIVES:
            found.append(_Mutation(line.start_offset, directive.name, None))
    return found, definitions, unsafe_to_regroup, function_like


class _Finder:
    def __init__(
        self,
        source: str,
        limits: ResourceLimits,
        budget: AnalysisBudget,
        configuration: MacroConfiguration | None = None,
        strict_merge: bool = False,
    ) -> None:
        self._source = source
        self._configuration = configuration
        self._strict_merge = strict_merge
        self._line_sensitive: int | None = None
        self._limits = limits
        self._budget = budget
        self.rewrites: list[BlockRewrite] = []
        self._collapsed: set[int] = set()  # Groups already part of a reported chain.
        self._emptied: set[int] = set()  # Groups deleted by an ``EMPTY_BLOCK`` rewrite.
        self._emptied_branches: set[int] = set()  # Branches deleted by an ``EMPTY_BRANCH`` one.

    # These are needed only once a pair has passed the cheaper checks, and most sources have
    # no such pair, so they are computed on first use.
    @cached_property
    def _plain(self) -> str:
        return strip_comments(self._source)

    @cached_property
    def _scanned(
        self,
    ) -> tuple[list[_Mutation], dict[str, list[str]], set[str], set[str]]:
        return _scan_directives(self._source, self._configuration)

    @property
    def _directives(self) -> tuple[list[_Mutation], dict[str, list[str]], set[str]]:
        mutations, definitions, unsafe, _ = self._scanned
        return mutations, definitions, unsafe

    @property
    def _function_like(self) -> set[str]:
        return self._scanned[3]

    @cached_property
    def _first_seen(self) -> dict[str, int]:
        """The offset of the first ``#define`` or ``#undef`` of each name in the file."""
        seen: dict[str, int] = {}
        for item in self._scanned[0]:
            if item.macro is not None and item.name in {"define", "undef"}:
                seen.setdefault(item.macro, item.offset)
        return seen

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

    def _has_unseen_name(self, names: frozenset[str], at: int) -> bool:
        """Whether some name has no definition before offset ``at`` in the file or the configuration.

        Such a name may be defined by a header cpre never read, as anything, including a
        name a body changes. A name the configuration marks undefined is known. A name the
        file defines only after ``at`` is still unknown at ``at``.
        """
        configuration = self._configuration
        if configuration is not None and configuration.unknown_names is UnknownNamePolicy.UNDEFINED:
            return False  # Closed world: a name nothing defines is undefined.
        configured = (
            {item.name for item in configuration.definitions} | configuration.undefined
            if configuration is not None
            else set()
        )
        first = self._first_seen
        return any(name not in configured and first.get(name, at) >= at for name in names)

    def _body_is_safe(self, block: ConditionalBlock, depends_on: frozenset[str]) -> bool:
        """Whether nothing in the block's body can change what ``depends_on`` mean."""
        start = block.branches[0].directive.source_range.end.offset
        assert block.endif is not None
        end = block.endif.source_range.start.offset
        mutations = self._directives[0]
        offsets = self._mutation_offsets
        index = bisect_left(offsets, start)
        unseen = self._strict_merge and self._has_unseen_name(depends_on, start)
        while index < len(offsets) and offsets[index] < end:
            item = mutations[index]
            if item.name in _OPAQUE_DIRECTIVES or item.name == "digraph":
                return False
            if item.macro is None or item.macro in depends_on or unseen:
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
        if id(first_group) in self._emptied or id(second_group) in self._emptied:
            return None
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
        for group, block in zip(groups, blocks, strict=True):
            self._scan_empty_block(group, block)
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
            if id(group) in self._emptied:
                continue
            if id(group) not in self._collapsed:
                self._scan_nesting(group, block)
            self._scan_empty_branches(group, block)
            self._scan_branches(group, block)

    # --- blocks and trailing branches with no content -------------------------------------

    def _empty_inside(
        self, group: ConditionalGroup, block: ConditionalBlock
    ) -> list[ConditionalGroup] | None:
        """The blocks inside ``block`` if nothing in it but blanks and empty blocks, else ``None``."""
        if block.endif is None or len(group.branches) != len(block.branches):
            return None
        nested: list[ConditionalGroup] = []
        for branch, structural in zip(group.branches, block.branches, strict=True):
            if len(branch.children) != len(structural.children):
                return None
            cursor = structural.body_range.start.offset
            for child, child_block in zip(branch.children, structural.children, strict=True):
                if not self._blank_between(cursor, child_block.source_range.start.offset):
                    return None
                inside = self._empty_inside(child, child_block)
                if inside is None:
                    return None
                nested.append(child)
                nested.extend(inside)
                cursor = child_block.source_range.end.offset
            if not self._blank_between(cursor, structural.body_range.end.offset):
                return None
        return nested

    def _conditions_in(self, block: ConditionalBlock) -> set[str]:
        """The identifiers tested by every directive of ``block``, nested blocks included."""
        names: set[str] = set()
        for branch in block.branches:
            names.update(_IDENTIFIER.findall(branch.directive.logical_condition or ""))
            for child in branch.children:
                names |= self._conditions_in(child)
        return names

    def _condition_is_harmless(self, directive: ConditionalDirective) -> bool:
        """Whether deleting ``directive`` cannot hide a preprocessing error in its condition.

        A condition that is not a well-formed expression, calls an unknown function-like macro
        or divides makes the preprocessor fail when it is evaluated; once the directive is gone
        nothing evaluates it.
        """
        if directive.kind in {"else", "endif"}:
            return True
        test = self._branch_test(directive)
        return (
            test is not None
            and self._is_expression(test)
            and self._calls_are_known(test)
            and "/" not in test
            and "%" not in test
            and self._macros_are_harmless(test)
        )

    def _macros_are_harmless(self, test: str) -> bool:
        """Whether no macro ``test`` uses (in this file) makes it fail once expanded.

        A macro that expands to a division, to nothing, or to something that is not a valid
        ``#if`` operand (``1.5``, a string) breaks the condition that uses it. The operand of ``defined`` is not expanded, so it does not count.
        """
        _, definitions, _ = self._directives
        used = _DEFINED_OPERAND.sub(" ", test)
        names = self._dependencies(frozenset(_IDENTIFIER.findall(used)))
        bodies = [_macro_body(text) for name in names for text in definitions.get(name, ())]
        return all(
            "/" not in body
            and "%" not in body
            and body.strip() != ""
            and self._is_expression(body)
            and self._calls_are_known(body)
            for body in bodies
        )

    def _directives_in(self, block: ConditionalBlock) -> list[ConditionalDirective]:
        found = []
        for branch in block.branches:
            found.append(branch.directive)
            for child in branch.children:
                found.extend(self._directives_in(child))
        return found

    def _scan_empty_block(self, group: ConditionalGroup, block: ConditionalBlock) -> None:
        """Report ``group`` when no branch of it, and nothing in it, has content."""
        if id(group) in self._collapsed:
            return  # Part of a collapsible chain, whose edit already covers this block.
        nested = self._empty_inside(group, block)
        if nested is None or block.endif is None:
            return
        span = block.source_range
        start, end = span.start.offset, span.end.offset
        if self._source[start:end] != self._plain[start:end]:
            return  # A comment would be lost with the block.
        if any(
            branch.analysis is None or branch.analysis.status != "reachable"
            for item in (group, *nested)
            for branch in item.branches
        ):
            return  # A dead or redundant branch, nested ones included, is reported as such.
        if not self._inert(self._dependencies(frozenset(self._conditions_in(block)))):
            return  # Deleting the conditions would change a counter or skip a pragma.
        if not all(self._condition_is_harmless(item) for item in self._directives_in(block)):
            return  # Deleting a condition that fails to evaluate would hide the error.
        self._emptied.add(id(group))
        self._emptied.update(id(item) for item in nested)
        self.rewrites.append(
            BlockRewrite(FindingKind.EMPTY_BLOCK, group, tuple(nested), span.start, span.end, "")
        )

    def _scan_empty_branches(self, group: ConditionalGroup, block: ConditionalBlock) -> None:
        """Report the last ``#elif`` and ``#else`` branches of ``group`` that have no content.

        Only a run at the end can go: an empty branch in the middle still decides which later
        branch is skipped when its condition is true.
        """
        count = len(block.branches)
        if count < 2 or block.endif is None or len(group.branches) != count:
            return
        cut = count
        while cut > 1 and self._blank_between(
            block.branches[cut - 1].body_range.start.offset,
            block.branches[cut - 1].body_range.end.offset,
        ):
            cut -= 1
        if cut == count:
            return
        qualifies = [
            self._branch_can_go(group.branches[i], block.branches[i]) for i in range(count)
        ]
        # Try the longest run of removable branches first, then shorter ones.
        runs = sorted(
            (
                (start, stop)
                for start in range(cut, count)
                for stop in range(start + 1, count + 1)
                if all(qualifies[start:stop])
            ),
            key=lambda run: (run[0] - run[1], run[0]),
        )
        chosen = next((run for run in runs if self._run_is_safe(block, *run)), None)
        if chosen is None:
            return
        first, last = chosen
        removed = block.branches[first:last]
        self._emptied_branches.update(id(item) for item in group.branches[first:last])
        self.rewrites.append(
            BlockRewrite(
                FindingKind.EMPTY_BRANCH,
                group,
                (),
                removed[0].directive.source_range.start,
                removed[-1].body_range.end,
                "",
                tuple(group.branches[first:last]),
            )
        )

    def _run_is_safe(self, block: ConditionalBlock, first: int, last: int) -> bool:
        """Whether deleting branches ``first:last`` changes no evaluation that matters.

        The deleted conditions must be inert, and a branch kept after the run is now evaluated
        whenever the run's tests were true before, so each must be harmless and inert too.
        """
        kept = block.branches[last:]
        if not all(self._condition_is_harmless(item.directive) for item in kept):
            return False
        names = {
            name
            for item in (*block.branches[first:last], *kept)
            for name in _IDENTIFIER.findall(item.directive.logical_condition or "")
        }
        return self._inert(self._dependencies(frozenset(names)))

    def _branch_can_go(self, branch: ConditionalBranch, structural: _StructuralBranch) -> bool:
        """Whether one empty branch can be deleted: plain, reachable and harmless to evaluate."""
        span = structural.directive.source_range
        if (
            self._source[span.start.offset : span.end.offset]
            != self._plain[span.start.offset : span.end.offset]
        ):
            return False  # A comment on the directive would be lost.
        if branch.analysis is None or branch.analysis.status != "reachable":
            return False
        return self._condition_is_harmless(structural.directive)

    # --- blocks whose only content is one block -----------------------------------------

    def _blank_between(self, start: int, end: int) -> bool:
        return start <= end and all(char in _BLANK for char in self._source[start:end])

    def _opener_is_plain(self, group: ConditionalGroup, block: ConditionalBlock) -> bool:
        """Whether ``block`` is a single reachable branch whose opening line can be rewritten."""
        opener = self._opener(group)
        if len(block.branches) != 1 or block.endif is None or opener is None:
            return False
        analysis = opener.analysis
        assert analysis is not None
        if opener.expression is not None and (
            expressions_differ(opener.expression, analysis.simplified)
            or expressions_differ(opener.expression, analysis.contextual)
        ):
            return False  # Simplify the condition first; the collapsed one would repeat it.
        directive = block.branches[0].directive
        if self._branch_test(directive) is None:
            return False
        span = directive.source_range
        raw = self._source[span.start.offset : span.end.offset]
        if raw != self._plain[span.start.offset : span.end.offset]:
            return False  # A comment on the opening line would be lost.
        line = raw.rstrip("\r\n")
        if "\n" in line or "\r" in line or _DIRECTIVE_START.match(raw) is None:
            return False
        names = self._dependencies(
            frozenset(_IDENTIFIER.findall(self._branch_test(directive) or ""))
        )
        return self._inert(names)

    def _only_child(
        self, group: ConditionalGroup, block: ConditionalBlock
    ) -> tuple[ConditionalGroup, ConditionalBlock] | None:
        """The block that is all of ``block``'s content, if it is joinable to ``block``."""
        if not self._opener_is_plain(group, block):
            return None
        children, structural = group.branches[0].children, block.branches[0].children
        if len(children) != 1 or len(structural) != 1 or block.endif is None:
            return None
        child, child_block = children[0], structural[0]
        if not self._opener_is_plain(child, child_block) or child_block.endif is None:
            return None
        inner_test = self._branch_test(child_block.branches[0].directive)
        if inner_test is None or not (
            self._is_expression(inner_test) and self._calls_are_known(inner_test)
        ):
            # The inner condition was never read while the outer one was false; written
            # after ``&&`` it is always parsed, so it must be well-formed.
            return None
        inner_endif = child_block.endif.source_range
        if (
            self._source[inner_endif.start.offset : inner_endif.end.offset]
            != self._plain[inner_endif.start.offset : inner_endif.end.offset]
        ):
            return None  # The inner ``#endif`` is deleted, and a comment on it with it.
        opener_end = block.branches[0].directive.source_range.end.offset
        if not self._blank_between(opener_end, child_block.source_range.start.offset):
            return None
        if not self._blank_between(
            child_block.endif.source_range.end.offset, block.endif.source_range.start.offset
        ):
            return None
        return child, child_block

    def _scan_nesting(self, group: ConditionalGroup, block: ConditionalBlock) -> None:
        """Report ``group`` when it holds one block, and that one holds one block, and so on."""
        chain = [(group, block)]
        while (child := self._only_child(*chain[-1])) is not None:
            chain.append(child)
        if len(chain) < 2:
            return
        tests = [self._branch_test(item.branches[0].directive) for _, item in chain]
        condition = " && ".join(self._and_operand(test) for test in tests if test is not None)
        directive = block.branches[0].directive
        raw = directive.source_range.text(self._source)
        indent = raw[: len(raw) - len(raw.lstrip())]
        newline = next((mark for mark in ("\r\n", "\n", "\r") if raw.endswith(mark)), "")
        innermost = chain[-1][1].branches[0]
        if self._blank_between(innermost.body_range.start.offset, innermost.body_range.end.offset):
            return  # Collapsing an empty block is never the point; delete it instead.
        body = innermost.body_range.text(self._source)
        assert block.endif is not None
        endif = block.endif.source_range
        self._collapsed.update(id(item) for item, _ in chain[1:])
        self.rewrites.append(
            BlockRewrite(
                FindingKind.COLLAPSIBLE_NESTING,
                group,
                tuple(item for item, _ in chain[1:]),
                directive.source_range.start,
                endif.end,
                f"{indent}#if {condition}{newline}{body}{endif.text(self._source)}",
            )
        )

    def _and_operand(self, test: str) -> str:
        """``test`` ready to sit next to ``&&``: bare only when that cannot change its meaning."""
        _, definitions, unsafe = self._directives
        if _SIMPLE_TEST.match(test) is not None:
            names = self._dependencies(frozenset(_IDENTIFIER.findall(test)))
            if names.isdisjoint(unsafe):
                return test
        return test if _is_parenthesized(test) else f"({test})"

    # --- adjacent branches of one block with the same body ---------------------------

    def _branch_test(self, directive: ConditionalDirective) -> str | None:
        """The branch's test written as an ``#if`` expression, or ``None``."""
        text = (directive.condition_text or "").strip()
        if not text or "\n" in text or "\r" in text:
            return None
        if directive.kind in {"if", "elif"}:
            return text
        if directive.kind in {"ifdef", "elifdef", "ifndef", "elifndef"}:
            if _WHOLE_IDENTIFIER.match(text) is None:
                return None
            negated = directive.kind in {"ifndef", "elifndef"}
            return f"!defined({text})" if negated else f"defined({text})"
        return None

    def _is_expression(self, text: str) -> bool:
        """Whether ``text`` is a well-formed ``#if`` expression, whatever its macros are.

        This is a conservative check of the shape only (operands and operators alternate,
        parentheses and ``?:`` pair up, calls have well-formed arguments). A branch after
        the first of a run was not read when an earlier branch was taken, so text such as
        ``B C`` was harmless there and would stop a joined condition from preprocessing.
        """
        tokens: list[tuple[str, str]] = []
        position = 0
        text = _HAS_INCLUDE.sub(
            "1", text
        ).strip()  # Its operand is a header name, not an expression.
        while position < len(text):
            match = _TOKEN.match(text, position)
            if match is None or match.lastgroup is None:
                return False
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
            position = match.end()
        return self._parse_expression(tokens, 0, ends_at_close=False) == len(tokens)

    def _parse_expression(
        self, tokens: list[tuple[str, str]], start: int, *, ends_at_close: bool
    ) -> int:
        """The index after the expression at ``start``, or -1 when it is malformed."""
        index = start
        questions = 0
        expect_operand = True
        while index < len(tokens):
            kind, value = tokens[index]
            if expect_operand:
                if kind == "unary" or (kind == "operator" and value in "+-"):
                    index += 1
                elif kind in {"number", "char"}:
                    index, expect_operand = index + 1, False
                elif kind == "open":
                    index = self._parse_expression(tokens, index + 1, ends_at_close=True)
                    if index < 0 or index >= len(tokens) or tokens[index][0] != "close":
                        return -1
                    index, expect_operand = index + 1, False
                elif kind == "name":
                    index = self._parse_name(tokens, index)
                    if index < 0:
                        return -1
                    expect_operand = False
                else:
                    return -1
            elif kind == "operator":
                questions += value == "?"
                questions -= value == ":"
                if questions < 0:
                    return -1
                index, expect_operand = index + 1, True
            elif kind == "close" and ends_at_close:
                break
            else:
                return -1
        if expect_operand or questions != 0:
            return -1
        return index

    def _parse_name(self, tokens: list[tuple[str, str]], index: int) -> int:
        """The index after the name (and its call or ``defined`` operand) at ``index``."""
        name = tokens[index][1]
        after = index + 1
        if name == "defined":
            if after < len(tokens) and tokens[after][0] == "name":
                return after + 1
            if (
                after + 2 < len(tokens)
                and tokens[after][0] == "open"
                and tokens[after + 1][0] == "name"
                and tokens[after + 2][0] == "close"
            ):
                return after + 3
            return -1
        if after >= len(tokens) or tokens[after][0] != "open":
            return after
        position = after + 1
        if position < len(tokens) and tokens[position][0] == "close":
            return position + 1
        position = self._parse_expression(tokens, position, ends_at_close=True)
        if 0 <= position < len(tokens) and tokens[position][0] == "close":
            return position + 1
        return -1

    def _calls_are_known(self, test: str) -> bool:
        """Whether every call in ``test`` is to ``defined``, a ``__has_*`` operator or a function-like macro.

        A branch after the first one was not evaluated when an earlier branch was taken, so an
        invalid expression there (``FOO(1)`` with no macro ``FOO``) was harmless. In a joined
        condition it is always read and would fail to preprocess.
        """
        for match in _CALL.finditer(test):
            name = match.group(1)
            if (
                name != "defined"
                and not name.startswith("__has_")
                and name not in self._function_like
            ):
                return False
        return True

    def _branch_is_joinable(
        self, branch: ConditionalBranch, structural: _StructuralBranch, *, later: bool = False
    ) -> bool:
        """Whether this branch's directive can be rewritten as part of a joined condition.

        ``later`` is set for a branch that would follow the first one of the run.
        """
        directive = structural.directive
        if branch.analysis is None or branch.analysis.status != "reachable":
            return False
        if id(branch) in self._emptied_branches:
            return False
        test = self._branch_test(directive)
        if test is None or (
            later and not (self._is_expression(test) and self._calls_are_known(test))
        ):
            return False
        span = directive.source_range
        raw = self._source[span.start.offset : span.end.offset]
        if raw != self._plain[span.start.offset : span.end.offset]:
            return False  # A comment on the line would be lost.
        if (
            "\n" in raw.rstrip("\r\n")
            or "\r" in raw.rstrip("\r\n")
            or _DIRECTIVE_START.match(raw) is None
        ):
            return False  # A line continuation or a digraph; keep it simple.
        depends_on = self._dependencies(frozenset(_IDENTIFIER.findall(test)))
        return self._inert(depends_on)

    def _operand(self, test: str) -> str:
        """``test`` ready to sit next to ``||``: bare when that cannot change its meaning."""
        if _SIMPLE_TEST.match(test) is not None:
            _, definitions, _ = self._directives
            names = self._dependencies(frozenset(_IDENTIFIER.findall(test)))
            if not any(
                "?" in text or "," in text for name in names for text in definitions.get(name, ())
            ):
                return test
        return f"({test})"

    def _join_rewrite(
        self,
        group: ConditionalGroup,
        block: ConditionalBlock,
        first: int,
        last: int,
    ) -> BlockRewrite:
        """The edit that joins branches ``first`` to ``last`` (both included) of ``block``."""
        directive = block.branches[first].directive
        raw = directive.source_range.text(self._source)
        indent = raw[: len(raw) - len(raw.lstrip())]
        newline = next((mark for mark in ("\r\n", "\n", "\r") if raw.endswith(mark)), "")
        keyword = "if" if directive.kind in {"if", "ifdef", "ifndef"} else "elif"
        tests = [self._branch_test(block.branches[i].directive) for i in range(first, last + 1)]
        condition = " || ".join(self._operand(test) for test in tests if test is not None)
        body = block.branches[first].body_range.text(self._source)
        return BlockRewrite(
            FindingKind.JOINABLE_BRANCHES,
            group,
            (),
            directive.source_range.start,
            block.branches[last].body_range.end,
            f"{indent}#{keyword} {condition}{newline}{body}",
            tuple(group.branches[first : last + 1]),
        )

    def _same_lines(self, branch: _StructuralBranch) -> str:
        """The branch's body with every line ending written as ``\\n``, for comparison."""
        return branch.body_range.text(self._source).replace("\r\n", "\n").replace("\r", "\n")

    def _scan_chain(self, group: ConditionalGroup, block: ConditionalBlock) -> None:
        """Report each run of adjacent branches whose bodies are the same text."""
        count = len(block.branches)
        index = 0
        while index < count:
            if not self._branch_is_joinable(group.branches[index], block.branches[index]):
                index += 1
                continue
            body = self._same_lines(block.branches[index])
            end = index
            while (
                end + 1 < count
                and self._branch_is_joinable(
                    group.branches[end + 1], block.branches[end + 1], later=True
                )
                and self._same_lines(block.branches[end + 1]) == body
            ):
                end += 1
            if end > index:
                self.rewrites.append(self._join_rewrite(group, block, index, end))
            index = end + 1

    def _scan_branches(self, group: ConditionalGroup, block: ConditionalBlock) -> None:
        if len(group.branches) != len(block.branches):
            return
        self._scan_chain(group, block)
        for branch, structural in zip(group.branches, block.branches, strict=True):
            self._scan_siblings(branch.children, structural.children)


def find_block_rewrites(
    source: str,
    tree: ConditionalTree,
    *,
    limits: ResourceLimits,
    budget: AnalysisBudget,
    structure: ConditionalStructureTree | None = None,
    configuration: MacroConfiguration | None = None,
    strict_merge: bool = False,
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
    finder = _Finder(source, limits, budget, configuration, strict_merge)
    finder._scan_siblings(tree.groups, structure.blocks)
    return [item for item in finder.rewrites if not finder.shifts_a_line_test(item, structure)]


__all__ = ["BlockRewrite", "find_block_rewrites"]
