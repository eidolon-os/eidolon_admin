"""Architecture invariant tests for the ``devices`` + ``nats_kv`` modules.

These tests are static: they parse each module's source via :mod:`ast`
and assert that the *imports* obey the four-layer rule established in
Phase 25's plan:

    router → orchestrator → repository → KVClient / httpx.AsyncClient

The point is to make accidental layer-skipping fail the test suite, not
code review. If someone reaches for ``httpx`` from inside ``router.py``
or imports ``nats`` inside ``orchestrator.py``, this catches it on the
next ``pytest`` run.

These are deliberately implementation-agnostic — we don't assert on
function bodies or call sites, only on what each file imports. That
covers the realistic regressions ("forgot to add a method to repository,
just inlined the kv.put") without locking us into specific code shapes.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

DEVICES_ROOT = (
    Path(__file__).resolve().parents[1]  # server/tests/ → server/
    / "eidolon_admin_server"
    / "app"
    / "devices"
)
NATS_KV_ROOT = DEVICES_ROOT.parent / "nats_kv"


# ---- helpers ---------------------------------------------------------------


def _imports_of(py_file: Path) -> set[str]:
    """Return the set of fully-qualified module names imported by ``py_file``.

    Both ``import x.y`` and ``from x.y import z`` are normalised to
    ``"x.y"``. Relative imports (``from . import x`` / ``from .foo import x``)
    are resolved against the file's package path so the assertion targets
    can use absolute names.
    """
    tree = ast.parse(py_file.read_text())
    pkg_parts = py_file.relative_to(DEVICES_ROOT.parent.parent.parent).with_suffix("").parts
    # Package path of the file itself (drop the module basename for resolution).
    pkg_path = list(pkg_parts[:-1])
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    imports.add(node.module)
            else:
                # Resolve relative: drop ``level`` segments from pkg_path,
                # then append node.module.
                base = pkg_path[: len(pkg_path) - node.level + 1]
                if node.module:
                    base = base + node.module.split(".")
                imports.add(".".join(base))
    return imports


def _module_imports(*relative_parts: str) -> set[str]:
    target = DEVICES_ROOT.joinpath(*relative_parts)
    if not target.exists():
        pytest.fail(f"expected module file not found: {target}")
    return _imports_of(target)


# ---- the four-layer invariants --------------------------------------------


def test_router_does_not_import_infrastructure_directly() -> None:
    """``router.py`` must not touch NATS / httpx / nats.* directly.

    All network and storage work belongs to the orchestrator. The router
    is HTTP shell only. If you find yourself wanting to add ``httpx`` here,
    the missing abstraction is on the orchestrator instead.
    """
    imports = _module_imports("router.py")
    forbidden = {"httpx", "nats"}
    leaks = {imp for imp in imports if imp.split(".")[0] in forbidden}
    assert not leaks, (
        f"router.py imports infrastructure modules {leaks}; move calls into "
        "orchestrator.py instead"
    )


def test_router_does_not_import_repository_or_kvclient() -> None:
    """The router shouldn't even *know* the repository or KVClient exists.

    It only knows about the orchestrator's exception hierarchy + DTOs.
    Importing repository here would let someone bypass orchestrator
    altogether for "trivial" reads — and trivial reads are how
    consistency rules erode.
    """
    imports = _module_imports("router.py")
    forbidden = {
        "eidolon_admin_server.app.nats_kv",
        "eidolon_admin_server.app.devices.repository",
    }
    leaks = {imp for imp in imports if any(imp.startswith(f) for f in forbidden)}
    assert not leaks, f"router.py imports repo/kv layer: {leaks}"


def test_orchestrator_does_not_import_router() -> None:
    """Layer rule: dependencies point DOWN. Orchestrator must not depend
    on router (which would create a cycle and let upper-layer concerns
    leak into business logic)."""
    imports = _module_imports("orchestrator.py")
    assert "eidolon_admin_server.app.devices.router" not in imports
    # Also forbid generic FastAPI / Starlette types in the business layer —
    # orchestrator raises domain exceptions, the router translates them.
    forbidden_namespaces = {"fastapi", "starlette"}
    leaks = {imp for imp in imports if imp.split(".")[0] in forbidden_namespaces}
    assert not leaks, (
        f"orchestrator.py imports HTTP framework modules {leaks}; "
        "raise domain exceptions and let router map to HTTPException"
    )


def test_repository_does_not_import_orchestrator_or_router() -> None:
    """Repository sits below orchestrator; it must not see anything above."""
    imports = _module_imports("repository.py")
    forbidden = {
        "eidolon_admin_server.app.devices.orchestrator",
        "eidolon_admin_server.app.devices.router",
    }
    leaks = {imp for imp in imports if imp in forbidden}
    assert not leaks, f"repository.py imports upper layer: {leaks}"
    # And repository must not bypass KVClient to talk to nats directly.
    assert "nats" not in {imp.split(".")[0] for imp in imports}, (
        "repository.py must use KVClient (from .nats_kv) — direct nats.* "
        "imports defeat the abstraction"
    )


def test_schemas_module_is_pydantic_only() -> None:
    """``schemas.py`` is just Pydantic models — no HTTP, no NATS, no
    orchestrator. Catching reshuffles here keeps the wire models
    portable to clients and tools that import them in isolation.
    """
    imports = _module_imports("schemas.py")
    allowed_top_levels = {
        "__future__",
        "datetime",
        "typing",
        "pydantic",
    }
    extra = {imp for imp in imports if imp.split(".")[0] not in allowed_top_levels}
    assert not extra, (
        f"schemas.py has unexpected imports: {extra}. Keep it pure data shape."
    )


# ---- nats_kv invariants ---------------------------------------------------


def test_nats_kv_does_not_know_about_devices_or_business_buckets() -> None:
    """``nats_kv`` is *infrastructure*. It must not reference any
    business module — devices, mappings, souls, agents. If it did, the
    repository's "I'm the only one who knows the schema" guarantee
    would dissolve.
    """
    for py in NATS_KV_ROOT.glob("*.py"):
        imports = _imports_of(py)
        for imp in imports:
            assert "devices" not in imp.split("."), (
                f"{py.name} imports {imp}: nats_kv must not know about devices"
            )


def test_devices_init_only_exports_router_and_orchestrator_surface() -> None:
    """``devices/__init__.py`` is the package's public face. The rest of
    admin (and tests) imports from here. The exports list pins what is
    public — adding a new symbol requires updating this test, which
    forces a deliberate review."""
    init = DEVICES_ROOT / "__init__.py"
    tree = ast.parse(init.read_text())
    # Find ``__all__`` assignment.
    all_value: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        all_value = [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]
    assert set(all_value) == {
        "ALL_BUCKETS",
        "DeviceBindingRepository",
        "DeviceOrchestrator",
        "router",
    }, (
        f"devices.__init__ exports drifted to {all_value!r}. Adding to the "
        "public surface requires a deliberate review — update this assertion "
        "if you're sure."
    )
