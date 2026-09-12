"""Java / Spring Boot plugin for EOS.

Uses regex-based parsing to extract packages, imports, classes, interfaces,
records, and basic Spring Boot annotations.
"""
import bisect
import re
from pathlib import Path
from typing import List

from ..base import LanguagePlugin
from ...knowledge.semantic import Export, FileSemantic, Import, Symbol

_RE_PACKAGE = re.compile(r"^\s*package\s+(?P<pkg>[\w\.]+)\s*;", re.MULTILINE)
_RE_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?(?P<module>[\w\.]+)\s*;", re.MULTILINE)
_RE_JAVADOC = re.compile(r"/\*\*(.*?)\*/", re.DOTALL)

# Heuristic to find class/interface/enum/record declarations
_RE_CLASS = re.compile(
    r"(?:public|private|protected|abstract|final|static|\s)*\b(?P<kind>class|interface|record|enum)\s+(?P<name>\w+)",
    re.MULTILINE
)

# Basic heuristic to find methods
_RE_METHOD = re.compile(
    r"(?:public|protected|private)\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+)?(?:[\w<>,\[\]\s]+)\s+(?P<name>\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
    re.MULTILINE
)

# Spring Boot Annotations Mapping
_SPRING_ANNOTATIONS = {
    "@Service": "service",
    "@RestController": "entry-point",
    "@Controller": "entry-point",
    "@Repository": "component",
    "@Component": "component",
    "@Configuration": "config",
}

_PRODUCT_VERSION_RE = re.compile(r"<product\.version>\s*([^<\s]+)\s*</product\.version>")


class JavaPlugin(LanguagePlugin):
    name = "java"
    extensions = (".java",)

    def detect(self, root: Path) -> bool:
        # Marker-based; .java files are caught by the extension walk.
        return (root / "pom.xml").exists() or (root / "build.gradle").exists()

    def parent_ref(self, root: Path) -> str | None:
        pom = root / "pom.xml"
        if not pom.is_file():
            return None
        try:
            text = pom.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match = _PRODUCT_VERSION_RE.search(text)
        return match.group(1) if match else None

    def parse_file(self, rel_path: str, content: str) -> FileSemantic:
        file_sem = FileSemantic(path=rel_path, language=self.name)
        self._prepare_lines(content)

        # Package (act as module prefix)
        pkg_match = _RE_PACKAGE.search(content)
        pkg_name = pkg_match.group("pkg") if pkg_match else ""

        self._extract_imports(file_sem, content)
        self._extract_symbols(file_sem, content, pkg_name)
        
        doc_match = _RE_JAVADOC.search(content)
        lines = []
        if doc_match:
            raw_doc = doc_match.group(1)
            for line in raw_doc.splitlines():
                line = line.strip()
                if line.startswith("*"):
                    line = line[1:].strip()
                if line:
                    lines.append(line)
        
        # --- Extract Spring Boot Architecture Semantics ---
        spring_features = []
        
        # 1. REST Endpoints
        base_path = ""
        rm_match = re.search(r'@RequestMapping\(\s*"?([^"\)]+)"?\s*\)', content)
        if rm_match:
            base_path = rm_match.group(1)
            
        endpoints = re.findall(r'@(Get|Post|Put|Delete|Patch)Mapping(?:\(\s*"?([^"\)]+)"?\s*\))?', content)
        if endpoints or base_path:
            spring_features.append("### 🌐 REST API Endpoints")
            if base_path and not endpoints:
                spring_features.append(f"- **Base Path:** `{base_path}`")
            for method, path in endpoints:
                full_path = f"{base_path}{path}" if path else base_path
                spring_features.append(f"- **{method.upper()}** `{full_path}`")
                
        # 2. External Calls / WebClient
        if "WebClient" in content or "RestTemplate" in content:
            spring_features.append("### 📡 External Integrations")
            if "WebClient" in content:
                spring_features.append("- Uses **WebClient** (Reactive HTTP Client)")
            if "RestTemplate" in content:
                spring_features.append("- Uses **RestTemplate** (HTTP Client)")
                
        # 3. Database Repositories
        if "JpaRepository" in content or "MongoRepository" in content:
            spring_features.append("### 🗄️ Database Access")
            spring_features.append("- Spring Data Repository detected")
            
        # 4. Kafka / Messaging
        kafka_listeners = re.findall(r'@KafkaListener\(topics\s*=\s*(.*?)\)', content)
        if kafka_listeners:
            spring_features.append("### 📨 Message Listeners (Kafka)")
            for topic in kafka_listeners:
                spring_features.append(f"- Listens to topic: `{topic}`")
                
        if spring_features:
            if lines:
                lines.append("\n---\n")
            lines.extend(spring_features)
            
        if lines:
            file_sem.doc = "\n".join(lines).strip()
        
        return file_sem

    def _extract_imports(self, file_sem: FileSemantic, content: str) -> None:
        for match in _RE_IMPORT.finditer(content):
            module_full = match.group("module")
            # For Java, module_full is like org.springframework.stereotype.Service
            # We treat the last part as the 'name' and the rest as the 'module'
            parts = module_full.rsplit(".", 1)
            if len(parts) == 2:
                module, name = parts
            else:
                module, name = module_full, module_full
                
            file_sem.imports.append(
                Import(
                    module=module,
                    name=name,
                    is_relative=False,
                    line=self._line(content, match),
                )
            )

    def _extract_symbols(self, file_sem: FileSemantic, content: str, pkg_name: str) -> None:
        # Detect the Spring role from annotations anywhere in the file.
        #
        # The match has to end on a token boundary: a plain substring test
        # makes "@Controller" fire inside "@ControllerAdvice" (an exception
        # handler read as an entry point), "@Component" inside
        # "@ComponentScan", and "@Configuration" inside
        # "@ConfigurationProperties" -- 60 files mis-tagged that way in a
        # single service.
        primary_kind = None
        for anno, mapped_kind in _SPRING_ANNOTATIONS.items():
            if re.search(re.escape(anno) + r"\b(?!\w)", content):
                primary_kind = mapped_kind
                break
        file_sem.role = primary_kind

        for match in _RE_CLASS.finditer(content):
            raw_kind = match.group("kind")
            name = match.group("name")
            
            # If we found a Spring annotation, override the class kind with the Spring role
            # This makes the graph much more descriptive (e.g. 'service' instead of 'class')
            kind = primary_kind if primary_kind and raw_kind == "class" else raw_kind
            
            # In Java, fully qualified name includes package
            full_name = f"{pkg_name}.{name}" if pkg_name else name
            
            file_sem.exports.append(Export(name=full_name, kind=kind, line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=full_name, kind=kind, line=self._line(content, match)))

        # Very basic method extraction
        for match in _RE_METHOD.finditer(content):
            # Exclude keywords
            if match.group("name") not in {"if", "for", "while", "switch", "catch"}:
                file_sem.symbols.append(
                    Symbol(name=match.group("name"), kind="method", line=self._line(content, match))
                )

    def _prepare_lines(self, content: str) -> None:
        """Precompute newline offsets once per file.

        Counting newlines from the start of the file per match is quadratic in
        file size, and a Java service parses thousands of matches per file.
        """
        self._offsets: List[int] = []
        start = content.find("\n")
        while start != -1:
            self._offsets.append(start)
            start = content.find("\n", start + 1)

    def _line(self, content: str, match: re.Match) -> int:
        return bisect.bisect_right(self._offsets, match.start()) + 1
