"""Read Java structure from masked source, by position rather than by pattern.

The previous parser asked regexes questions that regexes cannot answer. A
method was recognised by requiring `public|protected|private` and a trailing
`{`, so package-private methods, interface methods and generic declarations
were all invisible; measured on one 2,397-file tree, 5,943 methods were found
against roughly 7,516 candidates, none of them attached to the class that
declared them, and no field or `implements` was extracted at all.

This reads the masked text (core.plugins.java.lexer) with a brace-depth
counter and a stack of open type declarations. Nesting and ownership then fall
out of the stack instead of being pattern-matched, and a member is recognised
by *where it sits and how it ends* -- `(...)` then `{` or `;` is a method,
a name then `;` or `=` at type scope is a field -- which needs no modifier at
all.

It is not a Java grammar and does not try to be. It resolves what is decidable
from one file plus its imports, and says so when it cannot.
"""
from __future__ import annotations

import re

from core.knowledge.semantic import Annotation, Call, Field, Symbol, TypeRef
from core.plugins.java.lexer import Source, lex

_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE)
_TYPE_DECL = re.compile(
    r"\b(?P<kind>class|interface|enum|record)\s+(?P<name>\w+)")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_ANNOTATION = re.compile(r"@([A-Za-z_$][\w$.]*)")
# A call site: an optional receiver chain, then a name, then an open paren.
_CALL = re.compile(r"(?:(?P<recv>[A-Za-z_$][\w$]*)\s*\.\s*)?(?P<name>[A-Za-z_$][\w$]*)\s*\(")

_MODIFIERS = frozenset({
    "public", "protected", "private", "static", "final", "abstract", "synchronized",
    "native", "transient", "volatile", "strictfp", "default", "sealed", "non-sealed",
})
# Keywords that open a parenthesis but are not calls.
_NOT_A_CALL = frozenset({
    "if", "for", "while", "switch", "catch", "return", "new", "synchronized",
    "this", "super", "assert", "do", "else", "try", "throw", "instanceof", "case",
})
_TYPE_KINDS = frozenset({"class", "interface", "enum", "record"})


class _Scope:
    __slots__ = ("kind", "name", "depth", "start")

    def __init__(self, kind: str, name: str, depth: int, start: int):
        self.kind, self.name, self.depth, self.start = kind, name, depth, start


class JavaStructure:
    """Everything one Java file declares, references and calls."""

    def __init__(self, source: Source):
        self.source = source
        self.package: str | None = None
        self.imports: dict[str, str] = {}      # simple name -> fully qualified
        self.wildcards: list[str] = []
        self.symbols: list[Symbol] = []
        self.annotations: list[Annotation] = []
        self.fields: list[Field] = []
        self.type_refs: list[TypeRef] = []
        self.calls: list[Call] = []


def parse(text: str) -> JavaStructure:
    source = lex(text)
    out = JavaStructure(source)
    masked = source.masked

    package = _PACKAGE.search(masked)
    out.package = package.group(1) if package else None
    for match in _IMPORT.finditer(masked):
        qualified = match.group(1)
        simple = qualified.rsplit(".", 1)[-1]
        if simple == "*":
            out.wildcards.append(qualified[:-2])
        else:
            out.imports[simple] = qualified

    _walk(out, masked)
    return out


