#!/usr/bin/env python3
"""Fails when two shared processors define the same top-level symbol differently.

AutoPkg imports a processor with `importlib.util.spec_from_file_location` and never adds the
containing directory to `sys.path` (autopkglib/__init__.py, _load_processor_from_file). So a shared
processor cannot `import` a sibling module - the import raises and AutoPkg turns that into
AutoPackagerLoadError. Duplication is therefore the only portable way to share a helper
between processors in this repo, which makes *undetected drift* the real risk rather than the
duplication itself.

This compares the parsed AST of every duplicated module-level constant and function, minus
docstrings, so reformatting and per-file prose are free while a changed regex or a changed
comparison is not. The prerelease-marker regex is the reason it exists: it decides whether a
tag counts as a stable release, and a copy that drifted would report a behind pin as current
in one processor and not the other.

Run over the shared processors, or pass paths. Exits 1 listing every symbol that disagrees.
"""

import ast
import copy
import os
import sys
from typing import Dict, List, Tuple

# Symbols that differ between files by design. `__all__` names the processor in each file, and
# `description` is assigned from each class's own docstring.
EXEMPT_SYMBOLS = frozenset(("__all__", "description"))
DEFAULT_SEARCH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SharedProcessors"
)
EXIT_DRIFT_FOUND = 1
EXIT_UNPARSABLE = 2


def main(argv: List[str]) -> int:
    paths = argv[1:] or sorted(
        os.path.join(DEFAULT_SEARCH_DIR, name)
        for name in os.listdir(DEFAULT_SEARCH_DIR)
        if name.endswith(".py") and not name.startswith("_")
    )
    symbols: Dict[str, List[Tuple[str, str]]] = {}
    for path in paths:
        try:
            for name, dumped in top_level_symbols(path):
                symbols.setdefault(name, []).append((os.path.basename(path), dumped))
        except (OSError, SyntaxError) as parse_error:
            print("could not parse {}: {}".format(path, parse_error), file=sys.stderr)
            return EXIT_UNPARSABLE

    drifted = {
        name: places
        for name, places in symbols.items()
        if len(places) > 1 and len({dumped for _, dumped in places}) > 1
    }
    if not drifted:
        print(
            "{} file(s), {} duplicated symbol(s), no drift".format(
                len(paths), len([1 for places in symbols.values() if len(places) > 1])
            )
        )
        return 0
    for name, places in sorted(drifted.items()):
        print(
            "DRIFT: {} differs across {}".format(
                name, ", ".join(sorted(filename for filename, _ in places))
            ),
            file=sys.stderr,
        )
    print(
        "\nThese must stay identical, or be renamed to say they are deliberately different.",
        file=sys.stderr,
    )
    return EXIT_DRIFT_FOUND


def top_level_symbols(path: str) -> List[Tuple[str, str]]:
    """(name, normalized AST) for each module-level constant and function definition."""
    with open(path, encoding="utf-8") as source_file:
        tree = ast.parse(source_file.read(), filename=path)
    found: List[Tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in EXEMPT_SYMBOLS:
                found.append((node.name, normalized(node)))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in EXEMPT_SYMBOLS:
                    found.append((target.id, normalized(node.value)))
    return found


def normalized(node: ast.AST) -> str:
    """AST dump with positions and docstrings dropped: behaviour is compared, prose is not.

    Each copy is entitled to explain itself in the terms of the file it lives in - the
    auditors' `as_list` says why an override cannot pass an array, which is noise in
    SantaRuleGenerator. Comparing docstrings would report that as drift every time, and a
    check that cries wolf gets removed. Comments were never comparable anyway; the AST does
    not carry them.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node):
        node = copy.deepcopy(node)
        node.body = node.body[1:]
    return ast.dump(node, annotate_fields=True, include_attributes=False)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
