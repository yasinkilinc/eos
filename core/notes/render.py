"""Ranking notes against a query and rendering them into a context section."""
from __future__ import annotations

import math
import re
from pathlib import Path

from core.notes.store import (
    Note, _WORD, expired, is_bulk_index, load_generated, load_notes, note_synonyms, shared_notes, superseded,
)


# Similarity carried over from the one earlier iteration that actually ran a
# note store: word overlap weighted 0.6, tag overlap 0.4, and anything under
# the threshold treated as no match. Deliberately not embeddings — the runtime
# is copied into arbitrary projects and has to stay stdlib-only and offline.
_RELEVANCE_THRESHOLD = 0.15


# Relative to the best match, not absolute. The threshold above decides what
# counts as a match at all; this decides how far below the answer a result may
# sit and still be worth showing. Without it a vague question returned the
# whole neighbourhood -- "deploy" gave 14 notes and "deploy env1" 19 on a
# 106-note service, and a fresh session could not tell a strong match from a
# weak one because no score is printed. Swept against the 20-question golden
# set: recall@1 and recall@5 are unchanged at every floor up to 0.5; at 0.4 the
# mean result count falls 4.3 -> 2.8 and those two queries return 3 each.
# 0.4 rather than 0.5 keeps a margin under a floor that already cut to one.
SCORE_FLOOR = 0.4


# An issue key or any `NAME-123` identifier: kept whole, because its halves
# are not evidence. Split, "PROJ-1700" matched every note about PROJ-1588
# through the shared prefix alone.
_KEY = re.compile(r"(?<![\w-])([^\W\d_][^\W_]*)-(\d+)(?![\w-])")


def _words(text: str) -> set[str]:
    """The words a query or a note is matched on.

    Longer than two characters, casefolded. Two exceptions at two letters: a
    token written in capitals (PR, CI, DB) is an acronym, and a token with a
    letter outside ASCII ("aç" is Turkish for "open") is a word of another
    language, where the short ones are often the verbs -- English filler at
    that length is all ASCII. A word no note contains weighs nothing
    (`word_weights`), so a short stopword kept here costs no ranking. An
    identifier like PROJ-1700 counts as itself and its number, not its prefix.
    """
    words = set()
    prefixes = set()
    for match in _KEY.finditer(text):
        words.add(f"{match.group(1)}-{match.group(2)}".casefold())
        prefixes.add(match.group(1).casefold())
    for token in _WORD.findall(text):
        word = token.casefold()
        if len(word) > 2 or (len(token) == 2 and token.isalpha()
                             and (token.isupper() or not token.isascii())):
            words.add(word)
    return words - prefixes


def synonym_groups(groups: object) -> dict[str, str]:
    """Word -> the key of its synonym group, from a list of word lists.

    Each group becomes one concept: its members and a query naming any of them
    are all read as the same synthetic key, so a note written with "confluence"
    answers a question asked with "wiki", and naming both counts once. A member
    is one word as `_words` reads it; a phrase or an empty member is dropped and
    the rest of its group kept. A word already in an earlier group stays there
    -- groups are not merged, so one careless entry cannot join two meanings.
    Anything that is not a list of lists is no table at all.
    """
    canon: dict[str, str] = {}
    if not isinstance(groups, list):
        return canon
    for index, group in enumerate(groups):
        if not isinstance(group, list):
            continue
        members = []
        for member in group:
            words = _words(member) if isinstance(member, str) else set()
            if len(words) == 1 and len(member.split()) == 1:
                members.extend(words)
        members = [word for word in dict.fromkeys(members) if word not in canon]
        if len(members) < 2:
            continue
        for word in members:
            canon[word] = f"\x00synonym-{index}"
    return canon


def canonical_words(words: set[str], canon: dict[str, str] | None) -> set[str]:
    """`words` with every synonym-group member read as its group's key."""
    if not canon:
        return words
    return {canon.get(word, word) for word in words}


