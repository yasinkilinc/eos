"""Symbols with their parent and breadcrumb (2.x roadmap L3)."""
import sqlite3
import subprocess
import sys
from pathlib import Path

from core import inspector, symbols

REPO = Path(__file__).resolve().parents[1]


def test_a_python_method_names_its_class():
    rows = symbols.rows("app/billing.py", [
        {"name": "Invoice", "kind": "class", "line": 3},
        {"name": "Invoice.total", "kind": "method", "line": 7, "parent": "Invoice"},
        {"name": "helper", "kind": "function", "line": 20},
    ])
    method = rows[1]
    assert method["short"] == "total"
    assert method["parent_index"] == 0
    assert method["breadcrumb"] == "app/billing.py > Invoice > total"
    assert rows[2]["parent_index"] is None
    assert rows[2]["breadcrumb"] == "app/billing.py > helper"


def test_a_java_method_under_a_nested_class():
    rows = symbols.rows("src/Order.java", [
        {"name": "Order", "kind": "class", "line": 1},
        {"name": "Line", "kind": "class", "line": 5, "parent": "Order"},
        {"name": "price", "kind": "method", "line": 8, "parent": "Line"},
        {"name": "price", "kind": "method", "line": 30, "parent": "Order"},
    ])
    assert rows[2]["breadcrumb"] == "src/Order.java > Order > Line > price"
    assert rows[3]["breadcrumb"] == "src/Order.java > Order > price"


def test_a_parent_that_is_not_in_the_file_still_reads_in_the_breadcrumb():
    rows = symbols.rows("a.py", [{"name": "Gone.run", "kind": "method", "line": 4, "parent": "Gone"}])
    assert rows[0]["parent_index"] is None
    assert rows[0]["breadcrumb"] == "a.py > Gone > run"


def test_a_parent_cycle_does_not_hang():
    rows = symbols.rows("a.java", [
        {"name": "A", "kind": "class", "line": 1, "parent": "B"},
        {"name": "B", "kind": "class", "line": 2, "parent": "A"},
    ])
    assert all(row["breadcrumb"].startswith("a.java > ") for row in rows)


def _scanned(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "app").mkdir(parents=True)
    (proj / "app" / "billing.py").write_text(
        "class Invoice:\n    def total(self):\n        return 1\n\n\ndef helper():\n    return 2\n",
        encoding="utf-8")
    for args in (["init", str(proj)], ["scan", str(proj), "--full"], ["index", str(proj)]):
        done = subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), *args],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return proj


def test_find_symbol_says_where_a_symbol_sits(tmp_path):
    proj = _scanned(tmp_path)
    found = inspector.find_symbols(proj, "total")
    assert found[0]["breadcrumb"] == "app/billing.py > Invoice > total"
    assert found[0]["parent"] == "Invoice"


def test_the_index_holds_symbols_linked_to_their_parent(tmp_path):
    proj = _scanned(tmp_path)
    db = sqlite3.connect(proj / ".eos" / "data" / "eos.db")
    rows = {short: (sid, parent, crumb) for sid, short, parent, crumb in
            db.execute("SELECT sid, short, parent_id, breadcrumb FROM symbol")}
    assert rows["total"][1] == rows["Invoice"][0]
    assert rows["total"][2] == "app/billing.py > Invoice > total"
    assert rows["helper"][1] is None
    path, = db.execute("SELECT n.path FROM symbol s JOIN node n ON n.nid = s.nid WHERE s.short = 'total'").fetchone()
    assert path == "app/billing.py"


# --- review of L3 -------------------------------------------------------------------


def test_a_java_constructor_is_never_a_parent():
    rows = symbols.rows("src/com/acme/Line.java", [
        {"name": "com.acme.Line", "kind": "class", "line": 2},
        {"name": "Line", "kind": "method", "line": 4, "parent": "com.acme.Line"},
        {"name": "price", "kind": "method", "line": 5, "parent": "com.acme.Line"},
    ])
    assert rows[1]["parent_index"] == 0 and rows[2]["parent_index"] == 0
    assert rows[2]["breadcrumb"] == "src/com/acme/Line.java > com.acme.Line > price"


def test_two_classes_under_one_name_leave_the_parent_unresolved():
    rows = symbols.rows("A.java", [
        {"name": "com.acme.B", "kind": "class", "line": 3},
        {"name": "com.acme.B", "kind": "class", "line": 4},
        {"name": "m", "kind": "method", "line": 5, "parent": "com.acme.B"},
    ])
    assert rows[2]["parent_index"] is None
    assert rows[2]["breadcrumb"] == "A.java > com.acme.B > m"


def test_parent_resolution_is_linear():
    import time
    found = [{"name": "Big", "kind": "class", "line": 1}] + [
        {"name": f"Big.m{i}", "kind": "method", "line": i + 2, "parent": "Big"} for i in range(20000)]
    started = time.monotonic()
    rows = symbols.rows("big.py", found)
    assert time.monotonic() - started < 1.0
    assert all(row["parent_index"] == 0 for row in rows[1:])
