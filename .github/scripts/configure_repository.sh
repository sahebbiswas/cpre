#!/usr/bin/env bash
# Apply the repository settings CONTRIBUTING.md ("Repository settings") asks for:
#   * branch protection on main: required status checks (every CI job that ran on the
#     tip of main, plus "Version bumped"), branches up to date before merging. A new
#     protection also forbids force pushes and deletion; an existing one keeps all its
#     other rules and only has its required checks updated;
#   * Dependabot security updates and private vulnerability reporting.
#
# Needs the GitHub CLI signed in as a repository admin. It cannot run from CI, because
# the default workflow token cannot change these settings.
#
#   .github/scripts/configure_repository.sh            # apply
#   .github/scripts/configure_repository.sh --dry-run  # show what would be sent
set -euo pipefail

repo="${GITHUB_REPOSITORY:-sahebbiswas/cpre}"
branch="${BRANCH:-main}"
# Names in .github/workflows/ci.yml (tests/test_project_policy_files.py checks they match).
ci_workflow="CI"
version_job="Version bumped"
dry_run=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    # A mistyped flag must never fall through to changing the repository.
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
  esac
done

tip=$(gh api "repos/$repo/commits/$branch" --jq .sha)
# The jobs of the CI workflow on the tip of main: the release workflow also reports
# checks on release commits, and those must not be required of every pull request. Only a
# completed run counts: a queued or running one may not have listed all its jobs yet.
run=$(gh api "repos/$repo/actions/runs?head_sha=$tip&per_page=50" \
  --jq "[.workflow_runs[] | select(.name == \"$ci_workflow\" and .status == \"completed\")][0].id")
if [ -z "$run" ] || [ "$run" = null ]; then
  echo "error: no completed $ci_workflow workflow run found for $branch@$tip;" >&2
  echo "wait for CI on $branch to finish, then run this script again." >&2
  exit 1
fi
# --paginate: a run can have more than one page of jobs.
contexts=$(gh api --paginate "repos/$repo/actions/runs/$run/jobs?per_page=100" \
  --jq '.jobs[].name' | python3 -c 'import json, sys; print(json.dumps([l.rstrip("\n") for l in sys.stdin if l.strip()]))')
checks=$(python3 - "$contexts" "$version_job" <<'PY'
import json, sys

contexts = sorted(set(json.loads(sys.argv[1])) | {sys.argv[2]})
print(json.dumps({"strict": True, "contexts": contexts}))
PY
)

# If main is already protected, change only its required status checks: replacing the whole
# protection would drop review requirements, push restrictions and admin enforcement that
# were added since. Only a 404 means "not protected"; any other failure (network, auth, rate
# limit) stops the script instead of falling through to a full replace.
lookup_error=$(mktemp)
trap 'rm -f "$lookup_error"' EXIT
if existing=$(gh api "repos/$repo/branches/$branch/protection" 2> "$lookup_error"); then
  if ! echo "$existing" | python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("required_status_checks") else 1)'; then
    echo "error: $branch is protected but has no required status checks, and this script" >&2
    echo "will not rebuild an existing protection. Enable 'Require status checks to pass'" >&2
    echo "for $branch once under Settings -> Branches, then run this script again." >&2
    exit 1
  fi
  mode=update
  endpoint="repos/$repo/branches/$branch/protection/required_status_checks"
  method=PATCH
  payload=$checks
elif grep -q "HTTP 404" "$lookup_error"; then
  mode=create
  endpoint="repos/$repo/branches/$branch/protection"
  method=PUT
  # Administrators are not enforced, so the sole maintainer can still repair a broken CI;
  # turn on "Do not allow bypassing the above settings" if that is not wanted.
  payload=$(python3 - "$checks" <<'PY'
import json, sys

print(json.dumps({
    "required_status_checks": json.loads(sys.argv[1]),
    "enforce_admins": False,
    "required_pull_request_reviews": None,
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
}))
PY
)
else
  echo "error: could not read the protection of $branch:" >&2
  cat "$lookup_error" >&2
  exit 1
fi

echo "Branch protection for $repo@$branch ($mode: $method $endpoint):"
echo "$payload" | python3 -m json.tool
if $dry_run; then
  echo "Would also enable: dependency alerts, Dependabot security updates, private vulnerability reporting."
  echo "(dry run: nothing changed)"
  exit 0
fi

# The required checks come from the CI run on $tip; do not apply them if main has moved on.
if [ "$(gh api "repos/$repo/commits/$branch" --jq .sha)" != "$tip" ]; then
  echo "error: $branch moved while the script ran; run it again." >&2
  exit 1
fi

# Dependabot security updates need dependency alerts, so alerts come first. Every step
# runs even if an earlier one fails, so a refused setting does not leave the rest
# unapplied; the script exits non-zero at the end if any step failed.
failed=0
apply() {
  local description=$1; shift
  if "$@"; then echo "ok: $description"; else echo "FAILED: $description" >&2; failed=1; fi
}
apply "dependency alerts" gh api -X PUT "repos/$repo/vulnerability-alerts"
apply "Dependabot security updates" gh api -X PUT "repos/$repo/automated-security-fixes"
apply "private vulnerability reporting" gh api -X PUT "repos/$repo/private-vulnerability-reporting"
apply "branch protection on $branch ($mode)" \
  bash -c 'echo "$1" | gh api -X "$2" "$3" --input -' _ "$payload" "$method" "$endpoint"
if [ "$failed" = 0 ]; then echo "Done."; else echo "Some settings were not applied; see above." >&2; exit 1; fi