def _synonym_terms(words: set[str], canon: dict[str, str] | None) -> set[str]:
    """`words` plus every other spelling in the same synonym group.

    A note ranked relevant through a synonym alone (a query naming "wiki",
    a note saying only "confluence") has none of the query's literal words
    in its body, so narrowing by `words` finds no hit line and falls back to
    the whole body. Narrowing needs the sibling spellings actually written,
    not the group's synthetic key -- `canonical_words` is for scoring, this
    is for finding the line.
    """
    if not canon:
        return words
    groups: dict[str, set[str]] = {}
    for word, key in canon.items():
        groups.setdefault(key, set()).add(word)
    expanded = set(words)
    for word in words:
        key = canon.get(word)
        if key:
            expanded |= groups[key]
    return expanded


def word_weights(corpus: list[Note], query_words: set[str],
                 *, canon: dict[str, str] | None = None) -> dict[str, float]:
    """How much each query word is worth here: `log(notes / notes using it)`.

    Without this every word counts the same, and a question asked in a sentence
    is decided by its filler. Measured on a 106-note corpus with a 20-question
    golden set: "which Java class decides the order of the steps" could not find
    the note that answers it inside ten results, because "which", "class",
    "order" and "steps" are in half the corpus and outvoted the rest.

    A word in every note scores 0 and stops voting, which is the intent. A word
    in no note scores 0 too -- it cannot discriminate either, and treating an
    unmatched word as evidence of anything is what made long queries worse than
    short ones. When every word of a query is worthless the caller falls back to
    equal weights, so a search for a single ubiquitous word still answers with
    what it matched rather than with nothing.

    With `canon` (see `synonym_groups`) a group counts the notes using any of
    its members, so the concept is worth what the group is, not one spelling.
    """
    if not corpus or not query_words:
        return {}
    total = len(corpus)
    seen = {word: 0 for word in query_words}
    for note in corpus:
        present = canonical_words(
            _words(note.title) | _words(" ".join(note.tags)) | _words(note.body), canon)
        for word in query_words & present:
            seen[word] += 1
    return {word: math.log(total / count) if count else 0.0 for word, count in seen.items()}


def relevance(note: Note, query_words: set[str],
              weights: dict[str, float] | None = None, *, body: bool = True,
              canon: dict[str, str] | None = None) -> float:
    """How well one note answers a query, in 0.0 - 1.0.

    Scores how much of the *query* the note covers, rather than the Jaccard
    ratio over the union of both word sets. Jaccard divided by the note's own
    length, so a long descriptive title was a penalty: a note titled
    "AuditGroup must not call sink.record before super().invoke" scored 0.055
    for the query "AuditGroup" against a 0.15 threshold, and could not be found
    by a word out of its own title. Across the real corpus a term appearing in
    113 note bodies scored 0.000 everywhere.

    The body is included at a lower weight than the title. Excluding it
    entirely was the original fix for a different problem -- a paragraph of
    root-cause prose diluting a short query -- which coverage scoring does not
    have, because the denominator is the query and never the note.

    `weights` says what each query word is worth (see `word_weights`); without
    it every word is worth the same, which is the behaviour this had before a
    golden set showed what it costs. Coverage stays a ratio either way, so the
    0.0-1.0 range and the threshold mean what they always did.

    `body=False` scores the title and tags alone, for callers that put the
    result in front of a session nobody asked (the task brief): there one
    word of prose is not enough to speak.

    `canon` reads synonym-group members as their group's key, on the note's
    side; the caller passes query words already read the same way.
    """
    if not query_words:
        return 0.0

    title_words = canonical_words(_words(note.title), canon)
    tag_words = canonical_words(_words(" ".join(note.tags)), canon)
    body_words = canonical_words(_words(note.body), canon) if body else set()

    if weights:
        budget = sum(weights.get(word, 0.0) for word in query_words)
    else:
        budget = float(len(query_words))
    if budget <= 0:
        return 0.0

    def coverage(words: set[str]) -> float:
        if not words:
            return 0.0
        matched = words & query_words
        if weights:
            return sum(weights.get(word, 0.0) for word in matched) / budget
        return len(matched) / budget

    return min(
        1.0,
        0.5 * coverage(title_words)
        + 0.3 * coverage(tag_words)
        + 0.2 * coverage(body_words),
    )


