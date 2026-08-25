"""The machine this Eidolon lives on: how it is doing, and its services.

Mobile acts on the same services the Admin Web does; both go through Admin to
eidolond, which owns per-service desired state. The view is deliberately
narrower than Admin's: a person acts on "is it running, restart it", not on
endpoint addresses or contract ids.

Served from the LAN-facing process rather than through the management backend,
for the same reason the controller list is: these are facts about *the machine*,
reached with the credential this process is allowed to hold, and the judgement
that turns a byte count into "worth telling someone about" belongs on the side
that faces the person.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

RuntimeState = Literal[
    "unknown", "inactive", "starting", "ready", "degraded", "blocked", "failed"
]
MutationOperation = Literal["restart", "enable", "disable"]


class HostServiceControlError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminHostServicesPort(Protocol):
    async def list_services(self) -> dict: ...

    async def read_vitals(self) -> dict: ...

    async def mutate(
        self,
        *,
        service_id: str,
        operation: MutationOperation,
        expected_revision: int,
    ) -> dict: ...

    async def close(self) -> None: ...


class HostServiceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=128)
    required: bool
    enabled: bool
    revision: int = Field(ge=1)
    runtime_state: RuntimeState
    detail: str | None = None
    observed_at: datetime


class HostServiceInventoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: tuple[HostServiceView, ...] = ()


class VitalView(BaseModel):
    """One thing about the machine, said the way a person reads it.

    ``concern`` is decided here and nowhere below. The daemon that reads
    /proc knows how many bytes are free; whether that is worth telling someone
    about depends on what this Host is for — and this is the layer that faces
    the person whose Eidolon lives on it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    #: Already phrased: "31.2 GB 可用，共 58.0 GB"、"48.6°C"。The App shows this
    #: rather than doing arithmetic on raw bytes in a second place.
    reading: str
    #: none / watch / act. Absent readings are never a concern — not knowing
    #: is not the same as being fine, and it is said separately.
    concern: Literal["none", "watch", "act"] = "none"
    #: Set when the Host could not take this reading at all.
    unavailable_reason: str | None = None


class HostVitalsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["host.vitals"] = "host.vitals"
    contract_version: Literal["1"] = "1"
    observed_at: str
    vitals: tuple[VitalView, ...] = ()


class HostServiceMutationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=128)
    operation: MutationOperation
    enabled: bool
    revision: int = Field(ge=1)


