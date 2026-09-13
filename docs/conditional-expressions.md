# Concrete `#if` / `#elif` expression support

Concrete preprocessing evaluates reachable `#if` and `#elif` expressions only after preprocessing-token macro expansion. The evaluator is intentionally deterministic: it models the standard integer-expression surface without pretending to know an arbitrary compiler ABI, execution character set, or extension set.

## Evaluation order

For concrete preprocessing, cpre applies the condition pipeline in this order:

1. tokenize the directive expression;
2. resolve `defined NAME` and `defined(NAME)` against the current macro environment **without macro-expanding the operand**;
3. macro-expand the remaining preprocessing tokens;
4. reject reachable unmodeled predefined macros;
5. evaluate the resulting standard integer expression within the configured analysis budget.

Open-world configurations keep unresolved macro names unknown. Closed-world `UnknownNamePolicy.UNDEFINED` continues to provide the standard concrete behavior where otherwise-unmentioned identifiers are treated as undefined/zero.

## Supported integer-expression surface

The concrete evaluator uses a deterministic 64-bit `intmax_t`/`uintmax_t` model and supports:

- decimal, octal, hexadecimal, and binary integer constants accepted by cpre, including supported integer suffixes and digit separators;
- parentheses;
- unary `+`, `-`, `!`, and `~`;
- `*`, `/`, `%`, `+`, `-`;
- `<<`, `>>`;
- `<`, `<=`, `>`, `>=`, `==`, `!=`;
- bitwise `&`, `^`, `|`;
- logical `&&`, `||` with short-circuit evaluation;
- conditional `?:`, including right-associative nesting, standard precedence, short-circuit evaluation of the unselected arm, and signed/unsigned common-type conversion of the two result arms;
- object-like and function-like macro expansion before evaluation;
- `defined` in both standard spellings.

Assignment, increment/decrement, function-call syntax after macro expansion, comma expressions, compiler builtins, and statement-expression extensions remain outside the concrete condition model and produce a structured incomplete result when reachable.

## Character constants

Character constants are preprocessing constant-expression operands, but the standard permits their value in `#if` evaluation to depend on an implementation-selected character set. cpre therefore evaluates only facts that do not require guessing that implementation choice.

Supported deterministic cases include:

- ordinary basic single-character constants in Boolean context, such as `'a'` being nonzero;
- equality/inequality between deterministic ordinary character identities, such as `'a' == 'a'` and `'a' != 'b'`;
- equality/inequality with zero when nonzeroness is standard-stable;
- octal and hexadecimal numeric escapes whose values are in `0..0x7f`, such as `'\101' == 65`, `'\x41' == 65`, and `'\0' == 0`;
- standard simple escape identities, such as `'\n' == '\n'` and `'\n' != '\0'`;
- character constants produced by macro expansion.

cpre deliberately does **not** assume ASCII or a compiler's plain-`char` policy. For example, `'a' == 97` is incomplete rather than guessed. Numeric ordinary-character escapes above `0x7f` are also incomplete when their value can depend on target character width or signedness.

The following remain structured incomplete when their implementation-defined value or type is required:

- multicharacter constants such as `'ab'`;
- prefixed character constants (`L`, `u`, `U`, or `u8`) because cpre currently has no source-language-version/encoding configuration contract that would make their interpretation deterministic across supported C/C++ inputs;
- non-basic ordinary character values whose execution-character representation is not known;
- malformed or non-standard escape sequences.

An implementation-defined literal in a syntactically valid unselected expression arm may still be harmless when only its already-known type is needed. cpre never evaluates dead runtime operations such as division by zero merely to parse the unselected arm of `?:`, `&&`, or `||`.

## Deterministic integer boundary

All signed values are modeled within `[-2^63, 2^63 - 1]`; unsigned values use modulo `2^64`. Standard signed/unsigned conversions are applied within that model. Expressions requiring a larger signed range, invalid shift counts, selected division/modulo by zero, or other values outside the deterministic model return structured `unsupported_condition_expression` diagnostics rather than inheriting host-Python or host-compiler behavior.

The evaluator also consumes the normal `AnalysisOptions.max_work` budget and enforces an explicit nesting bound, so deeply nested standard expressions fail atomically with a structured incomplete result rather than exhausting Python recursion.
