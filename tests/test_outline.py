"""core.outline: headings and definitions with their line numbers."""
from core import outline


def test_python_java_and_markdown_outlines():
    py = "import os\n\nclass A:\n    def m(self):\n        pass\n\nasync def f():\n    pass\n"
    assert outline.outline(py, outline.kind_of("x.py")) == ["3: class A:", "4: def m(self):", "7: async def f():"]
    java = ("package a;\n@Service\npublic class OrderService {\n    private final Repo repo;\n"
            "    public Order find(String id) {\n        return repo.get(id);\n    }\n}\n")
    assert outline.outline(java, outline.kind_of("A.java")) == [
        "3: public class OrderService {", "5: public Order find(String id) {"]
    md = "# A\ntext\n```bash\n# comment\n```\n## B\n"
    assert outline.outline(md, "markdown") == ["1: # A", "6: ## B"]


def test_unknown_kinds_have_no_outline_and_long_ones_are_capped():
    assert outline.outline("anything\n", outline.kind_of("x.bin")) == []
    many = "\n".join(f"def f{i}():" for i in range(60))
    lines = outline.outline(many, "python", limit=40)
    assert len(lines) == 41 and lines[-1] == "(+20 more)"
