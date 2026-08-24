"""One place decides whether a caller is the Local API.

There were two copies of this check — one in the control plane, one in the
management ABI — and copies of an authentication rule drift the way any other
duplicated judgement drifts: the next fix lands in one of them.

It is written as a dependency rather than a helper on purpose. A helper has to
be *called*, so every new route is another chance to forget; a dependency is
attached to the router once and applies to whatever is mounted on it. That is
what turned 12 forgotten routes into an impossible mistake rather than a
recurring one.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request


async def require_local_api_credential(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Admit only a caller holding this Host's Local API service credential.

    Fails closed when the credential is unconfigured: an internal plane with no
    credential to check is not an open plane, it is a plane that cannot answer.
    That is a 503 (this Host is not configured) rather than a 401 (you did not
    prove who you are), because the caller has nothing to fix.
    """

    expected = request.app.state.settings.local_api_service_token.strip()
    if not expected:
        raise HTTPException(503, "Local API service credential is not configured")
    scheme, separator, token = (authorization or "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(token, expected)
    ):
        raise HTTPException(401, "Local API service authentication failed")
