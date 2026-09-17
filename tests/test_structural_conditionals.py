import pytest

import cpre


def test_simple_tree_has_exact_half_open_physical_ranges():
    source = "before\n  #if FLAG\nbody\n#else\nfallback\n#endif\nafter\n"
    tree = cpre.parse_conditionals(source, filename="sample.c")

    assert isinstance(tree, cpre.ConditionalStructureTree)
    assert tree.filename == "sample.c"
    assert tree.source == source
    assert not tree.diagnostics
    assert [directive.kind for directive in tree.directives] == ["if", "else", "endif"]

    block = tree.blocks[0]
    first, second = block.branches
    assert block.parent is None
    assert first.block is block and second.block is block
    assert first.directive.source_range.text(source) == "  #if FLAG\n"
    assert first.directive.condition_range.text(source) == "FLAG"
    assert first.directive.condition_text == "FLAG"
    assert first.directive.logical_condition == "FLAG"
    assert first.body_range.text(source) == "body\n"
    assert second.body_range.text(source) == "fallback\n"
    assert block.source_range.text(source) == "  #if FLAG\nbody\n#else\nfallback\n#endif\n"

    start = first.directive.condition_range.start
    end = first.directive.condition_range.end
    assert (start.offset, start.line, start.column) == (13, 2, 7)
    assert (end.offset, end.line, end.column) == (17, 2, 11)
    assert source[start.offset:end.offset] == "FLAG"


def test_nested_blocks_parent_links_and_c23_forms_are_preserved():
    source = (
        "#if ROOT\n"
        "#ifdef CHILD\n"
        "nested\n"
        "#endif\n"
        "#elifdef ALT\n"
        "alt\n"
        "#elifndef FALLBACK\n"
        "fallback\n"
        "#endif\n"
    )
    tree = cpre.parse_conditionals(source)

    assert not tree.diagnostics
    outer = tree.blocks[0]
    root, alt, fallback = outer.branches
    child = root.children[0]
    assert child.parent is root
    assert child.branches[0].directive.condition == cpre.DefinedVariable("CHILD")
    assert alt.directive.condition == cpre.DefinedVariable("ALT")
    assert fallback.directive.condition == cpre.Negation(cpre.DefinedVariable("FALLBACK"))
    assert [directive.kind for directive in tree.directives] == [
        "if",
        "ifdef",
        "endif",
        "elifdef",
        "elifndef",
        "endif",
    ]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_continued_conditions_retain_physical_text_and_logical_text(newline):
    source = newline.join(
        [
            "#if DEFI\\",
            "NED && \\",
            "  OTHER",
            "body",
            "#endif",
            "",
        ]
    )
    tree = cpre.parse_conditionals(source)

    assert not tree.diagnostics
    directive = tree.directives[0]
    assert directive.condition_text == "DEFI\\" + newline + "NED && \\" + newline + "  OTHER"
    assert directive.logical_condition == "DEFINED &&   OTHER"
    assert directive.condition_range.text(source) == directive.condition_text
    assert directive.tokens[0].text == "DEFINED"
    assert directive.tokens[0].source_range.text(source) == "DEFI\\" + newline + "NED"
    assert directive.condition_range.start.offset == source.index("DEFI")
    assert directive.condition_range.end.offset == source.index("OTHER") + len("OTHER")


def test_comments_literals_and_raw_strings_do_not_create_false_directives():
    source = (
        "/*\n#if BLOCK_COMMENT\n*/\n"
        "// #if LINE_COMMENT\n"
        "char *s = \"#if STRING // /*\";\n"
        "auto raw = R\"tag(\n#if RAW\n)tag\";\n"
        "/* prefix */ #if REAL /* trailing */\n"
        "body\n"
        "#endif // end\n"
    )
    tree = cpre.parse_conditionals(source)

    assert not tree.diagnostics
    assert [directive.kind for directive in tree.directives] == ["if", "endif"]
    assert tree.directives[0].condition == cpre.Variable("REAL")
    assert tree.directives[0].condition_text == "REAL"
    assert tree.blocks[0].branches[0].body_range.text(source) == "body\n"


def test_condition_range_keeps_internal_comment_but_excludes_trailing_comment():
    source = "#if FLAG /* duplicate */ && FLAG  /* keep */\n#endif\n"
    tree = cpre.parse_conditionals(source)
    directive = tree.directives[0]

    assert directive.condition_text == "FLAG /* duplicate */ && FLAG"
    assert directive.condition_range.text(source) == directive.condition_text
    assert directive.logical_condition.split() == ["FLAG", "&&", "FLAG"]


