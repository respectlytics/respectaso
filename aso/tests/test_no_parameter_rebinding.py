"""No loop, with or except in our code rebinds a parameter of its function.

2.28.0 shipped with every AI Competitor run failing: a loop over search
results was written ``for app in apps[:10]`` inside the function whose
``app`` parameter is the app every keyword is scored for, so from the second
keyword on it scored for a search result. A loop target is never meant to
replace a parameter, so this reads every function of ours and fails on one
that does. Plain assignments are left alone: ``language = language or
"en-US"`` is how a default is filled in.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PACKAGES = ("core", "aso", "aso_pro", "licensing", "llm_providers")


def _names(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List, ast.Starred)):
        for element in getattr(target, "elts", [getattr(target, "value", None)]):
            if element is not None:
                yield from _names(element)


def _own_nodes(node):
    """Every node of a function body, without entering nested scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _own_nodes(child)


def rebound_parameters(source, filename="<source>"):
    """(line, function, parameter) for each loop, with or except target that
    reuses a parameter's name."""
    found = []
    for fn in ast.walk(ast.parse(source, filename)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = fn.args
        params = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        params |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
        params -= {"self", "cls"}
        for node in _own_nodes(fn):
            if isinstance(node, (ast.For, ast.AsyncFor)):
                targets = _names(node.target)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                targets = _names(node.optional_vars)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                targets = [node.name]
            else:
                continue
            found += [(node.lineno, fn.name, name) for name in targets if name in params]
    return found


class NoParameterRebindingTest(SimpleTestCase):
    def test_the_check_finds_the_2_28_0_bug(self):
        source = (
            "def run(keywords, app=None):\n"
            "    for kw in keywords:\n"
            "        score(kw, app=app)\n"
            "        for app in search(kw):\n"
            "            record(app)\n"
        )
        self.assertEqual(rebound_parameters(source), [(4, "run", "app")])

    def test_it_leaves_defaults_and_nested_functions_alone(self):
        source = (
            "def run(language=None, app=None):\n"
            "    language = language or 'en-US'\n"
            "    def peers(apps):\n"
            "        for app in apps:\n"
            "            yield app\n"
            "    return [a for a in peers([])]\n"
        )
        self.assertEqual(rebound_parameters(source), [])

    def test_no_function_of_ours_rebinds_a_parameter(self):
        base = Path(settings.BASE_DIR)
        found = []
        for package in PACKAGES:
            for path in sorted((base / package).rglob("*.py")):
                parts = path.relative_to(base).parts
                if "tests" in parts or "migrations" in parts:
                    continue
                for line, function, name in rebound_parameters(path.read_text(), str(path)):
                    found.append(f"{path.relative_to(base)}:{line} {function}() reuses its parameter {name!r}")
        self.assertEqual(found, [], "\n".join(found))
