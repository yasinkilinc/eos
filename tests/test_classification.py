"""What a plugin detects from the source must reach the artifacts.

The Java plugin has always derived a Spring role from the annotations, and the
classifier has always thrown it away and guessed from the path instead -- whose
last resort, "main" in the filename, cannot fire on a Maven layout. Measured
across 2,149 real Java files: 0 entry points against 67 @RestController classes,
and EntryPoints.md 17 bytes on 15 of 18 instances.
"""
import shutil
from pathlib import Path

from core.knowledge.builder import KnowledgeBuilder
from core.lib.cache_store import CacheStore
from core.scanner import Scanner

FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"

CONTROLLER = "file:svc/src/main/java/com/example/api/GreetController.java"
ADVICE = "file:svc/src/main/java/com/example/api/ApiExceptionHandler.java"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    return root


def _build(root: Path):
    project = Scanner(root, CacheStore(root / ".eos" / "data" / "cache")).scan(full=True)
    return KnowledgeBuilder().build(project, root=root)


def test_rest_controller_becomes_an_entry_point(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    monkeypatch.chdir(root)
    graph = _build(root)

    assert CONTROLLER in graph.entry_points, (
        f"@RestController did not survive classification; entry points: {graph.entry_points}"
    )


def test_controller_advice_is_not_an_entry_point(tmp_path, monkeypatch):
    """The annotation test is a substring match, so '@Controller' fires inside
    '@ControllerAdvice'. An exception handler is not an entry point."""
    root = _copy(tmp_path)
    monkeypatch.chdir(root)
    graph = _build(root)

    assert ADVICE not in graph.entry_points, (
        "@ControllerAdvice was mistaken for @Controller"
    )


def test_a_file_in_the_test_tree_stays_a_test_whatever_it_mentions(tmp_path, monkeypatch):
    """The plugin reads annotations out of raw content, so an ArchUnit rule
    naming "@Service" in a string sets the role. Two such files on a real
    service moved out of `test` when the role was allowed to win."""
    root = _copy(tmp_path)
    monkeypatch.chdir(root)
    graph = _build(root)

    arch_test = next(
        n for n in graph.nodes if str(n.path).endswith("GreetArchTest.java")
    )
    assert arch_test.type == "test", f"a test was classified as {arch_test.type}"


def test_tech_stack_reads_manifests_not_just_languages(tmp_path, monkeypatch):
    """TechStack.md was byte-identical (48 bytes) for two unrelated projects
    because the stack was the language list restated."""
    root = _copy(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastapi", "sqlalchemy"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    graph = _build(root)

    assert "fastapi" in graph.tech_stack, f"manifest not read: {graph.tech_stack}"
    assert "sqlalchemy" in graph.tech_stack


def test_a_package_named_latest_does_not_swallow_a_controller():
    """The test check matched 'test' anywhere in the path, and a test outranks
    a plugin role -- so `com/acme/latest/OrderController.java`, `contest`,
    `testdata` and `ProtestController` all classified a real @RestController as
    a test."""
    from core.knowledge.classifier import Classifier
    from core.knowledge.semantic import FileSemantic

    classifier = Classifier()
    not_tests = [
        "src/main/java/com/acme/latest/OrderController.java",
        "src/main/java/com/acme/contest/PollController.java",
        "src/main/java/com/acme/api/ProtestController.java",
        "src/main/java/com/acme/testdata/SeedController.java",
    ]
    for path in not_tests:
        node_type, _ = classifier.classify_file(
            FileSemantic(path=path, language="java", role="entry-point")
        )
        assert node_type == "entry-point", f"{path} classified as {node_type}"

    real_tests = [
        "src/test/java/com/acme/api/OrderControllerTest.java",
        "tests/test_orders.py",
        "src/__tests__/Button.spec.ts",
    ]
    for path in real_tests:
        node_type, _ = classifier.classify_file(
            FileSemantic(path=path, language="java", role="entry-point")
        )
        assert node_type == "test", f"{path} classified as {node_type}"


def _stack_for(tmp_path, files: dict) -> set:
    """Tech stack detected for a project containing exactly `files`."""
    from core.knowledge.builder import KnowledgeBuilder

    root = tmp_path / "proj"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    builder = KnowledgeBuilder()
    builder._root = root
    return builder._stack_from_manifests()


def test_frameworks_come_from_declared_dependencies_not_from_prose(tmp_path):
    """Grepping the manifest text reported react for a package whose only
    mention of it was a description, spring-boot for one that explicitly
    EXCLUDED it, and django for a comment saying it is not used."""
    assert "react" not in _stack_for(
        tmp_path / "a",
        {"package.json": '{"description":"React context helpers","dependencies":{"lodash":"^4"}}'},
    )
    assert "spring-boot" not in _stack_for(
        tmp_path / "b",
        {"pom.xml": """<project><dependencies><dependency>
             <groupId>com.acme</groupId><artifactId>widget</artifactId>
             <exclusions><exclusion>
               <groupId>org.springframework.boot</groupId>
               <artifactId>spring-boot-starter-tomcat</artifactId>
             </exclusion></exclusions>
           </dependency></dependencies></project>"""},
    )
    assert "django" not in _stack_for(
        tmp_path / "c",
        {"requirements.txt": "# we deliberately do not use django here\nrequests==2.31.0\n"},
    )


def test_frameworks_are_still_found_when_genuinely_declared(tmp_path):
    assert "vite" in _stack_for(
        tmp_path / "d", {"package.json": '{"devDependencies":{"vite":"^6.0.0"}}'}
    )
    assert "spring-boot" in _stack_for(
        tmp_path / "e",
        {"pom.xml": """<project><dependencies><dependency>
             <groupId>org.springframework.boot</groupId>
             <artifactId>spring-boot-starter-web</artifactId>
           </dependency></dependencies></project>"""},
    )
    assert "fastapi" in _stack_for(
        tmp_path / "f", {"requirements.txt": "fastapi==0.115.0\npytest>=8\n"}
    )
    assert "sqlalchemy" in _stack_for(
        tmp_path / "g",
        {"pyproject.toml": '[project]\nname = "x"\ndependencies = ["SQLAlchemy>=2.0", "fastapi"]\n'},
    )


def test_framework_tokens_need_a_word_boundary(tmp_path):
    """`react` is a substring of io.projectreactor and reactor-core."""
    stack = _stack_for(
        tmp_path / "reactor",
        {"pom.xml": """<project><dependencies>
             <dependency><groupId>io.projectreactor</groupId>
             <artifactId>reactor-core</artifactId></dependency>
           </dependencies></project>"""},
    )
    assert "react" not in stack, f"reactor read as react: {sorted(stack)}"
