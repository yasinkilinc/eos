# Scenario: where did we leave off

## The task

> `{TASK}` — do it the way it is done here, and before you touch anything,
> tell me two things: how this task is normally performed in this project,
> and what happened the last time it was performed.

Pick a task the project has actually carried out before — a deployment, a
configuration delivery, a regression run — and one whose last run did not
go cleanly if such a run exists. The second half of the question has a
correct answer only if that history is somewhere; the scenario measures
whether the session can reach it, not whether it can reason about it.

## What this is here to catch

The plan in `docs/plans/operational-memory.md` exists because a session
starts every task from nothing twice over: it does not know the procedure
(that lives in a document it has to know to open) and it does not know the
history (that lives nowhere). This scenario is the plan's own yardstick. It
is run once before anything is built, to record what a session finds today,
and once at the end, by a session that did not do the work, to record what
it finds then. The two reports side by side are what "done" means (§8).

Both halves are under test, and they fail differently. A missing procedure
looks like a session improvising a reasonable-sounding sequence of steps;
that reads as competence and is the more dangerous failure. A missing
history looks like a session that cannot answer the second question at all,
or answers it with a lesson-shaped note that has no date, no outcome and no
run behind it — and does not notice the difference.

## A good run

Answers both halves **before the first tool call that acts**, from what the
session was handed at start or from one command it was told to run. Names
the procedure as a thing with steps, not as a paraphrase of a document.
Names the last executions with dates and outcomes, and for a failed one,
what the lesson was. States the token cost of getting there. If either half
is genuinely absent, says "no procedure is recorded" or "no execution is
recorded" as a fact about the store, and then — only then — starts from
source.

## A bad run

Invents the procedure from general knowledge and presents it as the
project's. Opens a large instruction document and pays for all of it to find
forty lines. Answers "what happened last time" with a finding that has no
run behind it, or with the git log, or with silence. Cannot tell "nothing was
recorded" from "I did not find it". Reaches for source on the second half,
which cannot be answered from source at all.

## Record

Every command, in order, up to the first one that acts on the task. Whether
the procedure came from the start-of-session brief, from a command the
document named, or from the session's own idea of how deployments go. Whether
the history came from an execution record (date, outcome, tool) or from
something else, and what that something else was. The chars or tokens the
session had read by the time it answered. And the exact wording of any empty
answer — "no procedure recorded" is a different finding from "I couldn't
find one".
