from pathlib import Path

from core import bench


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "acme-orders"
    (root / "src").mkdir(parents=True)
    (root / "src" / "orders.py").write_text(
        "class OrderService:\n    def submit(self):\n        return 1\n", encoding="utf-8"
    )
    (root / "src" / "billing.py").write_text(
        "class BillingService:\n    def charge(self):\n        return 2\n", encoding="utf-8"
    )
    return root


def test_sample_symbols_returns_known_locations(tmp_path):
    root = _project(tmp_path)
    symbols = bench.sample_symbols(root, n=10)
    names = {s.name for s in symbols}
    assert "OrderService" in names
    for symbol in symbols:
        assert (root / symbol.rel_path).is_file()


def test_report_marks_objective_vs_comparative(tmp_path):
    root = _project(tmp_path)
    report = bench.run(root, samples=2)
    markdown = report.to_markdown()
    assert "objective" in markdown.lower()
    assert "comparative" in markdown.lower()


def test_report_states_numbers_are_project_specific(tmp_path):
    root = _project(tmp_path)
    markdown = bench.run(root, samples=2).to_markdown()
    assert "this project" in markdown.lower()


def test_bench_command_dispatches_without_keyerror(tmp_path, capsys):
    """`bench` must be registered in main()'s commands dict, or this raises
    KeyError before cmd_bench ever runs.

    `--help` cannot stand in for this: argparse's help action exits during
    parsing, before `commands[args.command](args)` is reached, so a missing
    dict entry would never surface through it."""
    from core import eos as eos_cli

    root = _project(tmp_path)

    rc = eos_cli.main(["bench", str(root), "--samples", "2"])

    assert rc == 0
    assert (root / ".eos" / "data" / "bench.md").is_file()


def test_zero_or_negative_samples_degrade_without_raising(tmp_path):
    """`--samples 0` (or negative) drew an empty sample, and the objective
    recall was then computed as `hits / len(sampled)` -- a ZeroDivisionError
    on the very first call. Found by hand during self-review; locking it in
    here so it cannot come back unnoticed."""
    root = _project(tmp_path)

    for n in (0, -3):
        report = bench.run(root, samples=n)
        assert report.degraded_reason is not None
        # Rendering must not raise either.
        assert report.to_text()
        assert report.to_markdown()


def test_grep_baseline_excludes_eos_and_git(tmp_path, monkeypatch):
    """`_ensure_scanned` writes `.eos/data/cache/file_cache.json`, a JSON
    dump that contains every symbol's own name as a string -- e.g. the
    literal text "OrderService.submit". An unscoped `grep -rn` run after
    that file exists matches it on every query, inflating grep's measured
    output size and time with a hit against an artifact the benchmark
    itself just created, not against real project source."""
    root = _project(tmp_path)
    captured_commands: list[list[str]] = []
    real_run = bench.subprocess.run

    def spy_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(bench.subprocess, "run", spy_run)

    bench.run(root, samples=2)

    grep_commands = [cmd for cmd in captured_commands if cmd[0] == "grep"]
    assert grep_commands, "grep was never invoked"
    for cmd in grep_commands:
        assert "--exclude-dir=.eos" in cmd
        assert "--exclude-dir=.git" in cmd


def test_ground_truth_respects_scanner_ignore_config(tmp_path):
    """Ground truth must use the same file-inclusion decision `Scanner`
    uses for a real scan. A second, narrower ignore set here would sample
    ground-truth symbols from files `find_symbol` cannot see (e.g. a
    `.eos/config.toml`-excluded vendor directory), deflating its measured
    recall for a reason that has nothing to do with the tool."""
    root = _project(tmp_path)
    (root / "vendor").mkdir()
    (root / "vendor" / "gen.py").write_text(
        "class VendoredThing:\n    def run(self):\n        return 1\n", encoding="utf-8"
    )
    eos_dir = root / ".eos"
    eos_dir.mkdir()
    (eos_dir / "config.toml").write_text('[scan]\nignore = ["vendor"]\n', encoding="utf-8")

    names = {s.name for s in bench.sample_symbols(root, n=100)}

    assert "VendoredThing" not in names
    assert "OrderService" in names


def test_scan_side_effect_is_disclosed(tmp_path):
    """`eos bench` writes/rewrites `.eos/data/cache` and `.eos/data/brain`
    on every run. That must be said out loud, not discovered afterwards by
    someone who never ran `eos init`."""
    root = _project(tmp_path)
    report = bench.run(root, samples=2)

    assert report.scan_note
    assert ".eos" in report.scan_note
    assert report.scan_note in report.to_text()
    assert report.scan_note in report.to_markdown()