def search_notes(project_root: str | Path, query: str, limit: int | None = None, *,
                 generated: bool = False) -> list[Note]:
    """Notes relevant to `query`, most relevant first, with the project's synonym
    groups; `generated` ranks the generator notes with them (M3)."""
    corpus = (load_notes(project_root) + shared_notes(project_root)
              + (load_generated(project_root) if generated else []))
    return rank(corpus, query, limit, synonyms=note_synonyms(project_root))


def rank(corpus: list[Note], query: str, limit: int | None = None,
         *, synonyms: object = None) -> list[Note]:
    """The one note scorer (2.x roadmap F3): `corpus` ranked against `query`.

    `search_notes` passes one project's notes; a host searching several stores
    passes their union, so word weights are computed over what is searched.
    `synonyms` is a list of word groups (`[notes] synonyms`); without it a
    query is matched on its own words only.
    """
    canon = synonym_groups(synonyms) if synonyms else {}
    query_words = canonical_words(_words(query), canon)
    replaced = superseded(corpus)
    # Replaced and expired notes stay on disk as the record; search stops offering them.
    corpus = [note for note in corpus if note.path.name not in replaced and not expired(note)]
    weights = word_weights(corpus, query_words, canon=canon)
    # Every word ubiquitous (or the query is one such word): weighting has
    # nothing left to say, and falling through to equal weights answers with
    # what matched instead of with nothing.
    if not any(weight > 0 for weight in weights.values()):
        weights = None
    scored = [(relevance(note, query_words, weights, canon=canon), note) for note in corpus]
    matches = [pair for pair in scored if pair[0] >= _RELEVANCE_THRESHOLD]
    matches.sort(key=lambda pair: (-pair[0], pair[1].path.name))
    if matches:
        floor = matches[0][0] * SCORE_FLOOR
        matches = [pair for pair in matches if pair[0] >= floor]
    ranked = [note for _, note in matches]
    return ranked[:limit] if limit else ranked


# How many titles to list for notes that did not fit. Sized so the index costs
# roughly one note's worth of characters.
_OMITTED_TITLE_LIMIT = 25


# Only a finding is narrowed (2.x roadmap C3b). A lesson, a decision and a defect
# are worth re-reading only with all their sections (ADR-024), and a procedure's
# Rules come whole (report §17, C2) -- narrowing would keep the line that matched
# and drop the part that says what to do.
NARROWED_KINDS = ("finding",)


def task_terms(project_root: str | Path, text: str) -> list[str]:
    """The words a task's text narrows note bodies by, this project's synonyms included."""
    return word_terms(project_root, _words(text))


def word_terms(project_root: str | Path, words: set[str]) -> list[str]:
    """`task_terms` for words `_words` already read. Reading them again would lose
    a two-letter acronym: casefolded, `CI` is no longer written in capitals."""
    return sorted(_synonym_terms(set(words), synonym_groups(note_synonyms(project_root))))


def narrowed_body(note: Note, terms: list[str] | None) -> str:
    """A finding's body cut to the lines `terms` hit when it is long; anything else whole."""
    body = note.body
    if terms and note.kind in NARROWED_KINDS:
        from core.context import narrow

        if len(body) > narrow.LIMIT_CHARS:
            found = narrow.narrow_by_terms(body, terms)
            if found is not None:
                left = f"; {found.omitted} more hit line(s) left out" if found.omitted else ""
                body = (f"{found.text}\n_Narrowed to the lines the task's terms hit "
                        f"({', '.join(found.terms)}){left}; `eos note show <project> {note.path.name}` "
                        "reads all of it._")
    return body


def _render_note(note: Note, terms: list[str] | None = None) -> str:
    header = f"### {note.title} ({note.kind})"
    meta_bits = []
    if note.tags:
        meta_bits.append("tags: " + ", ".join(note.tags))
    if note.source:
        meta_bits.append(f"source: {note.source}")
    meta = f"_{' | '.join(meta_bits)}_\n\n" if meta_bits else ""
    return f"{header}\n\n{meta}{narrowed_body(note, terms)}"


