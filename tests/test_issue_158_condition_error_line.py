"""An invalid condition is reported at the physical position of the offending token (#158)."""

import pytest

from cpre import preprocess_source
from cpre.errors import ErrorCode, SourceLocation


def _location(source, **kwargs):
    (diagnostic,) = preprocess_source(source, **kwargs).incomplete
    return diagnostic.location


@pytest.mark.parametrize(
    "source, expected",
    [
        ("#if 1 \\\n int kept;\n#endif\n", SourceLocation(2, 2)),
        ("#if 1 /* c\n*/ int kept;\n#endif\n", SourceLocation(2, 4)),
        ("#if 0\n#elif 1 \\\n 2 3\n#endif\n", SourceLocation(3, 2)),
        ("#if 0\n#elif 1 /* c\n*/ 2 3\n#endif\n", SourceLocation(3, 4)),
        ("#if 1 \\\n / 0\n#endif\n", SourceLocation(2, 4)),
        ("int a;\n\\\n#if 1 2\n#endif\n", SourceLocation(3, 7)),
    ],
)
def test_evaluation_error_points_into_the_physical_line(source, expected):
    assert _location(source) == expected


def test_macro_expansion_in_a_continued_condition_is_located():
    source = '#define S "text"\n#if 1 \\\n && S\n#endif\n'
    assert _location(source) == SourceLocation(3, 5)


def test_predefined_macro_in_a_continued_condition_is_located():
    source = "#if 1 \\\n && __STDC_VERSION__ > 1\n#endif\n"
    diagnostic = preprocess_source(source).incomplete[0]
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert diagnostic.location == SourceLocation(2, 5)


def test_single_line_condition_error_carries_a_column():
    assert _location("#if 1 2\n#endif\n") == SourceLocation(1, 7)
