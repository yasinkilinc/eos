"""The ledger two sessions write to at the same time.

Notes are written by one session and read by the next. This is written by one
session while another is reading it, sometimes on another machine, and every
test here is about one of the three ways that goes wrong: a write that loses
another write, a claim that silently overwrites someone else's, and a ledger
that looks authoritative while being local to one laptop.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import work

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _project(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    return root


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def test_an_item_follows_its_events_through_every_state(tmp_path):
    project = _project(tmp_path)
    entry = work.open_item(project, "order capture chain", session="s1", agent="devin")

    assert work.items(project)[0].status == work.OPEN
    work.append(project, work.EVENT_CLAIM, entry.id, session="s1", agent="devin")
    assert work.items(project)[0].status == work.ACTIVE
    work.append(project, work.EVENT_BLOCK, entry.id, session="s1", body="env1 is down")
    item = work.items(project)[0]
    assert (item.status, item.reason) == (work.BLOCKED, "env1 is down")
    work.append(project, work.EVENT_UNBLOCK, entry.id, session="s1")
    assert work.items(project)[0].status == work.ACTIVE
    work.append(project, work.EVENT_DONE, entry.id, session="s1")

    assert work.items(project) == []  # done is not live work
    finished = work.items(project, status="all")[0]
    assert (finished.status, finished.holders) == (work.DONE, [])


def test_a_second_session_claiming_is_reported_not_resolved(tmp_path):
    """The failure this whole file exists for.

    Two agents on one item is the expensive outcome -- both spend a session on
    work only one of them needed to do -- and a ledger that resolved it by
    last-write-wins would show one holder and hide the collision from both.
    """
    project = _project(tmp_path)
    entry = work.open_item(project, "asset sync defect", session="s1", agent="devin", claim=True)

    work.append(project, work.EVENT_CLAIM, entry.id, session="s2", agent="claude")

    item = work.items(project)[0]
    assert item.contested is True
    assert item.holder_labels == ["devin/s1", "claude/s2"]
    assert item.status == work.ACTIVE  # still active: nothing was invalidated


def test_claiming_twice_from_one_session_does_not_double_the_holder(tmp_path):
    project = _project(tmp_path)
    entry = work.open_item(project, "asset sync defect", session="s1", agent="devin")

    work.append(project, work.EVENT_CLAIM, entry.id, session="s1", agent="devin")
    work.append(project, work.EVENT_CLAIM, entry.id, session="s1", agent="devin")

    assert work.items(project)[0].contested is False


def test_two_sessions_appending_keep_both_lines(tmp_path):
    """Append-only is the reason this is safe between a cloud session and a
    laptop; a read-modify-write of a status field would drop one of them."""
    project = _project(tmp_path)
    first = work.open_item(project, "one", session="s1")
    second = work.open_item(project, "two", session="s2")

    work.append(project, work.EVENT_LOG, first.id, session="s1", body="from session one")
    work.append(project, work.EVENT_LOG, second.id, session="s2", body="from session two")

    lines = work.path_for(project).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    assert {item.id for item in work.items(project)} == {first.id, second.id}


def test_an_event_whose_open_line_is_missing_still_shows_the_item(tmp_path):
    """A lost open line -- a bad merge in a git-tracked file -- would otherwise
    hide live work, which is the one failure this ledger exists to prevent."""
    project = _project(tmp_path)
    work.append(project, work.EVENT_CLAIM, "orphan-1a2b", session="s1", agent="devin")

    item = work.items(project)[0]
    assert (item.id, item.status) == ("orphan-1a2b", work.ACTIVE)


def test_a_corrupt_line_is_skipped_rather_than_taking_the_answer_with_it(tmp_path):
    project = _project(tmp_path)
    entry = work.open_item(project, "readable", session="s1")
    with open(work.path_for(project), "a", encoding="utf-8") as handle:
        handle.write("<<<<<<< HEAD\n{not json at all\n")

    found = work.items(project)

    assert [item.id for item in found] == [entry.id]


def test_an_item_nobody_has_touched_for_a_day_is_stale(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "long forgotten", session="s1", agent="devin", claim=True)

    item = work.items(project)[0]
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=work.STALE_AFTER_HOURS + 1)

    assert item.is_stale() is False
    assert item.is_stale(later) is True


def test_untaken_work_is_never_stale(tmp_path):
    """Staleness is a statement about a session that may be gone, not about
    age: an open item nobody claimed is not waiting on anyone."""
    project = _project(tmp_path)
    work.open_item(project, "nobody took this", session="s1")
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)

    assert work.items(project)[0].is_stale(later) is False


def test_an_ambiguous_name_refuses_instead_of_picking_one(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "order capture chain", session="s1")
    work.open_item(project, "order capture rules", session="s1")

    found = work.fold(work.load(project))

    with pytest.raises(LookupError):
        work.resolve(found, "order capture")
    assert work.resolve(found, "order capture rules").title == "order capture rules"


def test_a_credential_in_a_body_is_refused(tmp_path):
    """This file is committed and pushed the way the notes are."""
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="credential"):
        work.open_item(project, "wire up the client",
                       body="export API_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def test_a_placeholder_title_is_refused(tmp_path):
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="placeholder"):
        work.open_item(project, "<what this is about>")


def test_the_ledger_says_whether_anything_written_can_reach_another_machine(tmp_path):
    project = _project(tmp_path)

    assert work.sync_state(project)["state"] == "absent"
    work.open_item(project, "first item", session="s1")
    state = work.sync_state(project)
    assert state["state"] in ("untracked", "no-git")
    assert "another session" in work.sync_sentence(state) or "local to this machine" in work.sync_sentence(state)

    (project / ".gitignore").write_text(".eos/\n", encoding="utf-8")
    ignored = work.sync_state(project)
    assert ignored["state"] == "ignored"
    assert "will ever reach another session" in work.sync_sentence(ignored)


def test_sibling_ledgers_under_one_knowledge_root_are_read_together(tmp_path):
    """The arrangement `[knowledge] dir` exists for: a workspace of services
    sharing one knowledge root, where the work next door is the work that
    matters most."""
    workspace = tmp_path / "ws"
    for name in ("svc-a", "svc-b"):
        (workspace / name / ".eos").mkdir(parents=True)
        (workspace / name / ".eos" / "config.toml").write_text(
            f'[knowledge]\ndir = "../knowledge/{name}"\n', encoding="utf-8")
    work.open_item(workspace / "svc-a", "in service a", session="s1")
    work.open_item(workspace / "svc-b", "in service b", session="s2")

    ledgers = work.across_ledgers(workspace / "svc-a")

    assert [label for label, _ in ledgers] == ["svc-a", "svc-b"]
    titles = [item.title for _, ledger in ledgers
              for item in work.fold(work.load_path(ledger))]
    assert titles == ["in service a", "in service b"]


def test_the_context_block_names_what_did_not_fit(tmp_path):
    project = _project(tmp_path)
    for number in range(6):
        work.open_item(project, f"item number {number}", session="s1", agent="devin", claim=True)

    section = work.render_context_section(project, max_chars=300)

    assert section.startswith("## Work In Flight")
    assert "more item(s) did not fit" in section
    assert len(section) < 600


def test_there_is_no_context_block_when_nothing_is_in_flight(tmp_path):
    project = _project(tmp_path)
    entry = work.open_item(project, "already finished", session="s1")
    work.append(project, work.EVENT_DONE, entry.id, session="s1")

    assert work.render_context_section(project, max_chars=2000) == ""


def test_a_ticket_with_no_index_is_not_reported_as_a_ticket_with_no_commits(tmp_path):
    """The distinction the rest of this engine is built on: looked and found
    nothing, against never looked."""
    project = _project(tmp_path)

    assert work.ticket_commits(project, "TICKET-1") == {"indexed": False, "commits": []}


# --- through the CLI ---------------------------------------------------------

def test_the_cli_records_claims_and_lists_them(tmp_path):
    project = _project(tmp_path)

    added = _run(["work", "add", str(project), "--title", "top-up history path",
                  "--ticket", "TICKET-7", "--claim", "--session", "s1", "--agent", "claude"])
    assert added.returncode == 0, added.stderr

    listed = _run(["work", "list", str(project), "--format", "json"])
    assert listed.returncode == 0, listed.stderr
    items = json.loads(listed.stdout)
    assert [(item["status"], item["ticket"]) for item in items] == [("active", "TICKET-7")]


def test_an_empty_ledger_says_where_it_looked(tmp_path):
    """An empty list and a knowledge directory pointed elsewhere print the
    same silence, and every FM service points it outside the service."""
    project = _project(tmp_path)

    done = _run(["work", "list", str(project)])

    assert done.returncode == 0
    assert str(work.path_for(project)) in done.stdout


def test_the_index_holds_work_where_it_can_be_joined(tmp_path):
    """The ledger answers "what is in flight". The index is what lets that be
    asked *with* something else -- the commits naming the ticket, an
    extension's own rows, the notes written while it ran."""
    project = tmp_path / "proj"
    assert _run(["init", str(project), "--no-ai"]).returncode == 0
    entry = work.open_item(project, "order capture chain", ticket="TICKET-9",
                           session="s1", agent="devin", claim=True)
    work.append(project, work.EVENT_CLAIM, entry.id, session="s2", agent="claude")
    work.append(project, work.EVENT_BLOCK, entry.id, session="s1", body="waiting on env1")
    assert _run(["index", str(project)]).returncode == 0

    rows = _query(project, "SELECT id, status, ticket, holder_count, reason FROM work_item")

    assert rows == [(entry.id, "blocked", "TICKET-9", 2, "waiting on env1")]
    holders = _query(project, "SELECT session, agent FROM work_holder ORDER BY session")
    assert holders == [("s1", "devin"), ("s2", "claude")]
    events = _query(project, "SELECT event FROM work_event ORDER BY ord")
    assert [event for (event,) in events] == ["open", "claim", "claim", "block"]


