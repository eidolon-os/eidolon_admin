"""Which lane each source decides, recorded where the source is read.

This composition reads fifteen sources and draws one map. Until now it kept a
flat list of :class:`SourceStatus` rows and left every consumer to work out what
a failed source meant for the panel in front of them — so a screen could show an
empty devices list while ``hub.devices`` was reporting a timeout three rows down
in a diagnostics blob nobody rendered. "Nothing is happening" and "nobody could
tell me" are opposite facts, and a status list beside a payload cannot keep them
apart: the payload is what gets drawn.

So the lane is the record. Every read reports through :class:`LaneLedger` and
says which lanes its answer decides, at the point of the read; the ledger then
answers two different questions:

* the operator console still wants the flat per-source list, and gets it derived
  from the ledger, unchanged in shape;
* the Owner's map wants per-lane envelopes — state, reason, when, how long,
  whether it was cut short — which is what
  ``eidolon_sdk/contracts/mission_control/v1/mission-control-snapshot.schema.json``
  requires of a Host, and it gets those from the same ledger.

The totality is the point. :data:`SOURCE_LANES` must name every source, and
:meth:`LaneLedger.record` refuses one it has never heard of — so a source added
later cannot quietly decide nothing. A source that genuinely has no bearing on
the Owner's map registers an empty tuple, which is a statement rather than an
omission.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .schemas import SourceStatus

Lane = Literal[
    "devices",
    "services",
    "activities",
    "turns",
    "jobs",
    "memory",
    "events",
]

#: The Owner's map, lane by lane. Identity — the Owner, the roster, which
#: Companion answers by default — is deliberately absent: `/context` and the
#: roster are its authorities, and Mission Control only reports what it observed
#: about ids the caller already holds.
LANES: tuple[Lane, ...] = (
    "devices",
    "services",
    "activities",
    "turns",
    "jobs",
    "memory",
    "events",
)

#: Every source this composition reads, and the lanes its answer decides.
#:
#: Read it as "if this source could not answer, which parts of the map become
#: unknown". A source may decide several lanes: the Agent's turn feed is where
#: both `turns` and the activity projection come from, so losing it makes both
#: unknown rather than making one of them look quiet.
SOURCE_LANES: dict[str, tuple[Lane, ...]] = {
    # Nothing can be read at all without it — every lane is unknown.
    "control-plane": LANES,
    # Identity, not runtime. The Owner and the roster are read on their own
    # authorities; a failure here means there is no map to draw rather than a
    # lane that came back empty, and `build_snapshot` returns early for it.
    "data.owners": (),
    "data.companions": (),
    # Bodies, as this process can see them: the owner-isolated runtime
    # blackboard, which is the presence authority. Existence is not here — the
    # inventory is Claims joined with mounts, both behind a credential this
    # process does not hold — so the Owner's plane joins that in on the way out
    # and says so if it could not.
    "runtime.blackboard": ("devices",),
    # Which bodies are on their channel — the presence authority that actually
    # answers. Hub refuses liveness by contract and the blackboard's reader was
    # withdrawn, so before this the devices lane could only ever say 「未探测」
    # about a speaker that was plainly in a call. It decides the same lane as
    # the blackboard: they answer the same question, from different standing.
    "channel.presence": ("devices",),
    # Retired capabilities, kept registered because the composition still says
    # out loud that it is not asking for them. They decide nothing: the lane
    # stands on the two sources above.
    "hub.device_page": (),
    "hub.event_feed": (),
    # Decides nothing on the Owner's map today, and that is a statement. A Guard
    # binding is what marks a body as standing watch; the Guard runtime does not
    # exist, so no body is one, and darkening the whole lane over a decoration
    # that cannot yet be true would hide the bodies that are really there.
    "data.guard_bindings": (),
    # The floor of the whole thing, as the Host itself reports it — eidolond,
    # which drives supervisord on macOS and systemd on the Pi. The old label was
    # `services`, and it belonged to a probe in this process that fell back to
    # supervisord: on a Pi that made three running services read red. Renamed so
    # nothing can report under the old one.
    "host.services": ("services",),
    # Turns, and the activity chain projected from them.
    "agent.turns": ("turns", "activities"),
    "data.conversations": ("turns",),
    # Background work, and its share of the activity chain.
    "agent.long_tasks": ("jobs", "activities"),
    "data.jobs": ("jobs",),
    # What was remembered.
    "memory.runtime": ("memory",),
    # The Owner's moments, from the audit index that assigns them their order.
    # ``ingest_seq`` is what makes this lane resumable, and it is the reason the
    # events contract requires one.
    "audit.index": ("events",),
}


class UnregisteredSource(RuntimeError):
    """A read reported under a source no lane claims.

    Raised rather than tolerated. A source whose outcome reaches no lane is a
    source whose failure cannot reach a screen, which is the whole defect this
    ledger exists to prevent — and it is far easier to fix while adding the read
    than to notice later from a panel that renders blank on a broken Host.
    """


@dataclass(frozen=True)
class LaneOutcome:
    """What one lane can say about itself."""

    #: ``ok`` when everything it depends on answered; ``degraded`` when it was
    #: answered but cut short; ``unavailable`` when something it depends on could
    #: not be read at all.
    state: str
    #: Why, when the state is not ``ok``. Shown to a reader, so it names the
    #: source and carries the upstream's own words.
    detail: str = ""
    observed_at: datetime | None = None
    latency_ms: float | None = None
    truncated: bool = False

    @property
    def readable(self) -> bool:
        return self.state != "unavailable"


@dataclass
class LaneLedger:
    """One composition's reads, attributed to the lanes they decide."""

    _rows: list[SourceStatus] = field(default_factory=list)
    #: Latest outcome per source, per lane. Keyed by source rather than appended:
    #: a source read twice — a retry, or a fallback that succeeded after a first
    #: attempt failed — must not leave the lane looking failed forever, and the
    #: flat status list has always kept only the latest per source.
    _outcomes: dict[Lane, dict[str, SourceStatus]] = field(default_factory=dict)
    _truncated: set[Lane] = field(default_factory=set)

    def record(
        self,
        source: str,
        *,
        ok: bool,
        detail: str = "",
        started: float | None = None,
        latency_ms: float | None = None,
    ) -> SourceStatus:
        """Note one read, and file it under every lane it decides."""

        if source not in SOURCE_LANES:
            raise UnregisteredSource(
                f"{source!r} decides no lane. Add it to SOURCE_LANES in "
                "app/mission_control/lanes.py, naming the lanes its answer "
                "decides — an empty tuple if it genuinely decides none."
            )
        if latency_ms is None and started is not None:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
        status = SourceStatus(source=source, ok=ok, detail=detail, latency_ms=latency_ms)
        self._rows.append(status)
        for lane in SOURCE_LANES[source]:
            self._outcomes.setdefault(lane, {})[source] = status
        return status

    def truncate(self, lane: Lane, detail: str) -> None:
        """Say a lane was answered, but not in full."""

        if lane not in LANES:
            raise UnregisteredSource(f"{lane!r} is not a lane")
        self._truncated.add(lane)
        self._outcomes.setdefault(lane, {})
        if detail:
            self._rows.append(
                SourceStatus(source=f"{lane}.truncated", ok=True, detail=detail)
            )

    def statuses(self) -> list[SourceStatus]:
        """The flat per-source list, latest answer per source.

        What the operator console has always consumed. Derived rather than kept
        in parallel, so the two views cannot disagree about what happened.
        """

        out: dict[str, SourceStatus] = {}
        for row in self._rows:
            out[row.source] = row
        return list(out.values())

    def outcome(self, lane: Lane, *, observed_at: datetime | None = None) -> LaneOutcome:
        """What this lane may claim about itself."""

        if lane not in LANES:
            raise UnregisteredSource(f"{lane!r} is not a lane")
        when = observed_at or datetime.now(UTC)
        rows = list(self._outcomes.get(lane, {}).values())
        failures = [row for row in rows if not row.ok]
        answers = [row for row in rows if row.ok]
        if failures:
            # Some answered and some did not: partly known, and the reason for
            # the rest is attached. Calling that unavailable throws away what
            # was read; calling it ok hides that something is missing.
            return LaneOutcome(
                state="degraded" if answers else "unavailable",
                detail="；".join(_reason(row) for row in failures),
                observed_at=when,
                latency_ms=_slowest(failures + answers),
                truncated=lane in self._truncated,
            )
        if not answers:
            # Nobody read anything that bears on this lane. Not an empty lane —
            # an unasked one, and the difference is exactly what this file is for.
            return LaneOutcome(
                state="unavailable",
                detail=f"这台 Host 没有读取 {lane} 的来源",
                observed_at=when,
            )
        return LaneOutcome(
            state="degraded" if lane in self._truncated else "ok",
            detail="",
            observed_at=when,
            latency_ms=_slowest(answers),
            truncated=lane in self._truncated,
        )

    def outcomes(self, *, observed_at: datetime | None = None) -> dict[Lane, LaneOutcome]:
        return {lane: self.outcome(lane, observed_at=observed_at) for lane in LANES}


def _reason(row: SourceStatus) -> str:
    return f"{row.source}: {row.detail}" if row.detail else row.source


def _slowest(rows: list[SourceStatus]) -> float | None:
    seen = [row.latency_ms for row in rows if row.latency_ms is not None]
    return max(seen) if seen else None
