"""The changelog and security policy exist, are linked, and follow their stated format."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _package_version() -> str:
    """``__version__`` read from the source, so the test does not import the package."""
    init = (ROOT / "cpre" / "__init__.py").read_text(encoding="utf-8")
    # A release dry run sets a pre-release such as 0.42.3rc1 (CONTRIBUTING.md, "Releasing").
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)(?:rc\d+)?"$', init, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_changelog_has_an_unreleased_section_above_the_releases():
    headings = re.findall(r"^## \[?([^\]\n]+)\]?", CHANGELOG, re.MULTILINE)
    assert headings[0] == "Unreleased"
    versions = [h.split()[0] for h in headings if re.match(r"\d+\.\d+\.\d+", h)]
    assert versions == sorted(versions, key=lambda v: tuple(map(int, v.split("."))), reverse=True)


def test_the_running_version_is_not_older_than_the_newest_changelog_release():
    newest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG, re.MULTILINE)
    assert newest is not None
    as_tuple = lambda v: tuple(int(part) for part in v.split("."))  # noqa: E731
    assert as_tuple(_package_version()) >= as_tuple(newest.group(1))


def test_every_release_heading_has_a_link_target():
    for version in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG, re.MULTILINE):
        assert re.search(rf"^\[{re.escape(version)}\]: https://", CHANGELOG, re.MULTILINE)
    assert re.search(r"^\[Unreleased\]: https://", CHANGELOG, re.MULTILINE)


def test_security_policy_names_a_private_reporting_channel():
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security/advisories/new" in policy
    assert "Supported versions" in policy


def test_readme_contributing_and_json_contract_point_to_the_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "CHANGELOG.md" in readme and "SECURITY.md" in readme
    assert "CHANGELOG.md" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "../CHANGELOG.md" in (ROOT / "docs/json-contract.md").read_text(encoding="utf-8")


def test_dependabot_covers_actions_and_development_dependencies():
    config = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert re.search(r"^version: 2$", config, re.MULTILINE)
    assert re.search(r"^updates:$", config, re.MULTILINE)
    ecosystems = re.findall(r"package-ecosystem: (\S+)", config)
    assert sorted(ecosystems) == ["github-actions", "pip"]
    assert config.count("interval: weekly") == len(ecosystems)


def test_repository_settings_script_is_documented_and_valid_shell():
    script = ROOT / ".github/scripts/configure_repository.sh"
    assert script.is_file(), f"{script} not found"
    assert ".github/scripts/configure_repository.sh" in (ROOT / "CONTRIBUTING.md").read_text(
        encoding="utf-8"
    )
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available to syntax-check the script")
    subprocess.run([bash, "-n", str(script)], check=True, capture_output=True, timeout=30)


def test_the_version_reader_accepts_a_release_candidate(monkeypatch, tmp_path):
    """The release dry run sets ``0.42.3rc1``; the policy tests must still run then."""
    fake = tmp_path / "cpre"
    fake.mkdir()
    (fake / "__init__.py").write_text('__version__ = "0.42.3rc1"\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert _package_version() == "0.42.3"


def test_repository_settings_script_rejects_unknown_arguments():
    """A mistyped --dry-run must fail before anything can be applied."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    script = ROOT / ".github/scripts/configure_repository.sh"
    for argument in ("--dryrun", "-n", "apply"):
        done = subprocess.run(
            [bash, str(script), argument], capture_output=True, text=True, timeout=30
        )
        assert done.returncode == 2, argument
        assert "usage:" in done.stderr


def test_repository_settings_script_enables_alerts_before_security_updates():
    text = (ROOT / ".github/scripts/configure_repository.sh").read_text(encoding="utf-8")
    assert text.index("vulnerability-alerts") < text.index("automated-security-fixes")


def test_security_policy_has_a_route_when_private_reporting_is_off():
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Security contact request" in policy


def test_repository_settings_script_keeps_existing_branch_protection_rules():
    text = (ROOT / ".github/scripts/configure_repository.sh").read_text(encoding="utf-8")
    # An existing protection is updated through the required-status-checks endpoint only.
    assert "protection/required_status_checks" in text
    assert "method=PATCH" in text
    assert text.count("-X PUT") >= 1 and "method=PUT" in text  # created only when absent


def test_repository_settings_script_names_match_the_ci_workflow():
    """The workflow and job names the script requires are the ones ci.yml defines."""
    script = (ROOT / ".github/scripts/configure_repository.sh").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    workflow = re.search(r'^ci_workflow="([^"]+)"$', script, re.MULTILINE)
    job = re.search(r'^version_job="([^"]+)"$', script, re.MULTILINE)
    assert workflow is not None and job is not None
    assert re.search(rf"^name: {re.escape(workflow.group(1))}$", ci, re.MULTILINE)
    assert re.search(rf"^    name: {re.escape(job.group(1))}$", ci, re.MULTILINE)