def test_an_item_is_searchable_by_what_it_is_about(tmp_path):
    project = tmp_path / "proj"
    assert _run(["init", str(project), "--no-ai"]).returncode == 0
    work.open_item(project, "top-up history path", session="s1", agent="devin", claim=True)
    assert _run(["index", str(project)]).returncode == 0

    done = _run(["query", str(project), "--search", "top-up"])

    assert done.returncode == 0, done.stderr
    assert "top-up history path" in done.stdout, done.stdout


def test_appending_to_the_ledger_makes_the_index_stale(tmp_path):
    """The ledger is appended to far more often than notes are written, so it
    is the input most likely to leave an index behind. An index still showing
    an item as claimed after a session closed it is worse than no table."""
    project = tmp_path / "proj"
    assert _run(["init", str(project), "--no-ai"]).returncode == 0
    entry = work.open_item(project, "order capture chain", session="s1",
                           agent="devin", claim=True)
    assert _run(["index", str(project)]).returncode == 0
    work.append(project, work.EVENT_DONE, entry.id, session="s1")

    assert _run(["index", str(project)]).returncode == 0

    assert _query(project, "SELECT status FROM work_item") == [("done",)]


def test_statistics_count_a_collision_that_was_already_resolved(tmp_path):
    """The outcome numbers are replayed, not folded. A collision settled an
    hour later leaves no trace in the current state, and it is exactly the
    event worth counting: two sessions had already spent time on one item."""
    project = _project(tmp_path)
    entry = work.open_item(project, "order capture chain", session="s1",
                           agent="devin", claim=True)
    work.append(project, work.EVENT_CLAIM, entry.id, session="s2", agent="claude")
    work.append(project, work.EVENT_DONE, entry.id, session="s1", body="done")

    report = work.statistics(project)

    assert report["contested"] == 1, "a resolved collision still happened"
    assert report["contested_now"] == 0
    assert (report["closed"], report["done"]) == (1, 1)
    assert report["open_now"] == 0


