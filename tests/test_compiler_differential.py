"""Differential conformance tests against a reference C preprocessor (issue #98).

Each case in ``compiler_differential/cases`` is preprocessed by every reference
compiler found (``gcc``, ``clang`` and ``CPRE_REFERENCE_CC``) and by
``preprocess_source`` with the same configuration. See
``docs/compiler-differential.md`` for the comparison rules, the case format and
how to record a known difference.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

import cpre

HERE = Path(__file__).parent / "compiler_differential"
CASES = HERE / "cases"
KNOWN_DIFFERENCES = json.loads((HERE / "known_differences.json").read_text(encoding="utf-8"))

# Flags every reference run uses: preprocess only, no line markers, no target or
# vendor predefined macros and no system headers, so only standard behaviour is compared.
BASE_FLAGS = ("-E", "-P", "-undef", "-nostdinc", "-x", "c")
DEFAULT_STD = "-std=c11"
STANDARD_MACROS = ("__STDC__", "__STDC_VERSION__", "__STDC_HOSTED__")
DIRECTIVE = "// cpre-differential:"
TIMEOUT = 60  # seconds for one compiler run, so a hung compiler names its case

_TOKEN = re.compile(
    r"""
      /\*.*?\*/ | //[^\n]*                                   # comments (dropped)
    | (?:u8|u|U|L)?"(?:\\.|[^"\\\n])*"                       # string literal
    | (?:u8|u|U|L)?'(?:\\.|[^'\\\n])*'                       # character constant
    | \.?[0-9](?:[eEpP][+-]|[A-Za-z0-9_.]|'[A-Za-z0-9_])*    # pp-number
    | [A-Za-z_][A-Za-z0-9_]*                                 # identifier
    | %:%:|\.\.\.|<<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^!=<>]=|\#\#|<:|:>|<%|%>|%:
    | \S                                                     # any other character
    """,
    re.VERBOSE | re.DOTALL,
)
_DEFINE = re.compile(r"-D(?P<name>[A-Za-z_]\w*)(?:\((?P<params>[^)]*)\))?(?:=(?P<value>.*))?")
_PRAGMA_LINE = re.compile(r"^[ \t]*#[ \t]*pragma\b(?P<payload>[^\r\n]*)$", re.MULTILINE)


def tokens(text: str) -> tuple[str, ...]:
    """Preprocessing tokens of ``text`` with whitespace and comments removed."""
    return tuple(token for token in _TOKEN.findall(text) if not token.startswith(("/*", "//")))


def first_error_line(stderr: str, filename: str) -> int | None:
    """The line of the first ``file:line[:column]: error`` diagnostic for ``filename``."""
    pattern = rf"^{re.escape(filename)}:(\d+)(?::\d+)?: (?:fatal )?error\b"
    match = re.search(pattern, stderr, re.MULTILINE)
    return int(match[1]) if match else None


_LITERAL_OR_SPACE = re.compile(
    r"""((?:u8|u|U|L)?"(?:\\.|[^"\\])*"|(?:u8|u|U|L)?'(?:\\.|[^'\\])*')|\s+"""
)


def _normalize_payload(payload: str) -> str:
    """Collapse whitespace in a pragma payload, except inside string and character literals."""
    return _LITERAL_OR_SPACE.sub(lambda m: m[1] or " ", payload).strip()


def parse_reference_output(output: str) -> Outcome:
    """Tokens and ``#pragma`` payloads of a reference compiler's ``-E -P`` output."""
    # Compilers on Windows write CRLF; only the line structure matters here.
    output = re.sub(r"\r\n?", "\n", output)
    pragmas = tuple(_normalize_payload(m["payload"]) for m in _PRAGMA_LINE.finditer(output))
    return Outcome("accepted", tokens(_PRAGMA_LINE.sub("", output)), pragmas)


@dataclass(frozen=True)
class Case:
    path: Path
    flags: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def source(self) -> str:
        return self.path.read_bytes().decode("utf-8")

    @property
    def std(self) -> str:
        return next((flag for flag in self.flags if flag.startswith("-std=")), DEFAULT_STD)


def _load_case(path: Path) -> Case:
    first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
    flags = shlex.split(first_line[len(DIRECTIVE) :]) if first_line.startswith(DIRECTIVE) else []
    for flag in flags:
        if not (_DEFINE.fullmatch(flag) or re.fullmatch(r"-U\w+|-I\S+|-std=\S+", flag)):
            raise ValueError(f"{path.name}: unsupported flag {flag!r}")
    return Case(path, tuple(flags))