def host_service_inventory(document: dict) -> HostServiceInventoryView:
    services = document.get("services")
    if not isinstance(services, list):
        raise HostServiceControlError("Host service inventory is unreadable", status_code=502)
    try:
        return HostServiceInventoryView(
            services=tuple(
                HostServiceView(
                    service_id=item["service_id"],
                    required=item["required"],
                    enabled=item["enabled"],
                    revision=item["revision"],
                    runtime_state=item["runtime_state"],
                    detail=item.get("detail"),
                    observed_at=item["observed_at"],
                )
                for item in services
            )
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise HostServiceControlError(
            "Host service inventory did not match the expected shape", status_code=502
        ) from exc


def host_service_mutation(document: dict) -> HostServiceMutationView:
    try:
        return HostServiceMutationView(
            service_id=document["service_id"],
            operation=document["operation"],
            enabled=document["enabled"],
            revision=document["revision"],
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise HostServiceControlError(
            "Host service result did not match the expected shape", status_code=502
        ) from exc


class AdminHostServicesClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token
        self._timeout = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(trust_env=False)

    async def list_services(self) -> dict:
        return await self._request("GET", "/api/host/services")

    async def mutate(
        self,
        *,
        service_id: str,
        operation: MutationOperation,
        expected_revision: int,
    ) -> dict:
        return await self._request(
            "POST",
            f"/api/host/services/{quote(service_id, safe='')}/{operation}",
            json={"expected_revision": expected_revision},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
                **kwargs,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise HostServiceControlError("Host services are unreachable") from exc
        if response.status_code == 404:
            raise HostServiceControlError(
                "the Host does not manage this service", status_code=404
            )
        if response.status_code == 409:
            raise HostServiceControlError(
                "the service changed since it was read; refresh and retry",
                status_code=409,
            )
        if response.status_code >= 400:
            raise HostServiceControlError(
                "Host service control rejected the request",
                status_code=502 if response.status_code >= 500 else response.status_code,
            )
        try:
            document = response.json()
        except ValueError as exc:
            raise HostServiceControlError(
                "Host service response was not JSON", status_code=502
            ) from exc
        if not isinstance(document, dict):
            raise HostServiceControlError(
                "Host service response was not a JSON object", status_code=502
            )
        return document


#: When a reading stops being a number and starts being something to do.
#:
#: These are judgements, not measurements, which is why they live here and not
#: in the daemon: "a fifth of the disk left" means something different on a box
#: that holds one household's memories than on a build server. They are stated
#: as fractions of capacity so the same rule reads the same on a 32 GB card and
#: a 2 TB disk.
_DISK_WATCH = 0.20
_DISK_ACT = 0.08
_MEMORY_WATCH = 0.15
_MEMORY_ACT = 0.05
#: Load per core. Sustained above its core count, a machine is not keeping up.
_LOAD_WATCH = 1.0
_LOAD_ACT = 2.0
#: A Pi throttles itself around 80°C; 70 is where someone should look at where
#: the box is sitting.
_TEMPERATURE_WATCH = 70.0
_TEMPERATURE_ACT = 80.0

_VITAL_LABELS = {
    "disk.state": "存储空间",
    "disk.root": "系统盘",
    "memory.available": "内存",
    "cpu.load1": "处理器负载",
    "temperature": "温度",
    "uptime": "已运行",
}


def host_vitals(document: dict) -> HostVitalsView:
    """Turn readings into sentences, and say which ones are worth acting on."""

    raw = document.get("measurements")
    measurements = raw if isinstance(raw, list) else []
    shown: list[VitalView] = []
    for measurement in measurements:
        if not isinstance(measurement, dict):
            continue
        name = measurement.get("name")
        if not isinstance(name, str) or name not in _VITAL_LABELS:
            continue
        shown.append(_vital(name, measurement))
    return HostVitalsView(
        observed_at=str(document.get("observed_at", "")),
        vitals=tuple(shown),
    )


def _vital(name: str, measurement: dict) -> VitalView:
    label = _VITAL_LABELS[name]
    value = measurement.get("value")
    capacity = measurement.get("capacity")
    if not isinstance(value, (int, float)):
        # Not knowing is not the same as being fine, and it is not a concern
        # either: nothing here is worth waking someone for, and pretending to
        # a reading would be worse than admitting there is none.
        return VitalView(
            name=label,
            reading="读不到",
            unavailable_reason=str(measurement.get("unavailable_reason") or ""),
        )
    if name.startswith("disk."):
        return VitalView(
            name=label,
            reading=f"{_bytes(value)} 可用，共 {_bytes(capacity)}",
            concern=_ratio_concern(value, capacity, _DISK_WATCH, _DISK_ACT),
        )
    if name == "memory.available":
        return VitalView(
            name=label,
            reading=f"{_bytes(value)} 可用，共 {_bytes(capacity)}",
            concern=_ratio_concern(value, capacity, _MEMORY_WATCH, _MEMORY_ACT),
        )
    if name == "cpu.load1":
        cores = capacity if isinstance(capacity, (int, float)) and capacity else 1
        per_core = value / cores
        return VitalView(
            name=label,
            reading=f"{value:.2f}（{int(cores)} 核）",
            concern=(
                "act"
                if per_core >= _LOAD_ACT
                else "watch"
                if per_core >= _LOAD_WATCH
                else "none"
            ),
        )
    if name == "temperature":
        return VitalView(
            name=label,
            reading=f"{value:.1f}°C",
            concern=(
                "act"
                if value >= _TEMPERATURE_ACT
                else "watch"
                if value >= _TEMPERATURE_WATCH
                else "none"
            ),
        )
    return VitalView(name=label, reading=_duration(value))


def _ratio_concern(
    value: float,
    capacity: object,
    watch: float,
    act: float,
) -> Literal["none", "watch", "act"]:
    if not isinstance(capacity, (int, float)) or capacity <= 0:
        return "none"
    free = value / capacity
    if free <= act:
        return "act"
    if free <= watch:
        return "watch"
    return "none"


def _bytes(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "未知"
    for unit, size in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{int(value)} B"


def _duration(seconds: float) -> str:
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"
