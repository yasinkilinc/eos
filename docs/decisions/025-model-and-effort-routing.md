# ADR-025: EOS advises which model and how much effort; the harness executes

## Status

Accepted, 2026-09-25. Plan: `docs/plans/model-routing.md`.

## Context

A coding agent spends most of its budget on the model it runs and the effort
it reasons at. Both are usually left at whatever the session started with,
which means a typo fix and a service-boundary design are paid for at the same
rate. Agent harnesses that route by task — the three-tier router in the Ruflo
harness is the example studied — save most of that spend by sending simple
work to a cheap model and keeping the strong one for the work that needs it.

EOS is the one component every session here already consults about the task
before starting it (`eos brief --task`, ADR-021/024). It is the natural place
for that decision. It is also a component that does not call a language
model: nothing under `core/` imports a provider SDK or sends a prompt. The
harness runs EOS; EOS does not run the harness.

## Decision

**EOS advises; the harness executes.** `core/routing` produces a normalised
decision — a model id from a registry, an effort level, a one-sentence reason,
a confidence — and renders it where the harness can act on it: a `ROUTE` line
in the task brief, `eos route`, an MCP tool, and optionally a `PreToolUse`
hook that fills the model of a subagent call that did not name one. EOS never
emits provider API configuration and never calls a provider.

**The policy is deterministic and explainable.** A task is classified into a
fixed taxonomy of twelve types by weighted keyword tables and four ordered
rules. A complexity score is a weighted sum of seven named factors, each
between 0 and 1 and each printed with the decision; the sum is cut into
`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`. The same text under the same
configuration gives the same decision. There is no learned component: an
opaque decision about cost is one nobody can correct.

**The cheapest sufficient model wins.** Each level states a minimum
reasoning and coding capability; the registry answers with every available
model that meets it, cheapest first, and the first is chosen. The strongest
model is chosen only when the level requires it or when nothing else
qualifies — and then the reason says so.

**Effort is clamped, never invented.** Each registered model lists the effort
levels it accepts. One function turns a requested effort into one the model
supports — the requested value, else the highest supported value below it,
else the lowest supported. No other code path returns an effort, so no
decision can pair a model with a value its provider would refuse.

**Uncertainty alone never escalates.** Ruflo recorded (issue #2250) that an
uncertainty gate promoted nearly every trivial task to a stronger tier. Here
uncertainty is one factor at the smallest weight, with no gate of its own.

**Explicit choices are respected.** A model or effort given on the command
line, in the environment or in `.eos/config.toml` overrides the policy for
that half of the decision, in that precedence. An explicit model that is
unknown or unavailable is refused rather than substituted.

**Models live in a registry, not in the policy.** Three generic tiers ship as
a Python constant (the runtime updater copies `*.py` only), and a project
adds or corrects entries under `[model_routing.models]`. No model name
appears in the policy's logic.

**One decision per run, recorded without the task.** When a session has an
open execution (ADR-022), the decision is appended to it as a `decided` event
and later calls in the same run return it again instead of reclassifying. A
line in `.eos/data/routing.jsonl` records the decision with a hash of the
task text, never the text (ADR-019).

**Learning from outcomes is deferred.** The trace joins to the run's outcome,
and `eos route --stats` prints type, level, model and effort against it.
Nothing reads those outcomes to change a decision: that would be the engine
drawing a conclusion from runs, which ADR-018 does not allow it to do on its
own. The seam is one function, `policy.adjust_for_history`, which returns its
input unchanged. A later decision may give it something to read.

## Consequences

Without a `[model_routing]` table nothing a session sees changes: the brief's
ROUTE line is off by default, and every existing command's output is
byte-identical.

Effort is advisory in practice. The harness studied accepts a model per
subagent call but takes effort only per session or per agent definition, so
the ROUTE line names the command that sets it and the hook leaves it alone.

The classifier is keyword-based and English by default. A project whose
prompts are written in another language extends it in config; the engine
ships no second language (ADR-013's argument).

The default capability numbers are defaults, not facts about vendors. A
project that measures otherwise corrects them in config, and the policy
follows without a code change.
