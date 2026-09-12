"""CLI surface for the note store: `eos note add|list|search|skip`.

Subprocess, not direct import — this is the interface an agent actually
drives (and what the MCP tools will shell out through), so it is what proves
argparse wiring, not just core.notes itself.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _run(args, **kw):
    import subprocess

    return subprocess.run(EOS + args, capture_output=True, text=True, cwd=REPO, **kw)


def _init(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    r = _run(["init", str(proj)])
    assert r.returncode == 0, r.stderr
    return proj


def test_note_add_writes_a_file_and_prints_its_path(tmp_path):
    proj = _init(tmp_path)

    r = _run([
        "note", "add", str(proj),
        "--kind", "finding",
        "--title", "Cache warms on boot",
        "--body", "The first request pays for it otherwise.",
        "--tags", "cache, startup",
    ])

    assert r.returncode == 0, r.stderr
    written = list((proj / ".eos" / "knowledge").glob("*.md"))
    assert len(written) == 1
    assert written[0].name in r.stdout


def test_note_add_rejects_an_incomplete_defect_without_writing_anything(tmp_path):
    proj = _init(tmp_path)

    r = _run([
        "note", "add", str(proj),
        "--kind", "defect", "--title", "Pool exhausted",
    ])

    assert r.returncode != 0
    assert "cause" in r.stderr
    assert list((proj / ".eos" / "knowledge").glob("*.md")) == []


def test_note_add_refuses_a_scope_that_names_nothing(tmp_path):
    """`--scope ,` splits to nothing and used to be passed through as "no
    scope": exit 0, and a note born with neither `scope:` nor
    `scope_hashes:` -- un-monitorable for the rest of its life, from a typo,
    on the command that wrote every note in the corpus. Omitting --scope
    entirely stays legal (most notes have no scope); only a value that was
    typed and names nothing is refused."""
    proj = _init(tmp_path)

    r = _run([
        "note", "add", str(proj),
        "--kind", "finding",
        "--title", "Cache warms on boot",
        "--body", "The first request pays for it otherwise.",
        "--scope", ",",
    ])

    assert r.returncode != 0
    assert "scope" in r.stderr
    assert list((proj / ".eos" / "knowledge").glob("*.md")) == [], "nothing written"


def test_note_list_shows_a_previously_added_note(tmp_path):
    proj = _init(tmp_path)
    _run([
        "note", "add", str(proj),
        "--kind", "finding", "--title", "Cache warms on boot", "--body", "Slow first hit.",
    ])

    r = _run(["note", "list", str(proj)])

    assert r.returncode == 0, r.stderr
    assert "Cache warms on boot" in r.stdout


def test_note_search_finds_the_relevant_note_by_query(tmp_path):
    proj = _init(tmp_path)
    _run([
        "note", "add", str(proj),
        "--kind", "finding", "--title", "Connection pool exhausted",
        "--body", "The pool ran out of connections during deposits.",
    ])
    _run([
        "note", "add", str(proj),
        "--kind", "finding", "--title", "Unrelated topic", "--body", "Nothing to do with databases.",
    ])

    r = _run(["note", "search", str(proj), "database connection pool"])

    assert r.returncode == 0, r.stderr
    assert "Connection pool exhausted" in r.stdout
    assert "Unrelated topic" not in r.stdout


def test_note_skip_records_reason_and_session(tmp_path):
    proj = _init(tmp_path)

    r = _run(["note", "skip", str(proj), "--reason", "routine dependency bump", "--session", "s1"])

    assert r.returncode == 0, r.stderr
    skips = proj / ".eos" / "knowledge" / ".skips.jsonl"
    entry = json.loads(skips.read_text(encoding="utf-8").strip())
    assert entry["reason"] == "routine dependency bump"
    assert entry["session"] == "s1"
    assert str(skips) in r.stdout


def test_note_skip_accepts_path_via_flag_like_its_siblings(tmp_path):
    proj = _init(tmp_path)

    r = _run(["note", "skip", "--path", str(proj), "--reason", "routine dependency bump"])

    assert r.returncode == 0, r.stderr
    skips = proj / ".eos" / "knowledge" / ".skips.jsonl"
    entry = json.loads(skips.read_text(encoding="utf-8").strip())
    assert entry["reason"] == "routine dependency bump"


def _note(**kw):
    from pathlib import Path

    from core.notes import Note

    defaults = dict(
        path=Path("n.md"), kind="finding", title="", created="2026-09-05T00:00:00Z",
        source=None, tags=[], scope=[], scope_hashes=[], body="",
    )
    defaults.update(kw)
    return Note(**defaults)


def test_a_note_is_findable_by_a_word_from_its_own_title():
    """Jaccard over the union punished every descriptive title: a note titled
    "AuditGroup must not call sink.record before super().invoke" scored 0.055
    for the query "AuditGroup" against a 0.15 threshold -- it could not be
    found by a word out of its own title."""
    from core import notes

    note = _note(
        title="AuditGroup must not call sink.record before super().invoke",
        tags=["audit", "click"],
        body="The override double-counts every record it forwards.",
    )

    assert notes.relevance(note, notes._words("AuditGroup")) >= notes._RELEVANCE_THRESHOLD
    assert notes.relevance(note, notes._words("sink record")) >= notes._RELEVANCE_THRESHOLD


def test_a_term_that_appears_only_in_the_body_is_findable():
    from core import notes

    note = _note(title="Wallet balance returns 400", tags=["topup"],
                 body="The domain-config row WLT_BLNC was repointed to i2i.")

    assert notes.relevance(note, notes._words("WLT_BLNC")) >= notes._RELEVANCE_THRESHOLD


def test_an_unrelated_query_still_scores_below_the_threshold():
    from core import notes

    note = _note(title="Wallet balance returns 400", tags=["topup"],
                 body="The domain-config row was repointed.")

    assert notes.relevance(note, notes._words("kafka partition")) < notes._RELEVANCE_THRESHOLD


def test_root_cause_prose_is_not_mistaken_for_a_credential():
    """The guard read the keyword and ignored the value, so ordinary sentences
    about auth were refused -- and a forced writer would meet that on its first
    note."""
    from core import notes

    allowed = [
        "The gateway strips Authorization: Bearer headers before forwarding.",
        "The client sends apiKey=tenantId which the parent rejects.",
        "auth.token: refreshed on 401 by the retry filter.",
        "password: <redacted>",
        "token = ${VAULT_TOKEN}",
        # A sentence-ending period is itself a credential-charset character:
        # an earlier version of this check tested the fully raw value and
        # counted it, so an 11-character word plus its full stop reached the
        # 12-char floor and was refused -- exactly the over-refusal this task
        # exists to stop. Built via concatenation, not one literal, so the
        # word and its period never sit next to a keyword+assignment as an
        # unbroken 12+ run in this file's own source text (.githooks/pre-commit
        # would refuse that the same way it refused an earlier draft of the
        # secret fixtures below).
        "The retry filter logs credential: " + "acknowledge" + ".",
        "auth.token: " + "a" * 11 + ".",
        # An `Authorization:` header whose token is a placeholder, or absent
        # altogether, is documentation and must stay writable. These are the
        # forms the corpus actually uses.
        "Authorization: Bearer <token>",
        "Authorization: Basic <base64>",
        "Authorization: Bearer ${CSR_TOKEN}",
        "the Authorization header is required",
        "set Authorization to the CSR token",
        # The scheme word is followed by a real word here, not a token. Length
        # does not separate this from a credential -- "authentication" clears
        # the 12-character floor on its own -- so the shape test has to: an
        # opaque token, a UUID or a base64 blob carries a digit or a case
        # change, and an English word carries neither. Without that, teaching
        # the guard to look past "Basic" refuses this sentence.
        "Authorization: Basic authentication is required by the gateway",
    ]
    for text in allowed:
        assert notes._find_credential(text) is None, text


def test_an_authorization_header_carrying_a_real_token_is_refused():
    """A credential in a header is not written the way an assignment is.

    `_SECRET_ASSIGNMENT`'s value group reads the first \\S+ after the
    separator; for header syntax that is the SCHEME WORD ("Basic", "Bearer"),
    so every real header token was measured as a 5-6 character value, failed
    the 12-character floor, and was accepted. The branch base refused all
    three of these. `.githooks/pre-commit` does not catch them either -- its
    SECRET_ASSIGNMENT_PATTERN has no `authorization` keyword and wants the
    run immediately after the `[:=]` -- so this guard is the only layer that
    sees them.
    """
    import base64

    from core import notes

    # Assembled, not written as a literal: the repository then carries the
    # shape's description rather than an opaque blob, and the pair it encodes
    # is visibly synthetic.
    basic = base64.b64encode(b"fmuser:S3cr3tP4ssw0rd").decode()
    # An opaque bearer token. A `Bearer eyJ...` JWT is already caught by the
    # separate JWT rule; an opaque token or a base64 Basic blob was not.
    bearer = "8f4c2a91-77bd-4e0a-9c31-5be2f0a7dd18"
    refused = [
        f"Authorization: Basic {basic}",
        f"Authorization: Bearer {bearer}",
        f"curl -H 'Authorization: Basic {basic}' https://api.example.test/crm",
    ]
    for text in refused:
        assert notes._find_credential(text) is not None, text


def test_real_credentials_are_still_refused():
    from core import notes

    # Values are assembled at run time rather than written as literals: this
    # file is committed, and .githooks/pre-commit refuses a staged line that
    # assigns a credential-shaped value. It refused an earlier draft of this
    # very test -- which is the guard behaving correctly, and the reason the
    # fix in step 3 loosens the VALUE rule and not the commit gate.
    secret = "S" * 24
    # A real password commonly carries one character outside the credential
    # charset (trailing "!" or similar). _CREDENTIAL_VALUE is searched, not
    # fully matched, against the value: a 12+ credential-charset run is
    # refused no matter where in the value it sits. An earlier version of
    # this pattern full-string-matched the value and missed both of these --
    # a real regression this test now pins down. (This is a property of
    # notes.py's own guard, not a claim that .githooks/pre-commit finds these
    # the same way -- its SECRET_ASSIGNMENT_PATTERN requires the run to start
    # immediately after the assignment, so the leading-punctuation case here
    # would defeat it there but does not defeat this guard.)
    trailing_punct = "S" * 20 + "!"
    leading_punct = "!" + "S" * 20
    # A value padded with leading/trailing "." -- itself a credential-charset
    # character -- must still be refused: _find_credential used to test the
    # value *after* stripping surrounding punctuation, which could shorten a
    # 13+ char run below the 12-char floor before the length test ever saw
    # it (see the raw_value comment in core/notes.py).
    dot_padded = ".." + "S" * 11
    # A trailing period must not rescue a value whose credential-charset run
    # already reaches 12 without it: only the period is stripped before the
    # length test, not the 12-char run in front of it.
    trailing_period_still_credential = "S" * 12 + "."
    refused = [
        f"mongodb://fmuser:{secret}@db.host:27017/admin",
        "aws_secret_access_key=" + secret,
        "clientSecret: " + secret,
        "-----BEGIN RSA PRIVATE KEY-----",
        "password=" + trailing_punct,
        "password=" + leading_punct,
        "token=" + dot_padded,
        "password=" + trailing_period_still_credential,
    ]
    for text in refused:
        assert notes._find_credential(text) is not None, text


def test_scope_may_name_a_file_in_a_linked_parent(tmp_path):
    """FM services are thin overlays; the class a note is about usually lives
    in the parent. Today that path is refused and a bare class name hashes to
    null, so it is never staleness-checked."""
    from core import links, notes

    workspace = tmp_path / "ws"
    parent = workspace / "upstream-svc"
    (parent / "src").mkdir(parents=True)
    (parent / "src" / "Base.java").write_text("class Base {}\n", encoding="utf-8")
    project = workspace / "fm-svc"
    (project / ".eos").mkdir(parents=True)
    links.write_link(project, "parent", "../upstream-svc", "parent")

    path = notes.add_note(
        project, kind="finding", title="Base is instantiated reflectively",
        body="The overlay must not make the constructor private.",
        scope=[f"{links.PARENT_PREFIX}parent/src/Base.java"],
    )

    text = path.read_text(encoding="utf-8")
    assert f"{links.PARENT_PREFIX}parent/src/Base.java" in text
    assert "null" not in text.split("scope_hashes:")[1].split("---")[0]


def test_scope_still_refuses_a_path_outside_project_and_parents(tmp_path):
    from core import notes
    import pytest

    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    with pytest.raises(ValueError, match="escapes"):
        notes.add_note(project, kind="finding", title="x", body="y",
                       scope=["../../../etc/hosts"])


def test_scope_in_a_parent_refuses_an_unconfigured_label(tmp_path):
    from core import links, notes
    import pytest

    project = tmp_path / "fm-svc"
    (project / ".eos").mkdir(parents=True)

    with pytest.raises(ValueError, match="unconfigured parent link"):
        notes.add_note(project, kind="finding", title="x", body="y",
                       scope=[f"{links.PARENT_PREFIX}nope/Foo.java"])


def test_scope_in_a_parent_refuses_a_path_escaping_the_parent_root(tmp_path):
    from core import links, notes
    import pytest

    workspace = tmp_path / "ws"
    parent = workspace / "upstream-svc"
    parent.mkdir(parents=True)
    project = workspace / "fm-svc"
    (project / ".eos").mkdir(parents=True)
    links.write_link(project, "parent", "../upstream-svc", "parent")

    with pytest.raises(ValueError, match="escapes the parent root"):
        notes.add_note(project, kind="finding", title="x", body="y",
                       scope=[f"{links.PARENT_PREFIX}parent/../outside.txt"])


def test_scope_in_a_parent_refuses_a_sensitive_file(tmp_path):
    from core import links, notes
    import pytest

    workspace = tmp_path / "ws"
    parent = workspace / "upstream-svc"
    parent.mkdir(parents=True)
    (parent / "credentials.pem").write_text("dummy\n", encoding="utf-8")
    project = workspace / "fm-svc"
    (project / ".eos").mkdir(parents=True)
    links.write_link(project, "parent", "../upstream-svc", "parent")

    with pytest.raises(ValueError, match="sensitive file"):
        notes.add_note(project, kind="finding", title="x", body="y",
                       scope=[f"{links.PARENT_PREFIX}parent/credentials.pem"])


def test_stale_notes_resolves_a_parent_scoped_entry_instead_of_reporting_it_removed(tmp_path):
    """Regression for the false positive: stale_notes used to resolve
    `@parent:` entries as `project / entry`, which never exists under the
    project root, so a correctly-hashed parent-scoped note was reported
    "removed" the moment it was written -- before the parent file ever
    changed."""
    from core import links, notes

    workspace = tmp_path / "ws"
    parent = workspace / "upstream-svc"
    (parent / "src").mkdir(parents=True)
    (parent / "src" / "Base.java").write_text("class Base {}\n", encoding="utf-8")
    project = workspace / "fm-svc"
    (project / ".eos").mkdir(parents=True)
    links.write_link(project, "parent", "../upstream-svc", "parent")

    notes.add_note(
        project, kind="finding", title="Base is instantiated reflectively",
        body="The overlay must not make the constructor private.",
        scope=[f"{links.PARENT_PREFIX}parent/src/Base.java"],
    )

    assert notes.stale_notes(project) == []

    (parent / "src" / "Base.java").write_text("class Base { Base() {} }\n", encoding="utf-8")

    stale = notes.stale_notes(project)
    assert len(stale) == 1
    assert stale[0]["issues"] == [
        {"scope": f"{links.PARENT_PREFIX}parent/src/Base.java", "reason": "changed"}
    ]
