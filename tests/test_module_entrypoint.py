import subprocess
import sys


def test_package_module_entrypoint_runs_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "cpre", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
