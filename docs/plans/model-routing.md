# Plan: adaptive model and effort routing — target 1.2.0

**Status of this plan: OPEN.** Engine at the time of writing: 1.1.2. Nothing
has landed. The status table in §11 is updated in the same commit as the work
it describes; "where were we" is answered by running the checks in §10.

This file is written for the session that implements it. It assumes no
conversation history: every integration point is named by file and symbol as
verified on 2026-09-25 against 1.1.2, and every milestone ends in an
executable check. Read it whole before the first edit. Where this plan and
the code disagree, the code has moved — re-verify the anchor, then update
this plan in the same commit.

---

## 0. What is being built, in one paragraph

Before an agent starts a task, EOS decides **which model and how much
reasoning effort the task deserves**, prints the decision where the agent
already looks (`eos brief --task`, a new `eos route`, an MCP tool), records it
beside the run so the outcome can later be read against it, and never picks
the strongest model by default. The decision is deterministic and explainable:
a task type from a fixed taxonomy, a complexity level from a transparent
weighted score, the **cheapest registered model that meets the level's
minimum capability**, and an effort **clamped to what that model supports**.
The inspiration is the three-tier router in the Ruflo agent harness; what is
borrowed is the shape (classify → score → pick tier → advise the harness), not
the bandit, which ADR-018 rules out for now (§2).

## 1. The one fact that shapes everything

**EOS does not call a language model.** Nothing under `core/` imports a
provider SDK, reads an API key, or sends a prompt; the only `subprocess` uses
are git and the hook templates. Execution belongs to the harness (a coding
agent such as Claude Code, or another that reads `AGENTS.md`), which runs EOS
as a CLI, a hook, or an MCP server.

Consequences, each of which a later section builds on:

- The "provider adapter" of the requirement is a **harness adapter**: a
  normalised `Decision` is rendered into what the harness can act on (a
  subagent `model` argument, an advisory line in a hook's output). EOS never
  emits provider API configuration.
- There is no execution pipeline to hook into. The lifecycle that exists is
  the execution ledger: `eos run start` … events … `eos run finish --outcome`
  (ADR-022). The decision is bound to that.
- "Do not reclassify during the same task" means: one decision per open
  execution, remembered as an event on it, returned again on the next call.
- The task text is a query and is never stored (ADR-019). The trace keeps a
  hash of it, the task type and the decision — never the text.

## 2. Constraints this plan honours

- **ADR-001 / 010 — stdlib only under `core/`.** No new dependency. TOML is
  read with `tomllib` (Python ≥ 3.11, already required).
- **`core/lib/updater.py:30-38` copies only `*.py` into `.eos/runtime/`.**
  A data file under `core/` (a `.toml` registry) would not reach installed
  runtimes. **The default registry is a Python constant**
  (`core/routing/defaults.py`), overridable from `.eos/config.toml`.
- **ADR-018 — records runs, never verdicts.** The router is a deterministic
  policy, not a learner. Outcomes are *observations* stored next to the
  decision; nothing in this plan changes a decision because of a past
  outcome. The seam where that would later happen is one function
  (M4, `policy.adjust_for_history`), shipped as a no-op.
- **ADR-019 — never record what was asked.** Trace lines carry a task hash,
  never the task. `eos route` output prints the task back to the person who
  typed it; that is stdout, not a store.
- **ADR-021 / 022 — the writer is a hook or a wrapper, never a habit.** The
  brief carries the decision, so an agent with the prompt hook installed sees
  it without choosing to.
- **`tools/check-clean.sh`** denies a fixed list of internal identifiers in
  this repository. Nothing host-specific goes in; project keyword extensions
  live in the project's `.eos/config.toml`.
- **Backward compatible.** With no `[model_routing]` table in a project's
  config, every existing command's output is byte-identical (a test proves
  it for `eos brief`, M7).
- **Existing conventions.** Module docstrings say why the module exists and
  which rules are load-bearing (read `core/telemetry.py:1-38` for the
  register). Tests state a property per test name. Commit messages are
  imperative; a release commit is `release: X.Y.Z -- <what changed>`.

## 3. Target architecture

```text
task text (+ optional file paths, session)
  ↓
core/routing/classify.py   → TaskClass(type, confidence, matched)      deterministic keyword rules
  ↓
core/routing/score.py      → Complexity(score, level, factors)         weighted 0..1, four levels
  ↓
core/routing/policy.py     → Decision(model, effort, reason, confidence, override_source)
        ▲ registry.py  (ModelSpec table: defaults + config overrides)
        ▲ overrides    (CLI flag > env > config > auto)
  ↓
core/routing/trace.py      → .eos/data/routing.jsonl  (+ a `decided` event on the open execution)
  ↓
core/routing/adapters.py   → text for a human / the brief; a dict for a harness
  ↓
surfaces: eos route | eos brief --task | MCP `route` | (agent applies it)
```

Public entry point, and the only one other modules call:

```python
core.routing.route(project_root, task, *, files=(), session=None,
                   model=None, effort=None, record=True, fresh=False) -> Decision
```

Nothing under `core/ai/`, `core/brief.py` or `core/mcp_server.py` contains
routing logic; each calls `route()` and renders.

