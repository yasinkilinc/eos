"""Java read by position, not by pattern.

Measured on a 2,397-file Spring tree before this: 2,633 types, 5,943 methods
*none of which recorded the class that declared them*, 0 fields, 0 extends and
0 implements. Against it: 243 package-private type declarations, 353 interface
files, roughly 7,516 method-shaped declarations and 2,630 `private final`
fields. Endpoints fared worse -- 82 of 108 emitted paths were empty, because
`@RequestMapping(value = "/x", produces = ...)` defeats a regex that cannot
cross a quote, and 17 of that tree's 22 uses take exactly that form.

After: 2,610 types (the 23 lost were declarations inside comments and string
literals), 6,452 methods all attached to their class, 9,013 fields, 946
implements, 556 extends, and 101 endpoint paths with none empty.
"""
import shutil
from pathlib import Path

from core.plugins.java.plugin import JavaPlugin
from core.plugins.java import structure

FIXTURE = Path(__file__).parent / "fixtures" / "java_structure" / "svc"
SRC = FIXTURE / "src" / "main" / "java" / "com" / "example" / "orders"
TESTS = FIXTURE / "src" / "test" / "java" / "com" / "example" / "orders"


def _parse(path: Path):
    parsed = structure.parse(path.read_text(encoding="utf-8"))
    structure.resolve_calls(parsed)
    return parsed


def _semantic(path: Path):
    return JavaPlugin().parse_file(str(path), path.read_text(encoding="utf-8"))


def test_request_mapping_with_named_arguments_keeps_its_base_path():
    parsed = _parse(SRC / "OrderController.java")

    endpoints = JavaPlugin.endpoints(parsed)

    assert endpoints == [("POST", "/orders/submit")], (
        f"the multi-argument @RequestMapping form lost its base path: {endpoints}"
    )


def test_package_private_class_and_its_methods_are_extracted():
    semantic = _semantic(SRC / "ValidateAgeCommand.java")

    types = [s for s in semantic.symbols if s.kind != "method"]
    methods = [s for s in semantic.symbols if s.kind == "method"]

    assert any(s.name.endswith("ValidateAgeCommand") for s in types), (
        f"a class with no visibility modifier was not found: {[s.name for s in types]}"
    )
    assert {"execute", "helper"} <= {m.name for m in methods}, (
        f"methods on a package-private class were not found: {[m.name for m in methods]}"
    )


def test_interface_method_without_a_modifier_is_extracted():
    semantic = _semantic(SRC / "OrderCommand.java")

    methods = [s.name for s in semantic.symbols if s.kind == "method"]

    assert methods == ["execute"], (
        f"an abstract interface method ends in ';' not '{{'; it must still count: {methods}"
    )


def test_method_is_attached_to_its_declaring_type():
    semantic = _semantic(SRC / "ValidateAgeCommand.java")

    execute = next(s for s in semantic.symbols if s.name == "execute")

    assert execute.parent == "com.example.orders.ValidateAgeCommand", execute


def test_nested_class_method_names_its_own_class():
    semantic = _semantic(SRC / "ValidateAgeCommand.java")

    helper = next(s for s in semantic.symbols if s.name == "helper")

    assert helper.parent == "com.example.orders.Nested", (
        f"a nested class's method was attributed to the outer class: {helper.parent}"
    )


def test_component_annotation_value_is_the_bean_name():
    """The identifier a configuration-driven system looks a step up by, and the
    one the previous parser never captured."""
    parsed = _parse(SRC / "ValidateAgeCommand.java")

    component = next(a for a in parsed.annotations if a.name == "@Component")

    assert component.values.get("") == "validateAgeCommand", component


def test_implements_is_recorded_with_its_owner():
    parsed = _parse(SRC / "ValidateAgeCommand.java")

    implemented = [r for r in parsed.type_refs if r.relation == "implements"]

    assert [(r.name, r.owner) for r in implemented] == [("OrderCommand", "ValidateAgeCommand")], implemented


def test_final_field_records_its_declared_type():
    parsed = _parse(SRC / "ValidateAgeCommand.java")

    assert [(f.name, f.type) for f in parsed.fields] == [("policy", "AgeLimitPolicy")], parsed.fields


def test_annotated_field_is_still_a_field():
    """@InjectMocks and @Mock sit inside the same statement text, so a pattern
    anchored at the start of it saw an annotation and gave up -- which is most
    fields on a Mockito test."""
    parsed = _parse(TESTS / "ValidateAgeCommandTest.java")

    assert {(f.name, f.type) for f in parsed.fields} == {
        ("policy", "AgeLimitPolicy"), ("command", "ValidateAgeCommand")}, parsed.fields


