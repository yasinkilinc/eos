"""Scan cost must not be quadratic in project size or in file size.

Both of these were measured on real code before being written down here: a
2,140-file Java tree took 321 s with 98% of it in import resolution, and the
CSR frontend spent 1.7 s parsing 4,108 files and then 275 s building the graph.
Separately, one 4.6 MB bundled .js file took 330 s to parse, with each doubling
of file size costing 4x -- no output, no size cap, indistinguishable from a hang.
"""
import time

from core.knowledge.builder import KnowledgeBuilder
from core.knowledge.semantic import FileSemantic, Import, ProjectSemantic
from core.plugins.javascript.plugin import JavaScriptPlugin


def _project(n: int) -> ProjectSemantic:
    project = ProjectSemantic()
    for i in range(n):
        file = FileSemantic(path=f"pkg/mod{i}.py", language="python")
        file.imports.append(Import(module=f"pkg.mod{(i + 1) % n}", name=None))
        project.files.append(file)
    return project


def _best_of(fn, repeats: int = 3) -> float:
    """Fastest of several runs.

    A single wall-clock reading on a millisecond-scale workload is dominated by
    scheduler noise -- this pair of assertions failed once during a full-suite
    run that shared the machine with a large scan, and passed on three
    consecutive re-runs. The minimum is the closest thing to the work actually
    done, and a real quadratic (16x for 4x input) survives it easily.
    """
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def test_import_resolution_scales_linearly():
    builder = KnowledgeBuilder()
    small_project, large_project = _project(1000), _project(4000)

    small = _best_of(lambda: builder.build(small_project))
    large = _best_of(lambda: builder.build(large_project))

    # 4x the files costs ~4x linearly and ~16x quadratically. 8x is the midpoint
    # in log space, so neither noise nor a real regression lands near it.
    assert large < small * 8, (
        f"import resolution is quadratic: {small:.4f}s for 1000 files, "
        f"{large:.4f}s for 4000 ({large / small:.1f}x for 4x the input)"
    )


def test_line_numbering_scales_linearly():
    plugin = JavaScriptPlugin()
    small_src = "function a(){return 1};" * 8000
    large_src = "function a(){return 1};" * 32000

    small = _best_of(lambda: plugin.parse_file("small.js", small_src))
    large = _best_of(lambda: plugin.parse_file("large.js", large_src))

    assert large < small * 8, (
        f"line numbering is quadratic: {small:.4f}s at 8k matches, "
        f"{large:.4f}s at 32k ({large / small:.1f}x for 4x the input)"
    )