## 4. Integration points, as verified against 1.1.2

| Concern | Where | What to do there |
|---|---|---|
| CLI command | `core/eos.py`: `cmd_<name>` functions; parsers built in `main()` from `core/eos.py:2185`; `add_path(p)` helper at `:2192`; dispatch dict ending `"ai": cmd_ai` at `:2689` | add `cmd_route`, a `route` parser, one dispatch entry |
| Session fill-in | `core/eos.py:1953` `_FILL_SESSION` | add `("route", None)` so a recorded decision carries the harness session without `--session` |
| Telemetry | automatic for every command with a path (`core/eos.py:2705-2725`); flag names only | nothing — `eos route` is covered |
| Config | `.eos/config.toml`, read per module with `ConfigIO.read_toml(path).get("<table>", {})` (pattern: `core/telemetry.py:101-108`) | new table `[model_routing]`, read in `core/routing/config.py` |
| Brief | `core/brief.py:95` `build()`; `:280` `_task_sections(root, task) -> (sections, found)`; `:362` `_with_task()` assembles under a char cap and exempts lines starting with `"PROCEDURE  "` or `RULE_MARK` | append a `ROUTE` section; do not set `found`; exempt the ROUTE line from trimming |
| Execution ledger | `core/executions.py`: `current(root, session)` `:121`; `event(root, execution=None, *, kind, tool, target, ref, body, session)` `:206`; `KINDS` includes `"decided"` `:32`; `load(root)` returns `Record`s with `.events` (`Event` fields `:47-58`) | write one `decided` event per decision; read it back to reuse |
| Local per-project log | `core/telemetry.py:70` `path_for` → `.eos/data/<file>`; `:157` `record()` (mkdir, append JSON line, `_trim` to `MAX_LINES`, swallow `OSError`) | mirror it in `core/routing/trace.py` with `routing.jsonl` |
| Index for scope | `core/inspector.py:238` `impact(root, relative_path, depth=1)` → dict with `dependencies`, `dependents`, `file` (None when unindexed) | `score.py` calls it per `--file`; absence degrades, never fails |
| MCP tools | `core/mcp_server.py:100` `self.tools = {name: {description, inputSchema, handler}}`; dispatch `:352-364` | add `"route"` |
| Agent surfaces | `core/ai/templates/agents_section.md`, `skill.md` (rendered by `core/ai/writer.py:163` `write_all`; tests `tests/test_ai_writer.py` assert substrings, not whole files) | one paragraph each on the ROUTE line and how to apply it |
| Docs | `README.md:78` command table, `:141/:285/:505` config examples; `ARCHITECTURE.md:146` CLI table; `docs/decisions/` next number **025** | table rows, a config example, ADR-025 |
| Version | `core/VERSION` (`1.1.2`); `tests/test_distribution.py` and `test_smoke.py` read it rather than a literal | bump at the end (§9) |

## 5. Contracts

All dataclasses are frozen and live in `core/routing/types.py`.

```python
EFFORTS = ("low", "medium", "high", "xhigh", "max")          # total order, ascending
LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
TASK_TYPES = ("trivial_edit", "simple_implementation", "normal_implementation",
              "debugging", "refactoring", "code_review", "test_generation",
              "investigation", "architecture", "planning", "complex_reasoning",
              "repository_wide_change")

@dataclass(frozen=True)
class ModelSpec:
    id: str                      # registry key and what the harness receives, e.g. "sonnet"
    provider: str                # informational, e.g. "anthropic"
    reasoning: int               # 1..5
    coding: int                  # 1..5
    context_window: int          # tokens
    cost: float                  # relative; 1.0 = cheapest in the table
    efforts: tuple[str, ...]     # subset of EFFORTS, ascending
    task_types: tuple[str, ...]  # recommended, informational; never a hard filter
    status: str                  # "available" | "unavailable"
    aliases: tuple[str, ...] = ()

@dataclass(frozen=True)
class TaskClass:
    type: str; confidence: float; matched: tuple[str, ...]   # words that decided it

@dataclass(frozen=True)
class Complexity:
    score: float; level: str; factors: dict[str, float]      # each factor 0..1

@dataclass(frozen=True)
class Decision:
    task_type: str; level: str; score: float; model: str; effort: str
    reason: str; confidence: float
    override_source: str         # "auto" | "flag" | "env" | "config" | "run"
    factors: dict[str, float]; alternatives: tuple[str, ...]   # other models that qualified, cheapest first
    task_hash: str; execution: str | None; reused: bool
    def to_dict(self) -> dict: ...

def normalise(text) -> str      # lower-case, whitespace collapsed
def task_hash(text) -> str      # FNV-1a 32-bit of normalise(text), 8 hex chars
```

`score` was added during M1 so `eos route` can print `(score 0.68)` without
re-scoring.

Trace line (`.eos/data/routing.jsonl`), one per recorded decision:

```json
{"at": "…Z", "session": "…", "execution": "x-…", "work_item": null,
 "task_hash": "fnv1a32-hex", "type": "refactoring", "level": "HIGH",
 "model": "sonnet", "effort": "high", "reason": "…", "confidence": 0.89,
 "override_source": "auto", "reused": false}
```

