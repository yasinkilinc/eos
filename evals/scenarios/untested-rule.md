# Scenario: a refusal no test asserts

## The task

> This service refuses some requests with a coded error. Find one refusal in
> the `{FLOW}` flow that no test asserts, and get as far as you can toward a
> test for it. Tell me which refusal you picked and why that one.

Supply a flow the project actually runs. Do not name a code — picking one is
half the task, and naming it skips the half that exercises the documents.

## What this is here to catch

The path from "I should write a test" to "here is the one worth writing"
crosses four tools, and each hop is somewhere a reader can be stranded. The
question that lists a flow's untested refusals exists *because* a session had
to run the broad question and read a coverage column by eye, and nothing in
the documents told it that was the shortcut it was taking.

The coverage grades are the trap. `none` and `reachable` look adjacent and
mean opposite amounts of work: one is a test that does not exist, the other is
a fixture that already reaches the class with nothing asserting the branch. A
session that treats them as one list picks the expensive one and reports it as
though it had picked well.

## A good run

Finds the flow's untested refusals without reading a coverage column by eye.
Distinguishes `none` from `reachable` and says which it picked and why.
Reaches the source of the refusal — including when it lives in a linked parent
project rather than this one — before claiming anything about it. Treats the
draft as a skeleton and says what is missing from it.

## A bad run

Reports "no tests cover this" from a tally without opening the class. Picks a
`none` code because it sorted first, when a `reachable` one was one assertion
away. Presents the draft as a finished test. Counts a code named by a test as
covered behaviour, which the tool warns against on every run and which is the
easiest number here to quote as something it is not.

## Record

The tally, verbatim, with every grade. The count for the flow. Which grade the
chosen code had. Whether the source came from this project or a parent.
