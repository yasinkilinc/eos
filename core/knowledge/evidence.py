"""Provenance for derived facts: where a fact came from, and what was looked at.

Every other layer of EOS states what it knows. This one states how it knows it,
so a reader can disbelieve one fact without re-deriving all of them. The
working precedent is the journeys extension, whose `resolution` and
`candidates` columns record how a step was attributed and what else it could
have been; this generalises that to every detector.

Two records, deliberately separate:

* :class:`Fact` -- one thing a detector claims about one node or edge, with the
  detector that produced it, the line it was read from, and how sure it is.
* :class:`Coverage` -- what a detector *looked at*, whether or not it found
  anything. Without it a detector that finds nothing is indistinguishable from
  one that was never asked, which is how "no Kafka producers" and "nobody
  looked for Kafka producers" come to read the same. Measured: the generic
  Spring detectors return zero on a codebase whose messaging goes through a
  framework base class rather than an annotation.

Facts do NOT go into graph.json. That artifact is 25,466,819 bytes for 6,040
nodes and 72,139 edges on a real service; ~154 bytes of provenance per edge
adds 10.6 MB raw and ~15 MB pretty-printed, and MCP `get_graph` returns the
whole file. They travel in `.eos/data/brain/evidence.jsonl` instead and land in
SQLite, where they can be queried instead of read.
"""
from __future__ import annotations

import dataclasses
import datetime

# How a fact came to be known. The ladder is deliberately short, and the
# distinction that matters most is the first one: a fact read out of the source
# is not the same kind of thing as a fact an inference produced, and merging
# them is how a plausible guess becomes indistinguishable from a measurement.
EXTRACTED = "extracted"    # read directly out of the source
DOCUMENTED = "documented"  # stated by a human, in a note or a document
INFERRED = "inferred"      # derived by a heuristic; may be wrong
VERIFIED = "verified"      # confirmed by running something

ORIGINS = (EXTRACTED, DOCUMENTED, INFERRED, VERIFIED)

# Producers pick one of three. A free float invites invented precision -- 0.73
# claims a calibration nobody performed -- and the only decisions taken on this
# number are "trust it", "prefer something better if present" and "show it last".
CERTAIN = 1.0   # the source says so outright
LIKELY = 0.8    # one unambiguous signal, resolvable against the project
POSSIBLE = 0.5  # a heuristic that is right more often than not

CONFIDENCE = (CERTAIN, LIKELY, POSSIBLE)


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def detector(name: str, version: int = 1) -> str:
    """`name@version`, one token, so a fact records which build of a detector made it.

    Kept as a single string rather than two columns: it is only ever compared,
    grouped and displayed whole, and a detector whose rules change materially
    should take a new version rather than silently reinterpret its old rows.
    """
    return f"{name}@{version}"


@dataclasses.dataclass(frozen=True)
class Fact:
    """One claim about one subject.

    `subject_kind` is "node" or "edge"; `subject` is the node id, or an edge
    addressed as `src|dst|kind|imported` -- the edge table's primary key, so a
    fact joins to it without a surrogate id that a rebuild would renumber.
    """

    subject_kind: str
    subject: str
    predicate: str
    object: str | None
    origin: str
    confidence: float
    detector: str
    source_ref: str | None
    observed_at: str

    def as_row(self) -> tuple:
        return (self.subject_kind, self.subject, self.predicate, self.object,
                self.origin, self.confidence, self.detector, self.source_ref, self.observed_at)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Coverage:
    """What one detector looked at for one predicate, and how much it found.

    `files_eligible` is the population the detector was actually asked about --
    Java files for a Java detector -- not the whole project, so a zero here
    means "this project has nothing this detector applies to" while
    `hits == 0` with a non-zero `files_eligible` means "looked, found nothing".
    """

    detector: str
    predicate: str
    files_eligible: int = 0
    files_with_hits: int = 0
    hits: int = 0

    def record(self, found: int) -> None:
        """Account for one eligible file that produced `found` facts."""
        self.files_eligible += 1
        if found:
            self.files_with_hits += 1
            self.hits += found

    def as_row(self) -> tuple:
        return (self.detector, self.predicate, self.files_eligible, self.files_with_hits, self.hits)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def edge_subject(src: str, dst: str, kind: str, imported: str) -> str:
    return f"{src}|{dst}|{kind}|{imported}"