@pytest.mark.parametrize(
    ("source", "codes"),
    [
        (
            "#elif A\n#else\n#endif\n",
            [
                cpre.StructureDiagnosticCode.UNMATCHED_DIRECTIVE,
                cpre.StructureDiagnosticCode.UNMATCHED_DIRECTIVE,
                cpre.StructureDiagnosticCode.UNMATCHED_DIRECTIVE,
            ],
        ),
        (
            "#if A\n#else\n#else\n#elif B\n#endif\n",
            [
                cpre.StructureDiagnosticCode.DUPLICATE_ELSE,
                cpre.StructureDiagnosticCode.BRANCH_AFTER_ELSE,
            ],
        ),
        (
            "#if\n#endif\n",
            [cpre.StructureDiagnosticCode.MISSING_CONDITION],
        ),
        (
            "#ifdef 23\n#endif\n",
            [cpre.StructureDiagnosticCode.MALFORMED_MACRO_DIRECTIVE],
        ),
        (
            "#if A\n#else bad\n#endif bad\n",
            [
                cpre.StructureDiagnosticCode.TRAILING_DIRECTIVE_TEXT,
                cpre.StructureDiagnosticCode.TRAILING_DIRECTIVE_TEXT,
            ],
        ),
    ],
)
def test_recoverable_structure_returns_ordered_diagnostics_without_dropping_directives(
    source, codes
):
    tree = cpre.parse_conditionals(source)

    assert [diagnostic.code for diagnostic in tree.diagnostics] == codes
    assert len(tree.directives) == source.count("#")
    assert all(diagnostic.message for diagnostic in tree.diagnostics)
    assert all(diagnostic.source_range.text(source) for diagnostic in tree.diagnostics)
    assert [diagnostic.source_range.start.offset for diagnostic in tree.diagnostics] == sorted(
        diagnostic.source_range.start.offset for diagnostic in tree.diagnostics
    )


def test_unterminated_nested_blocks_extend_to_eof_and_are_retained():
    source = "#if OUTER\nouter\n#ifdef INNER\ninner"
    tree = cpre.parse_conditionals(source)

    assert [diagnostic.code for diagnostic in tree.diagnostics] == [
        cpre.StructureDiagnosticCode.UNTERMINATED_CONDITIONAL,
        cpre.StructureDiagnosticCode.UNTERMINATED_CONDITIONAL,
    ]
    outer = tree.blocks[0]
    inner = outer.branches[0].children[0]
    assert outer.source_range.text(source) == source
    assert inner.source_range.end.offset == len(source)
    assert inner.branches[0].body_range.text(source) == "inner"


def test_boolean_structure_is_configuration_independent_and_preserves_precedence():
    source = "#if A || B && !(defined(C) || VERSION > 3)\n#endif\n"
    tree = cpre.parse_conditionals(source)

    assert tree.directives[0].condition == cpre.Disjunction(
        (
            cpre.Variable("A"),
            cpre.Conjunction(
                (
                    cpre.Variable("B"),
                    cpre.Negation(
                        cpre.Disjunction(
                            (
                                cpre.DefinedVariable("C"),
                                cpre.Predicate("VERSION > 3"),
                            )
                        )
                    ),
                )
            ),
        )
    )


@pytest.mark.parametrize("text", ["A)", "(A", "A &&", "()", "(A)(B)", "A, B || C", "!"])
def test_malformed_or_unsupported_expression_is_retained_as_opaque(text):
    tree = cpre.parse_conditionals(f"#if {text}\n#endif\n")
    assert tree.directives[0].condition == cpre.Predicate(text)


def test_deterministic_repeated_parse_and_empty_input():
    assert cpre.parse_conditionals("").blocks == []
    source = "#if Z\n#ifdef A\n#endif\n#else\n#else\n#endif\n"
    first = cpre.parse_conditionals(source)
    second = cpre.parse_conditionals(source)

    assert first.directives == second.directives
    assert first.diagnostics == second.diagnostics


def test_leading_splice_belongs_to_directive_source_range():
    source = "\\\n#if A\nbody\n\\\n#endif\n"
    tree = cpre.parse_conditionals(source)

    assert not tree.diagnostics
    assert tree.directives[0].source_range.text(source) == "\\\n#if A\n"
    assert tree.directives[1].source_range.text(source) == "\\\n#endif\n"
    assert tree.blocks[0].branches[0].body_range.text(source) == "body\n"


def test_api_misuse_raises_typed_python_errors():
    with pytest.raises(TypeError):
        cpre.parse_conditionals(None)
    with pytest.raises(TypeError):
        cpre.parse_conditionals("", filename=1)
