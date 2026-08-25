"""One place turns an upstream refusal into a response, and logs it.

There were thirty-odd. Every route on the internal planes carried the same four
lines::

    except AuthorityFailure as exc:
        raise HTTPException(exc.status_code, detail=exc.to_wire().model_dump())

which is a rule written once per route rather than once. Two things followed
from that, and both of them cost a person real time:

**A route can forget.** Not hypothetically — the same reasoning already made
the Local API credential check a router dependency rather than a helper,
because "every new route is another chance to forget" had already happened
twelve times there. An uncaught ``AuthorityFailure`` is a 500 with no reason.

**Nobody logged the reason.** Thirty copies of a conversion and not one of them
recorded what it converted, so a Host that had been refusing every memory read
for two weeks left exactly one line per request in its journal::

    "GET /api/internal/v1/management/memory/library" 503 Service Unavailable

The sentence that would have ended the search — *this Host was never given the
memory service credential* — was constructed, serialised, relayed, and thrown
away, and no operator could reach it from the machine it happened on.

Registered as an exception handler, so it applies to whatever is mounted rather
than to whoever remembered. The routes keep no ``except`` at all: raising is the
contract, and this is the single reader of it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .errors import AuthorityFailure

_LOG = logging.getLogger("eidolon_admin.authority")

#: Which refusals are worth a stack-free warning and which are ordinary traffic.
#:
#: A stale revision and a Companion that is not this Owner's are the product
#: working; logging them at warning would train operators to ignore the level. A
#: Host with no credential, an authority that is down, or an answer outside its
#: contract are all "somebody has to do something", and those are the ones that
#: were invisible.
_OPERATOR_ACTIONABLE = frozenset(
    {"configuration", "unavailable", "runtime_missing", "upstream_failure", "contract_violation"}
)


def install_authority_failure_handler(app: FastAPI) -> None:
    """Make every ``AuthorityFailure`` on this app one logged, shaped response."""

    @app.exception_handler(AuthorityFailure)
    async def _handle(request: Request, exc: AuthorityFailure) -> JSONResponse:
        # Logged before the body is built, so a refusal is on the record even if
        # serialising it were ever to fail. The vocabulary is single-sourced now
        # and cannot, but this handler is the one place where "the error path
        # itself broke" has to stay recoverable.
        _LOG.log(
            logging.WARNING if exc.kind in _OPERATOR_ACTIONABLE else logging.INFO,
            "%s refused %s %s: %s (kind=%s upstream=%s code=%s retryable=%s)",
            exc.authority,
            request.method,
            request.url.path,
            exc.detail,
            exc.kind,
            exc.upstream_status,
            exc.code,
            exc.retryable,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.to_wire().model_dump()},
        )