Config (`.eos/config.toml`), all keys optional:

```toml
[model_routing]
enabled = true              # false: `eos route` still answers; brief and MCP add nothing
default_model = "auto"      # or a registry id / alias
default_effort = "auto"     # or one of EFFORTS
brief = "with-brief"        # "with-brief" | "always" | "never"   (see M7)
hook = false                # true: `eos ai update` installs the PreToolUse hook of M9

[model_routing.models.local-coder]     # add or override a registry entry; keys as ModelSpec
provider = "openai-compatible"
reasoning = 3
coding = 4
context_window = 128000
cost = 0.5
efforts = ["low", "medium", "high"]
status = "available"

[model_routing.keywords]               # extend the classifier, e.g. for another language
debugging = ["hata", "çöküyor"]
```

## 6. Milestones

Each milestone is one or more commits, each green under `python3 -m pytest -q`
and `bash tools/check-clean.sh` before the next begins. Do not bump
`core/VERSION` until §9.

### M0 — the decision record and the design note

Files: `docs/decisions/025-model-and-effort-routing.md`, this plan's §11 row.

ADR-025 states, in the register of ADR-024: EOS advises, the harness
executes; the policy is deterministic and explainable; the cheapest
sufficient model wins; effort is clamped, never invented; the trace stores no
task text; learning from outcomes is deferred and where it will plug in.
Keep it under 120 lines.

Check: `bash tools/check-clean.sh` passes. Commit: `docs: ADR-025 model and
effort routing`.

### M1 — registry, taxonomy, types

Files: `core/routing/__init__.py`, `types.py`, `defaults.py`, `registry.py`,
`config.py`; `tests/test_routing_registry.py`.

`defaults.py` — `DEFAULT_MODELS: tuple[ModelSpec, ...]`, three generic tiers
under the ids the harness's subagent tool accepts today (`haiku`, `sonnet`,
`opus`), with capability numbers that make the policy table in M4 pick
haiku for LOW, sonnet for MEDIUM and HIGH, opus for CRITICAL. **Effort lists
per model come from the harness verification in §12 — fill them from that
table, do not guess.** A comment above the tuple says these are defaults to
be corrected in config, not facts about vendors.

`config.py` — `load(project_root) -> RoutingConfig` (frozen dataclass:
`configured`, `enabled`, `default_model`, `default_effort`, `brief`, `hook`,
`models: dict`, `keywords: dict`), reading `[model_routing]` the way
`telemetry.enabled` does; malformed values and unknown keys raise
`ValueError` with the key named; a missing table yields the defaults with
`enabled=True` and **`configured=False`**. `configured` is what M7 gates the
brief's ROUTE line on: without it, "missing table → enabled" would change
every existing brief and break the byte-identical rule.

`registry.py` —
```python
def load(project_root) -> Registry            # defaults, then config overrides by id (merge, not replace)
class Registry:
    def get(self, name) -> ModelSpec | None   # by id or alias, case-insensitive
    def available(self) -> tuple[ModelSpec, ...]
    def candidates(self, *, min_reasoning, min_coding, effort=None) -> tuple[ModelSpec, ...]
        # available, meets both minima, supports `effort` when given; sorted by (cost, -reasoning, id)
```
Validation on load: ids unique after alias expansion; `efforts` non-empty and
each in `EFFORTS`; capability ints in 1..5; unknown keys in a config entry
are an error naming the key.

Tests (property per name): defaults load and validate; a config entry adds a
model; a config entry overriding `sonnet.efforts` changes only that field; an
unknown effort in config is refused with its name; alias lookup is
case-insensitive; `candidates` order is cost then reasoning; an
`unavailable` model is never a candidate.

Commit: `routing: model registry, taxonomy and contracts`.

### M2 — classification

Files: `core/routing/classify.py`; `tests/test_routing_classify.py`.

Deterministic, ordered, table-driven. Tokenise with lower-case word
boundaries (reuse the regex idea of `.claude/helpers`-style routers: single
tokens are `\b`-anchored so `ci` never matches inside `decision`; phrases
match literally). Each type has a keyword table of `(token, weight)`; the
type's score is the sum of matched weights; the highest wins; ties break by
the order of `TASK_TYPES`. Confidence = `best / (best + second + 1.0)`,
capped at 0.95; no match at all → `normal_implementation`, confidence 0.30.

Rules that override the sum, applied in this order after scoring:

1. Repository-wide phrases (`across the repo`, `every module`, `whole
   codebase`, `all services`, `everywhere`) → `repository_wide_change`.
2. Failure words (`fails`, `error`, `exception`, `stack trace`, `crash`,
   `broken`, `bug`, `regression`) with a change verb (`fix`, `resolve`,
   `repair`) → `debugging`; failure words without a change verb, or question
   openers (`why`, `where`, `what causes`, `investigate`, `find out`) →
   `investigation`.
3. Trivial signals (`typo`, `comment`, `rename`, `bump`, `version`, `readme`,
   `format`, `whitespace`, `docstring`) win only when no other table scored
   above 1.0.