def _walk(out: JavaStructure, masked: str) -> None:
    """One pass over the file, tracking brace depth and open declarations."""
    stack: list[_Scope] = []
    pending: list[Annotation] = []          # annotations read but not yet placed
    member_start = 0                        # where the current statement began
    depth = 0
    i = 0
    length = len(masked)

    while i < length:
        char = masked[i]

        if char == "@" and _ANNOTATION.match(masked, i):
            i = _read_annotation(out, masked, i, pending)
            continue

        if char == "{":
            head = masked[member_start:i]
            declared = _read_type_decl(out, head, member_start, depth, pending)
            if declared is not None:
                stack.append(_Scope(declared.kind, declared.name, depth, i))
                out.symbols.append(declared.symbol)
                pending = []
            else:
                method = _read_method(out, head, member_start, stack, pending)
                if method is not None:
                    stack.append(_Scope("method", method.name, depth, i))
                    out.symbols.append(method)
                    pending = []
                else:
                    # `if (policy.permits(x)) {` is a statement that ends in a
                    # brace, not a semicolon. Reading calls only at `;` lost
                    # every call made inside a condition -- which on guard-heavy
                    # code is most of them.
                    _read_calls(out, head, member_start, _enclosing_type(stack),
                                stack[-1].name if stack and stack[-1].kind == "method" else None)
            depth += 1
            member_start = i + 1
            i += 1
            continue

        if char == "}":
            depth -= 1
            while stack and stack[-1].depth >= depth:
                closed = stack.pop()
                _close(out, closed, i)
            member_start = i + 1
            i += 1
            continue

        if char == ";":
            head = masked[member_start:i]
            _read_statement(out, head, member_start, stack, pending, depth)
            pending = []
            member_start = i + 1
            i += 1
            continue

        i += 1

    # A file that does not close every brace still yields what it declared.
    while stack:
        _close(out, stack.pop(), length)


class _Declared:
    __slots__ = ("kind", "name", "symbol")

    def __init__(self, kind: str, name: str, symbol: Symbol):
        self.kind, self.name, self.symbol = kind, name, symbol


def _read_annotation(out: JavaStructure, masked: str, at: int,
                     pending: list[Annotation]) -> int:
    match = _ANNOTATION.match(masked, at)
    name = match.group(1)
    end = match.end()
    values: dict[str, str] = {}
    # Arguments are read by matching parentheses, which is the whole reason the
    # source is masked first: `@RequestMapping(value = "/x", produces = X.Y)`
    # defeats any regex that cannot cross a quote.
    probe = end
    while probe < len(masked) and masked[probe] in " \t\n\r":
        probe += 1
    if probe < len(masked) and masked[probe] == "(":
        close = _matching(masked, probe)
        values = _annotation_values(out.source, probe, close)
        end = close + 1
    annotation = Annotation(name="@" + name, values=values, line=out.source.line_of(at))
    out.annotations.append(annotation)
    pending.append(annotation)
    out.type_refs.append(TypeRef(name=name.rsplit(".", 1)[-1], relation="annotation",
                                 line=annotation.line))
    return end


