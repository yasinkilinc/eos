"""What is in flight, so the next session does not start from zero.

A note records what a scan cannot re-derive -- why something is the way it is.
That is durable by construction: it reads the same a month later. The state of
the work is not. "Two sessions are on the top-up path, one of them blocked on
an env1 sync" is true for an afternoon, and a corpus of notes carrying claims
of that shape would be a corpus that expired without anyone noticing.

So this is a separate ledger with a lifecycle, and the two do not mix: a note
that goes stale is amended, a work item that goes stale is finished or dropped.

Three properties are what let more than one session use it at once.

**It is an event log, not a record that is edited.** Every command appends one
line; an item's state is the fold of its events. Two sessions appending at the
same moment produce two lines in whichever order the filesystem takes them,
and neither loses the other's write -- which a read-modify-write of a status
field cannot promise between a cloud session and a local one.

**A second claim is reported, not resolved.** When a session claims an item
another session already holds, the fold says so and names both. Picking a
winner would be this tool deciding which of two agents is wasting its time,
and it has no way to know.

**It says how far behind it might be.** This travels between machines the way
notes do: through the knowledge directory, through git. An entry that has not
been committed and pushed is invisible to every other session, and a ledger
that did not say so would read as authoritative while being local-only.

It is deliberately not a planner. Nothing here schedules, assigns, estimates
or closes anything on its own -- every line was written by a session that said
so, and `done` means a session claimed it was done, not that anything checked.
`eos work show` prints the commits that name the item's ticket next to that
claim, so the difference is visible rather than assumed.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from core import notes

FILENAME = "work.jsonl"

# One event per command. The status of an item is the fold of its events.
EVENT_OPEN = "open"
EVENT_CLAIM = "claim"
EVENT_LOG = "log"
EVENT_BLOCK = "block"
EVENT_UNBLOCK = "unblock"
EVENT_DONE = "done"
EVENT_DROP = "drop"
EVENTS = (EVENT_OPEN, EVENT_CLAIM, EVENT_LOG, EVENT_BLOCK,
          EVENT_UNBLOCK, EVENT_DONE, EVENT_DROP)

OPEN = "open"
ACTIVE = "active"
BLOCKED = "blocked"
DONE = "done"
DROPPED = "dropped"
STATUSES = (OPEN, ACTIVE, BLOCKED, DONE, DROPPED)

# What `list` and the context block show, in the order a reader wants them:
# what is being worked on now, what is stuck, what nobody has taken. Finished
# and dropped items are kept -- they are the record of what happened -- and
# shown only when asked for.
LIVE = (ACTIVE, BLOCKED, OPEN)

# After this long with no event, an item someone claimed is reported as stale.
# It is not closed, and not reassigned: a session that went away without
# saying so and a session still thinking look identical from here, and only
# one of them is safe to take work from. Naming it is the whole intervention.
STALE_AFTER_HOURS = 24

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclasses.dataclass(frozen=True)
class Event:
    id: str
    event: str
    at: str
    session: str | None = None
    agent: str | None = None
    title: str | None = None
    body: str | None = None
    ticket: str | None = None
    scope: list | None = None
    branch: str | None = None
    commit: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Item:
    id: str
    title: str
    status: str
    opened_at: str
    updated_at: str
    ticket: str | None = None
    scope: list = dataclasses.field(default_factory=list)
    holders: list = dataclasses.field(default_factory=list)
    reason: str | None = None
    last: str | None = None
    last_by: str | None = None
    events: int = 0

    @property
    def contested(self) -> bool:
        """Held by more than one session at once. Reported, never resolved."""
        return len(self.holders) > 1

    @property
    def holder_labels(self) -> list:
        return [who(holder.get("session"), holder.get("agent")) for holder in self.holders]

    def is_stale(self, now: datetime.datetime | None = None) -> bool:
        if self.status not in (ACTIVE, BLOCKED):
            return False
        age = _age_hours(self.updated_at, now)
        return age is not None and age >= STALE_AFTER_HOURS

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["contested"] = self.contested
        data["stale"] = self.is_stale()
        return data


def path_for(project_root: str | Path) -> Path:
    """The ledger lives beside the notes, for the reason the notes do.

    `eos scan` rewrites everything under `.eos/data`, and what a session was
    doing cannot be recomputed from a file tree. It also means the ledger
    inherits the knowledge directory's git arrangement -- which is how an
    entry reaches another machine at all.
    """
    return notes.notes_dir(project_root) / FILENAME


def _slug(title: str, words: int = 3) -> str:
    parts = [part for part in _SLUG.sub("-", title.casefold()).split("-") if part]
    return "-".join(parts[:words]) or "item"


def make_id(title: str) -> str:
    """A short id a person types, with enough entropy that two sessions
    opening similar work in the same minute do not collide.

    Sequential numbering was the obvious alternative and is wrong here: two
    sessions appending concurrently would both pick the same next number, and
    the collision would surface as two items wearing one id.
    """
    return f"{_slug(title)}-{uuid.uuid4().hex[:4]}"


def git_head(project_root: str | Path) -> tuple[str | None, str | None]:
    """The commit and branch the tree was on, so an entry can be placed.

    One subprocess, not two: this runs on every append, and a ledger command
    that costs two process spawns is one an agent learns to skip.
    """
    git = shutil.which("git")
    if git is None:
        return None, None
    try:
        done = subprocess.run(
            [git, "-C", str(project_root), "rev-parse", "HEAD", "--abbrev-ref", "HEAD"],
            capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if done.returncode != 0:
        return None, None
    lines = done.stdout.decode("utf-8", errors="replace").split()
    commit = lines[0] if lines else None
    branch = lines[1] if len(lines) > 1 else None
    return commit, (None if branch == "HEAD" else branch)


def _refuse_unusable(value: str | None, what: str) -> None:
    """The two shapes that make an entry worse than no entry at all."""
    if value is None:
        return
    if notes._is_placeholder(value.strip()):
        raise ValueError(
            f"{what} is still the placeholder {value.strip()!r}. Write what this "
            "is actually about -- the next session reads this instead of asking you.")
    found = notes._find_credential(value)
    if found:
        raise ValueError(
            f"{what} contains credential-shaped text ({found}). This ledger is "
            "committed and pushed like the notes are; put the secret nowhere near it.")


def append(project_root: str | Path, event: str, item_id: str, *,
           session: str | None = None, agent: str | None = None,
           title: str | None = None, body: str | None = None,
           ticket: str | None = None, scope: list | None = None) -> Event:
    """Append one event. Nothing here rewrites a line that is already written."""
    from core.knowledge.evidence import utc_now

    if event not in EVENTS:
        raise ValueError(f"event must be one of {', '.join(EVENTS)}; got {event!r}")
    if not item_id.strip():
        raise ValueError("an event without an item id belongs to nothing")
    _refuse_unusable(title, "title")
    _refuse_unusable(body, "body")

    commit, branch = git_head(project_root)
    entry = Event(
        id=item_id.strip(), event=event, at=utc_now(),
        session=session, agent=agent, title=title, body=body,
        ticket=ticket, scope=list(scope) if scope else None,
        branch=branch, commit=commit,
    )
    target = path_for(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    return entry


def open_item(project_root: str | Path, title: str, *, body: str | None = None,
              ticket: str | None = None, scope: list | None = None,
              session: str | None = None, agent: str | None = None,
              claim: bool = False) -> Event:
    """Record a piece of work, optionally claiming it in the same breath.

    `claim` exists because the common case is a session opening the item it is
    starting right now, and making that two commands is how the second one
    gets skipped.
    """
    if not title.strip():
        raise ValueError("a work item without a title is not findable by anyone")
    item_id = make_id(title)
    entry = append(project_root, EVENT_OPEN, item_id, session=session, agent=agent,
                   title=title.strip(), body=body, ticket=ticket, scope=scope)
    if claim:
        return append(project_root, EVENT_CLAIM, item_id, session=session, agent=agent,
                      title=title.strip())
    return entry


def load_path(ledger: Path) -> list[Event]:
    """Every event in one ledger, oldest first. A bad line is skipped, not fatal.

    Same reasoning as the skips file: this is read on every `work list`, and a
    single unparseable line -- a half-written append, a merge conflict marker
    left in a git-tracked file -- must not take the whole answer with it.
    """
    if not ledger.is_file():
        return []
    try:
        text = ledger.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = [field.name for field in dataclasses.fields(Event)]
    found: list[Event] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if not isinstance(data, dict) or not data.get("id") or not data.get("event"):
                continue
            found.append(Event(**{name: data.get(name) for name in names}))
        except (ValueError, TypeError):
            continue
    return found


def load(project_root: str | Path) -> list[Event]:
    return load_path(path_for(project_root))


def fold(events: list[Event]) -> list[Item]:
    """The state of each item, from its events, in the order they were written.

    An event for an id with no `open` line does not vanish: it creates the
    item. A ledger where the open line was lost to a bad merge would otherwise
    hide live work, which is the one failure this file exists to prevent.
    """
    state: dict[str, Item] = {}
    for entry in events:
        item = state.get(entry.id)
        if item is None:
            item = Item(id=entry.id, title=entry.title or entry.id, status=OPEN,
                        opened_at=entry.at, updated_at=entry.at)
            state[entry.id] = item
        item.events += 1
        item.updated_at = entry.at
        if entry.title:
            item.title = entry.title
        if entry.ticket:
            item.ticket = entry.ticket
        if entry.scope:
            item.scope = list(entry.scope)
        if entry.body:
            item.last = entry.body
            item.last_by = who(entry.session, entry.agent)

        if entry.event == EVENT_CLAIM:
            if not any(holder.get("session") == entry.session for holder in item.holders):
                item.holders.append({"session": entry.session, "agent": entry.agent,
                                     "at": entry.at})
            item.status = ACTIVE
        elif entry.event == EVENT_BLOCK:
            item.status = BLOCKED
            item.reason = entry.body
        elif entry.event == EVENT_UNBLOCK:
            item.status = ACTIVE if item.holders else OPEN
            item.reason = None
        elif entry.event == EVENT_DONE:
            item.status = DONE
            item.holders = []
        elif entry.event == EVENT_DROP:
            item.status = DROPPED
            item.reason = entry.body
            item.holders = []
    return list(state.values())


_ORDER = {status: rank for rank, status in enumerate(LIVE + (DONE, DROPPED))}


def sort_items(found: list[Item]) -> list[Item]:
    """Active first, then blocked, then untaken; newest movement first within each.

    Two passes because Python's sort is stable and one key cannot hold one
    field ascending and another descending without contorting the values.
    """
    by_recency = sorted(found, key=lambda item: item.updated_at or "", reverse=True)
    return sorted(by_recency, key=lambda item: _ORDER.get(item.status, 9))


def items(project_root: str | Path, *, status: str | None = None) -> list[Item]:
    """Every item, folded. `status` filters; without one, live work only."""
    found = fold(load(project_root))
    if status == "all":
        return sort_items(found)
    if status:
        return sort_items([item for item in found if item.status == status])
    return sort_items([item for item in found if item.status in LIVE])


def statistics(project_root: str | Path, since: str | None = None) -> dict:
    """What actually happened here, replayed from the events.

    Counting how often a tool was called says nothing about whether it helped.
    These are the outcomes the ledger exists to change, and each of them is a
    number that should fall if it is working: two sessions landing on one item,
    a claim going quiet while someone still held it, work claimed and never
    closed. They are derived by replaying the events rather than folding to a
    current state, because a collision that was resolved an hour later is
    invisible in the final state and is exactly the event worth counting.

    Every number here measures what sessions *recorded*. An agent that closed
    an item without doing the work, or did the work without closing the item,
    is indistinguishable from here -- which is the same limit `eos findings`
    states about recorded runs, and is why none of this is presented as a
    verdict on anybody.
    """
    events = [entry for entry in load(project_root)
              if not since or (entry.at or "") >= since]
    if not events:
        return {"items": 0, "since": since, "events": 0}

    holders: dict[str, set] = {}
    contested: set = set()
    claimed_at: dict[str, str] = {}
    went_quiet: set = set()
    durations: list[float] = []
    endings = {DONE: 0, DROPPED: 0, BLOCKED: 0}
    last_event: dict[str, str] = {}
    ever_claimed: set = set()

    for entry in events:
        held = holders.setdefault(entry.id, set())
        # A gap while somebody held it: the session may have ended without
        # saying so, and the next one could not tell. Counted per item, once.
        if held and last_event.get(entry.id):
            gap = _between(last_event[entry.id], entry.at)
            if gap is not None and gap >= STALE_AFTER_HOURS:
                went_quiet.add(entry.id)
        last_event[entry.id] = entry.at

        if entry.event == EVENT_CLAIM:
            held.add(entry.session)
            ever_claimed.add(entry.id)
            claimed_at.setdefault(entry.id, entry.at)
            if len(held) > 1:
                contested.add(entry.id)
        elif entry.event in (EVENT_DONE, EVENT_DROP):
            endings[DONE if entry.event == EVENT_DONE else DROPPED] += 1
            opened = claimed_at.pop(entry.id, None)
            if opened:
                hours = _between(opened, entry.at)
                if hours is not None:
                    durations.append(hours)
            holders[entry.id] = set()
        elif entry.event == EVENT_BLOCK:
            endings[BLOCKED] += 1

    now = datetime.datetime.now(datetime.timezone.utc)
    current = fold(load(project_root))
    open_now = [item for item in current if item.status in LIVE]
    durations.sort()
    return {
        "since": since,
        "events": len(events),
        "items": len({entry.id for entry in events}),
        "claimed": len(ever_claimed),
        "contested": len(contested),
        "went_quiet": len(went_quiet),
        "closed": endings[DONE] + endings[DROPPED],
        "done": endings[DONE],
        "dropped": endings[DROPPED],
        "blocked": endings[BLOCKED],
        "open_now": len(open_now),
        "stale_now": len([item for item in open_now if item.is_stale(now)]),
        "contested_now": len([item for item in open_now if item.contested]),
        "median_hours_to_close": durations[len(durations) // 2] if durations else None,
        "longest_hours_to_close": durations[-1] if durations else None,
    }


def _between(first: str | None, second: str | None) -> float | None:
    start, end = _parse(first), _parse(second)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600


def resolve(found: list[Item], needle: str) -> Item:
    """One item, by id or by part of its title.

    Ambiguity is an error with the candidates named, never a first match:
    picking one would silently append an event to the wrong item, and nothing
    downstream could tell that had happened.
    """
    key = needle.strip().casefold()
    exact = [item for item in found if item.id.casefold() == key]
    matches = exact or [item for item in found
                        if item.id.casefold().startswith(key) or key in item.title.casefold()]
    if not matches:
        raise KeyError(needle)
    if len(matches) > 1:
        raise LookupError("\n".join(f"  {item.id}\t{item.title}" for item in matches[:10]))
    return matches[0]


def across_ledgers(project_root: str | Path) -> list[tuple[str, Path]]:
    """Every sibling ledger under the same knowledge root, labelled by directory.

    A workspace of services that share one knowledge directory -- which is the
    arrangement `[knowledge] dir` exists for -- has one ledger per service in
    sibling directories. Reading them together is how a session sees that the
    work it is about to start is already in flight next door.
    """
    own = path_for(project_root)
    root = own.parent.parent
    found: list[tuple[str, Path]] = []
    if root.is_dir():
        for candidate in sorted(root.iterdir()):
            ledger = candidate / FILENAME
            if candidate.is_dir() and ledger.is_file():
                found.append((candidate.name, ledger))
    if not any(ledger == own for _, ledger in found):
        found.insert(0, (own.parent.name, own))
    return found


def who(session: str | None, agent: str | None) -> str:
    if session and agent:
        return f"{agent}/{session}"
    return agent or session or "nobody"


def _parse(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _age_hours(value: str | None, now: datetime.datetime | None = None) -> float | None:
    parsed = _parse(value)
    if parsed is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - parsed).total_seconds() / 3600


def ago(value: str | None, now: datetime.datetime | None = None) -> str:
    hours = _age_hours(value, now)
    if hours is None:
        return "at an unknown time"
    if hours < 1:
        return "under an hour ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


# --- Whether any of this reaches another machine -----------------------------

def sync_state(project_root: str | Path) -> dict:
    """Where this ledger has got to, in terms of who else can see it.

    The whole point of the file is that a second session reads what the first
    one wrote, and between a cloud session and a laptop the transport is git.
    Every state below is a different answer to "can anyone else see this yet",
    and they are indistinguishable from the contents of the file.
    """
    ledger = path_for(project_root)
    state = {"path": str(ledger), "exists": ledger.is_file(),
             "gitignored": notes.is_notes_dir_gitignored(project_root)}
    if state["gitignored"]:
        state["state"] = "ignored"
        return state
    if not state["exists"]:
        state["state"] = "absent"
        return state

    git = shutil.which("git")
    if git is None:
        state["state"] = "unknown"
        return state

    porcelain = _git(git, ledger.parent, ["status", "--porcelain", "--", ledger.name])
    if porcelain is None:
        state["state"] = "no-git"
        return state
    if porcelain.startswith("??"):
        state["state"] = "untracked"
        return state
    if porcelain:
        state["state"] = "uncommitted"
        return state

    ahead = _git(git, ledger.parent, ["rev-list", "--count", "@{u}..HEAD", "--", ledger.name])
    if ahead is None:
        state["state"] = "committed"  # no upstream to be ahead of
    elif ahead.strip() not in ("", "0"):
        state["state"] = "unpushed"
    else:
        state["state"] = "pushed"
    return state


def _git(git: str, cwd: Path, args: list[str]) -> str | None:
    try:
        done = subprocess.run([git, "-C", str(cwd)] + args,
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace").strip()


_SYNC_SENTENCES = {
    "ignored": "This ledger is under a gitignored directory: nothing written here "
               "will ever reach another session.",
    "untracked": "This ledger is not tracked by git yet: commit it, or no other "
                 "session can read what is in it.",
    "uncommitted": "This ledger has uncommitted entries: another machine cannot see "
                   "them until they are committed and pushed.",
    "unpushed": "This ledger has commits that are not pushed: another machine cannot "
                "see them yet.",
    "pushed": "This ledger is pushed; another session sees it after a pull.",
    "committed": "This ledger is committed, and has no upstream to push to.",
    "no-git": "This ledger is not inside a git repository: it is local to this machine.",
    "unknown": "Whether this ledger has been shared could not be determined (no git here).",
    "absent": "No ledger has been written here yet.",
}


def sync_sentence(state: dict) -> str:
    return _SYNC_SENTENCES.get(state.get("state", ""), _SYNC_SENTENCES["unknown"])


# --- What a session starting work is shown -----------------------------------

def render_context_section(project_root: str | Path, max_chars: int) -> str:
    """A bounded '## Work In Flight' block for the context an agent is given.

    Notes answer "what was learned here"; this answers "what is happening here
    right now", and a session that reads one without the other either repeats
    work in progress or picks up an item someone is already holding.

    Items go in whole or not at all and the remainder is named, the same rule
    the notes block follows: a truncated list that looks complete is the one
    outcome worse than a short one.
    """
    found = items(project_root)
    if not found:
        return ""

    now = datetime.datetime.now(datetime.timezone.utc)
    lines = ["## Work In Flight\n"]
    included = 0
    for item in found:
        entry = "\n".join(_context_lines(item, now))
        if included > 0 and len("\n".join(lines + [entry])) > max_chars:
            break
        lines.append(entry)
        included += 1

    remaining = len(found) - included
    if remaining:
        lines.append(f"_{remaining} more item(s) did not fit. "
                     "`eos work list <project>` lists them._")
    return "\n".join(lines)


def _context_lines(item: Item, now: datetime.datetime) -> list[str]:
    marks = []
    if item.ticket:
        marks.append(item.ticket)
    if item.is_stale(now):
        marks.append(f"stale: untouched for over {STALE_AFTER_HOURS}h")
    if item.contested:
        marks.append("claimed by " + " and ".join(item.holder_labels))
    held = " and ".join(item.holder_labels) if item.holders else "nobody"
    suffix = f" [{'; '.join(marks)}]" if marks else ""
    lines = [f"- **{item.status}** `{item.id}` {item.title} — held by {held}, "
             f"{ago(item.updated_at, now)}{suffix}"]
    if item.status == BLOCKED and item.reason:
        lines.append(f"  - blocked on: {item.reason}")
    elif item.last:
        lines.append(f"  - last: {item.last}")
    return lines


# --- The claim, next to what git says about it -------------------------------

def ticket_commits(project_root: str | Path, ticket: str, limit: int = 5) -> dict:
    """Commits the index has that name this item's ticket.

    A work item is what a session said it was doing. This is the only part of
    it anything else can check, and it is reported as evidence rather than
    folded into the status: a ticket with no commits may be a claim that
    outran the work, or work that is not committed yet, and nothing here can
    tell those apart.

    Distinguishes "the index has none" from "there is no index", because the
    two print the same silence and lead to different next steps.
    """
    import sqlite3

    database = Path(project_root).expanduser().resolve() / ".eos" / "data" / "eos.db"
    if not database.is_file():
        return {"indexed": False, "commits": []}
    try:
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT c.sha, c.authored_at, c.subject FROM git_commit_ticket t "
                "JOIN git_commit c ON c.sha = t.sha WHERE t.key = ? "
                "ORDER BY c.ord DESC LIMIT ?", (ticket, limit)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"indexed": False, "commits": []}
    return {"indexed": True,
            "commits": [{"sha": sha, "at": at, "subject": subject} for sha, at, subject in rows]}
