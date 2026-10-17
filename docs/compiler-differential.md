# Compiler differential tests

`tests/test_compiler_differential.py` checks cpre's concrete preprocessing against real C preprocessors. Each case in [`tests/compiler_differential/cases`](../tests/compiler_differential/cases) is run through every reference compiler that is available and through `preprocess_source()` with the same configuration, and the results must agree.

The harness is test-only. It uses only the Python standard library and the compilers already installed on the machine, so it adds no cpre dependency. The compilers are a check on the cases, not the specification: where the C standard leaves behaviour to the implementation, cpre's documented contract decides, and the difference is recorded below.

## Running it

```bash
pytest tests/test_compiler_differential.py
```

The harness looks for `gcc` and `clang` on `PATH` and uses each one it finds that works, at most one per family. To use a specific compiler, set `CPRE_REFERENCE_CC` to its command, for example `CPRE_REFERENCE_CC="clang-18"`. If that command does not work, the tests fail instead of quietly using another one. When no compiler is found, the comparison tests are skipped with the reason `no reference C preprocessor found`. The other tests in the file, which check the corpus and the known-differences record, still run.

In CI the comparison runs on every runner: the Linux runners have GCC (and Clang), the macOS runners have Apple Clang (installed as both `gcc` and `clang`, so it runs once), and the Windows runners have MinGW GCC and Clang. A runner with neither compiler on `PATH` skips it.

## What is compared

Every reference run uses `-E -P -undef -nostdinc -x c -std=c11` (or the case's own `-std=`). That means preprocessing only, with no line markers, no target or vendor predefined macros and no system headers. The values of `__STDC__`, `__STDC_VERSION__` and `__STDC_HOSTED__` are read from the reference compiler for the same `-std=` and passed to cpre through `PreprocessingContext`. cpre runs with `unknown_names="undefined"`, the compiler's closed-world model. If a reference leaves one of those macros undefined (Clang targeting MSVC has no `__STDC__`), a case that uses it is skipped for that reference with the reason, because cpre never invents a standard macro's value.

Compiler output is read with its line endings normalized (Windows compilers write CRLF) before `#pragma` lines are found.

Both outputs are normalized the same way, and only in ways that cannot change meaning:

- Output is split into preprocessing tokens. Whitespace and comments between tokens are dropped, so spacing and line placement are not compared. String literals are compared exactly, so spacing produced by `#` is checked.
- `#pragma` lines in the compiler's output, including those `_Pragma` produces, are taken out of the token stream and compared, in order and with whitespace collapsed outside string and character literals, with the payloads cpre passes to its `pragma_handler`. The harness's handler consumes every pragma.

A case agrees with a reference when:

- the reference accepts it and cpre returns complete output with the same tokens and pragmas, or
- the reference rejects it (non-zero exit status), and cpre does not return complete output (it raises an error or returns an incomplete result) and reports the problem on the same physical line as the reference's first `file:line: error` diagnostic. The message text is not compared, because GCC and Clang word the same error differently.

A failing case reports the case file, what each side did and, when both produced output, a token-by-token diff.

## Case format

A case is a `.c` file in `tests/compiler_differential/cases`. Its name starts with the construct it tests (`macro_`, `if_`, `defined_`, `nested_`, `line_`, `pragma_`, …). The first line may give compiler options:

```c
// cpre-differential: -std=c2x -DFEATURE -DLEVEL=2 -DSCALE(x)=((x)*10) -UOLD -Iinclude
```

| Option | Compiler | cpre |
|---|---|---|
| `-DNAME`, `-DNAME=text`, `-DNAME(params)=text` | as given | `MacroDefinition` (`-DNAME` means `NAME` = `1`) |
| `-UNAME` | as given | `undefined` |
| `-Idir` | as given, relative to the cases directory | `SearchPathResolver` search path |
| `-std=` | as given | standard macro values read from the compiler |

Options apply in order, as for the compiler driver, so the last `-D` or `-U` for a name wins. Any other option is rejected, so a case cannot depend on something cpre is not told about. To add a case, add a file. If it differs from a compiler, fix cpre, or record the difference as described below.

Keep cases deterministic: no `__DATE__`, `__TIME__` or `__FILE__` in included headers, and nothing that depends on the target. Put a construct that differs into a case of its own, so recording it does not stop the rest of a file from being checked.

## Known differences

[`known_differences.json`](../tests/compiler_differential/known_differences.json) records each case where cpre is expected to differ, with:

- `issue`: the issue tracking it, or the one that decided the behaviour;
- `kind`: `bug`, `gap` (a missing feature) or `intentional`;
- `reference`: what the compiler does (`accepted` or `rejected`);
- `cpre`: exactly what cpre currently does, including the line it reports. For accepted output this includes the tokens.
- `compilers` (optional): the families it applies to, when only one differs.

A recorded case is still run, and it fails if the difference changes in any way, including disappearing. So when a fix lands, the test tells you to delete the entry, and a partial fix or a new divergence is noticed. Every entry must also be listed here.

| Case | Kind | Issue | Difference |
|---|---|---|---|
| `comment_across_line_directive.c` | intentional | #156 | Clang only: for a `#line` directive that a comment spreads over two physical lines, Clang gives the new number to the directive's second line, so `__LINE__` on the line after it is one higher. GCC and cpre apply the number to the line after the whole directive, as cpre does for a backslash-continued `#line`. |
| `if_character_constants.c` | intentional | #50 | cpre does not evaluate ordinary character constants such as `'A'` in `#if`, because their values depend on the execution character set. The result is incomplete instead. |
| `macro_stringify_indirect.c` | bug | #149 | Stringifying an argument passed in by another macro drops the spaces between its tokens. |
| `pragma_operator_indirect.c` | bug | #149 | The same bug in a `_Pragma` payload built through two macros. |
| `macro_va_opt_omitted.c` | gap | #151 | A C23 call that omits the variadic argument entirely is incomplete. |
