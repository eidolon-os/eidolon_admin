"""Every internal route requires the service credential — asserted by enumeration.

This file exists because of a real defect, not a hypothetical one. The internal
plane authenticated per handler, and 12 of its 23 routes never called the check.
Among the unguarded ones were ``PATCH /owners/{id}`` (rename an Owner) and
``PUT /companions/{id}/face`` (replace a Companion's face). Nothing was
exploited — the process listens on loopback — but the credential isolation that
the two-process split is *bought with* (plan §3.4.1) was not actually being
enforced at the boundary that spends it.

The fix was structural: the credential is a router-level dependency, so a route
cannot be mounted without it. This test is the matching gate. It does not list
the routes it expects; it **discovers** them from the mounted app, so a route
added tomorrow is covered by a test written today.
"""

from __future__ import annotations

import httpx
import pytest

from eidolon_admin_server.app.settings import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.component]

#: The planes whose callers are services holding a credential (plan §3.1).
SERVICE_PLANES = ("/api/control-plane/v1", "/api/internal/v1")
#: The operator plane is deliberately excluded and asserted separately below.
OPERATOR_PLANE = "/api/operator/v1"

TOKEN = "local-api-secret"


def _service_routes(app) -> list[tuple[str, str]]:
    found = [
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(SERVICE_PLANES)
        for method in operations
    ]
    assert found, "no service-plane routes found; this gate would pass vacuously"
    return sorted(found)


def _concrete(path: str) -> str:
    """Fill path parameters with a value no stub will ever find.

    Authentication is checked before the handler runs, so what the resource id
    is does not matter — but it must not be empty, or the request is a 404 from
    routing and the test would pass without ever reaching the credential check.
    """
    out = []
    for segment in path.split("/"):
        out.append("absent" if segment.startswith("{") else segment)
    return "/".join(out)


async def _call(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        return await client.request(method.upper(), _concrete(path), **kwargs)


async def test_no_service_route_answers_without_the_credential(app) -> None:
    app.state.settings = Settings(local_api_service_token=TOKEN)
    for method, path in _service_routes(app):
        response = await _call(app, method, path)
        assert response.status_code == 401, f"{method.upper()} {path} answered anonymously"


async def test_no_service_route_accepts_the_wrong_credential(app) -> None:
    """A token that is not this Host's is a 401, not a fallback to anonymous."""
    app.state.settings = Settings(local_api_service_token=TOKEN)
    for method, path in _service_routes(app):
        response = await _call(
            app, method, path, headers={"Authorization": "Bearer not-the-token"}
        )
        assert response.status_code == 401, f"{method.upper()} {path} took any token"


async def test_an_unconfigured_credential_closes_the_plane_rather_than_opening_it(
    app,
) -> None:
    """No credential configured means "cannot answer", never "everyone may".

    The distinction matters on a half-provisioned Host: the failure mode of a
    missing secret must not be an open door.
    """
    app.state.settings = Settings(local_api_service_token="")
    for method, path in _service_routes(app):
        response = await _call(
            app, method, path, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 503, f"{method.upper()} {path} answered unconfigured"


async def test_the_credential_is_compared_whole(app) -> None:
    """A prefix of the token is not the token, and neither is another scheme."""
    app.state.settings = Settings(local_api_service_token=TOKEN)
    for header in (
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN} ",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer",
        "",
    ):
        response = await _call(
            app,
            "GET",
            "/api/control-plane/v1/capabilities",
            headers={"Authorization": header} if header else {},
        )
        assert response.status_code == 401, f"accepted {header!r}"


async def test_the_operator_plane_is_not_quietly_on_the_service_plane(app) -> None:
    """Its Authorization header is a forwarded Hub credential, not a caller's.

    Asserted so that "the operator routes are exempt" stays a stated decision
    with a reason (see operator_plane/router.py) rather than something a future
    reader discovers and files as the same bug again.
    """
    operator = [
        path for path in app.openapi()["paths"] if path.startswith(OPERATOR_PLANE)
    ]
    assert operator, "the operator plane vanished; the exemption above is now stale"
    for path in operator:
        assert not path.startswith(SERVICE_PLANES)


async def test_one_place_decides_who_the_local_api_is() -> None:
    """Two copies of an authentication rule drift; the next fix lands in one.

    There were two, verbatim — one in the control plane, one in the management
    ABI. Source-level, because the second copy would pass every behavioural
    test in this file right up until the two disagreed.
    """
    from pathlib import Path

    server = Path(__file__).resolve().parents[1] / "eidolon_admin_server"
    definitions = [
        path
        for path in server.rglob("*.py")
        if "def require_local_api_credential" in path.read_text(encoding="utf-8")
        or "def _authorize_local_api" in path.read_text(encoding="utf-8")
    ]
    assert [path.name for path in definitions] == ["service_auth.py"]
