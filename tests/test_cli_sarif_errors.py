import json

from cpre.cli import main


def test_sarif_still_emits_valid_log_when_one_file_has_parse_error(tmp_path, capsys):
    good = tmp_path / "good.c"
    bad = tmp_path / "bad.c"
    good.write_text("#if A && A\n#endif\n", encoding="utf-8")
    bad.write_text("#if B &&\n#endif\n", encoding="utf-8")

    exit_code = main([str(good), str(bad), "--sarif"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert str(bad) in captured.err
    document = json.loads(captured.out)
    assert document["version"] == "2.1.0"
    assert len(document["runs"][0]["results"]) == 1
