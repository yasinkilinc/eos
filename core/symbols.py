"""Where a symbol sits: its parent and a breadcrumb (2.x roadmap L3).

The parsers already say which class a method belongs to (`parent`), each in
its own shape: the Python plugin names a method `Invoice.total` with parent
`Invoice`, the Java plugin names it `price` with parent `Line`. This reads both
the same way and gives every symbol

  short        its own name, without the class in front
  parent_index the index of its parent among the file's symbols, or None
  breadcrumb   `path > Class > method`, the small-to-big chain a reader or a
               ranking can climb from a method to its class to its file

A parent that is not among the file's symbols (a parser that saw the method
but not the class) still appears in the breadcrumb by name; a cycle is cut
where it repeats.
"""
from __future__ import annotations


def rows(path: str, found: list[dict]) -> list[dict]:
    short = []
    for symbol in found:
        name, parent = str(symbol.get("name") or ""), symbol.get("parent")
        short.append(name[len(parent) + 1:] if parent and name.startswith(f"{parent}.") else name)

    def parent_of(index: int) -> int | None:
        parent = found[index].get("parent")
        if not parent:
            return None
        last = str(parent).rsplit(".", 1)[-1]
        line = _int(found[index].get("line"))
        candidates = [i for i, symbol in enumerate(found)
                      if i != index and (symbol.get("name") == parent or short[i] == last)]
        before = [i for i in candidates if _int(found[i].get("line")) <= line]
        pool = before or candidates
        return max(pool, key=lambda i: _int(found[i].get("line"))) if pool else None

    parents = [parent_of(index) for index in range(len(found))]
    out = []
    for index, symbol in enumerate(found):
        chain, seen, at = [short[index]], {index}, index
        while True:
            up = parents[at]
            if up is None:
                named = found[at].get("parent")
                if named:
                    chain[:0] = str(named).split(".")
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
