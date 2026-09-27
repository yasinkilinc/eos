"""Where a symbol sits: its parent and a breadcrumb (2.x roadmap L3).

The parsers already say which class a method belongs to (`parent`), each in
its own shape: the Python plugin names a method `Invoice.total` with parent
`Invoice`, the Java plugin names it `price` with parent `Line`. This reads both
the same way and gives every symbol

  short        its own name, without the class in front
  parent_index the index of its parent among the file's symbols, or None
  breadcrumb   `path > Class > method`, the small-to-big chain a reader or a
               ranking can climb from a method to its class to its file

The parent is a container (a class, not a method or a constructor) with the
parent's exact name, else its short name; of several, the innermost whose
lines hold the symbol, else the nearest before it. A parent that is not among
the file's symbols still appears in the breadcrumb by name; a cycle is cut
where it repeats.
"""
from __future__ import annotations

# What a symbol of these kinds is never the parent of: a Java constructor shares
# its class's short name and would otherwise take every method after it.
NOT_CONTAINERS = frozenset({"method", "function", "constructor", "variable", "field", "property"})


def rows(path: str, found: list[dict]) -> list[dict]:
    short = []
    for symbol in found:
        name, parent = str(symbol.get("name") or ""), symbol.get("parent")
        short.append(name[len(parent) + 1:] if parent and name.startswith(f"{parent}.") else name)

    # Candidates by exact name and by short name, built once: O(n) per file.
    by_name: dict[str, list[int]] = {}
    by_short: dict[str, list[int]] = {}
    for index, symbol in enumerate(found):
        if str(symbol.get("kind") or "") in NOT_CONTAINERS:
            continue
        by_name.setdefault(str(symbol.get("name") or ""), []).append(index)
        by_short.setdefault(short[index], []).append(index)

    def parent_of(index: int) -> int | None:
        parent = found[index].get("parent")
        if not parent:
            return None
        exact = [i for i in by_name.get(str(parent), []) if i != index]
        pool = exact or [i for i in by_short.get(str(parent).rsplit(".", 1)[-1], []) if i != index]
        if len(pool) <= 1:
            return pool[0] if pool else None
        # Two containers under one name -- `Req.Builder` and `Resp.Builder`, which
        # the Java parser both calls `p.Builder`: the innermost one whose lines
        # hold the symbol, else the nearest one before it (review 19).
        line = _int(found[index].get("line"))
        holding = [i for i in pool if _int(found[i].get("line")) <= line <= _int(found[i].get("end_line"))]
        before = [i for i in pool if _int(found[i].get("line")) <= line]
        chosen = holding or before
        return max(chosen, key=lambda i: _int(found[i].get("line"))) if chosen else None

    parents = [parent_of(index) for index in range(len(found))]
    out = []
    for index, symbol in enumerate(found):
        chain, seen, at = [short[index]], {index}, index
        while True:
            up = parents[at]
            if up is None:
                named = found[at].get("parent")
                if named:
                    chain.insert(0, str(named))
                break
            if up in seen:
                break
            seen.add(up)
            chain.insert(0, short[up])
            at = up
        out.append({
            "name": str(symbol.get("name") or ""),
            "short": short[index],
            "kind": str(symbol.get("kind") or ""),
            "line": _int(symbol.get("line")),
            "end_line": _int(symbol.get("end_line")),
            "parent_index": parents[index],
            "breadcrumb": " > ".join([path, *chain]),
        })
    return out


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
