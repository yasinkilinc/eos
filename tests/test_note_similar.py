"""A note about the same thing as a live note must say it replaces it
(--supersedes) or what is different (--differs): an unrelated-looking second
note is how a fixed problem stayed "open" beside its fix."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _run(args):
    return subprocess.run(EOS + args, capture_output=True, text=True, cwd=REPO)


def _init(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    assert _run(["init", str(proj), "--no-ai"]).returncode == 0
    return proj


def _add(proj, title, body, *extra, kind="finding"):
    r = _run(["note", "add", str(proj), "--kind", kind, "--title", title, "--body", body, "--tags", "t", *extra])
    assert r.returncode == 0, r.stderr
    return Path(r.stdout.split()[-1])


def test_a_note_on_the_same_ticket_must_say_it_replaces_or_differs(tmp_path):
    proj = _init(tmp_path)
    old = _add(proj, "PROJ-7 export fails with 403", "The export call answers 403.", "--source", "PROJ-7")
    args = ["note", "add", str(proj), "--kind", "finding", "--source", "PROJ-7", "--tags", "t",
            "--title", "PROJ-7 export delivered", "--body", "Permission row added; export answers 200."]
    refused = _run(args)
    assert refused.returncode != 0
    assert old.name in refused.stderr and "--supersedes" in refused.stderr and "--differs" in refused.stderr
    assert _run(args + ["--supersedes", old.name]).returncode == 0


def test_differs_is_recorded(tmp_path):
    proj = _init(tmp_path)
    _add(proj, "PROJ-7 export fails with 403", "The export call answers 403.", "--source", "PROJ-7")
    written = _add(proj, "PROJ-7 export fails on retry", "A retry answers 403 too.", "--source", "PROJ-7",
                   "--differs", "the retry path, not the first call")
    text = written.read_text(encoding="utf-8")
    assert "differs:" in text and "the retry path, not the first call" in text


def test_a_very_similar_title_without_a_ticket_is_caught(tmp_path):
    proj = _init(tmp_path)
    _add(proj, "Export queue drains slowly at night", "Jobs wait an hour.")
    r = _run(["note", "add", str(proj), "--kind", "finding", "--tags", "t",
              "--title", "Export queue drains slowly", "--body", "Seen again on Tuesday."])
    assert r.returncode != 0 and "--differs" in r.stderr


def test_a_replaced_note_is_not_a_rival(tmp_path):
    proj = _init(tmp_path)
    old = _add(proj, "PROJ-7 export fails with 403", "The export call answers 403.", "--source", "PROJ-7")
    _add(proj, "PROJ-7 export delivered", "Export answers 200.", "--source", "PROJ-7", "--supersedes", old.name)
    _add(proj, "PROJ-7 export fails with 403 again", "Regression: 403 is back.", "--source", "PROJ-7",
         "--differs", "a regression after the delivery")
    r = _run(["note", "add", str(proj), "--kind", "finding", "--tags", "t", "--source", "PROJ-8",
              "--title", "PROJ-8 unrelated import", "--body", "Another ticket entirely."])
    assert r.returncode == 0, r.stderr


def test_a_decision_is_not_compared_with_a_finding(tmp_path):
    proj = _init(tmp_path)
    _add(proj, "PROJ-7 export fails with 403", "The export call answers 403.", "--source", "PROJ-7")
    r = _run(["note", "add", str(proj), "--kind", "decision", "--source", "PROJ-7",
              "--title", "PROJ-7 export fails with 403 is fixed through the bulk API",
              "--body", "Use the bulk API.\n\n## Why\nSpeed.\n\n## When\nAlways.\n\n## Component\nexport"])
    assert r.returncode == 0, r.stderr


def test_words_no_note_uses_make_a_title_different_not_similar(tmp_path):
    from core import notes
    words = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november "
             "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu amber "
             "basalt cobalt dune").split()
    for word in words:
        notes.add_note(tmp_path, kind="finding", title=f"{word.title()} service restarts cleanly",
                       body=f"The {word} pod restarts.", differs="filler")
    notes.add_note(tmp_path, kind="finding", title="Kafka topic names follow the domain", body="Names.")
    notes.add_note(tmp_path, kind="finding", title="Kafka consumer lag spikes at midnight", body="Lag.")
    notes.add_note(tmp_path, kind="finding", title="Kafka tüketici gecikmesi gece yükseliyor", body="Gecikme.")


def test_the_same_source_with_a_title_of_no_words_is_still_checked(tmp_path):
    import pytest
    from core import notes
    notes.add_note(tmp_path, kind="finding", title="PROJ-7 export fails with 403",
                   body="The export answers 403.", source="PROJ-7")
    with pytest.raises(notes.SimilarNoteError):
        notes.add_note(tmp_path, kind="finding", title="x", body="Now 200.", source="PROJ-7")
