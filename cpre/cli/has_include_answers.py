"""``cpre preprocess``: explicit ``--has-include`` answers for ``__has_include`` queries."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..include_queries import IncludeQuery, IncludeQueryProvider
from ..includes import IncludeForm

_SPEC = re.compile(r'(?:<(?P<angle>[^<>]+)>|"(?P<quoted>[^"]+)")=(?P<answer>[01])\Z')

Answers = dict[tuple[IncludeForm, str], bool]


def parse_has_include_answers(values: Iterable[str]) -> Answers:
    """Parse ``<header>=1`` / ``"header"=0`` options into answers keyed by form and spelling.

    Raises ``ValueError`` for a malformed value or two conflicting answers for one header.
    """
    answers: Answers = {}
    for value in values:
        match = _SPEC.match(value)
        if match is None:
            raise ValueError(
                f"--has-include: expected '<header>=1' or '\"header\"=0' (1 available, "
                f"0 unavailable), got {value!r}"
            )
        angle = match["angle"]
        key = (
            (IncludeForm.ANGLE, angle)
            if angle is not None
            else (IncludeForm.QUOTED, match["quoted"])
        )
        answer = match["answer"] == "1"
        if answers.setdefault(key, answer) != answer:
            raise ValueError(f"--has-include: conflicting answers for {value.rsplit('=', 1)[0]}")
    return answers


def answer_provider(answers: Answers) -> IncludeQueryProvider:
    """An ``include_query`` callback that answers only the headers given, else ``None``."""

    def provider(query: IncludeQuery) -> bool | None:
        return answers.get((query.form, query.header))

    return provider
