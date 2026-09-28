"""Writing notes: add, amend, and the ledger of deliberate skips."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from core.notes.store import (
    PROVENANCES, _front_matter, _hash_file, _one_note, _resolve_scope_entry, _slug, load_notes,
    notes_dir, parse_note,
)
from core.notes.guards import (
    DuplicateNoteError, _duplicate_escape, _find_credential, body_digest,
    normalized_title_key, paraphrase_of, trigram_jaccard,
)
from core.notes.procedures import _procedure_front
from core.notes.validate import (
    _DEFECT_HEADING, _DEFECT_SECTIONS, _canonical_scope, _compose_body,
    _refuse_placeholder, _refuse_scope,
)


SKIPS_FILE = ".skips.jsonl"


def skips_path(project_root: str | Path) -> Path:
    """Where 'this session had nothing worth recording' decisions are kept.

    A dotfile inside the notes directory: load_notes globs '*.md', so this is
    invisible to every reader of the corpus and cannot dilute retrieval.
    """
    return notes_dir(project_root) / SKIPS_FILE


def record_skip(project_root: str | Path, reason: str, session: str | None = None) -> Path:
    """Record a decision not to write a note. Appends; never rewrites.

    The reason is placeholder-guarded for the same reason `add_note`'s title
    is: the gate prints `eos note skip <root> --reason '...'` and its
    `--reason` IS quoted, so unlike the `eos note add` line beside it this one
    runs verbatim to exit 0 -- silencing the gate for the whole session on a
    reason that says nothing. Found by running every command the gate prints,
    in every shape, through a real shell.

    Credential-guarded for the same reason `add_note`'s body is, and with the
    same function: this is the writer an agent is routed to under gate
    pressure, and "<connection URI> is what broke it" is exactly the shape a
    reason takes. `.skips.jsonl` is gitignored (.gitignore:191), so a
    credential written here does not travel the way one in a note would --
    that bounds the damage, it does not remove the reason to refuse it.
    """
    _refuse_placeholder(reason, "skip reason")
    found = _find_credential(reason)
    if found:
        raise ValueError(
            "Refusing to write a skip reason containing what looks like a "
            f"credential: {found}. Describe the value instead of pasting it."
        )
    path = skips_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": datetime.date.today().isoformat(),
        "session": session,
        "reason": reason,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def was_skipped(project_root: str | Path, session: str) -> bool:
    """Whether `session` already recorded a decision not to write a note.

    Hardened the same way the gate's per-file `*.md` read is, though not for
    the same reason: the notes beside it are git-tracked, this file is not
    (`.skips.jsonl` is gitignored at .gitignore:191, deliberately -- it
    records a local, momentary fact). So a bad line here is one machine's
    problem, not every machine's; it is still hand-editable, still read by the
    gate on every Stop, and still able to silence it.
    `read_text()` with no `errors=` raised UnicodeDecodeError on
    non-UTF-8 bytes, and `.get()` on whatever `json.loads` returned raised
    AttributeError on a bare JSON scalar -- neither is a ValueError, both
    escaped to the gate's broad `except`, and the gate reads any exception as
    "cannot check -> do not block". Measured: gate 2 with no skips file, gate 0
    with either of those present.

    So the whole read fails CLOSED. A lookup that cannot read its file has
    found no skip, which is what False means here; failing open would silence
    the gate over a corrupt file, which is exactly the bug. Per-line, so one
    mangled line costs that line and not the file.
    """
    path = skips_path(project_root)
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in content.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        # A scalar or a list is not a skip record. `.get()` on one raises, and
        # a raise here reads as "do not block" three frames up.
        if isinstance(entry, dict) and entry.get("session") == session:
            return True
    return False


def add_note(
    project_root: str | Path,
    kind: str,
    title: str,
    body: str | None = None,
    tags: list[str] | None = None,
    scope: list[str] | None = None,
    source: str | None = None,
    cause: str | None = None,
    solution: str | None = None,
    metric: str | None = None,
    session: str | None = None,
    procedure: str | None = None,
    execution: str | None = None,
    evidence: str | None = None,
    supersedes: str | None = None,
    provenance: str | None = None,
    agent: str | None = None,
    valid_until: str | None = None,
) -> Path:
    """Write one note and return its path.

    One file per note, named ``<YYYYMMDD>-<slug>``. A single shared store was
    the obvious alternative and is the wrong one: notes are committed, and
    every author appending to one file turns routine work into merge conflicts.

    ``session``, like `record_skip`'s, is optional and purely a marker of
    which session wrote this note -- not identity or authorship in any wider
    sense. It exists so a caller (the Stop-hook gate) can ask "did THIS
    session record something" exactly, rather than guessing from mtime: this
    store is git-tracked, so a pull, checkout, stash pop or reinstall rewrites
    every file's mtime at once, and measured on the real corpus, 252 of 271
    notes already share just three mtime minutes -- bulk filesystem events,
    not writes.
    """
    # Before composing, so a placeholder is named as what the caller typed
    # rather than as whatever _compose_body wrapped it in. The gate's fallback
    # message prints `--title '...' --body '...'` and that runs verbatim too.
    _refuse_placeholder(title, "note title")
    if body is not None:
        _refuse_placeholder(body, "note body")
    if source is not None:
        _refuse_placeholder(source, "note source")
    _refuse_scope(scope)
    if evidence is not None and kind != "lesson":
        raise ValueError(f"--evidence is only for a lesson note (ADR-024); kind {kind!r} refuses it")
    if evidence is not None:
        # Same two guards as body/source/title (N10 review): --evidence is
        # prose a person reads, and it was going straight into the file
        # unchecked -- a pasted credential or an unfilled placeholder would
        # have been the one field on a lesson note nothing screened.
        _refuse_placeholder(evidence, "note evidence")
    provenance = _check_provenance(provenance, agent)
    valid_until = _check_valid_until(valid_until)
    content = _compose_body(kind, body, cause, solution, metric)

    found = _find_credential(f"{title}\n{content}\n{evidence or ''}")
    if found:
        raise ValueError(
            f"Refusing to write a note containing what looks like a credential: {found}. "
            "Describe the value instead of pasting it."
        )

    directory = notes_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.date.today().strftime("%Y%m%d")
    # _slug strips every character that is not a letter or digit, so a title
    # can never introduce a path separator or a parent reference here.
    path = directory / f"{stamp}-{_slug(title)}.md"

    existing = load_notes(project_root)
    replaced = None
    if supersedes:
        # A rewrite reads like the note it replaces: that one is not compared.
        replaced = _one_note(existing, supersedes, "--supersedes")
        existing = [note for note in existing if note.path.name != replaced.path.name]
    new_digest = body_digest(content)
    for note in existing:
        if body_digest(note.body) == new_digest:
            raise DuplicateNoteError(
                f"This body is already recorded in {note.path} "
                f"({note.title!r}). " + _duplicate_escape(session)
            )
    twin = paraphrase_of(existing, content)
    if twin is not None:
        raise DuplicateNoteError(
            f"{twin.path} ({twin.title!r}) already says this in other words "
            f"({trigram_jaccard(content, twin.body):.0%} of its word trigrams). Amend that note, "
            f"or say what is different. " + _duplicate_escape(session)
        )
    new_key = normalized_title_key(title)
    # An empty key means the title carried no comparable signal (e.g. "AB",
    # "Q3") -- not that it matches every other title reduced the same way.
    for note in (existing if new_key else ()):
        if normalized_title_key(note.title) == new_key:
            raise DuplicateNoteError(
                f"{note.path} ({note.title!r}) already covers this title. "
                f"Choose a title that says something different, or keep this "
                f"one and stay in that note. " + _duplicate_escape(session)
            )

    if path.exists():
        raise FileExistsError(
            f"A note already exists at {path}. Notes are append-only; "
            "edit that file directly or choose a different title."
        )

    project = Path(project_root).expanduser().resolve()
    scope_hashes = (
        [_hash_file(_resolve_scope_entry(project, entry)) for entry in scope] if scope else None
    )

    document = _front_matter(
        {
            "kind": kind,
            "title": title,
            "created": stamp[:4] + "-" + stamp[4:6] + "-" + stamp[6:],
            "source": source,
            "tags": tags,
            "scope": scope,
            "scope_hashes": scope_hashes,
            "session": session,
            **(_procedure_front(procedure or _slug(title), 0, 0, None, None)
               if kind == "procedure" else {"procedure": procedure}),
            "execution": execution,
            "evidence": evidence,
            "supersedes": replaced.path.name if replaced else None,
            "provenance": provenance,
            "agent": agent,
            "valid_until": valid_until,
        }
    )
    path.write_text(f"{document}\n\n{content}\n", encoding="utf-8")
    return path


def _check_provenance(provenance: str | None, agent: str | None) -> str | None:
    """Naming an agent says an agent wrote it; a person or a generator with an
    agent's name is a contradiction, refused rather than half-recorded."""
    if provenance is None:
        return "agent" if agent else None
    if provenance not in PROVENANCES:
        raise ValueError(f"provenance {provenance!r} is not one of {', '.join(PROVENANCES)}")
    if agent and provenance != "agent":
        raise ValueError(f"an agent name ({agent!r}) with provenance {provenance!r}; "
                         "an agent's note is provenance 'agent'")
    return provenance


