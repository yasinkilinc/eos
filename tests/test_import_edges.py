"""Import-edge resolution in KnowledgeBuilder.

These edges are what impact_analysis reports as blast radius, so a resolution
gap here does not surface as an error -- it surfaces as an agent being told a
file has no dependents when it has many.
"""
from core.knowledge.builder import KnowledgeBuilder
from core.knowledge.semantic import FileSemantic, Import, ProjectSemantic


def _project(*files: FileSemantic) -> ProjectSemantic:
    project = ProjectSemantic()
    for f in files:
        project.add_file(f)
    return project


def _import_edges(project: ProjectSemantic):
    graph = KnowledgeBuilder().build(project)
    return [e for e in graph.edges if e.kind == "import"]


def test_java_style_import_resolves_via_package_plus_class():
    # A Java plugin splits "import com.acme.svc.Thing;" into module="com.acme.svc"
    # (the package) and name="Thing" (the class). The file on disk is named after
    # the CLASS, so resolving the module alone can only ever hit a directory.
    consumer = FileSemantic(
        path="src/main/java/com/acme/app/Consumer.java",
        language="java",
        imports=[Import(module="com.acme.svc", name="Thing")],
    )
    target = FileSemantic(path="src/main/java/com/acme/svc/Thing.java", language="java")

    edges = _import_edges(_project(consumer, target))

    assert len(edges) == 1, "expected the java import to resolve to Thing.java"
    assert edges[0].target_id.endswith("src/main/java/com/acme/svc/Thing.java")


def test_module_only_import_still_resolves():
    # Python-style "import a.b" where the module itself is the file: the
    # fully-qualified attempt (a.b.None) must not break this pre-existing case.
    consumer = FileSemantic(
        path="app/main.py", language="python", imports=[Import(module="app.helper")]
    )
    target = FileSemantic(path="app/helper.py", language="python")

    edges = _import_edges(_project(consumer, target))

    assert len(edges) == 1
    assert edges[0].target_id.endswith("app/helper.py")


def test_from_import_prefers_the_module_when_the_name_is_a_symbol():
    # "from app.helper import run": module=app.helper is the file, name=run is a
    # function inside it. app/helper/run.py does not exist, so the module match
    # must still win rather than the import being dropped.
    consumer = FileSemantic(
        path="app/main.py",
        language="python",
        imports=[Import(module="app.helper", name="run")],
    )
    target = FileSemantic(path="app/helper.py", language="python")

    edges = _import_edges(_project(consumer, target))

    assert len(edges) == 1
    assert edges[0].target_id.endswith("app/helper.py")


def test_external_import_creates_no_edge():
    consumer = FileSemantic(
        path="src/main/java/com/acme/app/Consumer.java",
        language="java",
        imports=[Import(module="org.springframework.stereotype", name="Service")],
    )

    assert _import_edges(_project(consumer)) == []


def test_wildcard_import_does_not_resolve_to_a_bogus_file():
    # "import com.acme.svc.*" -> name="*"; there is no file called "*.java" and
    # inventing an edge to some arbitrary sibling would be worse than no edge.
    consumer = FileSemantic(
        path="src/main/java/com/acme/app/Consumer.java",
        language="java",
        imports=[Import(module="com.acme.svc", name="*")],
    )
    target = FileSemantic(path="src/main/java/com/acme/svc/Thing.java", language="java")

    assert _import_edges(_project(consumer, target)) == []
