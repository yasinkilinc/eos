"""The documents an agent reads, held against the commands it will run.

Three defects a fresh session found in an hour were all of one kind, and none
of them were reachable from a test of the engine -- the engine was right in
all three cases. What was wrong was the seam between the code and the reader:
a document promised more than the command delivers, an empty answer did not
say why it was empty, and a question the work needed did not exist.

A test cannot judge whether a sentence is *useful*; that still takes a session
with no context and a real question. It can refuse to ship a command the
documents name and the CLI does not have, a flag that was renamed out from
under an example, an answer that is silence, and a claim the command's own
output contradicts. Those are the parts that rot without anyone noticing,
because the person who renames a flag is not the person reading the README.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

README = REPO / "README.md"
SKILL = REPO / "core" / "ai" / "templates" / "skill.md"
# The two documents an agent is given: the README when a human onboards it,
# the skill when the platform loads EOS into a session. A third reader --
# this repository's own docs/ -- is for people, and is not checked here.
AGENT_DOCUMENTS = (README, SKILL)

# Placeholders the documents use for a project root, in every spelling they
# appear in. An example is checked for shape, not for data.
PATH_PLACEHOLDERS = {
    ".", "<project>", "<project-dir>", "/path/to/project", "/path/to/your/project",
    "<note-file>", "<path>",
}


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _subcommands() -> set[str]:
    """The real command list, read from argparse rather than repeated here."""
    done = _run(["--help"])
    assert done.returncode == 0, done.stderr
    choices = re.search(r"\{([a-z0-9,\-]+)\}", done.stdout)
    assert choices, done.stdout
    return set(choices.group(1).split(","))


class Call:
    """One `eos ...` invocation a document shows the reader."""

    def __init__(self, document: Path, line: int, text: str):
        self.document = document
        self.line = line
        self.text = text
        tokens = [t.strip("[]|,") for t in text.split()]
        self.tokens = [t for t in tokens if t and t != "\\"]
        self.command = self.tokens[0] if self.tokens else ""
        # `note add`, `ai update`, `ui start` -- the help lives on the child.
        self.sub = ""
        if len(self.tokens) > 1 and re.fullmatch(r"[a-z][a-z-]*", self.tokens[1]):
            self.sub = self.tokens[1]
        self.flags = [t for t in self.tokens if t.startswith("--")]

    @property
    def where(self) -> str:
        return f"{self.document.name}:{self.line}"

    def __repr__(self) -> str:
        return f"{self.where} `eos {self.text}`"


def _calls() -> list[Call]:
    """Every `eos ...` a document shows, from inline spans and fenced blocks.

    Both forms matter and they rot differently: an inline span is the one a
    reader copies mid-sentence, a fenced line is the one they paste whole.
    """
    found: list[Call] = []
    for document in AGENT_DOCUMENTS:
        fenced = False
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                body = line.split("#", 1)[0].strip()
                if body.startswith("eos "):
                    found.append(Call(document, number, body[4:].strip()))
                continue
            for span in re.findall(r"`eos ([^`]+)`", line):
                call = Call(document, number, span.split("#", 1)[0].strip())
                # `eos <command> <project>` is the documents explaining the
                # shape of a call, not showing one. There is nothing to check.
                if call.command.startswith("<"):
                    continue
                found.append(call)
    assert found, "no documented commands were found at all -- the extractor is broken"
    return found


DOCUMENTED_CALLS = _calls()


def test_every_command_the_documents_name_is_a_real_command():
    """A renamed or removed command that a document still names sends the
    reader to an argparse error, and an agent reading it has no way to tell
    the document is stale from the document itself."""
    real = _subcommands()

    unknown = sorted(
        {f"{call.where}: eos {call.command}"
         for call in DOCUMENTED_CALLS
         if call.command not in real and not call.command.startswith("-")}
    )

    assert not unknown, "documents name commands the CLI does not have:\n  " + "\n  ".join(unknown)


def test_every_flag_the_documents_show_still_exists():
    """The failure this catches is silent: a flag is renamed in the parser, the
    example keeps the old spelling, and the reader gets `unrecognized
    arguments` for something a document told them to run."""
    missing = []
    helps: dict[tuple[str, str], str] = {}
    for call in DOCUMENTED_CALLS:
        if not call.flags or call.command.startswith("-"):
            continue
        for flag in call.flags:
            target = (call.command, call.sub)
            if target not in helps:
                args = [call.command] + ([call.sub] if call.sub else []) + ["--help"]
                done = _run(args)
                helps[target] = done.stdout if done.returncode == 0 else ""
            if not helps[target]:
                continue  # not a subcommand pair; the command test reports it
            if flag not in helps[target]:
                missing.append(f"{call.where}: eos {call.command} {flag}")

    assert not missing, "documents show flags the CLI does not accept:\n  " + "\n  ".join(sorted(set(missing)))


def test_every_command_is_named_in_a_document_an_agent_loads():
    """A command nobody is told about is a command nobody runs. The third
    defect a fresh session found was of this shape from the other side: the
    question it needed did not exist, and it had no way to ask for one."""
    text = "\n".join(document.read_text(encoding="utf-8") for document in AGENT_DOCUMENTS)

    undocumented = sorted(
        command for command in _subcommands()
        if not re.search(rf"`?eos {re.escape(command)}\b", text)
    )

    assert not undocumented, (
        "commands no document an agent loads names:\n  " + "\n  ".join(undocumented))


# Each entry is a command whose answer here is legitimately nothing, and the
# reason it is nothing. Observed on a scanned Python project with no notes, no
# extensions, no Java and no recorded runs.
EMPTY_ANSWERS = [
    pytest.param(["rules", "."], id="no Java, so no coded refusals"),
    pytest.param(["ask", "."], id="no index extension provides a question"),
    pytest.param(["findings", "."], id="no run has been recorded"),
    pytest.param(["note", "list", "."], id="no notes exist yet"),
    pytest.param(["note", "search", ".", "matches-no-note-here"], id="a search that matches nothing"),
    pytest.param(["trace", ".", "src/main.py"], id="a file that reaches nothing"),
    pytest.param(["parents", "."], id="no parent project is linked"),
    pytest.param(["note", "audit", "."], id="no note has gone stale"),
]

# A bare negative tells the reader that the answer is empty and nothing else.
# Whether the tool looked and found none, or never looked at all, is the whole
# question -- and it is the one an empty answer of this shape leaves open.
BARE_NEGATIVES = {
    "", "[]", "{}", "none", "none.", "no results", "no results.", "not found",
    "not found.", "nothing", "nothing.", "0", "no matches", "no matches.",
}


@pytest.fixture(scope="module")
def empty_project(tmp_path_factory) -> Path:
    """A valid, scanned project whose every answer below is legitimately empty."""
    root = tmp_path_factory.mktemp("quiet") / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    done = _run(["scan", str(root), "--full"])
    assert done.returncode == 0, done.stderr
    return root


@pytest.mark.parametrize("args", EMPTY_ANSWERS)
def test_an_empty_answer_says_why_it_is_empty(args, empty_project):
    """Silence is not absence, and this is the floor that keeps them apart.

    A fresh session ran `eos rules`, got a bare negative, was told nothing it
    could act on and guessed at a rescan. The guess was wrong, and the hour it
    cost was spent proving the tool had in fact looked.

    This asserts only that an empty answer is a sentence rather than a shrug:
    it cannot tell a good explanation from a bad one. That much still takes a
    reader. What it can do is refuse the two shapes that are never right --
    printing nothing at all, and printing a negative with no subject.
    """
    done = _rewrite(args, empty_project)

    assert done.returncode == 0, done.stderr or done.stdout
    answer = (done.stdout or done.stderr).strip()

    assert answer, "an empty answer printed nothing at all: the reader cannot tell it ran"
    assert answer.lower() not in BARE_NEGATIVES, (
        f"an empty answer said only {answer!r}: it does not say whether anything looked")
    assert len(answer.split()) >= 6, (
        f"an empty answer said only {answer!r}: too short to name what was examined")


def _rewrite(args: list[str], root: Path) -> subprocess.CompletedProcess:
    """Run a documented call against the fixture, with `.` standing for it."""
    return _run([str(root) if token == "." else token for token in args])


def test_the_questions_an_extension_provides_are_all_listed(tmp_path):
    """`eos ask` with no question is the only place a reader learns which
    questions exist. A question that the listing omits is a question that,
    for every practical purpose, this project does not have."""
    root = tmp_path / "project"
    (root / ".eos").mkdir(parents=True)
    (root / "ext").mkdir()
    (root / "ext" / "questions_ext.py").write_text(
        'SCHEMA = "CREATE TABLE widget (name TEXT PRIMARY KEY);"\n'
        'QUESTIONS = {\n'
        '    "widgets": {"help": "Every widget.", "sql": "SELECT name FROM widget"},\n'
        '    "widget-untested": {"help": "Widgets no test names.",\n'
        '                        "sql": "SELECT name FROM widget WHERE name = ?"},\n'
        '}\n'
        '\n'
        'def load(build):\n'
        '    build.conn.execute("INSERT INTO widget(name) VALUES (\'hinge\')")\n',
        encoding="utf-8")
    (root / ".eos" / "config.toml").write_text(
        '[index]\nextensions = ["ext/questions_ext.py"]\n', encoding="utf-8")
    assert _run(["index", str(root)]).returncode == 0

    done = _run(["ask", str(root)])

    assert done.returncode == 0, done.stderr
    for name in ("widgets", "widget-untested"):
        assert name in done.stdout, f"`eos ask` does not list {name}:\n{done.stdout}"
        assert "Every widget." in done.stdout or "Widgets no test names." in done.stdout, (
            "the listing names the questions but not what they answer")


# A claim the output does not deliver, and the shape the output actually has.
# `draft-test` emits a skeleton: the right method name, the assertion library
# the neighbouring tests use, and the code to assert -- with the arrangement
# left to the reader. A reviewer told it "writes the test" trusts it instead
# of reading it, which is the one thing a skeleton must not be trusted for.
OVERCLAIMS = {
    "draft-test": (
        "writes the first version", "writes the test", "generates the test",
        "writes it for you", "writes the missing test",
    ),
}


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+|\n\|", text)


@pytest.mark.parametrize("command, forbidden", sorted(OVERCLAIMS.items()))
def test_nothing_claims_more_than_the_command_delivers(command, forbidden, empty_project):
    """The second defect, pinned at every site that can repeat it.

    It was fixed once in a table and survived in the prose two paragraphs
    below, because the fix was made where the reviewer was looking rather than
    everywhere the claim appears. The documents and the command's own messages
    are checked together for that reason.
    """
    sources = [(document.name, document.read_text(encoding="utf-8"))
               for document in AGENT_DOCUMENTS]
    for probe in (["findings"], ["draft-test", "NO_SUCH_CODE"]):
        done = _rewrite(probe + ["."], empty_project)
        sources.append((f"eos {' '.join(probe)}", (done.stdout or "") + (done.stderr or "")))

    offences = [
        f"{where}: {sentence.strip()}"
        for where, text in sources
        for sentence in _sentences(text)
        if command in sentence
        for claim in forbidden
        if claim in sentence.lower()
    ]

    assert not offences, (
        f"`eos {command}` is described as doing more than it does:\n  " + "\n  ".join(offences))
