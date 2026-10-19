"""Which API endpoints a change to rdst's Python code can reach.

Builds a declaration-level dependency graph of the rdst package with the
standard library only, so it runs without rdst's dependencies installed. A
declaration is a top-level function, class, or assignment; an import bound
at module level is an alias declaration that points at what it imports.
Edges run from a declaration to everything it references, including imports
made inside functions and literal ``import_module`` calls.

The mounted route table comes from the same parse: ``APIRouter`` prefixes,
route decorators, and ``include_router`` calls. A changed declaration is
walked up its reverse edges to the handlers that depend on it, which gives
the endpoints the change can alter.

Input on stdin, JSON:
  {"root": "rdst", "changes": {"rdst/x.py": {"new": [[a, b]], "old": [[a, b]],
   "old_source": "..." | null}}}
With ``--routes ROOT`` it prints the mounted route table instead.

Output on stdout, JSON:
  {"endpoints": [{"method", "path", "handler", "chain"}],
   "reached": {file: {module_file: chain}}, "unreachable": [file]}
"""

from __future__ import annotations

import ast
import json
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    "tests",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "keyservice",
    "devtools",
    "web_dist",
    "build",
    "dist",
}
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "api_route", "websocket"}
MODULE = "<module>"
# `if __name__ == "__main__":` runs only as a script, so nothing depends on it.
MAIN = "<main>"
WHOLE = "*"


@dataclass
class Module:
    name: str
    path: Path
    is_package: bool
    tree: ast.Module | None = None
    # decl name -> line ranges; `<module>` can own many separate statements
    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    # decl name -> referenced (module, decl) nodes
    refs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    # class name -> its method names
    classes: dict[str, set[str]] = field(default_factory=dict)
    # module-level name -> the class it is an instance of, as written (`x = C()`)
    instances: dict[str, str] = field(default_factory=dict)
    # module-level imports: bound name -> node
    scope: dict[str, tuple[str, str]] = field(default_factory=dict)


def module_name(root: Path, path: Path) -> tuple[str, bool]:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


def discover(root: Path) -> dict[str, Module]:
    modules: dict[str, Module] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        name, is_package = module_name(root, path)
        if name:
            modules[name] = Module(name, path, is_package)
    return modules


def resolve_relative(module: Module, level: int, target: str | None) -> str:
    base = module.name.split(".")
    if not module.is_package:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, target] if target else base)


def is_main_guard(node: ast.stmt) -> bool:
    test = node.test if isinstance(node, ast.If) else None
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def top_level_names(node: ast.stmt) -> list[str]:
    if is_main_guard(node):
        return [MAIN]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    names: list[str] = []
    for target in targets:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                names.append(sub.id)
    return names


def span(node: ast.stmt) -> tuple[int, int]:
    start = node.lineno
    for decorator in getattr(node, "decorator_list", []):
        start = min(start, decorator.lineno)
    return start, node.end_lineno or node.lineno