def test_repository_settings_script_stops_instead_of_guessing_about_protection():
    text = (ROOT / ".github/scripts/configure_repository.sh").read_text(encoding="utf-8")
    assert 'grep -q "HTTP 404"' in text  # only a 404 means "not protected"
    assert "could not read the protection" in text  # any other failure stops the script
    assert "has no required status checks" in text  # never rebuild an existing protection
    assert "--paginate" in text  # every CI job is required, not just the first page
    assert "moved while the script ran" in text  # required checks match the tip they came from


_GH_STUB = r"""#!/usr/bin/env bash
# Stand-in for the GitHub CLI: logs every call (and any JSON piped in) to $GH_LOG.
args="$*"
echo "CALL $args" >> "$GH_LOG"
case "$args" in
  *"--input -"*) echo "BODY $(cat)" >> "$GH_LOG"; exit 0 ;;
  *"/protection/required_status_checks"*|*vulnerability*|*automated-security-fixes*) exit 0 ;;
  *"/protection"*)
    case "$GH_PROTECTION" in
      existing) echo '{"required_status_checks": {"contexts": ["old"]}, "enforce_admins": {"enabled": true}}' ;;
      unchecked) echo '{"enforce_admins": {"enabled": true}}' ;;
      missing) echo "gh: Branch not protected (HTTP 404)" >&2; exit 1 ;;
      *) echo "gh: Bad credentials (HTTP 401)" >&2; exit 1 ;;
    esac ;;
  *"/commits/"*) echo "${GH_TIP:-abc123}" ;;
  *"/actions/runs?"*) echo "${GH_RUN:-42}" ;;
  *"/jobs?"*) printf 'test (3.10)\nlint\n' ;;
esac
"""


def _run_settings_script(tmp_path, protection, *args, run="42"):
    import os

    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("needs a POSIX bash to run the stubbed gh")
    stub = tmp_path / "gh"
    stub.write_text(_GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_PROTECTION": protection,
        "GH_RUN": run,
        # The script reads these; a fork's CI or a developer's shell must not redirect it.
        "GITHUB_REPOSITORY": "sahebbiswas/cpre",
        "BRANCH": "main",
    }
    script = ROOT / ".github/scripts/configure_repository.sh"
    done = subprocess.run(
        [bash, str(script), *args], capture_output=True, text=True, env=env, timeout=60
    )
    return done, (log.read_text(encoding="utf-8") if log.exists() else "")


def test_settings_script_updates_only_the_required_checks_of_an_existing_protection(tmp_path):
    done, log = _run_settings_script(tmp_path, "existing")
    assert done.returncode == 0, done.stderr
    assert "-X PATCH repos/sahebbiswas/cpre/branches/main/protection/required_status_checks" in log
    assert "-X PUT repos/sahebbiswas/cpre/branches/main/protection " not in log + " "
    assert '"Version bumped"' in log and '"test (3.10)"' in log and '"lint"' in log


def test_settings_script_creates_protection_only_after_a_404(tmp_path):
    done, log = _run_settings_script(tmp_path, "missing")
    assert done.returncode == 0, done.stderr
    assert "-X PUT repos/sahebbiswas/cpre/branches/main/protection --input -" in log
    assert '"allow_force_pushes": false' in log and '"allow_deletions": false' in log


def test_settings_script_stops_on_any_other_lookup_failure(tmp_path):
    done, log = _run_settings_script(tmp_path, "error")
    assert done.returncode == 1
    assert "could not read the protection" in done.stderr
    assert "-X PUT" not in log and "-X PATCH" not in log


def test_settings_script_does_not_rebuild_a_protection_without_status_checks(tmp_path):
    done, log = _run_settings_script(tmp_path, "unchecked")
    assert done.returncode == 1
    assert "has no required status checks" in done.stderr
    assert "-X PUT" not in log and "-X PATCH" not in log


def test_settings_script_needs_a_completed_ci_run(tmp_path):
    done, log = _run_settings_script(tmp_path, "existing", run="null")
    assert done.returncode == 1
    assert "no completed CI workflow run" in done.stderr
    assert "-X PUT" not in log and "-X PATCH" not in log


def test_settings_script_dry_run_previews_everything_and_changes_nothing(tmp_path):
    done, log = _run_settings_script(tmp_path, "existing", "--dry-run")
    assert done.returncode == 0, done.stderr
    for setting in ("dependency alerts", "Dependabot security updates", "private vulnerability"):
        assert setting in done.stdout
    assert "-X " not in log
