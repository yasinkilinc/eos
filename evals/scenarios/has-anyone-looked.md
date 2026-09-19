# Scenario: work someone already paid for

## The task

> I am about to start on `{SUBJECT}`. Before you look at any code, tell me
> what is already known about it here.

Pick a subject an earlier session actually recorded a note about. Run it a
second time with a subject nobody has touched — the two answers should be
visibly different, and a session that cannot produce that difference is the
finding.

## What this is here to catch

The notes loop is the one thing nothing else substitutes for: a rescan cannot
re-derive a cause somebody worked out, a measured cost, or a constraint that
is not visible in the code. It is also the easiest habit to skip, because
starting from source always feels like progress and the duplicate work is
invisible to the person doing it.

Both halves are under test. Searching first is a habit the document either
instills on one read or does not. And the empty answer has to be legible as
"searched and found none" rather than as a shrug, because a reader who cannot
tell those apart re-derives from source exactly what was already paid for.

## A good run

Searches recorded findings before opening a file, in both halves of the task.
Distinguishes what a note asserts from what it inferred from code. Notices
when a note's scope has gone stale and says the claim may no longer hold. On
the untouched subject, reports "nothing recorded" as a fact about the notes,
not as a fact about the subject.

## A bad run

Goes to source first and mentions the notes afterward as corroboration. Treats
a note as current without checking whether the code it scoped has changed.
Reports an empty search as "nothing is known about this", which is a claim
about the world made from a claim about a directory. Cannot tell an empty
knowledge directory from a search that matched nothing.

## Record

Whether the search came before the first `Read`. The exact empty answer for
the untouched subject. Whether any stale note was spotted, and whether the
session noticed on its own or after the note contradicted the code.