def test_statistics_count_a_claim_that_went_quiet_while_somebody_held_it(tmp_path):
    project = _project(tmp_path)
    entry = work.open_item(project, "long forgotten", session="s1", agent="devin", claim=True)
    work.append(project, work.EVENT_LOG, entry.id, session="s1", body="picking this up")
    # Backdate everything but the last event, so the gap is in the middle of
    # the item's life rather than between now and its last event.
    ledger = work.path_for(project)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    year = entry.at[:4]
    ledger.write_text("\n".join(
        [line.replace(year, str(int(year) - 1), 1) for line in lines[:-1]] + lines[-1:]
    ) + "\n", encoding="utf-8")

    report = work.statistics(project)

    assert report["went_quiet"] == 1


def test_statistics_say_there_is_nothing_to_measure_rather_than_printing_zeroes(tmp_path):
    project = _project(tmp_path)

    done = _run(["work", "stats", str(project)])

    assert done.returncode == 0
    assert "nothing yet to measure" in done.stdout, done.stdout


def test_statistics_can_be_windowed(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "recorded today", session="s1", agent="devin", claim=True)

    now = work.load(project)[0].at

    assert work.statistics(project, since=now)["items"] == 1
    assert work.statistics(project, since="2099-01-01")["items"] == 0


def _query(project, sql):
    import sqlite3

    conn = sqlite3.connect(project / ".eos" / "data" / "eos.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_doctor_reports_a_contested_item_and_a_stale_claim(tmp_path):
    """Both are states nothing else surfaces: a contested item is two agents
    spending two sessions on one piece of work, and a stale claim is a session
    that may simply be gone. Neither is fatal and neither should be silent."""
    project = tmp_path / "proj"
    assert _run(["init", str(project), "--no-ai"]).returncode == 0
    entry = work.open_item(project, "order capture chain", session="s1",
                           agent="devin", claim=True)
    work.append(project, work.EVENT_CLAIM, entry.id, session="s2", agent="claude")
    # Backdate the whole ledger rather than sleeping: staleness is a clock
    # question and the clock is the one thing a test may not wait on.
    ledger = work.path_for(project)
    ledger.write_text(ledger.read_text(encoding="utf-8").replace(
        entry.at[:4], str(int(entry.at[:4]) - 1)), encoding="utf-8")

    done = _run(["doctor", str(project)])

    assert done.returncode == 0, done.stderr
    assert "held by more than one session" in done.stdout, done.stdout
    assert "may be gone" in done.stdout, done.stdout
    assert entry.id in done.stdout


def test_doctor_says_when_the_ledger_cannot_reach_another_machine(tmp_path):
    project = tmp_path / "proj"
    assert _run(["init", str(project), "--no-ai"]).returncode == 0
    (project / ".gitignore").write_text(".eos/\n", encoding="utf-8")
    work.open_item(project, "invisible to everyone else", session="s1")

    done = _run(["doctor", str(project)])

    assert done.returncode == 0, done.stderr
    assert "will ever reach another session" in done.stdout, done.stdout


def test_naming_two_items_at_once_is_an_error_with_both_named(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "order capture chain", session="s1")
    work.open_item(project, "order capture rules", session="s1")

    done = _run(["work", "claim", str(project), "order capture", "--session", "s2"])

    assert done.returncode == 1
    assert "order-capture-chain" in done.stderr and "order-capture-rules" in done.stderr