4. `implement / add / create / build` split: `simple_implementation` when the
   text names one thing and no conjunction of verbs; `normal_implementation`
   otherwise. Concretely: one change verb and no ` and ` joining two verbs.

Project keyword extensions from `[model_routing.keywords]` are appended to
the table for the named type with weight 1.0.

As built (M2), three readings of the rules above were fixed where the plain
wording misclassified ordinary sentences:
- Rule 2's question openers count only at the **start** of the text ("design
  the boundaries where…" is not a question). The failure-word split applies
  only when `debugging` or `investigation` won the sum, so "add error
  handling" stays an implementation.
- Rule 4 also requires **at most twelve words**; a long sentence rarely names
  one change.
- `fix` weighs 0.5 in the debugging table so "fix the typo" stays trivial
  under rule 3, while "fix the crash" still reaches debugging through the
  failure word. A rule override reports confidence `max(0.80, scored)`.
- Single tokens and the last word of a phrase accept a plain inflection
  (`s|es|ed|ing|d`), so `unit test` matches "unit tests".

Tests: one per taxonomy type with a plain English sentence; `decision` does
not classify as devops-style `ci`; the tie-break is stable; the same text
gives the same `TaskClass` twice; config keywords reach the classifier; a
100-word paste still classifies in under 5 ms (`time.perf_counter`, loose
bound).

Commit: `routing: deterministic task classification`.

### M3 — complexity score

Files: `core/routing/score.py`; `tests/test_routing_score.py`.

`score(task, task_class, *, files=(), project_root=None) -> Complexity`.

Factors, each 0..1, each explained in the result's `factors` dict:

| factor | source |
|---|---|
| `scope` | base by type (`trivial_edit` .1, `simple_implementation` .25, `normal_implementation` .45, `test_generation` .35, `code_review` .35, `debugging` .5, `refactoring` .6, `investigation` .5, `planning` .5, `architecture` .85, `complex_reasoning` .85, `repository_wide_change` 1.0) |
| `file_count` | `len(files)` when given, else a number before `file(s)`/`module(s)` in the text, else 1 → `min(n / 8, 1)` |
| `dependency_count` | with `project_root` and `files`: sum of `dependents` from `inspector.impact` per file → `min(n / 20, 1)`; without an index, 0 and `factors["dependency_count_source"] = "none"` |
| `architectural_impact` | type in (`architecture`, `repository_wide_change`) 1.0; `refactoring` .6; plus .3 for any of `schema`, `migration`, `public api`, `contract`, `interface`, `boundary` (cap 1) |
| `reasoning_required` | by type: `complex_reasoning` 1, `debugging` .7, `architecture` .8, `investigation` .6, `planning` .6, `refactoring` .5, others .3, `trivial_edit` .1 |
| `failure_risk` | .0 base; +.4 for `production`, `prod`, `payment`, `billing`, `auth`, `security`, `data loss`, `migration`, `irreversible`; +.3 if type is `debugging`; cap 1 |
| `uncertainty` | `1 - task_class.confidence`, +.2 for `not sure`, `somehow`, `maybe`, `unknown`; cap 1 |

Weights: `scope .15, file_count .15, dependency_count .10,
architectural_impact .20, reasoning_required .20, failure_risk .15,
uncertainty .05`. Level thresholds on the weighted sum: `< .30 LOW`,
`< .55 MEDIUM`, `< .80 HIGH`, else `CRITICAL`.

Floors and caps after thresholding:
- `architecture`, `repository_wide_change`, `complex_reasoning` → at least `HIGH`.
- `trivial_edit` → at most `LOW` unless `failure_risk ≥ .5`.
- **Uncertainty alone never raises a level** (its weight is .05 and there is
  no escalation gate on it). This is the lesson Ruflo recorded as issue
  #2250: an uncertainty gate promoted every trivial task.

**As built (M3): recalibrated.** Measured on eighteen representative
sentences, the cuts above left every ordinary implementation at LOW (score
≈ 0.18) and CRITICAL unreachable without file data, because most tasks arrive
without paths and `file_count` / `dependency_count` sit near zero. The shipped
cuts are **`< .15 LOW, < .35 MEDIUM, < .55 HIGH, else CRITICAL`**, and
`reasoning_required` separates the implementation types (`simple_implementation`
.2, `normal_implementation` .4, `code_review` .4; other values as above).
Resulting spread: typo/bump .07 LOW, "add a getter" .12 LOW, normal
implementation .18 MEDIUM, a crash fix .29 MEDIUM, "refactor the auth flow and
update tests" .42 HIGH, service-boundary design .58 CRITICAL. Factors are
rounded to four places, so they sum to the score within 1e-4, not 1e-9.

Tests: each level reachable by a sentence; the floors and the cap; `--file`
count moves `file_count`; a fixture project with an index moves
`dependency_count` and no index yields the `"none"` marker; factors sum to
the score within 1e-9; determinism.

Commit: `routing: transparent complexity score`.

### M4 — policy, overrides, decision

Files: `core/routing/policy.py`, `core/routing/__init__.py` (`route()`);
`tests/test_routing_policy.py`.

Level → requirement:

