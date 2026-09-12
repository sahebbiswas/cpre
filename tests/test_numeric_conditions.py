import pytest

from cpre import AnalysisOptions, ErrorCode, preprocess_source


def chosen(condition, definitions=""):
    source = f"{definitions}#if {condition}\ntrue_branch\n#else\nfalse_branch\n#endif\n"
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    return "true_branch" in result.source


def test_defined_integer_macro_drives_comparison():
    assert chosen("FOO > 2", "#define FOO 3\n")
    assert not chosen("FOO > 3", "#define FOO 3\n")


@pytest.mark.parametrize(
    "condition, expected",
    [
        ("1 + 2 * 3 == 7", True),
        ("(1 + 2) * 3 == 9", True),
        ("8 >> 2 == 2", True),
        ("1 << 3 == 8", True),
        ("(6 & 3) == 2", True),
        ("(1 | 2) == 3", True),
        ("(7 ^ 3) == 4", True),
        ("-7 / 3 == -2", True),
        ("-7 % 3 == -1", True),
        ("0 && (1 / 0)", False),
        ("1 || (1 / 0)", True),
    ],
)
def test_integer_operator_precedence_and_short_circuit(condition, expected):
    assert chosen(condition) is expected


def test_integer_suffixes_bases_and_unsigned_conversion():
    assert chosen("0x10UL == 020 && 0b100u == 4")
    assert chosen("-1 > 0u")
    assert chosen("0xffffffffffffffffu + 1u == 0u")
    assert chosen("~0u == 0xffffffffffffffffu")


def test_object_aliases_expand_before_evaluation():
    definitions = "#define BASE 2\n#define VALUE BASE + 3\n"
    assert not chosen("VALUE * 2 == 10", definitions)
    assert chosen("VALUE * 2 == 10", "#define BASE 2\n#define VALUE (BASE + 3)\n")


def test_function_macro_invocation_expands_in_condition():
    definitions = "#define TWICE(x) ((x) * 2)\n#define VALUE 3\n"
    assert chosen("TWICE(VALUE) >= 6", definitions)


def test_defined_operator_mixes_with_numeric_expression():
    assert chosen("defined(FOO) && FOO == 3", "#define FOO 3\n")


def test_unknown_identifiers_and_definedness_remain_unresolved():
    for condition in ("VERSION >= 4", "defined(FOO) && FOO == 3"):
        result = preprocess_source(f"#if {condition}\nyes\n#endif\n")
        assert not result.complete
        assert result.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION


def test_unsupported_numeric_operation_is_structured():
    result = preprocess_source("#if 1 ? 2 : 0\nyes\n#endif\n")
    assert not result.complete
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION
    assert result.incomplete[0].location.line == 1


def test_deep_numeric_nesting_is_structured_instead_of_recursing():
    # Arithmetic forces the concrete numeric fallback; a bare constant is
    # resolved by the symbolic Boolean layer before the numeric parser runs.
    condition = "(" * 60 + "1 + 0" + ")" * 60
    result = preprocess_source(f"#if {condition}\nyes\n#endif\n")
    assert not result.complete
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION
    assert "nesting" in result.incomplete[0].message


def test_deep_unary_nesting_is_structured_instead_of_recursing():
    # Keep an arithmetic leaf so the expression reaches the numeric fallback
    # instead of being solved entirely by Boolean simplification.
    result = preprocess_source("#if " + "!" * 60 + "(1 + 0)\nyes\n#endif\n")
    assert not result.complete
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION
    assert "nesting" in result.incomplete[0].message


def test_numeric_work_uses_analysis_budget():
    result = preprocess_source(
        "#define FOO 3\n#if FOO + FOO + FOO + FOO > 2\nyes\n#endif\n",
        options=AnalysisOptions(max_work=10),
    )
    assert not result.complete
    assert result.incomplete[0].code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED
