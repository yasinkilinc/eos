"""CLI surface for parent links: `eos init --link-parent` and `eos parents`."""
import subprocess
import sys
from pathlib import Path

from core.lib.config_io import ConfigIO

REPO = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), *args],
        capture_output=True, text=True, **kw,
    )


def test_init_with_link_parent_saves_the_link(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    parent = tmp_path / "parent-microservices" / "upstream-orders"
    parent.mkdir(parents=True)

    r = _run(["init", str(proj), "--link-parent", str(parent)])

    assert r.returncode == 0, r.stderr
    cfg = ConfigIO.read_toml(proj / ".eos" / "config.toml")
    assert cfg["links"]["parent"]["path"] == str(parent)
    assert cfg["links"]["parent"]["role"] == "parent"


def test_init_with_link_parent_detects_product_version_from_pom(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "pom.xml").write_text(
        "<project><properties><product.version>v4.0.6-fm</product.version></properties></project>",
        encoding="utf-8",
    )
    parent = tmp_path / "upstream-orders"
    parent.mkdir()

    r = _run(["init", str(proj), "--link-parent", str(parent)])

    assert r.returncode == 0, r.stderr
    cfg = ConfigIO.read_toml(proj / ".eos" / "config.toml")
    assert cfg["links"]["parent"]["ref"] == "v4.0.6-fm"


def test_init_with_link_parent_still_saves_when_parent_path_is_missing(tmp_path):
    # fetch-parent-projects.sh may not have run yet -- the link should not be
    # blocked on the target existing, only warned about.
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    missing_parent = tmp_path / "does-not-exist-yet"

    r = _run(["init", str(proj), "--link-parent", str(missing_parent)])

    assert r.returncode == 0, r.stderr
    cfg = ConfigIO.read_toml(proj / ".eos" / "config.toml")
    assert cfg["links"]["parent"]["path"] == str(missing_parent)
    assert "does not exist" in r.stdout.lower()


def _git_branch(repo: Path, branch: str) -> None:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"],
        check=True,
    )


def test_parents_lists_a_configured_link_with_resolved_status(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    parent = tmp_path / "upstream-orders"
    parent.mkdir()
    assert _run(["init", str(proj), "--link-parent", str(parent)]).returncode == 0

    r = _run(["parents", str(proj)])

    assert r.returncode == 0, r.stderr
    assert "parent" in r.stdout
    assert str(parent) in r.stdout
    assert "found" in r.stdout.lower()


def test_parents_reports_a_missing_parent_path(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    assert _run(["init", str(proj), "--link-parent", str(tmp_path / "nowhere")]).returncode == 0

    r = _run(["parents", str(proj)])

    assert r.returncode == 0, r.stderr
    assert "missing" in r.stdout.lower()


def test_parents_does_not_flag_a_ref_that_is_a_substring_of_the_branch(tmp_path):
    # Real observed case: acme-orders's product.version is "v4.0.6-fm" while the
    # actual upstream-orders checkout sits on branch "release/v4.0.6-fm" -- an exact
    # equality check would flag this as mismatched on every correctly
    # configured project.
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "pom.xml").write_text(
        "<project><properties><product.version>v4.0.6-fm</product.version></properties></project>",
        encoding="utf-8",
    )
    parent = tmp_path / "upstream-orders"
    parent.mkdir()
    _git_branch(parent, "release/v4.0.6-fm")
    assert _run(["init", str(proj), "--link-parent", str(parent)]).returncode == 0

    r = _run(["parents", str(proj)])

    assert r.returncode == 0, r.stderr
    assert "may not match" not in r.stdout.lower()


def test_parents_hints_at_a_ref_that_does_not_match_at_all(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "pom.xml").write_text(
        "<project><properties><product.version>v4.0.6-fm</product.version></properties></project>",
        encoding="utf-8",
    )
    parent = tmp_path / "upstream-orders"
    parent.mkdir()
    _git_branch(parent, "release/v3.9.0-fm")
    assert _run(["init", str(proj), "--link-parent", str(parent)]).returncode == 0

    r = _run(["parents", str(proj)])

    assert r.returncode == 0, r.stderr
    assert "may not match" in r.stdout.lower()
