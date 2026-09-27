"""What a note may not carry or repeat: credentials, an existing note's body or
title, the same content in other words."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from core.notes.store import _WORD


# Words that routinely follow "password:" or "token=" in prose about an API.
# Without them the guard fires on ordinary sentences, and a guard that fires on
# ordinary sentences gets routed around instead of obeyed.
_PLACEHOLDER_VALUES = {
    "required", "optional", "null", "none", "empty", "blank", "missing",
    "redacted", "present", "absent", "invalid", "valid", "expired", "unset",
}


# `\b` cannot open this pattern: `_` is a word character, so there is no
# boundary in front of the keyword in `aws_secret_access_key` or before the
# capital in `clientSecret`, and neither shape was ever caught. The keyword may
# also carry a suffix (`secret` + `_access_key`), and the workspace's notes
# write credentials into markdown tables as often as into assignments, so `|`
# counts as a separator.
#
# NOT the same rule as .githooks/pre-commit's SECRET_ASSIGNMENT_PATTERN, and it
# must not be described as such -- see the note above _CREDENTIAL_VALUE, which
# lists the ways the two disagree. The keyword lists differ too: `authorization`
# is here and not there.
_SECRET_ASSIGNMENT = re.compile(
    r"(?:^|[^A-Za-z0-9])"
    r"(?P<key>password|passwd|pwd|passphrase|client[-_]?secret|aws[-_]?secret[-_]?access[-_]?key"
    r"|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|authorization)"
    r"[A-Za-z0-9_-]*"
    r"\s*[=:|]\s*(?P<value>\S+)",
    re.IGNORECASE,
)


# A credential is recognised by its VALUE, not by the word in front of it.
# Reading only the keyword refused "the gateway strips Authorization: Bearer
# headers" -- a sentence, not a secret. This requires a 12+ character run of
# credential-charset characters somewhere in the value -- the same FLOOR
# .githooks/pre-commit's SECRET_ASSIGNMENT_PATTERN uses, checked with
# .search() rather than a full-string match so that one stray character
# (leading or trailing) does not exempt an otherwise credential-shaped run.
# This is NOT equivalent to the commit hook and must not be described as
# such: the hook is a three-stage pipeline (SECRET_ASSIGNMENT_PATTERN, then
# a PLACEHOLDER_PATTERN carve-out, then a REFERENCE_PATTERN carve-out that
# whitelists any value containing a credential keyword itself, e.g.
# "password = password_field.get()"), and this function reproduces none of
# the placeholder or reference stages.
#
# The two agree on the real corpus and disagree in BOTH directions on
# constructed input. Measured by running this guard and the hook itself, as
# actual shell, over the same files -- 271 notes / 13,326 lines: 0 refusals
# each, 0 disagreements. On an adversarial set, 9 disagreements:
#
#   refused here, accepted by the hook: `Authorization: Basic <base64>` and
#     `Authorization: Bearer <opaque>` (no `authorization` keyword there, and
#     it wants the 12-char run immediately after the `[:=]`); `clientSecret:
#     <secret>` (its keyword needs a separator before `secret`); a PEM block;
#     a `Bearer eyJ...` JWT (it has neither pattern).
#   refused by the hook, accepted here: `credential: acknowledge` ending in
#     a full stop -- the period pushes an 11-letter word to 12 charset
#     characters there, and the hook refused this very comment block until
#     the period was moved out of the quote; `PGPASSWORD=` and
#     `SSH_BASTION_PASS=` assignments, which the hook
#     names explicitly and _SECRET_ASSIGNMENT cannot reach at all, because
#     `(?:^|[^A-Za-z0-9])` finds no boundary inside PGPASSWORD. That last
#     pair is a pre-existing hole here, not a regression, and is left alone.
#
# So this is deliberately the simpler check the note store needs, not a
# reimplementation of the commit gate, and neither layer subsumes the other.
_CREDENTIAL_VALUE = re.compile(r"[A-Za-z0-9+/=_.~-]{12,}")


# An HTTP `Authorization:` header does not put its credential where an
# assignment does. `_SECRET_ASSIGNMENT`'s value group reads the first \S+ after
# the separator, which for header syntax is the SCHEME WORD -- so
# "Authorization: Basic <28 chars of base64>" was measured as a 5-character
# value, failed the 12-character floor and was accepted. The credential is the
# token after the scheme. `.githooks/pre-commit` misses these too (no
# `authorization` keyword, and it wants the run immediately after the `[:=]`),
# so this is the only layer that sees them, and the note store IS committed.
_AUTH_HEADER = re.compile(
    r"authorization[A-Za-z0-9_-]*\s*[=:|]\s*"
    r"(?:basic|bearer|digest|negotiate|ntlm|token|apikey)\s+"
    r"(?P<value>\S+)",
    re.IGNORECASE,
)


_CREDENTIAL_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # The keyword rule reads an assignment, so it cannot see a password carried
    # in the userinfo of a connection URI: the name in front of the `:` there is
    # the database user, not a keyword. That is exactly the shape this
    # workspace's DB notes are written in (`mongodb://user:pass@host`), and the
    # shape of the one leak .githooks/pre-commit records as having happened.
    # `$`, `<` and `{` stay out of the value charset so placeholders survive.
    ("connection string credential",
     re.compile(r"://[A-Za-z0-9_.~%-]+:[A-Za-z0-9+/=_.~%-]{8,}@")),
)


def _looks_opaque(value: str) -> bool:
    """Whether a value is a token rather than a word.

    "Authorization: Basic authentication is required" is prose;
    "Authorization: Basic Zm11c2VyOlMz..." is a credential. Length does not
    separate them -- "authentication" clears the 12-character floor on its own
    -- so shape has to: an opaque token, a UUID or a base64 blob carries a
    digit or a case change, and an English word carries neither. Base64 of any
    real `user:password` pair mixes case even when it happens to carry no
    digit.

    Used only on the `Authorization:` branch below, which accepts everything
    today, so it can only tighten. Applying it to the assignment branch as
    well would loosen that one -- an all-lowercase passphrase is a real
    password shape -- and this fix is not about relaxing what already works.
    """
    return any(char.isdigit() for char in value) or (
        value != value.lower() and value != value.upper()
    )


def _credential_value(raw_value: str) -> bool:
    """Whether a captured value is a real credential rather than a placeholder.

    Length/charset test strips only TRAILING punctuation, not leading, before
    searching -- neither extreme is right. Testing the fully stripped value let
    a leading-dot-padded run get trimmed below the 12-char floor (the
    dot_padded case in tests/test_note_cli.py). Testing raw_value un-stripped
    went too far the other way: a sentence-ending period is itself a
    credential-charset character, so an 11-character word immediately followed
    by a full stop reached 12 raw and was refused -- exactly the over-refusal
    this guard exists to avoid (see the prose cases in
    test_root_cause_prose_is_not_mistaken_for_a_credential). A trailing period
    is always sentence punctuation in this corpus, never part of a credential;
    a leading one is not assumed to be either way, so it is left alone.
    """
    value = raw_value.strip("\"'`.,;|")
    if not value or value.lower() in _PLACEHOLDER_VALUES:
        return False
    if value.startswith(("<", "$", "{")) or set(value) <= {"*", ".", "x", "X"}:
        return False
    return bool(_CREDENTIAL_VALUE.search(raw_value.rstrip("\"'`.,;|")))


def _find_credential(text: str) -> str | None:
    """Return a description of the first credential-looking thing found."""
    for description, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return description

    for match in _AUTH_HEADER.finditer(text):
        raw_value = match.group("value")
        if _credential_value(raw_value) and _looks_opaque(raw_value.strip("\"'`.,;|")):
            return "Authorization header credential"

    for match in _SECRET_ASSIGNMENT.finditer(text):
        if _credential_value(match.group("value")):
            return f"assigned secret ({match.group('key')})"
    return None


class DuplicateNoteError(ValueError):
    """A note that restates one already in the store."""


def _duplicate_escape(session: str | None) -> str:
    """The tail of a duplicate refusal: how to get past it, exactly.

    The old message ended at "read that file and edit it directly", which is a
    dead end when the writer is answering a Stop-hook block. Reproduced end to
    end: a previous session records a finding; this session meets the refusal,
    appends to the existing note as instructed, and the gate still exits 2 --
    because the gate matches on the note's `session` front-matter field, which
    the append does not touch. The only exit left was `eos note skip`, which
    records that nothing was learned: precisely the wrong answer.

    Adding this session's id to that note's front matter is what actually
    satisfies the check (verified), and nothing said so. With no session id in
    hand there is nothing to name, so that half is left off rather than
    printed with a blank after it. `eos note amend` did not exist when this
    was written; it does now, and does exactly what this message describes
    doing by hand (rewrite the body, set the session) in one command.
    """
    if not session:
        return "Append anything new to that file rather than writing a second note."
    return (
        "Append anything new to that file, then record this session against it "
        f"by adding a `session: {session}` line to its front matter. Appending "
        "alone does not satisfy the Stop-hook gate, which reads that field -- "
        "and `eos note skip` is not the way past a duplicate."
    )


def body_digest(body: str) -> str:
    """Hash of a note body with whitespace normalised.

    Byte-identical bodies already exist 15 times over in the corpus, the
    largest group spanning 12 services -- all of them generated. A writer that
    is forced to produce a note on every session will add more.
    """
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()


def normalized_title_key(title: str) -> frozenset[str]:
    """Token set of a title, case- and punctuation-insensitive, digits masked.

    Measured over the corpus this key catches all 15 genuine duplicate groups
    and false-merges 5 -- api part-notes whose titles differ only by a number,
    which is why the digits are masked rather than dropped. On the 9
    hand-written notes it produces zero collisions.
    """
    lowered = re.sub(r"\d+", "#", title.lower())
    return frozenset(w for w in re.findall(r"[a-z#]+", lowered) if len(w) > 2)


# A body sharing this share of word trigrams with an existing note says the
# same thing in other words (2.x roadmap M5). Measured before it was set: no
# pair in the host's 20 stores reaches 0.6. Shorter bodies share structure, not
# content, and are not compared.
PARAPHRASE_JACCARD = 0.8


PARAPHRASE_MIN_TRIGRAMS = 20


# A fence may be indented (inside a list). One that never closes is prose: taking
# it to the end would hide a real duplicate behind a stray marker (review 11).
_FENCED = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.M | re.S)


def _prose(text: str) -> str:
    """The note's own words: fenced blocks and `>` quotes are evidence two
    different notes may share (whole-branch review)."""
    text = _FENCED.sub(" ", text or "")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def _trigrams(text: str) -> set[tuple[str, str, str]]:
    words = [token.casefold() for token in _WORD.findall(_prose(text))]
    return {tuple(words[index:index + 3]) for index in range(len(words) - 2)}


def trigram_jaccard(first: str, second: str) -> float:
    a, b = _trigrams(first), _trigrams(second)
    return len(a & b) / len(a | b) if a and b else 0.0


def paraphrase_of(existing: list, body: str, exclude: Path | None = None):
    """The first existing note whose body this one repeats in other words, or None."""
    mine = _trigrams(body)
    if len(mine) < PARAPHRASE_MIN_TRIGRAMS:
        return None
    for note in existing:
        if exclude is not None and note.path == exclude:
            continue
        theirs = _trigrams(note.body)
        if len(theirs) >= PARAPHRASE_MIN_TRIGRAMS and len(mine & theirs) / len(mine | theirs) >= PARAPHRASE_JACCARD:
            return note
    return None