ALL_CASES = tuple(_load_case(path) for path in sorted(CASES.glob("*.c")))


@dataclass(frozen=True)
class Reference:
    """One reference compiler: its command and family (``gcc`` or ``clang``)."""

    command: tuple[str, ...]
    family: str

    def run(self, case: Case) -> Outcome:
        try:
            completed = subprocess.run(
                [*self.command, *BASE_FLAGS, case.std, *_compiler_flags(case), case.name],
                cwd=CASES,
                capture_output=True,
                check=False,
                timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"{self.family} did not finish {case.name} within {TIMEOUT} seconds")
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace")
            return Outcome(
                "rejected", detail=stderr.strip(), line=first_error_line(stderr, case.name)
            )
        return parse_reference_output(completed.stdout.decode("utf-8"))

    @cache  # noqa: B019 - references live for the whole session
    def standard_macros(self, std: str) -> dict[str, str]:
        """The standard predefined macro values the reference uses for ``std``."""
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory, "probe.c")
            probe.write_text("\n".join(STANDARD_MACROS) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [*self.command, *BASE_FLAGS, std, str(probe)],
                capture_output=True,
                check=True,
                timeout=TIMEOUT,
            )
        values = completed.stdout.decode("utf-8").split()
        return {name: value for name, value in zip(STANDARD_MACROS, values) if value != name}


def _compiler_flags(case: Case) -> list[str]:
    return [flag for flag in case.flags if not flag.startswith("-std=")]


