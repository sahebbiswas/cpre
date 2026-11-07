"""The sentence that explains a block-rewrite suggestion, shared by every output."""

from __future__ import annotations

from collections.abc import Sequence

from ._types import FindingKind


def where(lines: Sequence[int]) -> str:
    """``line 5``, ``lines 5 and 8`` or ``lines 3, 5 and 8``."""
    if len(lines) == 1:
        return f"line {lines[0]}"
    return "lines " + ", ".join(str(line) for line in lines[:-1]) + f" and {lines[-1]}"


def block_note(kind: FindingKind | str, lines: Sequence[int]) -> str:
    """Why a block is reported as ``kind``, naming the lines of the blocks it involves."""
    kind = FindingKind(kind)
    if kind is FindingKind.MERGEABLE_BLOCKS:
        return f"the same condition is tested again at {where(lines)}; the blocks can be merged"
    if kind is FindingKind.COMPLEMENTARY_BLOCKS:
        return (
            f"the opposite condition is tested at {where(lines)}; "
            "the blocks can be one #if with an #else"
        )
    if kind is FindingKind.JOINABLE_BRANCHES:
        return (
            f"the branches at {where(lines)} have the same body; "
            "they can be joined into one branch with ||"
        )
    if kind is FindingKind.COLLAPSIBLE_NESTING:
        noun = "a nested block" if len(lines) == 1 else "nested blocks"
        return (
            f"the only content of this block is {noun} at {where(lines)}; "
            "they can be one #if testing the conditions together with &&"
        )
    if kind is FindingKind.EMPTY_BLOCK:
        if lines:
            return (
                f"this block has no content, nor do the blocks at {where(lines)}; "
                "all of it can be deleted"
            )
        return "this block has no content; it can be deleted"
    if kind is FindingKind.EMPTY_BRANCH:
        if len(lines) == 1:
            return f"the empty branch at {where(lines)} ends the block; it can be deleted"
        return f"the empty branches at {where(lines)} end the block; they can be deleted"
    raise ValueError(f"no note for {kind}")


__all__ = ["block_note", "where"]
