"""Notes linked to notes: edges derived on every build, never written by hand.

Measured on the host: 1 of 238 notes cites another by name, while 40 share a
ticket key with another. So the edges are derived, deterministically:

  supersedes  a note replaces another (`eos note add --supersedes`)
  cites    a body names another note: `note:<file stem>` or its file name
  lesson   a lesson note names the procedure it was learned in
  scope    two notes watch the same file
  ticket   two notes name the same ticket key (the project's pattern)

A key or a file shared by more than MAX_SHARED notes links nothing: shared by
that many, it says nothing about any two of them. Pure: notes in, edges out.
The brief and `eos note related` compute them from the notes each time (25 ms on
238 notes), so they are current even when the index is not; the index keeps a
copy in `note_edge` for `eos query` joins.
"""
from __future__ import annotations

import collections
import dataclasses
import re

MAX_SHARED = 8
WEIGHT = {"supersedes": 3, "cites": 3, "lesson": 3, "scope": 2, "ticket": 1}
_STEM = re.compile(r"(?:note:)?\b([0-9]{8}-[a-z0-9][a-z0-9-]*)(?:\.md)?\b")


@dataclasses.dataclass(frozen=True)
class Edge:
    src: str          # note file name
    dst: str
    kind: str
    via: str | None   # the ticket key or scope file; None for cites and lesson


def _shared(groups: dict[str, list[str]], kind: str) -> list[Edge]:
    found = []
    for via, files in sorted(groups.items()):
        files = sorted(set(files))
        if 2 <= len(files) <= MAX_SHARED:
            found += [Edge(a, b, kind, via) for i, a in enumerate(files) for b in files[i + 1:]]
    return found


def _scope_key(project_root, entry: str) -> str:
    """One file, however it was written (review 13): relative, `./`, absolute."""
    if project_root is None:
        return entry
    try:
        from pathlib import Path

        from core import notes

        project = Path(project_root).expanduser().resolve()
        anchor = notes._scope_anchor(project, entry)
        if anchor is None:
            return entry
        resolved = anchor[1].resolve()
        # Shown as the reason for a link: relative inside the project.
        return resolved.relative_to(project).as_posix() if resolved.is_relative_to(project) else str(resolved)
    except Exception:  # noqa: BLE001 - an entry that cannot be resolved keys by its text
        return entry


def edges(corpus, ticket_pattern: re.Pattern | None, project_root=None) -> list[Edge]:
    by_stem = {note.path.stem: note.path.name for note in corpus}
    by_slug = {note.procedure: note.path.name for note in corpus if note.kind == "procedure" and note.procedure}
    found: list[Edge] = []
    tickets: dict[str, list[str]] = collections.defaultdict(list)
    scopes: dict[str, list[str]] = collections.defaultdict(list)
    for note in corpus:
        name = note.path.name
        for stem in dict.fromkeys(_STEM.findall(note.body or "")):
            target = by_stem.get(stem)
            if target and target != name:
                found.append(Edge(name, target, "cites", None))
        if note.supersedes and note.supersedes in by_stem.values() and note.supersedes != name:
            found.append(Edge(name, note.supersedes, "supersedes", None))
        if note.kind == "lesson" and note.procedure in by_slug:
            found.append(Edge(name, by_slug[note.procedure], "lesson", None))
        for entry in dict.fromkeys(note.scope or []):
            scopes[_scope_key(project_root, entry)].append(name)
        if ticket_pattern is not None:
            from core import notes as notes_module

            # Not a key quoted in a code block or a `>` quote (review 13).
            text = f"{note.title} {' '.join(note.tags or [])} {notes_module._prose(note.body or '')}"
            for key in dict.fromkeys(ticket_pattern.findall(text)):
                tickets[key].append(name)
    return found + _shared(scopes, "scope") + _shared(tickets, "ticket")


def _why(edge: Edge, toward_self: bool) -> str:
    if edge.kind == "cites":
        return "cited by it" if not toward_self else "cites it"
    if edge.kind == "supersedes":
        return "replaces this note" if toward_self else "replaced by this note"
    if edge.kind == "lesson":
        return "lesson of this procedure" if toward_self else "the procedure it was learned in"
    return f"{edge.kind} {edge.via}"


def related(corpus, file: str, ticket_pattern: re.Pattern | None, limit: int = 5,
            project_root=None) -> list[tuple]:
    """(note, why) for the notes linked to `file`, strongest link first."""
    by_name = {note.path.name: note for note in corpus}
    score: dict[str, int] = collections.Counter()
    reason: dict[str, tuple[int, str]] = {}
    for edge in edges(corpus, ticket_pattern, project_root):
        if file not in (edge.src, edge.dst):
            continue
        other = edge.dst if edge.src == file else edge.src
        weight = WEIGHT[edge.kind]
        score[other] += weight
        why = _why(edge, toward_self=edge.dst == file)
        if other not in reason or weight > reason[other][0]:
            reason[other] = (weight, why)
    ranked = sorted(score, key=lambda name: (-score[name], by_name[name].title))
    return [(by_name[name], reason[name][1]) for name in ranked[:limit]]


def pattern_for(project_root) -> re.Pattern | None:
    try:
        from pathlib import Path

        from core.index import _ticket_pattern

        return _ticket_pattern(Path(project_root).expanduser().resolve())
    except Exception:  # noqa: BLE001 - a bad pattern costs the ticket edges, not the graph
        return None