def _family(command: tuple[str, ...]) -> str | None:
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory, "probe.c")
        probe.write_text("", encoding="utf-8")
        try:
            completed = subprocess.run(
                [*command, "-dM", "-E", "-x", "c", str(probe)],
                capture_output=True,
                check=False,
                timeout=TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    if completed.returncode != 0:
        return None
    macros = completed.stdout.decode("utf-8", "replace")
    if "__clang__" in macros:
        return "clang"
    if "__GNUC__" in macros:
        return "gcc"
    return None


def _discover_references() -> tuple[list[Reference], list[str]]:
    candidates: list[tuple[str, ...]] = []
    override = os.environ.get("CPRE_REFERENCE_CC")
    if override:
        candidates.append(tuple(shlex.split(override)))
    candidates += [(name,) for name in ("gcc", "clang") if shutil.which(name)]
    references: dict[str, Reference] = {}
    skipped = []
    for command in candidates:
        family = _family(command)
        if family is None:
            skipped.append(f"{' '.join(command)}: not a working GCC- or Clang-compatible compiler")
        elif family not in references:
            references[family] = Reference(command, family)
    if override and not any(
        ref.command == tuple(shlex.split(override)) for ref in references.values()
    ):
        raise RuntimeError(f"CPRE_REFERENCE_CC={override!r} is not usable: {skipped}")
    return list(references.values()), skipped


REFERENCES, _UNUSABLE = _discover_references()
_NO_REFERENCE = (
    "no reference C preprocessor found (looked for gcc and clang on PATH; "
    "set CPRE_REFERENCE_CC to choose one)"
)


@dataclass(frozen=True)
class Outcome:
    """What one preprocessor did with a case.

    ``kind`` is ``accepted`` (with normalized tokens and pragma payloads),
    ``rejected`` (an error) or ``incomplete`` (cpre declined to certify output).
    """

    kind: str
    tokens: tuple[str, ...] = ()
    pragmas: tuple[str, ...] = ()
    detail: str = ""
    line: int | None = None  # physical line of the first error, when rejected or incomplete

    def describe(self) -> str:
        if self.kind == "accepted":
            return "accepted"
        where = f" at line {self.line}" if self.line is not None else ""
        return f"{self.kind}{where}: {self.detail}"

    def signature(self) -> str:
        """``describe()``, plus the output itself for an accepted run."""
        if self.kind != "accepted":
            return self.describe()
        return " ".join(["accepted:", *self.tokens, *(f"#pragma {p}" for p in self.pragmas)])


def _configuration(case: Case) -> cpre.MacroConfiguration:
    # Options apply in command-line order, as for the compiler driver: the last -D or -U
    # for a name wins (None marks a name left undefined).
    final: dict[str, cpre.MacroDefinition | None] = {}
    for flag in case.flags:
        define = _DEFINE.fullmatch(flag)
        if define:
            # -DNAME means NAME=1, as for the compiler driver.
            value = "1" if define["value"] is None else define["value"]
            parameters = None
            variadic = False
            if define["params"] is not None:
                names = [name.strip() for name in define["params"].split(",") if name.strip()]
                variadic = bool(names) and names[-1] == "..."
                parameters = tuple(names[:-1] if variadic else names)
            final[define["name"]] = cpre.MacroDefinition(
                define["name"], value, parameters, variadic
            )
        elif flag.startswith("-U"):
            final[flag[2:]] = None
    definitions = [definition for definition in final.values() if definition is not None]
    undefined = [name for name, definition in final.items() if definition is None]
    return cpre.MacroConfiguration(
        definitions=definitions, undefined=undefined, unknown_names="undefined"
    )


def run_cpre(case: Case, reference: Reference) -> Outcome:
    pragmas: list[str] = []

    def consume(pragma: cpre.Pragma) -> cpre.PragmaDisposition:
        pragmas.append(_normalize_payload(pragma.payload))
        return cpre.PragmaDisposition.CONSUME

    search = [CASES / flag[2:] for flag in case.flags if flag.startswith("-I")]
    try:
        result = cpre.preprocess_source(
            case.source,
            filename=case.name,
            configuration=_configuration(case),
            context=cpre.PreprocessingContext(standard_macros=reference.standard_macros(case.std)),
            pragma_handler=consume,
            include_resolver=cpre.includes.SearchPathResolver(search, quote_paths=[CASES]),
        )
    except cpre.CpreError as error:
        location = getattr(error, "location", None)
        line = location.line if location is not None else None
        return Outcome("rejected", detail=f"{type(error).__name__}: {error}", line=line)
    if not result.complete:
        codes = ", ".join(sorted({str(item.code.value) for item in result.incomplete}))
        lines = [item.location.line for item in result.incomplete if item.location is not None]
        return Outcome("incomplete", detail=codes, line=min(lines, default=None))
    assert result.source is not None
    return Outcome("accepted", tokens(result.source), tuple(pragmas))


def agrees(reference: Outcome, ours: Outcome) -> bool:
    """The comparison contract (see docs/compiler-differential.md)."""
    if reference.kind == "accepted":
        return ours.kind == "accepted" and (ours.tokens, ours.pragmas) == (
            reference.tokens,
            reference.pragmas,
        )
    # The reference rejects the input: cpre must not claim complete output, and must
    # report the problem on the same physical line. Message wording is not compared.
    # A reference error we cannot place (say, in an included header) is no evidence that
    # cpre agrees, so it does not count as a match.
    return ours.kind != "accepted" and reference.line is not None and ours.line == reference.line


def _report(case: Case, reference: Reference, theirs: Outcome, ours: Outcome) -> str:
    lines = [
        f"{case.path.relative_to(HERE.parent)} differs from {reference.family}",
        f"  {reference.family}: {theirs.describe()}",
        f"  cpre: {ours.describe()}",
    ]
    if theirs.kind == ours.kind == "accepted":
        lines += difflib.unified_diff(
            [*theirs.tokens, *(f"#pragma {p}" for p in theirs.pragmas)],
            [*ours.tokens, *(f"#pragma {p}" for p in ours.pragmas)],
            fromfile=reference.family,
            tofile="cpre",
            lineterm="",
        )
    return "\n".join(lines)


def _known(case: Case, reference: Reference) -> dict[str, str] | None:
    entry = KNOWN_DIFFERENCES.get(case.name)
    if entry is None or reference.family not in entry.get("compilers", ["gcc", "clang"]):
        return None
    return entry


PAIRS = [
    pytest.param(case, reference, id=f"{case.name}-{reference.family}")
    for case in ALL_CASES
    for reference in REFERENCES
]


@pytest.mark.skipif(not REFERENCES, reason=_NO_REFERENCE)
@pytest.mark.parametrize(("case", "reference"), PAIRS or [pytest.param(None, None, id="none")])
def test_cpre_matches_reference(case: Case, reference: Reference) -> None:
    # cpre never invents a standard macro's value, so a case using one the reference
    # leaves undefined (clang targeting MSVC has no __STDC__) cannot be compared.
    standard = reference.standard_macros(case.std)
    missing = [name for name in STANDARD_MACROS if name not in standard and name in case.source]
    if missing:
        pytest.skip(f"{reference.family} does not define {', '.join(missing)} on this target")
    theirs, ours = reference.run(case), run_cpre(case, reference)
    known = _known(case, reference)

    if known is None:
        assert agrees(theirs, ours), _report(case, reference, theirs, ours)
        return

    # A recorded difference must still exist exactly as documented, so that a fix
    # (or a new divergence) is noticed and the record is updated.
    assert not agrees(theirs, ours), (
        f"{case.name} now agrees with {reference.family}; "
        "remove its entry from known_differences.json"
    )
    assert theirs.kind == known["reference"], _report(case, reference, theirs, ours)
    assert ours.signature() == known["cpre"], _report(case, reference, theirs, ours)


def test_corpus_covers_the_required_constructs() -> None:
    assert len(ALL_CASES) >= 25
    prefixes = {case.name.split("_", 1)[0] for case in ALL_CASES}
    assert {"macro", "if", "defined", "nested", "line", "pragma"} <= prefixes


def test_known_differences_are_documented() -> None:
    names = {case.name for case in ALL_CASES}
    for name, entry in KNOWN_DIFFERENCES.items():
        assert name in names, f"known difference for missing case {name}"
        assert entry["reason"].strip() and re.fullmatch(r"#\d+", entry["issue"])
        assert entry["reference"] in {"accepted", "rejected"}
        assert entry["kind"] in {"bug", "gap", "intentional"}
        assert set(entry.get("compilers", ["gcc", "clang"])) <= {"gcc", "clang"}
    documented = (HERE.parents[1] / "docs" / "compiler-differential.md").read_text("utf-8")
    for name in KNOWN_DIFFERENCES:
        assert f"`{name}`" in documented, f"{name} is not listed in docs/compiler-differential.md"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a  +\tb", ("a", "+", "b")),
        ("x/* c */y // tail\nz", ("x", "y", "z")),
        ("\"a  b\" L'c' 0x1fUL 1.5e+3 .5", ('"a  b"', "L'c'", "0x1fUL", "1.5e+3", ".5")),
        ("a<<=b...c->d##e", ("a", "<<=", "b", "...", "c", "->", "d", "##", "e")),
    ],
)
def test_tokens_normalize_only_whitespace_and_comments(text, expected):
    assert tokens(text) == expected


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_reference_output_extracts_pragma_lines(newline):
    output = newline.join(["int a;", "#pragma omp   parallel", "  # pragma pack(1)", "int b;", ""])

    outcome = parse_reference_output(output)

    assert outcome.tokens == ("int", "a", ";", "int", "b", ";")
    assert outcome.pragmas == ("omp parallel", "pack(1)")


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("x.c:2:2: error: #endif without #if\n", 2),
        ("x.c:1: error: unterminated #if\n", 1),
        ("x.c:3:1: warning: unused\nx.c:7:5: fatal error: oops\n", 7),
        ("other.h:4:1: error: elsewhere\n", None),
    ],
)
def test_first_error_line(stderr, expected):
    assert first_error_line(stderr, "x.c") == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("omp   parallel\tfor ", "omp parallel for"),
        ('message("a  b")', 'message("a  b")'),
        ('message(  \'x\'  ,  L"a \\"  b" )', 'message( \'x\' , L"a \\"  b" )'),
    ],
)
def test_pragma_payloads_keep_whitespace_inside_literals(payload, expected):
    assert _normalize_payload(payload) == expected