| level | min_reasoning | min_coding | effort band (preferred first) |
|---|---|---|---|
| LOW | 1 | 2 | `low`, `medium` |
| MEDIUM | 3 | 3 | `medium`, `high` |
| HIGH | 4 | 4 | `high`, `xhigh` |
| CRITICAL | 5 | 4 | `xhigh`, `max` |

Type adjustments: `code_review`, `test_generation` → `min_coding + 1`;
`investigation`, `planning`, `complex_reasoning` → `min_reasoning + 1`; both
capped at 5. If no available model meets the adjusted minima, relax the
adjustment; if still none, pick the most capable available model and say so
in `reason` (`"no registered model meets HIGH; using the strongest
available"`). The router never returns an empty decision.

Model choice: `registry.candidates(...)[0]` — cheapest sufficient. The rest
of the list becomes `alternatives`.

Effort choice — `clamp(model, requested) -> str`, the single place an
effort is validated:
```
if requested in model.efforts: return requested
lower = [e for e in model.efforts if EFFORTS.index(e) < EFFORTS.index(requested)]
return lower[-1] if lower else model.efforts[0]
```
Auto effort: the band is ordered by preference, so the rule is: the first
element of the band the model supports; if it supports none of the band,
`clamp(model, band[0])`.

Override matrix (`override_source` in the decision):

| model | effort | behaviour |
|---|---|---|
| auto | auto | full policy |
| fixed | auto | skip candidate search; effort = first of the level's band the model supports, else clamp |
| auto | fixed | candidates filtered to models supporting that effort; if none, full policy and clamp, reason says the effort was unsupported by every candidate |
| fixed | fixed | model as given; effort clamped if unsupported, reason says so |

Precedence of sources: CLI flag > `EOS_ROUTE_MODEL` / `EOS_ROUTE_EFFORT` >
`[model_routing] default_model/default_effort` > auto. A fixed model that is
**unknown** or **unavailable** is refused (`ValueError` naming it and
listing available ids); `eos route` turns that into exit 2. Never silently
substitute for an explicit choice.

Confidence: `round(0.5 * class.confidence + 0.5 * min(1, distance_to_nearest_threshold / 0.10), 2)`
— 0.10 is half a middle band after the M3 recalibration (`policy.SETTLED_DISTANCE`).
A refused override raises `routing.OverrideError` (a `ValueError`), which is
what M6 maps to exit 2.

`reason`: one sentence, built from the type, level, the two strongest
factors, the file count when known, and any clamp or fallback that
happened. Example: `Multi-file refactoring with architectural impact (0.6)
and 4 files; cheapest model meeting HIGH; effort high.`

`policy.adjust_for_history(decision, project_root) -> Decision` — returns its
input unchanged; docstring names ADR-018 and says what a future version may
read (`trace.outcomes_by(type, level, model)`). This is the only seam.

`route()` in `__init__.py` composes: config → (reuse check, M5) → classify
→ score → policy → adjust_for_history → (record, M5) → Decision.

Tests, each named for the requirement it proves: trivial task → cheapest
model, low effort; normal coding task → MEDIUM tier; debugging; architecture
→ at least HIGH; repository-wide → HIGH or CRITICAL; capability mismatch
falls back and says so; an effort the model lacks is clamped and never
returned; explicit model respected; explicit effort respected when
supported; `model=auto`; `effort=auto`; unknown model refused; two registries
with different effort lists give different clamps for the same task;
identical inputs give identical `Decision`s across 50 runs; **for every
(level × registry entry) the returned `(model, effort)` pair is in the
registry** (a parametrised sweep).

Commit: `routing: policy, overrides and the decision`.

### M5 — trace, the `decided` event, reuse within a run

Files: `core/routing/trace.py`; `route()` wiring; `tests/test_routing_trace.py`.

`trace.record(project_root, decision, session)` mirrors
`telemetry.record`: `.eos/data/routing.jsonl`, `MAX_LINES = 5000`, never
raises. `trace.load(project_root)` and `trace.outcomes(project_root)` — the
latter joins trace lines to `executions.load()` by execution id and returns
`(type, level, model, effort, outcome)` rows; this is what `eos route
--stats` prints (M6) and what the future seam would read.

Binding to the run: when `executions.current(project_root, session)` finds
an open execution and `record=True`, `route()` also appends
`executions.event(root, kind="decided", tool="route", ref=f"route:{task_hash}",
body=f"{type} {level} {model} {effort} {override_source}")`. `_checked`
in `executions.py` bounds `ref`/`body`; keep the body to those five tokens.

Reuse: before classifying, `route()` looks at the open execution's events for
the latest `kind == "decided" and tool == "route"`; if present and not
`fresh`, it rebuilds the `Decision` from the body with `reused=True` and
`override_source="run"`, and records nothing. A different task text in the
same run still reuses — the requirement is one decision per task lifecycle,
and the run is the lifecycle. `--fresh` bypasses and records a new event.

The brief (M7) calls `route(..., record=False)`: it fires on every prompt,
and a trace line per prompt would be noise, not memory.

