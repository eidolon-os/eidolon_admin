"""The boundary that makes two processes worth their cost.

The public management surface listens on the LAN and holds exactly one
credential: the token for one loopback service. The authority credentials for
Data, Hub and Kernel live in the other process. That is the whole reason this is
two processes rather than one (plan §3.4.1), and it decays the moment either
half drifts — a business judgement made on the LAN side, or an authority
credential read there.

Source-level guards rather than review habits, because both drifts are one
plausible-looking commit away.
"""

from __future__ import annotations

from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "eidolon_admin_server"
PUBLIC_MANAGEMENT = SERVER / "local_api/management"
INTERNAL_MANAGEMENT = SERVER / "app/management"


def _sources(directory: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*.py"))
    }


def test_the_lan_facing_side_reads_no_authority_credential() -> None:
    """It may hold the token for the loopback service, and nothing else.

    If the public surface could read Data's token, a compromise of the process
    listening on 0.0.0.0 would hand over authority writes rather than the
    ability to call one loopback service.
    """
    forbidden = (
        "data_authority_token",
        "data_workspace_authority_token",
        "hub_authority_token",
        "kernel_authority_token",
    )
    for name, source in _sources(PUBLIC_MANAGEMENT).items():
        for token in forbidden:
            assert token not in source, f"{name} reads an authority credential"


def test_the_lan_facing_side_talks_to_no_authority_directly() -> None:
    """One hop, to one place. Anything else is a second door into the same data."""
    forbidden = (
        "companion-authority",
        "workspace-authority",
        "device-authority",
        "api/kernel/v1",
        "sqlalchemy",
        "eidolon_data",
    )
    for name, source in _sources(PUBLIC_MANAGEMENT).items():
        for marker in forbidden:
            assert marker not in source, f"{name} reaches past the loopback boundary"


def test_the_public_surface_holds_no_business_rule() -> None:
    """Authenticate, decode, call, map — and nothing that decides an outcome.

    A decision made here is a decision made on the side with no credentials and
    no authority facts, which is exactly the side that must not be trusted with
    it. ``capabilities`` and ``limits`` are relayed, never computed.
    """
    router = (PUBLIC_MANAGEMENT / "router.py").read_text(encoding="utf-8")
    for marker in ("_CAPABILITIES", "_ENABLED", "capabilities = {", "read_context"):
        assert marker not in router, f"router.py computes {marker!r} instead of relaying it"


def test_the_credential_holding_side_serves_no_public_route() -> None:
    """Its prefix is internal, and it is not another branch of control-plane.

    That path family already answers to a browser holding an operator credential
    and to a loopback service holding a service token; a third meaning on it is
    how the confusion this plan exists to remove got here.
    """
    router = (INTERNAL_MANAGEMENT / "router.py").read_text(encoding="utf-8")
    # The prefix is what decides the mount point, so that is what is asserted;
    # and it must not be reached by extending the operator plane's router.
    assert 'prefix="/internal/v1/management"' in router
    assert "control_plane.router" not in router


def _route_signatures(source: str) -> list[tuple[str, set[str]]]:
    """Every function this module mounts as a route, and its parameter names."""

    import ast

    routes: list[tuple[str, set[str]]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        mounted = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "router"
            for decorator in node.decorator_list
        )
        if not mounted:
            continue
        signature = node.args
        names = {
            argument.arg
            for argument in (
                *signature.posonlyargs,
                *signature.args,
                *signature.kwonlyargs,
            )
        }
        routes.append((node.name, names))
    assert routes, "no routes found — the parser stopped seeing this module"
    return routes


def test_the_owner_is_never_a_public_input() -> None:
    """The public surface derives the Owner; only the internal ABI takes one.

    The internal one may, because its single caller is the boundary that just
    verified a Controller session and passes the Owner bound to it.
    """
    # Read off the route signatures rather than off the text before the first
    # class. The text proxy went wrong the first time a module-level helper took
    # an ``owner_id`` — which is not a public input at all — and it would also
    # have missed a route defined *after* a class, which is the case it exists
    # to catch. Parsing is both stricter and quieter.
    public = (PUBLIC_MANAGEMENT / "router.py").read_text(encoding="utf-8")
    for name, arguments in _route_signatures(public):
        assert "owner_id" not in arguments, f"{name} takes an Owner from a caller"

    internal = (INTERNAL_MANAGEMENT / "router.py").read_text(encoding="utf-8")
    assert "owner_id: str," in internal
    # That the internal ABI is credential-gated is asserted where it is true —
    # against the mounted routes, in test_service_plane_authentication.py. This
    # assertion used to name the helper that did the checking, which made a
    # refactor that *strengthened* the check look like a regression.
    assert "require_local_api_credential" in internal


def test_capabilities_stay_false_until_a_slice_is_declared_closed() -> None:
    """The enabled set is the one place a feature becomes visible.

    Kept as its own guard so switching a capability on is a deliberate edit to a
    named set, not a side effect of adding a route.
    """
    context = (INTERNAL_MANAGEMENT / "context.py").read_text(encoding="utf-8")
    # One assignment, one place. What it contains is the application's business
    # (and is asserted in test_management_context.py); what matters here is that
    # no other line in this module can make a capability true.
    assert context.count("_ENABLED") == 2, "declared once, read once"
    assert "_ENABLED: frozenset[str] = frozenset(" in context
    # The read withholds rather than grants: a capability is *not* offered
    # unless its slice is in the set. The distinction matters for this gate,
    # because the boolean map is now derived from the withheld map rather than
    # computed beside it — one computation, so the two cannot disagree.
    assert "if name not in _ENABLED:" in context
    assert (
        "capabilities={name: name not in unavailable for name in _CAPABILITIES}"
        in context
    ), "the boolean map must be derived from the withheld map, not computed twice"


def test_neither_a_policy_ref_nor_an_act_is_a_public_input() -> None:
    """The two things a client must not be able to say about a Body assignment.

    ``policy_refs`` is on the canonical command and empty on every Host, because
    nothing in this product defines or evaluates a resource policy. A client
    that could name refs would be storing names no evaluator ever reads, which
    reads like a constraint and enforces nothing. When a policy authority exists
    this is the review to hold; until then the field is not offered.

    ``origin`` says which act a change is part of — a person choosing, or an
    archive letting a Body go — and the authority derives the recorded
    provenance from it. A client that could name the act could claim its own
    change was somebody else's consequence, and provenance is exactly what a
    screen later turns into "you cleared this" rather than "it was put away".
    """

    public = (PUBLIC_MANAGEMENT / "router.py").read_text(encoding="utf-8")
    for name, arguments in _route_signatures(public):
        assert "policy_refs" not in arguments, f"{name} takes policy refs from a caller"
        assert "origin" not in arguments, f"{name} takes an act from a caller"
    assert "policy_refs" not in public
    # The request models are where a body field would hide from a signature.
    assert "origin: " not in public

    # And the internal ABI, which may name the act, still may not name the refs:
    # its caller knows which workflow it is running, and nobody knows a policy.
    internal = (INTERNAL_MANAGEMENT / "router.py").read_text(encoding="utf-8")
    assert "policy_refs" not in internal