def test_pragma_payloads_with_different_string_spacing_differ():
    assert _normalize_payload('message("a  b")') != _normalize_payload('message("a b")')


@pytest.mark.parametrize(
    ("flags", "source", "expected"),
    [
        (("-DX=1", "-UX"), "#ifdef X\nyes\n#endif\nX\n", ("X",)),
        (("-UX", "-DX=2"), "X\n", ("2",)),
        (("-DX=1", "-DX=2"), "X\n", ("2",)),
        (("-DX", "-UY", "-DY=3"), "X Y\n", ("1", "3")),
    ],
)
def test_configuration_applies_flags_in_order(flags, source, expected):
    configuration = _configuration(Case(CASES / "x.c", flags))
    result = cpre.preprocess_source(source, configuration=configuration)
    assert tokens(result.source) == expected


def test_configuration_maps_compiler_flags():
    case = Case(CASES / "x.c", ("-DA", "-DB=2", "-DF(x)=x", "-UC", "-std=c2x"))
    configuration = _configuration(case)
    assert case.std == "-std=c2x"
    assert _compiler_flags(case) == ["-DA", "-DB=2", "-DF(x)=x", "-UC"]
    result = cpre.preprocess_source("A B F(3) C\n", configuration=configuration)
    assert tokens(result.source) == ("1", "2", "3", "C")