As built (M5): the event body carries **seven** tokens — the five above plus
`score` (4 places) and `confidence` (2 places) — so a reused decision prints
the same numbers. Reuse is also skipped when the caller passes an explicit
`model` or `effort` (an explicit choice must not be answered with an older
automatic one); a reused model that has since become unknown or unavailable,
or no longer accepts the effort, is not reused. `trace.record` writes only
into a root that already has `.eos/`, so `eos route` in an arbitrary folder
leaves nothing behind. `trace.stats()` groups `outcomes()` per
(type, level, model, effort) for M6's `--stats`.

Tests: a line is written with no task text (assert the text is absent from
the file); the file is trimmed; a write failure does not break `route()`
(monkeypatch `open` to raise); with an open run the event appears and a
second `route()` returns `reused=True`; `--fresh` records again; no open run
→ no event, trace still written; `outcomes()` joins to a finished run's
outcome.

Commit: `routing: trace and the decided event`.

### M6 — `eos route`

Files: `core/eos.py`; `tests/test_routing_cli.py`.

Parser (register after `brief`):
```
route <path> <task…>   [--file F]…  [--model M] [--effort E] [--json]
                       [--fresh] [--no-record] [--session S] [--stats]
```
`task` is `nargs="+"`, joined with spaces, so quoting is optional like
`eos compose`. `--stats` ignores the task and prints the outcomes table.

Human output, exactly:
```
Task: Refactor authentication flow and update tests

Type:        refactoring
Complexity:  HIGH  (score 0.68)
Model:       sonnet
Effort:      high
Confidence:  0.89
Source:      auto

Reason:
Multi-file refactoring with behavioural impact and test changes.

Factors: architectural_impact 0.60, reasoning_required 0.50, scope 0.60, …
Alternatives: opus
```
`--json` prints `Decision.to_dict()` and nothing else. Exit 0; exit 2 on a
refused override with the message on stderr; exit 1 on an unexpected error.
`("route", None)` joins `_FILL_SESSION`.

Tests (subprocess, the `tests/test_brief.py` style): the human format has
the eight labelled lines; `--json` parses and round-trips the fields;
`--model nope` exits 2 and names the available ids; `--effort max` on a model
without `max` prints the clamped value and a reason mentioning the clamp;
`--file` changes the file_count factor; `--stats` on an empty project prints
a "no decisions recorded" line; telemetry (when enabled) records `route` with
flag names and no task.

As built (M6): `task` is `nargs="*"`, not `"+"` — with `"+"` the path-only
form `eos route . --stats` cannot parse. A missing task exits 2 and, when the
first argument is not a directory, says it was taken as the path. The
Factors line prints all seven numeric factors in weight order.
`tests/test_documents_match_reality.py` refuses a command no agent document
names, so the README command-table row moved from M8 into this milestone.

Commit: `cli: eos route`.

### M7 — surfaces: brief, MCP, agent templates

Files: `core/brief.py`, `core/mcp_server.py`, `core/ai/templates/agents_section.md`,
`core/ai/templates/skill.md`; tests in `test_brief.py`, `test_mcp_index_tools.py`
(or a new `test_routing_surfaces.py`), `test_ai_writer.py`.

