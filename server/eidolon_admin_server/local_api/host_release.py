"""Which target release this Host is serving the phone from.

A target release installs every component under
``/opt/eidolon/releases/<release_id>/<component>`` and points a stable
``/opt/eidolon/current/<component>`` link at it. The link is a statement about
the next start, not about this process: a unit that re-executed between its
stop and the link flip keeps serving the release it loaded, and on 2026-09-16
that was a Host whose ``current`` links named a release its processes were not
running. So the answer is read from the code this process actually loaded, once,
when it loaded it — never from the link at request time.

A process started from a source checkout has no release, and says so with a
null rather than a guess.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Where a target release installs its components.
RELEASES_ROOT = Path("/opt/eidolon/releases")

#: The release descriptor's own ``release_id`` pattern.
RELEASE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_RELEASE_ID = re.compile(RELEASE_ID_PATTERN)


class HostReleaseView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["host.release"] = "host.release"
    contract_version: Literal["1"] = "1"
    #: Always present. Null means this Host runs from a source checkout, not
    #: that the Host could not tell.
    release_id: str | None = Field(pattern=RELEASE_ID_PATTERN)


def release_of(loaded_from: Path) -> str | None:
    """The release a resolved code path belongs to, if it belongs to one."""

    try:
        parts = loaded_from.relative_to(RELEASES_ROOT).parts
    except ValueError:
        return None
    # ``<release_id>/<component>/...``: a path that stops at the releases
    # directory, or at a release directory itself, is not a component's code.
    if len(parts) < 3 or not _RELEASE_ID.fullmatch(parts[0]):
        return None
    return parts[0]


def _loaded_release() -> str | None:
    # A console script's shebang already names the release path, and taking
    # it as written survives an ``/opt`` that is itself a link. Resolving is
    # for a unit started through ``current`` instead.
    here = Path(__file__).absolute()
    return release_of(here) or release_of(here.resolve())


#: Read at import, which is when this process loaded its code. Reading at
#: request time would follow ``current`` to whatever it names by then.
RUNNING_RELEASE: str | None = _loaded_release()
