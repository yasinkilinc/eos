"""`eos consolidate` (2.x roadmap L1): a fold-only report of what needs attention."""
import subprocess
import sys
from pathlib import Path

from core import consolidate, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
BASE = ("the nightly job reconciles pending orders with the payment provider and moves every order "
        "that has no settlement after two days into the failed state releasing its reserved number "
        "for the next activation that asks for one in the same pool")


def test_the_report_names_what_needs_attention_and_changes_nothing(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    made = subprocess.run(EOS + ["procedure", "new", str(root), "--title", "Release the service", "--steps", "-"],
                          input="Tag it (tool: nothing-here)\n", capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    alike = BASE.replace("two days", "three days").replace("same pool", "same number pool")
    assert 0.6 <= notes.trigram_jaccard(BASE, alike) < notes.PARAPHRASE_JACCARD
    notes.add_note(root, "finding", "Pending orders are failed by the nightly job", BASE)
    notes.add_note(root, "finding", "Reconciliation releases reserved numbers", alike)
    before = sorted(p.read_text() for p in (root / ".eos" / "knowledge").glob("*.md"))

    data = consolidate.report(root)
    assert data["procedures"]["never_run"] and data["procedures"]["lint"]
    assert data["near_duplicates"] and data["near_duplicates"][0][0] >= 0.6
    text = consolidate.render(data)
    assert "PROCEDURES NEVER RUN (1)" in text and "NOTES THAT READ ALIKE (1)" in text
    assert sorted(p.read_text() for p in (root / ".eos" / "knowledge").glob("*.md")) == before

    done = subprocess.run(EOS + ["consolidate", str(root)], capture_output=True, text=True)
    assert done.returncode == 0 and "Nothing here was changed" in done.stdout


def test_a_step_tool_no_run_ever_recorded_is_named(tmp_path, monkeypatch):
    """Seen on the host: three steps' scripts recorded nothing, so no run could say
    whether they passed -- and nothing said so."""
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    made = subprocess.run(EOS + ["procedure", "new", str(root), "--title", "Ship it", "--steps", "-",
                                 "--success", "it runs"],
                          input="Build (tool: mvn)\nCheck grants (tool: grants)\n", capture_output=True, text=True)
    slug = made.stdout.split("\t")[0]
    run = subprocess.run(EOS + ["run", "start", str(root), "--title", "Ship once", "--procedure", slug,
                                "--session", "s1"], capture_output=True, text=True).stdout.split()[0]
    subprocess.run(EOS + ["run", "event", str(root), run, "--kind", "ran", "--tool", "automation/mvn.sh",
                          "--exit", "0"], capture_output=True)
    subprocess.run(EOS + ["run", "finish", str(root), run, "--outcome", "ok"], capture_output=True)
    data = consolidate.report(root)
    assert data["procedures"]["silent_steps"] == {slug: ["step 2 (tool: grants)"]}
    assert f"STEPS NO RUN RECORDED (1)" in consolidate.render(data)