def methods_of(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [s for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]


def body_decl(cls: str) -> str:
    return f"{cls}.<body>"


def statement_spans(node: ast.stmt) -> list[tuple[str, tuple[int, int]]]:
    """Declarations a top-level statement defines, with their lines. A class
    is split into its methods and `Class.<body>`, the rest of the class."""
    if isinstance(node, ast.ClassDef):
        found = [(body_decl(node.name), span(node))]
        found += [(f"{node.name}.{m.name}", span(m)) for m in methods_of(node)]
        return found
    return [(name, span(node)) for name in top_level_names(node) or [MODULE]]


def spans_of(tree: ast.Module) -> dict[str, list[tuple[int, int]]]:
    spans: dict[str, list[tuple[int, int]]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            spans.setdefault(MODULE, []).append(span(node))
            continue
        for name, lines in statement_spans(node):
            spans.setdefault(name, []).append(lines)
    return spans


class Graph:
    def __init__(self, root: Path):
        self.root = root
        self.modules = discover(root)
        self.reverse: dict[tuple[str, str], set[tuple[str, str]]] = {}
        # module -> router variable -> prefix
        self.routers: dict[str, dict[str, str]] = {}
        # (module, handler) -> [(method, path within router, router variable)]
        self.handlers: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        # (module, router variable) -> mount prefixes
        self.mounts: dict[tuple[str, str], list[str]] = {}
        # Modules that build the API app.
        self.app_modules: set[str] = set()
        for module in self.modules.values():
            self.scan(module)
        for module in self.modules.values():
            self.index(module)
        for module in self.modules.values():
            self.find_app_modules(module)
        route_modules = {module for module, _ in self.handlers}
        for module in self.modules.values():
            for decl, targets in module.refs.items():
                for target in targets:
                    # The app imports every router to register it; that is not a
                    # dependency on the handlers' code.
                    if module.name in self.app_modules and target[0] in route_modules:
                        continue
                    self.reverse.setdefault(target, set()).add((module.name, decl))

    def module_of(self, dotted: str) -> str | None:
        """Longest known module that is a prefix of a dotted name."""
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.modules:
                return candidate
        return None

    def target(self, dotted: str, name: str | None) -> tuple[str, str] | None:
        """The node an import of ``name`` from ``dotted`` binds to."""
        if name is None:
            module = self.module_of(dotted)
            return (module, WHOLE) if module else None
        if f"{dotted}.{name}" in self.modules:
            return (f"{dotted}.{name}", WHOLE)
        if dotted in self.modules:
            return (dotted, name)
        return None

    def find_app_modules(self, module: Module) -> None:
        """Mark the module that constructs FastAPI(), found by what it does so a
        move does not hide it; reaching it alters the whole app, not one route."""
        if module.tree is not None and any(
            isinstance(sub, ast.Call) and call_name(sub) == "FastAPI" for sub in ast.walk(module.tree)
        ):
            self.app_modules.add(module.name)

    def scan(self, module: Module) -> None:
        """First pass: parse, and record imports, classes, and instances."""
        try:
            module.tree = ast.parse(module.path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            return
        for node in module.tree.body:
            self.collect_imports(module, node, module.scope, top=True)
            if isinstance(node, ast.ClassDef):
                module.classes[node.name] = {m.name for m in methods_of(node)}
            elif (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
            ):
                for name in top_level_names(node):
                    module.instances[name] = node.value.func.id

    def index(self, module: Module) -> None:
        if module.tree is None:
            return
        local = set()
        for node in module.tree.body:
            local.update(top_level_names(node))
        for node in module.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            scope = dict(module.scope)
            self.collect_imports(module, node, scope, top=False)
            if isinstance(node, ast.ClassDef):
                self.index_class(module, node, scope, local)
            else:
                refs = self.references(module, node, scope, local)
                for decl, lines in statement_spans(node):
                    module.spans.setdefault(decl, []).append(lines)
                    module.refs.setdefault(decl, set()).update(refs - {(module.name, decl)})
            self.collect_routes(module, node, scope)
        # Module-level imports are alias declarations: `from .service import run`
        # in a package makes `package.run` point at `package.service.run`.
        for alias, target in module.scope.items():
            module.refs.setdefault(alias, set()).add(target)
        # Every declaration depends on the module's top-level statements.
        if MODULE in module.refs:
            for decl in list(module.refs):
                if decl != MODULE:
                    module.refs[decl].add((module.name, MODULE))
        # Depending on a whole module means depending on each declaration.
        module.refs.setdefault(WHOLE, set()).update((module.name, d) for d in module.refs if d != WHOLE)

    def index_class(
        self,
        module: Module,
        node: ast.ClassDef,
        scope: dict[str, tuple[str, str]],
        local: set[str],
    ) -> None:
        """`C.m` for each method, `C.<body>` for the rest, and `C` for all of it."""
        body = body_decl(node.name)
        methods = methods_of(node)
        rest: list[ast.AST] = [*node.decorator_list, *node.bases, *node.keywords]
        rest += [s for s in node.body if s not in methods]
        body_refs: set[tuple[str, str]] = set()
        for part in rest:
            body_refs |= self.references(module, part, scope, local, node.name)
        module.spans.setdefault(body, []).append(span(node))
        module.refs.setdefault(body, set()).update(body_refs - {(module.name, body)})
        whole = module.refs.setdefault(node.name, set())
        whole.add((module.name, body))
        for method in methods:
            decl = f"{node.name}.{method.name}"
            refs = self.references(module, method, scope, local, node.name)
            module.spans.setdefault(decl, []).append(span(method))
            module.refs.setdefault(decl, set()).update(refs - {(module.name, decl)})
            module.refs[decl].add((module.name, body))
            whole.add((module.name, decl))

    def dynamic_candidates(self, module: Module, call: ast.Call) -> list[str] | None:
        """Modules a computed ``import_module`` can load, when its literal prefix or
        its ``__package__`` anchor bounds them: `f"features.x.{name}"`, or a name
        relative to the calling package. None when nothing bounds it."""
        argument = call.args[0] if call.args else None
        prefix = ""
        if isinstance(argument, ast.JoinedStr) and argument.values:
            first = argument.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                prefix = first.value
        anchored = len(call.args) > 1 and isinstance(call.args[1], ast.Name) and call.args[1].id in {
            "__package__",
            "__name__",
        }
        if prefix.startswith(".") or (anchored and not prefix):
            if not anchored:
                return None
            level = len(prefix) - len(prefix.lstrip(".")) or 1
            base = resolve_relative(module, level, None)
            prefix = f"{base}.{prefix.lstrip('.')}" if base else prefix.lstrip(".")
        if not prefix or prefix.count(".") < 1:
            return None
        found = [name for name in self.modules if name.startswith(prefix) and name != module.name]
        return found or None

    def local_instances(
        self,
        module: Module,
        node: ast.AST,
        scope: dict[str, tuple[str, str]],
        local: set[str],
    ) -> dict[str, tuple[str, str]]:
        """Names inside a declaration bound to an instance of a known class: from a
        constructor call, an annotation, or a typed parameter. Method calls on them
        resolve to the method; any other use of them depends on the whole class."""

        def resolve(expr: ast.expr | None) -> tuple[str, str] | None:
            if isinstance(expr, ast.Call):
                expr = expr.func
            if isinstance(expr, ast.Name):
                if expr.id in scope:
                    return self.class_of(scope[expr.id])
                if expr.id in local:
                    return self.class_of((module.name, expr.id))
            return None

        found: dict[str, tuple[str, str]] = {}
        if node is module.tree:
            return found
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                if isinstance(sub.value, ast.Call) and (cls := resolve(sub.value)):
                    found[sub.targets[0].id] = cls
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                if cls := resolve(sub.annotation) or (
                    resolve(sub.value) if isinstance(sub.value, ast.Call) else None
                ):
                    found[sub.target.id] = cls
            elif isinstance(sub, ast.arg) and (cls := resolve(sub.annotation)):
                found[sub.arg] = cls
        return found

    def class_of(self, target: tuple[str, str], depth: int = 0) -> tuple[str, str] | None:
        """The class a node is, or is an instance of, following import aliases."""
        module_name, decl = target
        module = self.modules.get(module_name)
        if module is None or depth > 5:
            return None
        if decl in module.classes:
            return target
        if decl in module.instances:
            written = module.instances[decl]
            if written in module.classes:
                return (module_name, written)
            if written in module.scope:
                return self.class_of(module.scope[written], depth + 1)
            return None
        if decl in module.scope:
            return self.class_of(module.scope[decl], depth + 1)
        return None

    def member(self, cls: tuple[str, str], attr: str) -> tuple[str, str]:
        module = self.modules[cls[0]]
        if attr in module.classes.get(cls[1], set()):
            return (cls[0], f"{cls[1]}.{attr}")
        # Inherited or data attributes live in the class body, which depends on
        # the base classes as a whole.
        return (cls[0], body_decl(cls[1]))

    def collect_imports(
        self, module: Module, node: ast.AST, scope: dict[str, tuple[str, str]], top: bool
    ) -> None:
        nodes = [node] if top else list(ast.walk(node))
        for sub in nodes:
            if isinstance(sub, ast.Import):
                for alias in sub.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    dotted = alias.name if alias.asname else alias.name.split(".")[0]
                    found = self.target(dotted, None)
                    if found:
                        scope[bound] = found
            elif isinstance(sub, ast.ImportFrom):
                base = (
                    resolve_relative(module, sub.level, sub.module)
                    if sub.level
                    else (sub.module or "")
                )
                for alias in sub.names:
                    if alias.name == "*":
                        found = self.target(base, None)
                        if found:
                            scope[f"*{base}"] = found
                        continue
                    found = self.target(base, alias.name)
                    if found:
                        scope[alias.asname or alias.name] = found

    def references(
        self,
        module: Module,
        node: ast.AST,
        scope: dict[str, tuple[str, str]],
        local: set[str],
        self_class: str | None = None,
    ) -> set[tuple[str, str]]:
        refs: set[tuple[str, str]] = {t for k, t in scope.items() if k.startswith("*")}
        parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
        instances = self.local_instances(module, node, scope, local)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute):
                chain = attribute_chain(sub)
                if chain and chain[0] in scope:
                    found = scope[chain[0]]
                    if found[1] == WHOLE and len(chain) > 1:
                        deeper = self.target(".".join([found[0], *chain[1:-1]]), chain[-1])
                        refs.add(deeper or found)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                parent = parents.get(sub)
                if sub.id in instances:
                    cls = instances[sub.id]
                    if isinstance(parent, ast.Attribute) and parent.value is sub:
                        refs.add(self.member(cls, parent.attr))
                    else:
                        refs.add(cls)
                    continue
                if sub.id == "self" and self_class:
                    target: tuple[str, str] | None = (module.name, self_class)
                elif sub.id in scope:
                    target = scope[sub.id]
                elif sub.id in local:
                    target = (module.name, sub.id)
                else:
                    target = None
                if target is None:
                    continue
                cls = self.class_of(target)
                if cls is None:
                    refs.add(target)
                    continue
                if target != cls and sub.id != "self":
                    refs.add(target)  # the instance's own construction
                if isinstance(parent, ast.Attribute) and parent.value is sub:
                    refs.add(self.member(cls, parent.attr))
                elif isinstance(parent, ast.Call) and parent.func is sub and target == cls:
                    refs.add(self.member(cls, "__init__"))
                    refs.add((cls[0], body_decl(cls[1])))
                    # Where the new object goes decides what it depends on.
                    use = parents.get(parent)
                    if isinstance(use, ast.Attribute) and use.value is parent:
                        refs.add(self.member(cls, use.attr))
                    elif not (
                        (isinstance(use, ast.Assign) and all(isinstance(t, ast.Name) for t in use.targets))
                        or (isinstance(use, ast.AnnAssign) and isinstance(use.target, ast.Name))
                    ):
                        refs.add(cls)
                elif sub.id != "self":
                    refs.add(cls)
            elif isinstance(sub, ast.Call) and call_name(sub) in {"import_module", "__import__"}:
                argument = sub.args[0] if sub.args else None
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    found = self.target(argument.value, None)
                    if found:
                        refs.add(found)
                elif (candidates := self.dynamic_candidates(module, sub)) is not None:
                    refs.update((name, WHOLE) for name in candidates)
        return refs

    def collect_routes(
        self, module: Module, node: ast.stmt, scope: dict[str, tuple[str, str]]
    ) -> None:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if call_name(node.value) == "APIRouter":
                prefix = keyword_string(node.value, "prefix") or ""
                for name in top_level_names(node):
                    self.routers.setdefault(module.name, {})[name] = prefix
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                    continue
                method = decorator.func.attr
                router = decorator.func.value
                if method in ROUTE_METHODS and isinstance(router, ast.Name):
                    path = first_string(decorator) or ""
                    self.handlers.setdefault((module.name, node.name), []).append(
                        (method.upper(), path, router.id)
                    )
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.func.attr != "include_router" or not sub.args:
                    continue
                chain = attribute_chain(sub.args[0]) if isinstance(sub.args[0], ast.Attribute) else None
                if chain and len(chain) == 2 and chain[0] in scope:
                    router_module = scope[chain[0]][0]
                    self.mounts.setdefault((router_module, chain[1]), []).append(
                        keyword_string(sub, "prefix") or ""
                    )

    def endpoints_of(self, handler: tuple[str, str]) -> list[tuple[str, str]]:
        found = []
        for method, path, router in self.handlers.get(handler, []):
            prefix = self.routers.get(handler[0], {}).get(router, "")
            for mount in self.mounts.get((handler[0], router), []):
                found.append((method, f"{mount}{prefix}{path}"))
        return found

    def decls_in(self, module: Module, ranges: list[list[int]], source: str | None) -> set[str]:
        """Declarations of ``module`` touched by line ranges of ``source``."""
        if source is None:
            spans = module.spans
        else:
            try:
                spans = spans_of(ast.parse(source))
            except SyntaxError:
                return {WHOLE}
        touched: set[str] = set()
        for start, end in ranges:
            hit = {
                name
                for name, lines in spans.items()
                if any(a <= end and start <= b for a, b in lines)
            }
            # A line inside a method belongs to the method, not the class body.
            for name in list(hit):
                if name.endswith(".<body>"):
                    cls = name[: -len(".<body>")]
                    if any(h.startswith(f"{cls}.") and h != name for h in hit):
                        hit.discard(name)
            touched |= hit or {MODULE}
        # An import or other top-level statement changing reaches every declaration.
        return {WHOLE} if MODULE in touched else touched

    def propagate(self, seeds: dict[tuple[str, str], list[str]]) -> dict[tuple[str, str], list[str]]:
        reached = dict(seeds)
        queue = deque(seeds)
        while queue:
            node = queue.popleft()
            # The app module is where every route meets; the walk stops there
            # rather than reaching every route through it.
            if node[0] in self.app_modules:
                continue
            nexts = set(self.reverse.get(node, set()))
            if node[1] not in (WHOLE, MODULE):
                nexts |= self.reverse.get((node[0], WHOLE), set())
            for consumer in nexts:
                if consumer in reached:
                    continue
                reached[consumer] = [*reached[node], self.label(consumer)]
                queue.append(consumer)
        return reached

    def label(self, node: tuple[str, str]) -> str:
        """A chain step as the selector reads one: the file, then the declaration."""
        module = self.modules.get(node[0])
        where = str(module.path) if module else node[0]
        return where if node[1] in (WHOLE, MODULE) else f"{where}::{node[1]}"


def attribute_chain(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return [node.id, *reversed(parts)]
    return None


def call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def keyword_string(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return None


def first_string(node: ast.Call) -> str | None:
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return keyword_string(node, "path")


def file_paths(module: Module) -> list[tuple[str, int]]:
    """Files and directories the module names relative to itself, as
    `Path(__file__)...` or `os.path.dirname(__file__)` joined with literal parts,
    with the line naming each. A path with no literal part names the package
    itself, which says nothing about any one file, so it is left out."""
    tree = ast.parse(module.path.read_text(encoding="utf-8", errors="replace"))
    here = module.path.resolve()
    names: dict[str, Path] = {}

    def base(node: ast.expr) -> Path | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            in ("Path", "dirname")
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "__file__"
        ):
            is_dirname = isinstance(node.func, ast.Attribute) and node.func.attr == "dirname"
            return here.parent if is_dirname else here
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("resolve", "absolute") and not node.args:
                return base(node.func.value)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            inner = base(node.value)
            return inner.parent if inner else None
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            inner = base(node.value.value)
            return inner.parents[node.slice.value] if inner else None
        if isinstance(node, ast.Name):
            return names.get(node.id)
        return None

    def joined(node: ast.expr) -> tuple[Path, bool] | None:
        """The path an expression names, and whether a literal part was joined."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = joined(node.left)
            if left is None:
                return None
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
                return left[0] / node.right.value, True
            return left
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and node.args
        ):
            first = joined(node.args[0])
            if first is None:
                return None
            path, literal = first
            for arg in node.args[1:]:
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    break
                path, literal = path / arg.value, True
            return path, literal
        found = base(node)
        return (found, isinstance(node, ast.Name)) if found else None

    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = joined(node.value)
            if isinstance(target, ast.Name) and value and value[1]:
                names[target.id] = value[0]
    seen: set[int] = set()
    for node in ast.walk(tree):
        if id(node) in seen or not isinstance(node, (ast.BinOp, ast.Call, ast.Name)):
            continue
        value = joined(node)
        if not value or not value[1]:
            continue
        for inner in ast.walk(node):
            seen.add(id(inner))
        out.append((str(value[0].resolve()), node.lineno))
    return out


def main() -> None:
    if sys.argv[1:2] == ["--assets"]:
        root = Path(sys.argv[2]).resolve()
        assets = []
        for module in discover(root).values():
            try:
                found = sorted(set(file_paths(module)))
            except (SyntaxError, ValueError, OSError):
                continue
            for path, line in found:
                assets.append({"path": path, "module": str(module.path), "line": line})
        json.dump(assets, sys.stdout)
        return
    if sys.argv[1:2] == ["--routes"]:
        graph = Graph(Path(sys.argv[2]).resolve())
        routes = sorted(
            {(m, p) for handler in graph.handlers for m, p in graph.endpoints_of(handler)}
        )
        json.dump([{"method": m, "path": p} for m, p in routes], sys.stdout)
        return
    request = json.load(sys.stdin)
    root = Path(request["root"]).resolve()
    graph = Graph(root)
    by_path = {module.path.resolve(): module for module in graph.modules.values()}
    result: dict[str, list] = {
        "endpoints": [],
        # changed file -> module file reached -> first chain reaching it
        "reached": {},
        "unreachable": [],
    }
    for file, change in request["changes"].items():
        module = by_path.get((Path(file)).resolve())
        if module is None:
            continue
        seeds: dict[tuple[str, str], list[str]] = {}
        touched = graph.decls_in(module, change.get("new", []), None)
        if change.get("old"):
            touched |= graph.decls_in(module, change["old"], change.get("old_source"))
        if WHOLE in touched:
            touched = {WHOLE, *module.spans, *module.refs}
        for decl in touched:
            node = (module.name, decl)
            seeds[node] = [f"{file}::{decl}" if decl not in (WHOLE, MODULE) else file]
        reached = graph.propagate(seeds)
        files: dict[str, list[str]] = {}
        for (name, _), chain in reached.items():
            if name in graph.modules:
                files.setdefault(str(graph.modules[name].path), chain)
        result["reached"][file] = files
        hit = False
        for node, chain in reached.items():
            for method, path in graph.endpoints_of(node):
                result["endpoints"].append(
                    {"method": method, "path": path, "handler": graph.label(node), "chain": chain}
                )
                hit = True
            # The app wires every route, so reaching it reaches them all.
            if node[0] in graph.app_modules:
                hit = True
        if not hit:
            result["unreachable"].append(file)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
