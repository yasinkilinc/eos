"""`aliases` front matter: another language's words for a note, scored as its title."""
import subprocess
import sys
from pathlib import Path

from core import notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


def _add(root, title, *extra):
    done = subprocess.run(EOS + ["note", "add", str(root), "--kind", "finding", "--tags", "flow",
                                 "--title", title, "--body", f"{title} body text.", *extra],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _write(path, aliases_line):
    path.write_text(f"---\nkind: finding\ntitle: T\ncreated: 2026-01-01\n{aliases_line}---\n\nbody\n",
                    encoding="utf-8")
    return notes.parse_note(path)


def test_scalar_and_list_aliases_parse(tmp_path):
    assert _write(tmp_path / "a.md", "aliases: iptal\n").aliases == ("iptal",)
    assert _write(tmp_path / "b.md", "aliases:\n  - sipariş iptali\n  - cancel\n").aliases == (
        "sipariş iptali", "cancel")
    assert _write(tmp_path / "c.md", "").aliases == ()


def test_alias_ranks_a_note_for_a_query_in_another_language(tmp_path):
    root = _project(tmp_path)
    _add(root, "Order cancel flow", "--alias", "sipariş iptali")
    _add(root, "Refund processing rules")
    _add(root, "Invoice numbering scheme")
    ranked = notes.search_notes(root, "sipariş iptali nasıl")
    assert ranked and ranked[0].title == "Order cancel flow"
    assert [n.title for n in ranked] == ["Order cancel flow"]


def test_alias_is_repeatable_and_amend_adds_without_losing(tmp_path):
    root = _project(tmp_path)
    _add(root, "Order cancel flow", "--alias", "sipariş iptali", "--alias", "iptal")
    path = notes.load_notes(root)[0].path
    assert notes.parse_note(path).aliases == ("sipariş iptali", "iptal")
    done = subprocess.run(EOS + ["note", "amend", str(path), "--path", str(root), "--alias", "vazgeç"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert notes.parse_note(path).aliases == ("sipariş iptali", "iptal", "vazgeç")
    done = subprocess.run(EOS + ["note", "amend", str(path), "--path", str(root),
                                 "--append", "More detail."], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert notes.parse_note(path).aliases == ("sipariş iptali", "iptal", "vazgeç")
