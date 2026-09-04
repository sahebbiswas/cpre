from cpre.cli import main


def test_cli_renders_structured_parse_error_and_returns_two(tmp_path, capsys):
    source = tmp_path / "broken.c"
    source.write_text("#if A &&\n#endif\n", encoding="utf-8")

    result = main([str(source)])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert str(source) in captured.err
    assert "expected an operand" in captured.err
    assert "line 1, column 7" in captured.err