def render_context_section(
    project_root: str | Path, query: str | None, max_chars: int
) -> str:
    """Render a bounded '## Accumulated Knowledge' block for get_context.

    With a query, notes are ranked by relevance and unrelated ones are left
    out entirely rather than padded in at low rank; without one (plain
    `eos context` has no task to score against) the most recently written
    notes lead, on the theory that recency is the next best signal absent a
    query. Either way, notes are dropped whole once the budget is spent, and
    the ones that did not fit are listed by title, so a trimmed context does
    not look like a complete one.
    """
    if query:
        candidates = search_notes(project_root, query)
    else:
        corpus = load_notes(project_root) + shared_notes(project_root)
        replaced = superseded(corpus)
        candidates = [n for n in sorted(corpus, key=lambda n: n.path.name, reverse=True)
                      if n.path.name not in replaced and not expired(n)]
    if not candidates:
        return ""

    # C4: a note already contained, whole and normalised, in a file the
    # harness always loads (`[ai] loaded`) is left out -- the harness has it
    # in context already, so delivering it again is a token spent on nothing
    # new. Unset (the common case), `already_loaded_text` is "" and every
    # note passes through exactly as it did before this existed.
    from core.context import dedup

    loaded_text = dedup.always_loaded_text(project_root)
    left_out = 0
    if loaded_text:
        kept = [n for n in candidates if not dedup.already_loaded(n.body, loaded_text)]
        left_out = len(candidates) - len(kept)
        candidates = kept
    if not candidates:
        return (f"## Accumulated Knowledge\n\n_{left_out} note(s) left out: already in an "
                "always-loaded file._") if left_out else ""

    # Findings lead, indexes trail. Without this a bulk-generated inventory
    # entry outranks a trap someone learned the hard way purely by being newer,
    # and since the budget fits one or two full notes it can take the only slot.
    # Measured on a 64-note service 2026-09-18: 23 of the 64 were generated.
    # With a query, relevance already decides the order and must not be undone.
    #
    # `is_bulk_index`, not `is_generated`: a journey note is written by a script
    # out of prose a person wrote, and sorting it with the endpoint tables put
    # 56 of this workspace's best notes behind every note carrying a ticket key.
    if not query:
        candidates.sort(key=is_bulk_index)

    lines = ["## Accumulated Knowledge"]
    included = 0
    terms = task_terms(project_root, query) if query else None
    for note in candidates:
        entry = _render_note(note, terms)
        projected = len("\n\n".join(lines + [entry]))
        if included > 0 and projected > max_chars:
            break
        lines.append(entry)
        included += 1

    if left_out:
        lines.append(f"_{left_out} note(s) left out: already in an always-loaded file._")

    # Titles for what did not fit, not just a count. A note the reader cannot
    # see the existence of cannot be asked for, and the budget fits only one or
    # two full notes against a corpus of sixty -- so the old
    # "63 more note(s) omitted" line hid the entire corpus behind a number.
    # A title line is ~80 characters against a note's ~3,000, so the index is
    # affordable where the bodies are not; it is capped and reports its own
    # remainder so a trimmed list still says what it left out.
    remaining = candidates[included:]
    if remaining:
        lines.append(
            f"_{len(remaining)} more note(s) did not fit. "
            "Read one with `eos note show <project> <name>`._"
        )
        # The index obeys the same budget the bodies do. It is allowed to be
        # partial -- a truncated list still names notes the reader could not
        # otherwise know exist -- but it may not be the thing that blows the
        # budget, which would make a small context larger than a large one.
        listed = 0
        for note in remaining[:_OMITTED_TITLE_LIMIT]:
            entry = f"- {note.title}"
            if len("\n\n".join(lines + [entry])) > max_chars:
                break
            lines.append(entry)
            listed += 1
        if len(remaining) > listed:
            tail = f"- _…and {len(remaining) - listed} more_"
            if len("\n\n".join(lines + [tail])) <= max_chars or listed == 0:
                lines.append(tail)

    # No hard truncation here: slicing the joined text by character count can
    # as easily eat the "N more note(s) did not fit" line above as it can eat note
    # prose, which would silently hide exactly the fact this function exists
    # to report. A note whose own rendering exceeds max_chars is the one
    # allowed overage — one full note beats a mid-sentence cut or a lost
    # drop-count, and it is still exactly one note, not an unbounded list.
    return "\n\n".join(lines)
