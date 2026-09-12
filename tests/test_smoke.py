"""End-to-end smoke test for the EOS CLI.

Bootstraps a throwaway project, runs init → scan → doctor, and asserts
that the knowledge artifacts (brain vault + graph.json) are produced and
internally consistent. Guards against regressions in the init/update/
reconcile consistency group (runtime/ layout, VERSION location, cache dir).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from core import inspector

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
# See test_distribution.py: assert against the shipped version, not a literal.
VERSION = (REPO / "core" / "VERSION").read_text(encoding="utf-8").strip()


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, cwd=REPO, **kw)


def test_init_scan_doctor_produces_artifacts(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "main.py").write_text("import utils\nutils.hi()\n", encoding="utf-8")
    (proj / "utils.py").write_text("def hi():\n    print('hi')\n", encoding="utf-8")

    r = _run(["init", str(proj)])
    assert r.returncode == 0, r.stderr
    # Layout contract: runtime/ holds VERSION + manifest.json (not engine/, not top-level).
    assert (proj / ".eos" / "runtime" / "VERSION").is_file()
    assert (proj / ".eos" / "runtime" / "manifest.json").is_file()
    assert not (proj / ".eos" / "VERSION").exists(), "top-level .eos/VERSION must not exist"
    assert not (proj / ".eos" / "engine").exists(), "engine/ dir must not exist (use runtime/)"

    r = _run(["scan", str(proj), "--full"])
    assert r.returncode == 0, r.stderr

    brain = proj / ".eos" / "data" / "brain"
    assert (brain / "_index.md").is_file(), "_index.md missing"
    assert (brain / "graph.json").is_file(), "graph.json missing"
    # cache must be a single file, not a dir-in-dir (regression guard for cache path bug)
    cache = proj / ".eos" / "data" / "cache" / "file_cache.json"
    assert cache.is_file(), f"cache file missing or is a directory: {cache}"

    graph = json.loads((brain / "graph.json").read_text(encoding="utf-8"))
    assert len(graph["nodes"]) >= 2, "expected at least 2 nodes"
    assert any(e["kind"] == "import" for e in graph["edges"]), "expected an import edge"

    r = _run(["doctor", str(proj)])
    assert r.returncode == 0, r.stderr
    assert "healthy" in r.stdout

    r = _run(["info", str(proj)])
    assert r.returncode == 0, r.stderr
    assert VERSION in r.stdout


def test_brain_is_documents_not_an_obsidian_vault(tmp_path):
    """The brain is the five BRAIN_FILES — no per-component notes, no wikilinks.

    Replaces test_brain_wikilinks_resolve_to_files. Components/ was one note
    per file (93% of every .eos tree) that no CLI command or MCP tool ever
    opened, so it is no longer written; the [[wikilink]] markup that pointed
    at it would now dangle, and dangling link syntax costs tokens in the
    context while resolving to nothing, so it is gone too. The old test's
    invariant (every link resolves) is preserved in its only remaining
    satisfiable form: there are no links to resolve.
    """
    proj = tmp_path / "wiki"
    proj.mkdir()
    (proj / "main.py").write_text("import helpers\nhelpers.go()\n", encoding="utf-8")
    (proj / "helpers.py").write_text("def go():\n    pass\n", encoding="utf-8")

    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    brain = proj / ".eos" / "data" / "brain"
    assert not (brain / "Components").exists(), "Components/ must no longer be generated"

    for name in inspector.BRAIN_FILES:
        assert (brain / name).is_file(), f"{name} missing from the brain"

    link_re = re.compile(r"\[\[([^\]]+)\]")
    dangling = set()
    for md in brain.rglob("*.md"):
        dangling.update(link_re.findall(md.read_text(encoding="utf-8")))
    assert not dangling, f"brain still emits wikilinks pointing at nothing: {sorted(dangling)}"


def test_incremental_scan_uses_cache(tmp_path):
    proj = tmp_path / "inc"
    proj.mkdir()
    (proj / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")

    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0
    cache = proj / ".eos" / "data" / "cache" / "file_cache.json"
    first = json.loads(cache.read_text(encoding="utf-8"))
    assert "a.py" in first

    # Second scan without --full: cache should still track a.py (reused, not removed).
    assert _run(["scan", str(proj)]).returncode == 0
    second = json.loads(cache.read_text(encoding="utf-8"))
    assert "a.py" in second, "incremental scan lost cached file entry"


def test_runtime_is_self_contained(tmp_path):
    """A deployed instance runs eos commands from .eos/runtime/ without the dev repo.

    Guards #20: the deployed eos.py bootstraps a virtual `core` package pointing
    at the runtime directory, so imports resolve to runtime siblings.
    """
    import shutil as _shutil

    proj = tmp_path / "portable"
    proj.mkdir()
    (proj / "main.py").write_text("import helpers\nhelpers.go()\n", encoding="utf-8")
    (proj / "helpers.py").write_text("def go():\n    pass\n", encoding="utf-8")

    assert _run(["init", str(proj)]).returncode == 0
    runtime_eos = proj / ".eos" / "runtime" / "eos.py"
    assert runtime_eos.is_file(), "init must deploy runtime/eos.py"

    # Run doctor + scan from the deployed runtime copy (not the dev core/eos.py).
    r = subprocess.run(
        [sys.executable, str(runtime_eos), "doctor", str(proj)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"runtime doctor failed:\n{r.stderr}"
    assert "healthy" in r.stdout

    r = subprocess.run(
        [sys.executable, str(runtime_eos), "scan", str(proj), "--full"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"runtime scan failed:\n{r.stderr}"
    assert (proj / ".eos" / "data" / "brain" / "graph.json").is_file()
    _ = _shutil  # silence unused import linters


def test_typescript_extracts_type_enum_interface(tmp_path):
    """TS plugin surfaces type aliases, enums, interfaces, and namespaces."""
    proj = tmp_path / "tsproj"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"tsproj","type":"module"}', encoding="utf-8")
    (proj / "types.ts").write_text(
        "export type Status = 'on' | 'off';\n"
        "export interface User { id: number; }\n"
        "export enum Color { Red, Green }\n"
        "export namespace Util { export const x = 1; }\n",
        encoding="utf-8",
    )

    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    graph = json.loads((proj / ".eos" / "data" / "brain" / "graph.json").read_text(encoding="utf-8"))
    node = next(n for n in graph["nodes"] if n["path"] == "types.ts")
    exports = node["metadata"]["exports"]
    # All four TS constructs should appear as exports.
    assert "Status" in exports
    assert "User" in exports
    assert "Color" in exports
    assert "Util" in exports


def test_ai_summary_is_compact_and_complete(tmp_path):
    """AI_SUMMARY.md covers scale, stack, entry points, hubs, and structure."""
    proj = tmp_path / "aiproj"
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "main.py").write_text(
        '"""Order processing service entry point."""\nimport repo\nimport utils\nrepo.save()\n',
        encoding="utf-8",
    )
    (proj / "src" / "repo.py").write_text("def save():\n    pass\n", encoding="utf-8")
    (proj / "src" / "utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    summary = (proj / ".eos" / "data" / "brain" / "AI_SUMMARY.md").read_text(encoding="utf-8")
    assert "# Project Summary" in summary
    assert "Order processing service entry point." in summary  # docstring pulled in
    assert "## Scale" in summary
    assert "## Entry Points" in summary
    assert "## Hub Components" in summary  # repo.py has inbound
    assert "## Module Structure" in summary
    assert "python" in summary
    # Token-economical: no wikilink noise.
    assert "[[" not in summary
    # Reasonable length guard — should be well under ~2KB for 3 files.
    assert len(summary) < 4000


def test_folder_hierarchy_edges_present(tmp_path):
    """Folder nodes + folder-hierarchy edges appear in graph.json."""
    proj = tmp_path / "nested"
    os.makedirs(proj / "src" / "a" / "b", exist_ok=True)
    (proj / "src" / "a" / "b" / "deep.py").write_text("pass\n", encoding="utf-8")
    (proj / "src" / "shallow.py").write_text("pass\n", encoding="utf-8")

    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    graph = json.loads((proj / ".eos" / "data" / "brain" / "graph.json").read_text(encoding="utf-8"))
    folders = [n for n in graph["nodes"] if n["type"] == "folder"]
    folder_labels = {n["label"] for n in folders}
    assert {"src", "a", "b"}.issubset(folder_labels), f"missing folders in {folder_labels}"
    hier = [e for e in graph["edges"] if e["kind"] == "folder-hierarchy"]
    assert len(hier) >= 4, f"expected >=4 hierarchy edges, got {len(hier)}"

    # Architecture.md must be generated and mention the folder structure.
    arch = (proj / ".eos" / "data" / "brain" / "Architecture.md").read_text(encoding="utf-8")
    assert "## Folder Structure" in arch
    assert "## Leaf Components" in arch or "## Hub Components" in arch

    # Architecture.md must be generated and mention the folder structure.
    arch = (proj / ".eos" / "data" / "brain" / "Architecture.md").read_text(encoding="utf-8")
    assert "## Folder Structure" in arch
    assert "## Leaf Components" in arch or "## Hub Components" in arch


def test_init_repairs_malformed_instance(tmp_path):
    """A foreign/partial .eos must be repaired, not silently accepted.

    An unrelated CLI that also answers to "eos" leaves a .eos tree with none
    of our markers. init used to return 0 on any existing .eos, so such an
    instance could never be repaired and reported an empty instance_id
    forever.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    project = tmp_path / "proj"
    project.mkdir()

    # Foreign layout plus a developer's own file that must survive.
    foreign = project / ".eos"
    (foreign / "knowledge").mkdir(parents=True)
    (foreign / "graphs").mkdir()
    (foreign / "postman").mkdir()
    keepsake = foreign / "postman" / "TICKET-1.postman_collection.json"
    keepsake.write_text('{"info":"developer content"}', encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(repo / "core" / "eos.py"), "init", str(project)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    assert (foreign / "id.txt").is_file(), "id.txt was not created"
    assert (foreign / "id.txt").read_text(encoding="utf-8").strip()
    assert (foreign / "config.toml").is_file(), "config.toml was not created"
    assert (foreign / "runtime" / "VERSION").is_file(), "runtime was not deployed"
    assert keepsake.is_file(), "developer content was destroyed"
    assert (foreign / "knowledge").is_dir(), "pre-existing directory was destroyed"

    doctor = subprocess.run(
        [sys.executable, str(repo / "core" / "eos.py"), "doctor", str(project)],
        capture_output=True, text=True,
    )
    assert doctor.returncode == 0, f"doctor still unhappy: {doctor.stdout}"


def test_init_preserves_existing_instance_id(tmp_path):
    """Repair must never re-issue the identity of an already-valid instance."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    project = tmp_path / "proj"
    project.mkdir()
    eos_py = str(repo / "core" / "eos.py")

    subprocess.run([sys.executable, eos_py, "init", str(project)], capture_output=True, text=True)
    first = (project / ".eos" / "id.txt").read_text(encoding="utf-8").strip()

    subprocess.run([sys.executable, eos_py, "init", str(project)], capture_output=True, text=True)
    second = (project / ".eos" / "id.txt").read_text(encoding="utf-8").strip()

    assert first == second, "instance id changed on re-init"


def test_scan_removes_a_stale_components_vault(tmp_path):
    """A tree scanned by an older EOS keeps its Components/ notes forever
    otherwise: the generator stopped writing them but never removed them,
    leaving 13,447 dead files across this workspace's services. The
    generator owns that directory, so it cleans it up on the next scan --
    self-healing for anyone who upgrades, without migration machinery.
    """
    proj = tmp_path / "stale"
    proj.mkdir()
    (proj / "main.py").write_text("import helpers\nhelpers.go()\n", encoding="utf-8")
    (proj / "helpers.py").write_text("def go():\n    pass\n", encoding="utf-8")

    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    # Simulate what an older EOS left behind.
    stale = proj / ".eos" / "data" / "brain" / "Components"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "OldNote.md").write_text("# OldNote\n", encoding="utf-8")

    assert _run(["scan", str(proj), "--full"]).returncode == 0

    assert not stale.exists(), "a stale Components/ vault must be removed by the next scan"
