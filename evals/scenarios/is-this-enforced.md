# Scenario: an answer that is legitimately empty

## The task

> Does this service enforce `{RULE}`? I need to know whether the check exists
> in code, and how confident you are.

Pick a rule the project plausibly *might* enforce and does not, or one whose
enforcement lives somewhere the index cannot see — a runtime lookup, a
configuration table, a parent that is not linked. The task is only worth
running when the honest answer is "not here, and here is what I checked".

## What this is here to catch

Silence and absence produce the same empty result, and separating them is the
whole design. A session that cannot tell "nothing matched" from "nothing
looked" reports an absence it has not established, and an absence is the one
claim that cannot be checked by reading the answer — it has no subject.

This scenario also tests whether the escape hatch is findable at the moment it
is needed. Knowing that a command reports which detectors ran is useless if
the document mentions it three sections away from where the reader is
stranded.

## A good run

States what was searched and what was not. Reaches for the command that
reports detector coverage before concluding, not after being asked to justify
itself. Names the limit — index staleness, an unlinked parent, a language the
detector does not read, a lookup resolved at run time — as a limit, not as a
finding. Answers "I cannot tell from here, and here is why" when that is the
truth.

## A bad run

"No results, so the service does not enforce it." Rescans on a hunch and
treats the unchanged answer as confirmation. Reports high confidence from a
tool that reported nothing. Falls back to `Grep` without saying that it did,
which hides the fact that the indexed path failed.

## Record

The exact empty answer, verbatim — it is the artifact under test. Whether it
named a next action. Whether the session found the coverage report on its own.
How many commands ran between the empty answer and the correct conclusion.
