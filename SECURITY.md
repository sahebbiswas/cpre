# Security policy

## Supported versions

Security fixes go into the latest release of the current major version. Older
major versions are not patched; upgrade to the latest release.

## Reporting a vulnerability

Please report vulnerabilities privately, not in a public issue or pull request.
Use GitHub's private reporting: open the repository's **Security** tab and choose
**Report a vulnerability** (or go to
<https://github.com/sahebbiswas/cpre/security/advisories/new>).

Include the cpre version (`cpre --version`), the command or API call, and the
smallest input that shows the problem. cpre is maintained by one person, so the
response is best effort: expect an acknowledgement within about a week, and a fix
or a mitigation plan after the report is confirmed. Reporters are credited in the
release notes unless they ask not to be.

## What counts

cpre analyzes untrusted C and C++ source, so these are in scope:

- a crash, hang or unbounded memory or time use on crafted input that the documented
  resource limits (`--max-atoms`, `--max-bdd-nodes`, `--max-work`, expansion limits)
  should have stopped
- reading or writing a file outside what the command line, `-I`/`--iquote`
  directories and the `simplify-macros --rewrite` target list allow, for example
  through crafted `#include` operands
- `--rewrite` changing a file's meaning or writing to a path other than the one given
- anything that makes cpre execute code from its input

Not in scope: a wrong or incomplete analysis result without one of the above (open a
regular issue; cpre reports such cases as incomplete where it can), and the contents of
files you pass to the tool.

cpre has no runtime dependencies and never runs a compiler or a shell.
