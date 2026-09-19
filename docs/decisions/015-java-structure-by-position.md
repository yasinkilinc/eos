# ADR-015: Read Java by Position, Not by Pattern

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

Java support was 204 lines of regular expressions. Measured against one
2,397-file Spring tree, it produced 2,633 types and 5,943 methods, **none of
which recorded the class that declared them**, plus zero fields, zero
`extends` and zero `implements`. Against that: 243 package-private type
declarations, 353 interface files, roughly 7,516 method-shaped declarations
and 2,630 `private final` fields.

Every defect was a quoting or nesting failure, which no regex can express:

- A method was recognised by requiring `public|protected|private` **and** a
  trailing `{`, so package-private methods, interface methods and generic
  declarations were all invisible.
- `@RequestMapping(value = "/x", produces = MediaType…)` did not match, because
  the pattern's character class cannot cross a quote. 17 of that tree's 22
  `@RequestMapping` uses take that form, and 82 of 108 emitted endpoint paths
  came out empty.
- `@Service` inside an ArchUnit rule's string literal set a file's role. That
  false positive was fenced off with a token-boundary guard in the plugin and a
  second guard in the classifier rather than fixed.
- Seven files that are entirely commented out were reported as declaring
  interfaces that do not exist in the compiled code.
- Newline offsets were stored on the plugin instance, and `PluginRegistry`
  holds one `JavaPlugin` for the process.

The consequence reached the top-level answer. `impact_analysis` returned zero
dependents for every file in a service's overlay, because **142 of its 143
test-to-subject relationships are same-package and all 142 carry no import at
all** — Java does not require one, so the relationship leaves no trace in the
import statements an import-only graph is built from.

## Decision

Parse in two passes, both stdlib (ADR-010).

**A masking lexer** (`core/plugins/java/lexer.py`) replaces string literals,
character literals and both comment forms with filler of the same length,
preserving every offset and newline, and keeps the real literal values in a
table keyed by where they started. A whole class of defect disappears at the
source rather than being guarded against: a declaration inside a comment is no
longer a declaration, a `}` in a Javadoc no longer closes a class, and an
annotation argument list can be read by matching parentheses.

**A scope-stack scanner** (`core/plugins/java/structure.py`) walks the masked
text with a brace-depth counter and a stack of open declarations. Nesting and
ownership fall out of the stack. A member is recognised by where it sits and
how it ends — `(...)` then `{` or `;` is a method, a name then `;` or `=` at
type scope is a field — so no modifier is required and abstract methods count.

It is not a Java grammar and does not try to be. It resolves what is decidable
from one file plus its imports, and records what it could not.

**`LanguagePlugin` is unchanged.** No new abstract method: `detect` and
`parse_file` still suffice, because everything new rides on a richer
`FileSemantic` (`package`, `annotations`, `fields`, `type_refs`, `calls`, and
`Symbol.signature`/`returns`/`modifiers`/`annotations`/`end_line`). Other
plugins leave the new lists empty, and empty means "this detector did not
look", which coverage records.

**Edges are declaration-site only** — `extends`, `implements`, a field's type,
a `new`, and a call whose receiver resolves to a declared field type. Parameter
and return types are deliberately excluded: they are the long tail with the
least "changing X breaks Y" signal, and including them multiplies edge count
without improving the answer. Measured growth on one service with its parent:
64,479 → 83,209 edges, and full scan 7.17 s → 13.78 s (1.92x, inside the 2x
gate set before the work started).

**Type resolution tries explicit import, then the file's own package.** The
second rule is the one an import-only graph cannot express.

**`impact` follows all of them by default.** Restricting it to `import` was
what made the measured answer empty.

## Consequences

Measured on the same 2,397-file tree:

| | before | after |
|---|---|---|
| types | 2,633 | 2,610 |
| methods | 5,943 | 8,262 |
| methods attached to their class | 0 | 8,262 |
| abstract/interface methods | 0 | 1,810 |
| fields | 0 | 8,852 |
| `implements` / `extends` | 0 / 0 | 946 / 556 |
| endpoint paths, non-empty / empty | 26 / 82 | 101 / 0 |

The 23 fewer types are declarations inside comments and string literals; seven
files that are entirely commented out now correctly declare nothing.

Dependent recall against a ground-truth oracle, across four services:
**339 of 345 pairs, up from 0**. The oracle is built by a different rule than
the thing it measures — a same-package `<Name>Test` whose *masked* source names
`<Name>` as a token — and `eos bench` prints that rule beside the number, per
ADR-011. On a project with no Java the probe reports absent, never 0%.

- `_CACHE_FORMAT` 3 → 4. No migration: a format mismatch drops the cache.
- The classifier's test-tree guard and the plugin's token-boundary guard are
  now belt-and-braces rather than load-bearing; both are kept, because they
  cost nothing and the tests that pin them still describe real regressions.
- The remaining known gap is a subject referenced only as a local variable's
  declared type. One of 345 pairs. Capturing local declarations would close it
  and add volume; it was not worth the trade at this ratio.
