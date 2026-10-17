"""Regression tests for #149 and #175: `#` keeps a space wherever its tokens were separated.

Whitespace travels with each token (``Token.space_before``), so an argument that reached
`#` through another macro, or a ``__VA_OPT__`` group, is spaced as it was written
(C11 6.10.3.2p2) and never from unrelated text at the call site.
"""

import pytest

import cpre
from cpre.lexer import tokenize

CONFIGURATION = cpre.MacroConfiguration(unknown_names="undefined")
HEADER = "#define S(x) #x\n#define D(x) S(x)\n#define E\n#define P a b\n"


def one(source: str) -> str:
    result = cpre.preprocess_source(HEADER + source, configuration=CONFIGURATION)
    assert result.complete, result.incomplete
    return result.source.rstrip("\n").split("\n")[-1].strip()


def test_issue_examples():
    source = "#define S(x) #x\n#define D(x) S(x)\n#define O(c) D(omp c)\nD(a b)\nO(parallel for)\n"
    result = cpre.preprocess_source(source, configuration=CONFIGURATION)
    assert [line.strip() for line in result.source.split("\n")[-3:-1]] == [
        '"a b"',
        '"omp parallel for"',
    ]


def test_pragma_payload_through_two_macros():
    source = (
        "#define DO_PRAGMA(x) _Pragma(#x)\n#define OMP(c) DO_PRAGMA(omp c)\nOMP(parallel for)\n"
    )
    payloads = []

    def consume(pragma):
        payloads.append(pragma.payload)
        return cpre.PragmaDisposition.CONSUME

    result = cpre.preprocess_source(source, configuration=CONFIGURATION, pragma_handler=consume)
    assert result.complete
    assert [" ".join(p.split()) for p in payloads] == ["omp parallel for"]


@pytest.mark.parametrize(
    "use, expected",
    [
        ("D(a b)", '"a b"'),
        ("D(a   /* c */ b)", '"a b"'),
        ("D(a/**/b)", '"a b"'),
        ("D(ab)", '"ab"'),
        ("D(P)", '"a b"'),
        ("D(P+P)", '"a b+a b"'),
        ("D(-P-)", '"-a b-"'),
        ("D(E a E b)", '"a b"'),
        ("D(a E+E b)", '"a + b"'),
        ("D( E  P  E )", '"a b"'),
    ],
)
def test_spacing_through_another_macro(use, expected):
    assert one(use) == expected


def test_spacing_of_a_parameter_use_inside_the_inner_macro():
    source = "#define T(x) S(<x>)\n#define U(x) S(x x)\nT(a b)\nU(a b)\n"
    result = cpre.preprocess_source(HEADER + source, configuration=CONFIGURATION)
    assert [line.strip() for line in result.source.split("\n")[-3:-1]] == [
        '"<a b>"',
        '"a b a b"',
    ]


def test_expansion_inherits_the_spacing_of_its_invocation():
    source = "#define C(a, b) a ## b\nD(C(x, y) C(p, q))\nD(C(x, y)C(p, q))\n"
    result = cpre.preprocess_source(HEADER + source, configuration=CONFIGURATION)
    lines = [line.strip() for line in result.source.split("\n")[-3:-1]]
    assert lines == ['"xy pq"', '"xypq"']


@pytest.mark.parametrize("indent", ["", " ", "    ", "\t\t", " /* pad */ "])
def test_va_opt_group_ignores_the_call_site(indent):
    source = f"#define V(...) #__VA_OPT__(a b   c /* x */ d)\n{indent}V(1)\n"
    result = cpre.preprocess_source(source, configuration=CONFIGURATION)
    assert result.complete
    assert result.source.split("\n")[-2].split("*/")[-1].strip() == '"a b c d"'


def test_va_opt_group_without_spaces_stays_joined():
    source = "#define V(...) #__VA_OPT__(a+b)\n   V(1)\n"
    result = cpre.preprocess_source(source, configuration=CONFIGURATION)
    assert result.source.split("\n")[-2].strip() == '"a+b"'


def test_tokens_record_whitespace_before_them():
    tokens = [t for t in tokenize("a b/**/c\n\td(e)") if t.kind not in {"space", "comment"}]
    assert [(t.text, t.space_before) for t in tokens] == [
        ("a", False),
        ("b", True),
        ("c", True),
        ("d", True),
        ("(", False),
        ("e", False),
        (")", False),
    ]


def test_spliced_lines_do_not_count_as_whitespace():
    tokens = [t for t in tokenize("a\\\nb c") if t.kind not in {"space", "comment"}]
    assert [(t.text, t.space_before) for t in tokens] == [("ab", False), ("c", True)]


def test_space_before_is_not_part_of_token_equality():
    first = tokenize("x")[0]
    second = tokenize(" x")[1]
    assert first.space_before is False and second.space_before is True
    assert (first.text, first.kind) == (second.text, second.kind)
