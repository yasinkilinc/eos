# ADR-020: Work in flight is a ledger, not a note

## Status

Accepted, 2026-09-21.

## Context

Notes record what a scan cannot re-derive: why something is the way it is.
They are durable by construction — a note is as true in a month as it was the
day it was written, and `eos note audit` exists to catch the cases where it
stopped being.

What sessions kept needing, and had nowhere to put, is the other thing: what
is happening right now. Two agents work this codebase — Devin in the cloud,
Claude locally — and a session begins knowing nothing about the one that ran
this morning. The observable costs of that are ordinary and repeated: two
sessions starting the same item, a session redoing an investigation another
one was halfway through, a claim left open by a session that ended without
saying so, a blocker rediscovered at its own cost.

Putting this into notes was the obvious cheap option and is wrong. A note that
says "I am working on the top-up path" is false by the next afternoon, and a
corpus that accumulates those is a corpus whose average claim is stale — which
devalues every note in it, including the ones that are still true. The
existing audit machinery makes the mismatch sharper: a stale note is amended;
work that ended is *finished*, which is not the same operation.

## Decision

A separate append-only ledger, `work.jsonl`, in the knowledge directory beside
the notes and `verifications.jsonl`. Every command appends one event; an
item's state is the fold of its events.

Three properties follow from two sessions writing at once, and each of them
was chosen against an easier alternative:

**Append-only, folded on read.** A record with a mutable `status` field would
be simpler and would lose writes: a cloud session and a laptop appending
within the same minute both read, both write, and one overwrites the other.
Appends do not have that failure. The cost is that reading is a fold, which on
a ledger of this size is nothing.

**A second claim is reported, never resolved.** When a session claims an item
another session holds, both holders are named on every listing. Last-write-
wins would show one holder and hide the collision from both agents — which is
the exact outcome the ledger exists to prevent. EOS has no way to know which
of two agents should stop, and a tool that guessed would be confidently wrong
in a way nobody checks.

**Staleness is reported, not acted on.** An `active` item with no event for 24
hours is marked stale and left alone. Auto-closing it would erase a claim a
session might still be working; reassigning it would be the same guess as
above. A session that vanished and a session still thinking are
indistinguishable from here, and saying so is the whole intervention.

The ledger also reports its own sync state on every listing — pushed,
unpushed, uncommitted, untracked, gitignored. Cross-machine flow happens
through git, the same transport the notes use, and a ledger that read as
authoritative while sitting uncommitted on one laptop would be worse than no
ledger: it would answer "nobody is on this" with confidence, wrongly.

Live items are injected into `eos context` at 8% of the budget, second in the
document, ahead of everything derived from the code — the one fact here whose
value expires.

## Consequences

An item is what a session *said*, not what happened. `done` means a session
claimed it was done. The only cross-check available is git, so `eos work show`
prints the commits naming the item's ticket next to the claim, and refuses to
merge them: a ticket with no commits is either uncommitted work or a claim
that outran it, and nothing distinguishes those from here.

Nothing is enforced. There is no gate that refuses a commit from a session
that claimed nothing — deliberately, and for the reason recorded in the
workspace note on hookless platforms: build the cheap mechanism, measure
whether it is used, and only then pay for distribution across eighteen
repositories. The read side is wired to a hook because reaching for it must
not be a decision (ADR-021); the write side stays a habit until there is
evidence it needs to be a wall.

Notes and work do not merge. If a session learns something durable while
working an item, that is a note, and the item points at nothing: two records
with different lifetimes should not share a lifecycle.

The ledger is also folded into `eos.db` (`work_item`, `work_holder`,
`work_event`) so it can be joined against the commits naming its ticket, an
extension's rows and the notes written alongside it. The file stays the source
of truth and `eos work` reads it directly, so there is one answer to "what is
the status of this item" and not two; the index is built by calling the same
fold the CLI uses. It carries no `stale` column, for the same reason the rest
of this decision gives: staleness is a question about now, and a value
computed at build time would answer it with the moment the index was built.
