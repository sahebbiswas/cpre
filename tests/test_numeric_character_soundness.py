import pytest

from cpre import ErrorCode, preprocess_source


@pytest.mark.parametrize("literal", [r"'\200'", r"'\400'", r"'\x100'"])
def test_high_numeric_character_escape_truth_is_structured(literal):
    result = preprocess_source(f"#if {literal}\nyes\n#endif\n")

    assert not result.complete
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION
    assert "target char signedness/width" in result.incomplete[0].message


def test_low_numeric_character_escape_remains_concrete():
    result = preprocess_source("#if '\\177' == 127\nyes\n#endif\n")

    assert result.complete
    assert "yes" in result.source