def _matching(text: str, open_at: int) -> int:
    """Index of the parenthesis closing the one at `open_at`, or end of text."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


def _annotation_values(source: Source, open_at: int, close_at: int) -> dict[str, str]:
    """Named arguments, plus the single unnamed one under "".

    Values come from the literal table, because the masked text no longer
    holds them -- which is exactly what makes `@Component("beanName")`
    recoverable where the old regex lost it.
    """
    body = source.masked[open_at + 1:close_at]
    values: dict[str, str] = {}
    literals = [(at, source.literals[at]) for at in sorted(source.literals)
                if open_at < at < close_at]
    if not literals:
        return values
    for at, value in literals:
        # The name is whatever `key =` precedes this literal inside the parens.
        before = source.masked[open_at + 1:at]
        cut = max(before.rfind(","), before.rfind("{"))
        assignment = before[cut + 1:]
        key, sep, _ = assignment.partition("=")
        values[key.strip() if sep else ""] = value
    return values


def _read_type_decl(out: JavaStructure, head: str, offset: int, depth: int,
                    pending: list[Annotation]) -> _Declared | None:
    match = _TYPE_DECL.search(head)
    if match is None:
        return None
    kind, name = match.group("kind"), match.group("name")
    line = out.source.line_of(offset + match.start())
    tail = head[match.end():]
    for relation, keyword in (("extends", "extends"), ("implements", "implements")):
        section = _clause(tail, keyword)
        for referenced in _type_names(section):
            out.type_refs.append(TypeRef(name=referenced, relation=relation,
                                         line=line, owner=name))
    for annotation in pending:
        annotation.target = name
    symbol = Symbol(
        name=name, kind=kind, line=line,
        modifiers=sorted(word for word in _IDENT.findall(head) if word in _MODIFIERS),
        annotations=[a.name for a in pending],
    )
    return _Declared(kind, name, symbol)


def _clause(tail: str, keyword: str) -> str:
    """The text a `extends`/`implements` keyword introduces, up to the next one."""
    match = re.search(rf"\b{keyword}\b", tail)
    if match is None:
        return ""
    rest = tail[match.end():]
    stop = re.search(r"\b(extends|implements|permits)\b", rest)
    return rest[:stop.start()] if stop else rest


def _type_names(section: str) -> list[str]:
    """Top-level names in a type list, ignoring generic arguments.

    `implements Handler<Event>, Closeable` is Handler and Closeable -- Event is
    a parameter of the first, not a second interface.
    """
    names, depth, current = [], 0, []
    for char in section:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            names.append("".join(current))
            current = []
        elif depth == 0:
            current.append(char)
    names.append("".join(current))
    out = []
    for name in names:
        simple = name.strip().rsplit(".", 1)[-1]
        if simple and _IDENT.fullmatch(simple):
            out.append(simple)
    return out


def _read_method(out: JavaStructure, head: str, offset: int, stack: list[_Scope],
                 pending: list[Annotation]) -> Symbol | None:
    """A declaration ending in `(...)` immediately before a `{` is a method body.

    No modifier is required, which is the point: package-private methods and
    interface default methods were both invisible before.
    """
    owner = _enclosing_type(stack)
    if owner is None:
        return None
    if any(scope.kind == "method" for scope in stack):
        return None            # a local class or lambda body, not a member
    close = head.rstrip().rfind(")")
    if close == -1:
        return None
    open_at = _matching_back(head, close)
    if open_at is None:
        return None
    before = head[:open_at].strip()
    match = None
    for match in _IDENT.finditer(before):
        pass
    if match is None:
        return None
    name = match.group(0)
    if name in _NOT_A_CALL or name in _MODIFIERS:
        return None
    # A declaration names a return type before the method name; a call names a
    # receiver and a dot. Without this, `this.foo = new Foo(bar)` reads as a
    # declaration of a method called Foo.
    if before[:match.start()].rstrip().endswith("."):
        return None
    returns = before[:match.start()].strip().split()
    if not returns:
        return None
    for annotation in pending:
        annotation.target = f"{owner}.{name}"
    return Symbol(
        name=name, kind="method", line=out.source.line_of(offset + match.start()),
        parent=owner,
        signature=" ".join(head.split()),
        returns=returns[-1] if returns and returns[-1] not in _MODIFIERS else None,
        modifiers=sorted(word for word in returns if word in _MODIFIERS),
        annotations=[a.name for a in pending],
    )


def _matching_back(text: str, close_at: int) -> int | None:
    depth = 0
    for i in range(close_at, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                return i
    return None


_LEADING_ANNOTATION = re.compile(r"^\s*@[A-Za-z_$][\w$.]*\s*")


def _strip_annotations(declaration: str) -> str:
    """Remove leading `@Anno` / `@Anno(...)` from a declaration."""
    text = declaration
    while True:
        match = _LEADING_ANNOTATION.match(text)
        if match is None:
            return text
        text = text[match.end():]
        if text.startswith("("):
            close = _matching(text, 0)
            text = text[close + 1:]


_FIELD = re.compile(
    r"^(?P<mods>(?:\s*(?:public|protected|private|static|final|transient|volatile)\b)*)"
    r"\s*(?P<type>[A-Za-z_$][\w$.]*(?:\s*<[^;]*>)?(?:\s*\[\s*\])*)"
    r"\s+(?P<name>[A-Za-z_$][\w$]*)\s*$")


def _read_statement(out: JavaStructure, head: str, offset: int, stack: list[_Scope],
                    pending: list[Annotation], depth: int) -> None:
    """A `;`-terminated statement: a field at type scope, or calls anywhere."""
    owner = _enclosing_type(stack)
    method = stack[-1].name if stack and stack[-1].kind == "method" else None

    if owner is not None and method is None:
        # A signature terminated by `;` is an abstract or interface method. It
        # has a body nowhere, so the brace path never sees it, and requiring a
        # body is how 353 interface files came to declare no methods at all.
        # Only a statement with no initialiser can be a signature. A field
        # written `private final Foo foo = new Foo(bar);` ends in `)` too, and
        # reading it as a method silently dropped the field and its type.
        abstract = _read_method(out, head, offset, stack, pending) if "=" not in head else None
        if abstract is not None:
            abstract.modifiers = sorted(set(abstract.modifiers) | {"abstract"})
            abstract.end_line = abstract.line
            out.symbols.append(abstract)
            return

        # Annotations sit inside the same statement text, so a field written as
        # `@InjectMocks private FooService service;` never reached the field
        # pattern -- which is most fields on a Mockito test and every
        # `@Qualifier`-injected collaborator.
        declaration = _strip_annotations(head.split("=", 1)[0]).strip()
        match = _FIELD.match(declaration)
        if match is not None:
            type_name = match.group("type").split("<")[0].strip().rstrip("[]").strip()
            simple = type_name.rsplit(".", 1)[-1]
            line = out.source.line_of(offset + match.start("name"))
            out.fields.append(Field(
                name=match.group("name"), type=simple, owner=owner, line=line,
                modifiers=sorted(word for word in match.group("mods").split() if word in _MODIFIERS),
                annotations=[a.name for a in pending],
            ))
            out.type_refs.append(TypeRef(name=simple, relation="field", line=line, owner=owner))

    _read_calls(out, head, offset, owner, method)


def _read_calls(out: JavaStructure, head: str, offset: int,
                owner: str | None, method: str | None) -> None:
    for match in _CALL.finditer(head):
        name = match.group("name")
        if name in _NOT_A_CALL:
            continue
        receiver = match.group("recv")
        if receiver in _NOT_A_CALL:
            receiver = None
        out.calls.append(Call(
            method=name, receiver=receiver, from_type=owner, from_method=method,
            line=out.source.line_of(offset + match.start("name")),
        ))
    for match in re.finditer(r"\bnew\s+([A-Za-z_$][\w$.]*)", head):
        simple = match.group(1).rsplit(".", 1)[-1]
        out.type_refs.append(TypeRef(name=simple, relation="new",
                                     line=out.source.line_of(offset + match.start(1)),
                                     owner=owner))


def _enclosing_type(stack: list[_Scope]) -> str | None:
    for scope in reversed(stack):
        if scope.kind in _TYPE_KINDS:
            return scope.name
    return None


def _close(out: JavaStructure, scope: _Scope, at: int) -> None:
    line = out.source.line_of(at)
    for symbol in out.symbols:
        if symbol.name == scope.name and symbol.line <= line and not symbol.end_line:
            symbol.end_line = line
            break


def resolve_calls(structure: JavaStructure) -> None:
    """Attach a declared type to each call's receiver, where one is knowable.

    Fields first, then the receiver read as a type name itself (a static call).
    Anything else stays None rather than being guessed -- an unresolved call is
    a fact about what could not be determined, and coverage reports it.
    """
    by_owner: dict[tuple[str | None, str], str] = {}
    for field in structure.fields:
        by_owner[(field.owner, field.name)] = field.type
    for call in structure.calls:
        if call.receiver is None:
            call.receiver_type = call.from_type
            continue
        declared = by_owner.get((call.from_type, call.receiver))
        if declared is not None:
            call.receiver_type = declared
        elif call.receiver[:1].isupper():
            call.receiver_type = call.receiver