def _check_valid_until(valid_until: str | None) -> str | None:
    """An ISO date, today or later: a note already expired when written would
    never be offered by search."""
    if valid_until is None:
        return None
    try:
        until = datetime.date.fromisoformat(valid_until)
    except ValueError:
        raise ValueError(f"--valid-until {valid_until!r} is not a date (YYYY-MM-DD)") from None
    if until < datetime.date.today():
        raise ValueError(f"--valid-until {valid_until} is in the past; the note would never be offered")
    return until.isoformat()


def amend_note(
    note_path: str | Path,
    project_root: str | Path,
    body: str | None = None,
    reaffirm: str | None = None,
    scope: list[str] | None = None,
    session: str | None = None,
    provenance: str | None = None,
    agent: str | None = None,
    valid_until: str | None = None,
) -> Path:
    """Rewrite one note in place and re-hash its scope.

    Re-hashing is the point: `scope_hashes` is what `stale_notes` compares
    against, so this is the only operation that can clear a stale flag. That
    is also why an unchanged body is refused -- re-hashing without revising
    would close the flag without doing the work, and the gate would have
    taught agents to do exactly that.

    `--reaffirm` exists for the case where the file changed in a way that
    does not touch the note's claim. Without it the only escape would be to
    alter the body artificially, which is the dead end `skip` became in
    phase 1.

    `--scope`, on its own or alongside `--body`/`--reaffirm`, replaces the
    note's scope list wholesale (same shape as `add_note`'s `scope`). This
    is the escape for a deleted or moved scoped file: `stale_notes` reports
    that as `reason: "removed"`, and re-hashing the OLD path can only ever
    refuse -- there is no file there to hash. Re-pointing the note at the
    new location is the actual remedy, and it must not force a prose edit:
    a note whose claim is still correct needs only `--scope`, nothing else.
    Omitting `--scope` leaves the recorded scope untouched.

    One invariant governs every refusal below: an amend either records
    something a human wrote -- a revised body, or a reaffirm reason -- or it
    genuinely re-points the note at different files. It may never leave a
    previously-monitored note un-monitored, and it may never clear a stale
    flag while recording nothing. Three shapes of `--scope` violate that and
    are refused:

    - An explicitly empty `--scope`, never read as "clear the scope":
      `effective_scope` would end up `[]`, `scope_hashes` would never be
      computed, and `_front_matter` drops both `scope` and `scope_hashes`
      for being empty -- the note would be un-monitored with no trace, on
      exit 0, on the one command that is the escape from a blocking gate.
      Clearing scope entirely is not a thing this function does; if it is
      ever wanted it must be its own loud, explicit operation, not a side
      effect of a blank value.
    - A `--scope` naming the same SET of entries the note already has,
      for the same reason an unchanged `--body` is refused. Set, not list:
      see `_canonical_scope`.
    - A `--scope` that would leave no entry resolving to a real file, and a
      scope-only `--scope` that retires the stale flag of an entry which
      still exists and changed -- by keeping and re-hashing it, or by
      dropping it so the note stops claiming to be about it. All of these
      clear a flag while saying nothing; all become legal the moment the
      same call carries `--body` or `--reaffirm`, because then a human has
      written down why. Dropping an entry whose file is GONE is not one of
      them: that is the maintenance `--scope` exists for, and it needs no
      reason.

    `session` is overwritten, not appended, only when the caller supplies
    one; omitting it preserves whatever session the note already carried.
    Its single consumer is the Stop gate asking "did THIS session record
    something", so it means "the session that last wrote this note", and an
    omitted `--session` must not erase that record. Time provenance lives in
    the created/updated pair instead.

    `--provenance`, `--agent` and `--valid-until` (N8) each independently
    keep the note's existing value when their flag is not given -- a metadata
    amend that only widens `valid_until` must not clear who wrote it. Given,
    they are checked exactly as `add_note` checks them (`_check_provenance`,
    `_check_valid_until`), against the value this call is about to write, so
    `--agent` alone still implies `provenance: agent` and naming one while
    the note (new or kept) is `provenance: human` is still refused. Setting
    only these three is a valid amend on its own, with no `--body` required:
    unlike a body revision, recording who wrote a note or how long it holds
    changes nothing the duplicate or stale-flag guards above care about.

    `note_path` must resolve inside `notes_dir(project_root)` and must parse
    as a note (front matter with a non-empty kind and title). Both guard the
    same failure mode: a wrong or defaulted `--path` -- or a target that is
    not a note at all -- must refuse loudly rather than silently rewrite the
    wrong file or drop scope_hashes to null and un-monitor the note forever.
    Deliberately keyed off `notes_dir()`, not `project_root` itself: a
    project's notes commonly live outside its own tree (an FM service's
    `[knowledge] dir` points at a sibling), so containment has to follow
    wherever notes actually live, not assume it is under the project root.

    A scope entry that had a recorded hash is refused if the re-hash comes
    back None -- checked against whichever scope list ends up being written,
    old or replaced by `--scope` -- because that is "the file I described is
    gone", which must reach a human, not retire the note silently. A new
    body is also refused if it duplicates another note's body (the same
    rule `add_note` enforces, checked only for `--body`: a `--reaffirm`
    string is built as `body + "[date] Still holds: <reason>"`, so the dated
    line makes it near-unique by construction and this check could only
    misfire on a pre-existing twin the caller cannot see, on the one
    command that is the escape from a blocking gate), if it is empty, or --
    for a `defect` note -- if it drops the Root cause / Solution / Metric
    sections `_compose_body` requires. A `--reaffirm` reason must not be
    empty either, or it produces a note that still looks recorded and says
    nothing.
    """
    if body is not None and reaffirm is not None:
        raise ValueError("amend accepts only one of --body or --reaffirm")
    if (body is None and reaffirm is None and scope is None
            and provenance is None and agent is None and valid_until is None):
        raise ValueError("amend needs at least one of --body, --reaffirm, --scope, "
                         "--provenance, --agent, or --valid-until")
    if scope is not None and not scope:
        raise ValueError(
            "--scope must name at least one file; an empty value is refused "
            "rather than treated as 'clear the scope'. Clearing scope entirely "
            "is not supported today."
        )
    _refuse_scope(scope)

    project = Path(project_root).expanduser().resolve()
    path = Path(note_path).expanduser().resolve()

    directory = notes_dir(project_root)
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise ValueError(
            f"{path} is not inside the notes directory ({directory}). Check "
            "--path: it defaults to the current directory, and a wrong or "
            "omitted --path would silently stop monitoring the note instead "
            "of failing loudly."
        ) from exc

    note = parse_note(path)
    if not note.kind or not note.title:
        raise ValueError(
            f"{path} does not look like a note (front matter has no kind or "
            "title); refusing to rewrite a file amend_note did not write."
        )

    # A flag not given keeps what the note already has; given, it is checked
    # against the value the note is about to carry, so `--agent` alone still
    # forces `provenance: agent` and a contradiction with the kept (or new)
    # provenance is still refused, exactly as `add_note` refuses it.
    effective_agent = agent if agent is not None else note.agent
    effective_provenance = _check_provenance(
        provenance if provenance is not None else note.provenance, effective_agent
    )
    effective_valid_until = _check_valid_until(valid_until) if valid_until is not None else note.valid_until

    # `--agent` alone implies `provenance: agent`, so it writes both.
    metadata = [key for key, given in (
        ("provenance", provenance is not None or agent is not None),
        ("agent", agent is not None), ("valid_until", valid_until is not None)) if given]
    if metadata and body is None and reaffirm is None and scope is None:
        # Who wrote a note and how long it holds are not a revision of it:
        # `updated` keeps the day its content last changed and `session` the
        # session that last wrote it, so a migration marking a whole store
        # neither resets every note's age nor claims them all. Only the
        # named fields change, every other byte stays (M1).
        from core.lib import atomic, lock
        from core.notes.store import _set_front

        fields = {"provenance": effective_provenance, "agent": effective_agent,
                  "valid_until": effective_valid_until}
        with lock.locked(path):
            raw = path.read_text(encoding="utf-8")
            atomic.write_text(path, _set_front(raw, {key: fields[key] for key in metadata}))
        return path

    if body is not None:
        if not body.strip():
            raise ValueError("amend body must not be empty or whitespace-only")
        _refuse_placeholder(body, "amend body")
        if body_digest(body) == body_digest(note.body):
            raise ValueError(
                "The body is unchanged. Amend is for revising a note that no "
                "longer matches the code; use --reaffirm to say the note still "
                "holds despite the file changing."
            )
        if note.kind == "defect":
            missing = [
                name for name in _DEFECT_SECTIONS if not _DEFECT_HEADING[name].search(body)
            ]
            if missing:
                raise ValueError(
                    "A defect note must keep its Root cause, Solution and Metric "
                    f"sections; missing: {', '.join(missing)}"
                )
        if note.kind == "procedure":
            # The same checks as when it was written: steps, and the Rules cap
            # that keeps the brief's exemption bounded.
            _compose_body("procedure", body, None, None, None)
        new_digest = body_digest(body)
        for other in load_notes(project_root):
            if other.path == path:
                continue
            if body_digest(other.body) == new_digest:
                raise DuplicateNoteError(
                    f"This body already appears in {other.path} ({other.title!r}). "
                    "Choose a body that says something that note does not, or "
                    "revise that note instead."
                )
        content = body.strip()
    elif reaffirm is not None:
        if not reaffirm.strip():
            raise ValueError("amend reaffirm reason must not be empty or whitespace-only")
        _refuse_placeholder(reaffirm, "amend reaffirm reason")
        stamp = datetime.date.today().isoformat()
        content = f"{note.body.strip()}\n\n[{stamp}] Still holds: {reaffirm.strip()}"
    else:
        # Reached with `scope` still None for a metadata-only amend (N8):
        # --provenance/--agent/--valid-until alone, no --body, --reaffirm or
        # --scope. Nothing about scope was asked to change, so there is
        # nothing to compare here.
        if scope is not None and _canonical_scope(scope) == _canonical_scope(note.scope):
            raise ValueError(
                "--scope names the same set of entries the note already has "
                "(reordering them, or repeating one, re-points nothing), so "
                "re-hashing would clear the stale flag while recording "
                "neither a revision nor a reason. Use --body to revise what "
                "the note claims, or --reaffirm to record why it still holds "
                "despite the change."
            )
        content = note.body.strip()

    found = _find_credential(f"{note.title}\n{content}")
    if found:
        raise ValueError(
            f"Refusing to write a note containing what looks like a credential: {found}. "
            "Describe the value instead of pasting it."
        )

    if body is None and reaffirm is None and scope is None:
        # A metadata-only amend (--provenance/--agent/--valid-until alone,
        # N8): nothing about scope was asked to change, so it is not
        # re-hashed or re-validated either. Falling into the block below
        # would re-hash every scope entry regardless, and if one had changed
        # for reasons this amend never claims about, it would refuse the
        # whole call -- coupling a metadata edit to an unrelated scope guard
        # the docstring above says a metadata-only amend does not touch.
        effective_scope = note.scope
        scope_hashes = note.scope_hashes
    else:
        effective_scope = scope if scope is not None else note.scope
        # A --body or --reaffirm in the same call is the human record that buys
        # every transition below. A scope-only amend has to stand on the scope
        # change alone, and there are things a scope change cannot buy.
        records_a_reason = body is not None or reaffirm is not None
        # Keyed by the entry STRING, not position: once --scope can replace the
        # list wholesale, index i in the new list has no relationship to index i
        # in the old one. A positional check refused a harmless reorder (claiming
        # an entry had a recorded hash it never had), silently skipped checking
        # every entry past the shortest of the two lists (b and c in [a,b,c] -> [a]
        # were dropped with no check at all), and let a null-hash position wave
        # through a new entry that resolves to nothing.
        old_hash_by_entry = dict(zip(note.scope, note.scope_hashes))

        scope_hashes = None
        if effective_scope:
            scope_hashes = []
            for entry in effective_scope:
                new_hash = _hash_file(_resolve_scope_entry(project, entry))
                old_hash = old_hash_by_entry.get(entry)
                if old_hash is not None and new_hash is None:
                    raise ValueError(
                        f"Scope entry {entry!r} no longer resolves to a file, though "
                        "it had a recorded hash. Either the file was deleted or moved "
                        "-- fix the note's `scope:` to point at its new location -- "
                        "or --path names the wrong project root."
                    )
                scope_hashes.append(new_hash)

        # stale_notes skips a null hash, so a note left with no non-null hash can
        # never be flagged again. Reaching that state through `--scope` -- naming
        # a class, a directory, a typo -- is the same permanent un-monitoring an
        # empty `--scope` is refused for, with a `scope:` line as its only trace.
        # A human-written reason buys the transition, because code a note
        # described genuinely can be deleted; nothing else does. Notes that never
        # had a resolving entry (five in the real corpus are scoped to class
        # names) lose nothing here and are not refused.
        was_monitored = any(hash_ is not None for hash_ in note.scope_hashes)
        if (
            not records_a_reason
            and was_monitored
            and not any(hash_ is not None for hash_ in scope_hashes or ())
        ):
            monitored = ", ".join(
                repr(entry) for entry, hash_ in old_hash_by_entry.items() if hash_ is not None
            )
            raise ValueError(
                "This amend would leave the note with no scope entry that resolves "
                "to a file, so nothing could ever flag it stale again; today it is "
                f"monitored through {monitored}. Point --scope at a file that exists, "
                "or -- if the code this note described is gone -- record that with "
                "--body or --reaffirm in the same call."
            )

        # One rule with two arms, and the last of the three because it is the
        # least severe: a scope-only amend may not retire the stale flag of an
        # entry that had a recorded hash, still resolves to a real file, and now
        # hashes differently -- whether it KEEPS that entry (and re-hashes it in
        # place, which appending one unrelated file to the scope was enough to
        # reach) or DROPS it (the note quietly stops claiming to be about the file
        # that moved under it). Both clear a flag and record nothing about the
        # code. The dropping arm is the lesson `eos note skip` taught in phase 1:
        # under gate pressure agents find the cheapest exit and the cheapest exit
        # becomes the default, and "I am no longer about that file" costs one flag
        # and says nothing. If a narrowing is genuine, the sentence explaining it
        # is the sentence worth having in the note anyway.
        #
        # Dropping an entry whose file is GONE is the opposite: that is the
        # maintenance --scope exists for (round 2 added it for exactly a deleted
        # or moved file) and it stays free of any reason. So does dropping an
        # entry that never resolved -- it was never monitored, so nothing retires.
        if not records_a_reason:
            for entry, old_hash in old_hash_by_entry.items():
                if old_hash is None:
                    continue
                try:
                    current = _hash_file(_resolve_scope_entry(project, entry))
                except ValueError:
                    # An entry that cannot be resolved at all any more (an
                    # `@parent:` label the config no longer configures) is in the
                    # same position as a deleted file: there is nothing to compare
                    # and re-pointing away from it is the remedy, not an evasion.
                    # An entry the caller KEEPS still raises this, unchanged, from
                    # the hashing loop above.
                    continue
                if current is None or current == old_hash:
                    continue
                action = (
                    "re-hash it in place"
                    if entry in effective_scope
                    else "drop it from the scope"
                )
                raise ValueError(
                    f"Scope entry {entry!r} changed since this note was recorded and "
                    f"the file still exists, so a scope-only amend cannot {action}: "
                    "that clears the stale flag while recording nothing about the "
                    "change. Add --reaffirm to say the note still holds, or --body to "
                    "revise what it claims. (Dropping an entry whose file is gone "
                    "needs no reason.)"
                )

    document = _front_matter(
        {
            "kind": note.kind,
            "title": note.title,
            "created": note.created,
            "updated": datetime.date.today().isoformat(),
            "source": note.source,
            "tags": note.tags,
            "scope": effective_scope,
            "scope_hashes": scope_hashes,
            "session": session if session is not None else note.session,
            # A revised step list must not reset a procedure's history to zero.
            **(_procedure_front(note.procedure or _slug(note.title), note.runs_ok or 0,
                                note.runs_failed or 0, note.last_verified, note.last_execution)
               if note.kind == "procedure" else {"procedure": note.procedure}),
            # A lesson keeps the run that taught it, and its evidence, through any rewrite.
            "execution": note.execution,
            "evidence": note.evidence,
            "supersedes": note.supersedes,
            # --provenance/--agent/--valid-until (N8): the value this call
            # computed above, which is the note's own kept value when the
            # flag was not given.
            "provenance": effective_provenance,
            "agent": effective_agent,
            "valid_until": effective_valid_until,
        }
    )
    path.write_text(f"{document}\n\n{content}\n", encoding="utf-8")
    return path
