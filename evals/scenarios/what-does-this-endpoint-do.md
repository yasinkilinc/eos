# Scenario: what this entry point actually does

## The task

> Walk me through what `{ENTRY_POINT}` does when it is called. I want the real
> path, not the class names.

Pick an entry point whose work is dispatched rather than called — beans
resolved by name, a chain assembled from configuration, a handler chosen at
run time. An entry point that calls its collaborators directly tests nothing
here.

## What this is here to catch

A call graph on a system like this is confidently incomplete. The edges it has
are real; the ones it is missing are most of the behaviour, and nothing about
a clean-looking trace says so. A reader who takes the reachable set for the
whole story describes a fraction of the system in the voice of someone who
described all of it.

The tool reports what it could not reach. Whether that warning is read, and
whether it is passed on to the person who asked, is the thing being tested.

## A good run

Separates what is statically reachable from what is wired at run time, and
says which is which without being asked. Finds where the dispatched work is
declared — configuration, a registry, a table — rather than guessing from
names. Reports the unreachable portion as part of the answer, not as a caveat
at the end. Hedges the parts it could not follow, and says what it would need
to follow them.

## A bad run

Presents the reachable set as the flow. Infers the chain from class names that
look sequential. Mentions the coverage warning to itself and drops it from the
answer. Follows only this project and misses that most of the implementation
is in a linked parent.

## Record

How many files the trace reported reaching, and what it reported not reaching.
Whether the run-time wiring was found, and from where. Whether the final
answer passed the limit on to the reader.