Brief — in `_task_sections`, after the existing sections, when
`config.enabled` and `config.brief != "never"`:
```
ROUTE  refactoring HIGH → sonnet/high (conf 0.89) — Multi-file refactoring with …
  Apply: Task({model: "sonnet"}) for subagents; /effort high for this session; eos route . "<task>" for the factors
```
Rules: the section never sets `found` when `brief == "with-brief"` (a prompt
with nothing recorded behind it still costs nothing); with `"always"` it
sets `found` so every task prompt carries the line. The `ROUTE  ` prefix
joins the trimming exemption in `_with_task` (two spaces, like
`PROCEDURE  `). `record=False`. Any exception from `route()` is swallowed
here: the brief must not fail because of the router (the hook's own rule).

MCP — `"route"`: `inputSchema` `{task: string (required), files: string[],
model: string, effort: string}`; handler `route(project_root, …,
record=False).to_dict()`. The server stays read-only.

Templates — one paragraph in `agents_section.md` after the `eos brief`
paragraph and one bullet in `skill.md`'s "What is not a judgement call":
the ROUTE line is the model and effort this project's policy chose; use it
when spawning work (the subagent `model` argument), respect an explicit
override in it, and `eos route` explains the decision. Add `route` to the
skill's tool table with cost "low" and cheaper alternative "none — nothing
else holds the policy". The existing template tests assert substrings; add
one for `"eos route"`.

Tests: brief without `[model_routing]` is byte-identical to a captured
expectation (build the expectation by calling `brief.build` before
monkeypatching `routing.route` to raise — same output either way); with
`enabled = true` the ROUTE line appears; with `brief = "never"` it does not;
`--task-only` with nothing recorded still prints nothing under
`"with-brief"` and prints the ROUTE line under `"always"`; the ROUTE line
survives a tiny `--budget`; the MCP tool returns the decision dict; the
rendered skill mentions `eos route`.

**As built (M7), three corrections to the text above:**
- **No new MCP tool.** `tests/test_mcp_index_tools.py` pins the roster at 12
  ("capability arrives by widening tools, never by adding them" — the roster
  is paid on every request). `get_context` was widened instead: `route: true`
  (with `task`) adds a `route` key holding `Decision.to_dict()`, and `files`,
  `model`, `effort` feed it; `record=False`. A project with `enabled = false`
  gets `{"disabled": …}`. The skill's tool table says so in the `get_context`
  row; a test asserts the roster did not grow.
- **The ROUTE line is gated on `configured`** (M1), not only `enabled`, so a
  project without `[model_routing]` never runs the router from the brief —
  the byte-identical test monkeypatches `routing.route` to raise and passes.
- **The budget loop keeps going for exempt lines.** It used to `break` at the
  first over-budget line, which would drop a ROUTE section placed after the
  notes. It now skips non-exempt lines once trimmed and still lets exempt ones
  (`PROCEDURE  `, `  RULE  `, `ROUTE  `, `  Apply: `) through. Existing briefs
  are unchanged: no exempt line ever followed a trimmed one before.
- `core/routing/adapters.py` (§8) landed here: `render()`, `headline()`,
  `brief_lines()`. The skill also gained an `eos route` row in its command table.

Commit: `routing: brief line, MCP tool, agent surfaces`.

### M8 — documentation

Files: `README.md`, `ARCHITECTURE.md`.

README: a row for `eos route` in the command table (`:78` region); a
"Which model, how much effort" section after the telemetry section (`:505`
region) with the config example from §5 and the override matrix in prose;
the ROUTE line described under the brief. ARCHITECTURE: one row in the CLI
table; one line under "Knowledge Pipeline" saying routing is advisory and
where the trace lives.

Commit: `docs: model and effort routing`.

## 7. Details that decide whether this ships clean

1. **Determinism.** No `random`, no time-dependent input to the decision
   (`at` is in the trace only), no dict ordering the result depends on:
   iterate `TASK_TYPES`, `EFFORTS` and the sorted candidate list.
2. **The task never lands in a file.** `task_hash` is FNV-1a 32-bit of the
   normalised text (lower-case, collapsed whitespace), hex. Grep the test
   project's `.eos/` for a sentinel word after every recording test.
3. **Never an unsupported pair.** `policy` is the only module that returns a
   `(model, effort)`; `clamp` is the only function that returns an effort;
   the sweep test in M4 is the proof.
4. **Registry ids are what the harness receives.** A `Decision.model` is a
   registry id; aliases are for input only. Adapters (§8) map ids to harness
   arguments; today's ids coincide with the subagent tool's aliases, and if
   that changes, the mapping is in one place.
5. **Byte-identical without config.** The brief test in M7 is the gate;
   also run `eos brief` on `tests/fixtures/java_structure/svc` before and
   after M7 by hand once and diff.
6. **The brief's cost.** The ROUTE section is two lines; keep the reason
   under 120 characters in the brief (`_clip` exists in `brief.py`).

## 8. Harness adapter (`core/routing/adapters.py`)

What Claude Code exposes, as reported from its documentation by a research
pass on 2026-09-25 (`code.claude.com/docs/en/model-config`, `/sub-agents`,
`/hooks`). **Re-verify each line against those pages before M1 and M9; the
docs move.**

| Knob | Where | Values | Can EOS set it? |
|---|---|---|---|
| Subagent model | `Task` tool `model` argument; agent frontmatter `model:` | `default`, `best`, `fable`, `opus`, `sonnet`, `haiku`, `opus[1m]`, `sonnet[1m]`, `opusplan`, full ids | Yes — a `PreToolUse` hook on `Task` may return `updatedInput` with `tool_input.model` rewritten (M9). Advisory otherwise. |
| Subagent effort | agent frontmatter `effort:` | `low` `medium` `high` `xhigh` `max` (or a token budget) | Only by writing agent files; not per call. Advisory. |
| Session effort | `effortLevel` in settings, `--effort`, `/effort`, `CLAUDE_CODE_EFFORT_LEVEL`, per-model `modelSettings.<id>.effort` | same five, plus `ultracode` | No — hooks cannot change effort; `UserPromptSubmit` returns no `updatedInput`. Advisory: the ROUTE line names the `/effort` command. |

`render(decision, agent) -> dict` where `agent` is what `eos brief --agent`
receives (`claude`, `devin`, or `None`):

- `claude`: `{"subagent": {"model": decision.model}, "effort": {"value":
  decision.effort, "apply": "/effort <value>", "applies": "advisory"},
  "line": <the ROUTE line>}`.
- anything else: `{"line": <the ROUTE line>}` — advisory text only; no
  other harness is known to take a per-task model argument.

The ROUTE line's second line therefore reads:
`  Apply: Task({model: "<id>"}) for subagents; /effort <effort> for this
session; eos route . "<task>" for the factors`.

Registry ids stay `haiku` / `sonnet` / `opus` — the three aliases every
surface above accepts. `fable`, `best`, `opusplan` and the `[1m]` variants
are **not** default registry entries: a project that wants them adds them in
config with their own capability numbers; the router then treats them like
any other id. Do not special-case them in code.

### M9 (optional, off by default) — the `PreToolUse` hook on `Task`

Files: `core/ai/templates/pretooluse_task.py`, `core/ai/writer.py`
(install when `[model_routing] hook = true`), `tests/test_ai_writer.py`.

This is the one place the decision becomes an action rather than advice,
and it is the shape the Ruflo harness uses. The hook receives the `Task`
call's `tool_input`; when `tool_input.model` is **unset**, it runs
`eos route <cwd> "<tool_input.prompt>" --json --no-record --session <id>`
(the subagent prompt is the task; it is passed to one process and not
stored), and returns `{"hookSpecificOutput": {"hookEventName":
"PreToolUse", "updatedInput": {…tool_input, "model": decision.model}}}`.
When `tool_input.model` is set, the hook returns nothing: an explicit choice
is respected (M4 override rule, applied at the harness). Any failure →
exit 0, no output, like the other hook templates. Effort is not touched
(the harness does not allow it).

`writer.write_all` adds the `PreToolUse` entry with `"matcher": "Task"` to
`.claude/settings.json` the way it adds `SessionStart` and `Stop`, only when
the config asks; `eos ai update` with the flag off removes only its own
entry (the existing merge rules in `writer.py` preserve foreign hooks —
tests `test_ai_writer.py:55-78` show the contract).

Tests: the hook leaves an explicit `model` alone; rewrites an unset one from
a stubbed `eos route --json`; exits 0 with no output when `eos` fails; the
settings entry appears only with the flag and disappears without it while a
foreign `PreToolUse` entry survives.

Commit: `routing: optional PreToolUse hook applies the model to subagents`.

## 9. Release and carry-over

After M8: `core/VERSION` → `1.2.0`; commit `release: 1.2.0 -- adaptive model
and effort routing`; `python3 -m pytest -q && bash tools/check-clean.sh`;
push `main`. The host workspace that vendors this repository pulls it by
`git subtree` and re-runs its installers; that procedure is recorded on the
host side, not here.

## 10. Acceptance — run these, do not reason about them

```bash
python3 -m pytest -q tests/test_routing_*.py tests/test_brief.py tests/test_ai_writer.py
bash tools/check-clean.sh
python3 core/eos.py route tests/fixtures/java_structure/svc "fix the typo in the readme"
python3 core/eos.py route tests/fixtures/java_structure/svc "design the service boundaries for a new billing domain" --json
python3 core/eos.py route tests/fixtures/java_structure/svc "refactor the auth flow and update tests" --effort max
python3 core/eos.py brief tests/fixtures/java_structure/svc --task "refactor the auth flow" --task-only   # empty unless config enables it
grep -rc "auth flow" tests/fixtures/java_structure/svc/.eos/ || true                                    # must be 0 lines
```

Expected: LOW/low on the first; HIGH or CRITICAL on the second; the third
prints a clamped effort with the clamp in its reason; the fourth prints
nothing; the grep finds no task text.

## 11. Status

| Milestone | Lands in | Commit | Done |
|---|---|---|---|
| M0 ADR-025 | 1.1.2 (unreleased) | this commit: `docs: ADR-025 model and effort routing` | 2026-09-25 |
| M1 registry, taxonomy, types | 1.1.2 (unreleased) | `routing: model registry, taxonomy and contracts` | 2026-09-25 |
| M2 classification | 1.1.2 (unreleased) | `routing: deterministic task classification` | 2026-09-25 |
| M3 complexity score | 1.1.2 (unreleased) | `routing: transparent complexity score` | 2026-09-25 |
| M4 policy and decision | 1.1.2 (unreleased) | `routing: policy, overrides and the decision` | 2026-09-25 |
| M5 trace and decided event | 1.1.2 (unreleased) | `routing: trace and the decided event` | 2026-09-25 |
| M6 `eos route` | 1.1.2 (unreleased) | `cli: eos route` | 2026-09-25 |
| M7 brief, MCP, templates | 1.1.2 (unreleased) | `routing: brief line, MCP context, agent surfaces` | 2026-09-25 |
| M8 docs | 1.1.2 (unreleased) | `docs: model and effort routing` | 2026-09-25 |
| M9 PreToolUse hook (optional) | — | | |
| release 1.2.0 | — | | |

## 12. Open questions — resolve before M1's defaults and M7's templates

1. **Effort values per model.** The harness documents one set of five for
   the session and the agent frontmatter (§8); it does not document which
   models accept which. The default registry gives every default entry all
   five and a comment saying so; a project narrows a model's `efforts` in
   config when it observes a refusal. The clamp then does the rest. Do not
   invent per-model differences.
2. **Re-verify §8's table** against the three documentation pages on the
   day M1 starts; record the date in `defaults.py`'s comment. If
   `PreToolUse` `updatedInput` turns out not to reach `Task`, M9 is dropped
   and §8's "advisory" wording is the whole story — nothing else changes.
3. **Model ids.** Confirm the subagent tool still accepts `haiku`, `sonnet`,
   `opus` at implementation time; the registry ids must be exactly those.
4. **Non-English tasks.** The classifier is English by default; a project
   whose prompts are in another language adds `[model_routing.keywords]`.
   No built-in second language — ADR-013's argument applies.