def test_call_resolves_through_a_field_type():
    parsed = _parse(SRC / "ValidateAgeCommand.java")

    permits = next(c for c in parsed.calls if c.method == "permits")

    assert permits.receiver == "policy" and permits.receiver_type == "AgeLimitPolicy", permits
    assert permits.from_method == "execute", permits


def test_a_declaration_inside_a_string_or_comment_is_not_a_declaration():
    """The whole reason the source is masked first. Before, an @Service inside
    an ArchUnit rule's string set the file's role, and was fenced off with a
    token-boundary guard in the plugin and a second guard in the classifier."""
    semantic = _semantic(SRC / "Trap.java")
    parsed = _parse(SRC / "Trap.java")

    names = {s.name.rsplit(".", 1)[-1] for s in semantic.symbols}
    assert "Ghost" not in names and "Commented" not in names and "Fake" not in names, names
    assert semantic.role is None, (
        f"an @Service written inside a string literal set the file's role: {semantic.role}"
    )
    assert not [r for r in parsed.type_refs if r.name in {"Phantom", "Bad", "Nothing"}], parsed.type_refs


def test_a_javadoc_brace_does_not_close_the_class():
    semantic = _semantic(SRC / "Trap.java")

    assert "rule" in {s.name for s in semantic.symbols if s.kind == "method"}, (
        f"a '}}' in the class Javadoc ended the class early: {[s.name for s in semantic.symbols]}"
    )


def test_parsing_one_file_does_not_affect_the_next():
    """PluginRegistry holds a single JavaPlugin for the process, and the old
    parser stored its newline offsets on that instance."""
    plugin = JavaPlugin()
    big = (SRC / "ValidateAgeCommand.java").read_text(encoding="utf-8")
    small = (SRC / "CommandResult.java").read_text(encoding="utf-8")

    plugin.parse_file("big.java", big)
    after = plugin.parse_file("small.java", small)
    alone = JavaPlugin().parse_file("small.java", small)

    assert [(s.name, s.line) for s in after.symbols] == [(s.name, s.line) for s in alone.symbols], (
        "line numbers depended on what was parsed before"
    )


def test_a_file_that_is_entirely_commented_out_declares_nothing():
    """Seven files on the measured tree are exactly this, and the old parser
    reported a phantom interface for each."""
    dead = "//public interface Ghost {\n//    void method();\n//}\n"

    semantic = JavaPlugin().parse_file("Ghost.java", dead)

    assert semantic.symbols == [], semantic.symbols


def test_bench_reports_dependent_recall_as_absent_not_zero_without_java(tmp_path):
    """A 0% line on a project with no Java would be a false claim about EOS --
    the same principle the coverage table applies to detectors."""
    from core import bench

    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")

    assert bench._java_dependent_pairs(tmp_path) == []
    assert bench._bench_dependents(tmp_path) is None


def test_the_dependent_oracle_ignores_a_name_that_only_appears_in_a_comment(tmp_path):
    """Masking is what keeps the oracle independent of the thing it measures."""
    from core import bench

    main = tmp_path / "src" / "main" / "java" / "com" / "example"
    test = tmp_path / "src" / "test" / "java" / "com" / "example"
    main.mkdir(parents=True)
    test.mkdir(parents=True)
    (main / "Widget.java").write_text("package com.example;\npublic class Widget { }\n", encoding="utf-8")
    (test / "WidgetTest.java").write_text(
        "package com.example;\n// This test does not touch Widget yet.\nclass WidgetTest { }\n",
        encoding="utf-8")

    assert bench._java_dependent_pairs(tmp_path) == [], (
        "a subject named only in a comment is not a reference"
    )


def test_entry_points_document_names_the_routes_it_serves(tmp_path):
    """The parser carried endpoints in node.doc and no generator rendered one,
    so a file listing 20 controllers named zero of their endpoints."""
    import shutil
    from core.generators.markdown.brain import BrainGenerator
    from core.knowledge.builder import KnowledgeBuilder
    from core.lib.cache_store import CacheStore
    from core.scanner import Scanner

    root = tmp_path / "svc"
    shutil.copytree(FIXTURE, root)
    project = Scanner(root, CacheStore(root / ".eos" / "data" / "cache")).scan(full=True)
    graph = KnowledgeBuilder().build(project, root=root)
    brain = root / ".eos" / "data" / "brain"
    BrainGenerator(graph).generate(brain)

    rendered = (brain / "EntryPoints.md").read_text(encoding="utf-8")

    assert "OrderController" in rendered, rendered
    assert "**POST** `/orders/submit`" in rendered, (
        f"the entry point was listed without the route it serves:\n{rendered}"
    )
