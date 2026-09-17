import pytest

import cpre


def test_comments_are_masked_before_conditional_tokenization():
    source = "#if FLAG /* trailing condition comment */\nbody\n#endif // trailing endif comment\n"

    tree = cpre.parse_conditionals(source)

    assert not tree.diagnostics
    opening, closing = tree.directives
    assert opening.condition_text == "FLAG"
    assert opening.logical_condition == "FLAG"
    assert [token.text for token in opening.tokens] == ["FLAG"]
    assert opening.condition_range.text(source) == "FLAG"
    assert closing.tokens == ()


@pytest.mark.parametrize("prefix", ["u8", "u", "U", "L"])
def test_prefixed_raw_strings_cannot_introduce_conditional_directives(prefix):
    source = (
        f'auto text = {prefix}R"tag("embedded quote"\n'
        "#if FAKE\n"
        ")tag\";\n"
        "#if REAL\n"
        "body\n"
        "#endif\n"
    )

    tree = cpre.parse_conditionals(source)

    assert not tree.diagnostics
    assert [directive.kind for directive in tree.directives] == ["if", "endif"]
    assert tree.directives[0].condition == cpre.Variable("REAL")
    assert tree.blocks[0].branches[0].body_range.text(source) == "body\n"
