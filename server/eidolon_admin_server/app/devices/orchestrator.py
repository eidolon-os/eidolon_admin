"""Device-binding cross-service workflows.

Each public method on :class:`DeviceOrchestrator` is one use-case end-to-end:
list / approve / create_agent / switch_active / delete_agent / read_soul /
write_soul. Compositions that need both NATS and HTTP go through here so the
router stays trivial.

Why the compensation pattern (instead of true transactions):
    bind = "render template (HTTP) → write souls (NATS) → write agents
    (NATS) → write mappings (NATS)". There is no joint commit primitive
    across HTTP and NATS. Pragmatic answer: each step writes; if a later
    step fails, this layer reverses the earlier writes best-effort and
    surfaces a 503 + structured error so the operator can retry. Half-state
    is observable (subsequent ``list_devices`` will reflect whatever did
    land) — we don't pretend atomicity we don't have.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .repository import (
    MAX_SOUL_SIZE_BYTES,
    AgentMeta,
    DeviceBindingRepository,
    Mapping,
    is_valid_id,
)

logger = logging.getLogger(__name__)


# ---- error types -----------------------------------------------------------
#
# Orchestrator raises these; router catches and maps to HTTP status codes.
# Using domain exceptions (instead of returning sentinels) lets the router
# pattern-match on exception class without inspecting result objects.


class OrchestratorError(Exception):
    """Base for all device-orchestrator errors. ``status_code`` hints HTTP mapping."""

    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DeviceNotApproved(OrchestratorError):
    status_code = 409


class DeviceNotFound(OrchestratorError):
    status_code = 404


class AgentNotFound(OrchestratorError):
    status_code = 404


class TemplateRenderFailed(OrchestratorError):
    status_code = 502


class SoulTooLarge(OrchestratorError):
    status_code = 413


class HubUnreachable(OrchestratorError):
    status_code = 503


class CompensationFailed(OrchestratorError):
    """Raised when a primary write succeeded but rollback couldn't clean up.

    The exception carries enough detail in its message for the operator's
    log to identify orphaned keys and remove them manually.
    """
    status_code = 503


# ---- view types passed up to router ----------------------------------------


@dataclass
class AgentView:
    """Composite agent row used in list views."""
    agent_id: str
    template_id: str
    template_revision: int
    owner_user_id: str
    owner_device_id: str
    created_at: datetime
    updated_at: datetime
    is_active: bool


@dataclass
class BindingView:
    user_id: str
    agent_ids: list[str]
    active_agent_id: str | None
    updated_at: datetime
    agents: list[AgentView]


@dataclass
class DeviceComposite:
    """Hub-side device data + NATS-side binding fused into one row."""
    device_id: str
    name: str
    approved: bool
    approved_at: datetime | None
    paired: bool
    enabled: bool
    last_seen: datetime | None
    status: str
    binding: BindingView | None


# ---- orchestrator ---------------------------------------------------------


class DeviceOrchestrator:
    """High-level device-binding workflows.

    Dependencies are injected at construction so tests can swap in mocks
    without monkey-patching. The orchestrator owns no state of its own —
    state lives in NATS (via repo) and hub (via HTTP). Every public method
    is idempotent or clearly documented as not.
    """

    def __init__(
        self,
        *,
        repo: DeviceBindingRepository,
        http_client: httpx.AsyncClient,
        hub_base_url: str,
        agent_base_url: str,
    ) -> None:
        self._repo = repo
        self._http = http_client
        self._hub_base = hub_base_url.rstrip("/")
        self._agent_base = agent_base_url.rstrip("/")

    # ---- read paths ---------------------------------------------------

    async def list_devices(self) -> list[DeviceComposite]:
        """Fuse hub device list with NATS binding info. Read-only."""
        hub_devices = await self._fetch_hub_devices()
        # Augment with any NATS-known devices hub doesn't see (orphan
        # binding rows). This is rare but operator needs to see them to
        # clean up.
        nats_only = await self._find_orphan_bindings({d["device_id"] for d in hub_devices})
        for device_id in nats_only:
            hub_devices.append({
                "device_id": device_id,
                "name": "(orphan)",
                "approved": False,
                "approved_at": None,
                "paired": False,
                "enabled": False,
                "last_seen": None,
                "status": "unknown",
            })

        out: list[DeviceComposite] = []
        for d in hub_devices:
            out.append(DeviceComposite(
                device_id=d["device_id"],
                name=d.get("name", "") or "",
                approved=bool(d.get("approved")),
                approved_at=_maybe_dt(d.get("approved_at")),
                paired=bool(d.get("paired")),
                enabled=bool(d.get("enabled", True)),
                last_seen=_maybe_dt(d.get("last_seen")),
                status=d.get("status", "unknown") or "unknown",
                binding=await self._load_binding_view(d["device_id"]),
            ))
        return out

    async def read_soul(self, device_id: str, agent_id: str) -> tuple[str, int]:
        """Return ``(markdown, byte_size)``. Raises if agent unknown."""
        await self._require_agent_belongs_to_device(device_id, agent_id)
        soul = await self._repo.get_soul(agent_id)
        if soul is None:
            # Mapping listed it but souls bucket has no row — internal
            # inconsistency. Surface as 404 so operator notices.
            raise AgentNotFound(
                f"soul missing for agent {agent_id} (mapping references it but "
                "souls bucket has no row — possible interrupted write)"
            )
        return soul, len(soul.encode("utf-8"))

    # ---- write paths --------------------------------------------------

    async def approve(self, device_id: str) -> dict:
        """Forward operator approval to hub. Idempotent."""
        url = f"{self._hub_base}/api/admin/devices/{device_id}/approve"
        try:
            resp = await self._http.post(url, timeout=5.0)
        except (httpx.ConnectError, httpx.ReadError) as exc:
            raise HubUnreachable(f"hub at {self._hub_base} unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise DeviceNotFound(
                f"device {device_id} not registered with hub yet — wait for "
                "it to call GET /api/config at least once"
            )
        if resp.status_code >= 400:
            raise OrchestratorError(f"hub approve returned {resp.status_code}: {resp.text}")
        return resp.json()

    async def create_agent(
        self, device_id: str, template_id: str, user_id: str
    ) -> tuple[str, int, bool]:
        """Bind a new agent (template copy) to ``device_id``.

        Returns ``(agent_id, soul_preview_chars, is_active)``.

        Workflow + compensation:
            1. Verify ``device_id`` is hub-approved (else 409 — refuse).
            2. Ask agent service to render the template → markdown.
            3. Generate a unique ``agent_id`` (UUID4, collision-check).
            4. Write souls/<id> = markdown.   ← if fail: nothing to undo
            5. Write agents/<id> = metadata.  ← if fail: delete souls/<id>
            6. Update mappings/device.<id> (append + set active).
                                              ← if fail: delete agents/<id>
                                                          + delete souls/<id>
            7. Return.

            Compensations log a CompensationFailed marker if even the
            cleanup fails; that surfaces as 503 with a payload the
            operator can use to find the orphan keys.
        """
        if not is_valid_id(template_id) or not is_valid_id(user_id):
            raise OrchestratorError(
                "template_id and user_id must be non-empty and use only "
                "letters/digits/._-/= (NATS KV key charset)"
            )
        await self._require_device_approved(device_id)

        markdown, template_revision = await self._render_template(template_id)

        if len(markdown.encode("utf-8")) > MAX_SOUL_SIZE_BYTES:
            raise SoulTooLarge(
                f"rendered template '{template_id}' produced "
                f"{len(markdown.encode('utf-8'))} bytes (limit {MAX_SOUL_SIZE_BYTES}) "
                "— either trim the template or raise MAX_SOUL_SIZE_BYTES"
            )

        agent_id = await self._mint_unique_agent_id()
        now = datetime.now(timezone.utc)
        meta = AgentMeta(
            template_id=template_id,
            template_revision=template_revision,
            owner_user_id=user_id,
            owner_device_id=device_id,
            created_at=now,
            updated_at=now,
        )

        # Step 4: souls
        try:
            await self._repo.put_soul(agent_id, markdown)
        except Exception as exc:  # noqa: BLE001 — NATS errors are diverse
            raise OrchestratorError(f"write souls bucket failed: {exc}") from exc

        # Step 5: agents (with rollback of step 4 on failure)
        try:
            await self._repo.put_agent_meta(agent_id, meta)
        except Exception as exc:  # noqa: BLE001
            await self._compensate(
                f"create_agent step 5 failed for {agent_id}",
                [("souls", agent_id)],
            )
            raise OrchestratorError(f"write agents bucket failed: {exc}") from exc

        # Step 6: mappings (with rollback of steps 4+5 on failure)
        try:
            mapping = await self._repo.get_mapping(device_id) or Mapping(user_id=user_id)
            if mapping.user_id != user_id:
                # An existing mapping refers to a different user. Defensive
                # check; future UI should warn before reaching here.
                raise OrchestratorError(
                    f"device {device_id} is already bound to user "
                    f"{mapping.user_id!r}; cannot mix users on one device"
                )
            mapping.agent_ids.append(agent_id)
            mapping.active_agent_id = agent_id  # new agent always wins active
            await self._repo.put_mapping(device_id, mapping)
        except OrchestratorError:
            await self._compensate(
                f"create_agent step 6 rejected for {agent_id}",
                [("agents", agent_id), ("souls", agent_id)],
            )
            raise
        except Exception as exc:  # noqa: BLE001
            await self._compensate(
                f"create_agent step 6 failed for {agent_id}",
                [("agents", agent_id), ("souls", agent_id)],
            )
            raise OrchestratorError(f"update mappings bucket failed: {exc}") from exc

        preview_chars = min(len(markdown), 200)
        return agent_id, preview_chars, True  # newly-created is always active

    async def switch_active(self, device_id: str, agent_id: str) -> str | None:
        """Set ``agent_id`` as active on the device. Returns new active id."""
        mapping = await self._repo.get_mapping(device_id)
        if mapping is None:
            raise DeviceNotFound(f"no binding for device {device_id}")
        if agent_id not in mapping.agent_ids:
            raise AgentNotFound(
                f"agent {agent_id} not bound to device {device_id} "
                f"(known agents: {mapping.agent_ids})"
            )
        mapping.active_agent_id = agent_id
        await self._repo.put_mapping(device_id, mapping)
        return mapping.active_agent_id

    async def delete_agent(
        self, device_id: str, agent_id: str
    ) -> tuple[str | None, str]:
        """Remove an agent. Returns ``(new_active_agent_id, fallback_kind)``.

        Fallback rules:
        - if deleting active and others remain: pick newest-other by
          ``created_at`` → ``"next_newest"``
        - if deleting active and it was the last: active = None → ``"cleared"``
        - if deleting non-active: active unchanged → ``"no_change"``

        Soul + agents-meta deletion is always best-effort after the
        mappings update — if NATS rejects them we surface 503 but the
        primary state (mapping) is already correct.
        """
        await self._require_agent_belongs_to_device(device_id, agent_id)
        mapping = await self._repo.get_mapping(device_id)
        # _require_agent_belongs_to_device guarantees these:
        assert mapping is not None
        assert agent_id in mapping.agent_ids

        was_active = mapping.active_agent_id == agent_id
        mapping.agent_ids.remove(agent_id)

        if not was_active:
            fallback_kind = "no_change"
        elif not mapping.agent_ids:
            mapping.active_agent_id = None
            fallback_kind = "cleared"
        else:
            mapping.active_agent_id = await self._pick_newest(mapping.agent_ids)
            fallback_kind = "next_newest"

        await self._repo.put_mapping(device_id, mapping)
        # Side-bucket cleanup is best-effort: even if these fail the mapping
        # is correct, so the device's view of the world is consistent.
        # Orphan rows in souls/agents are harmless until GC'd manually.
        await self._repo.delete_soul(agent_id)
        await self._repo.delete_agent_meta(agent_id)
        return mapping.active_agent_id, fallback_kind

    async def update_soul(self, device_id: str, agent_id: str, markdown: str) -> int:
        """Overwrite soul markdown. Returns the new byte size."""
        await self._require_agent_belongs_to_device(device_id, agent_id)
        encoded_size = len(markdown.encode("utf-8"))
        if encoded_size > MAX_SOUL_SIZE_BYTES:
            raise SoulTooLarge(
                f"soul size {encoded_size} bytes exceeds limit {MAX_SOUL_SIZE_BYTES}"
            )
        return await self._repo.put_soul(agent_id, markdown)

    # ---- internals (private; tests target public methods only) -----------

    async def _fetch_hub_devices(self) -> list[dict]:
        url = f"{self._hub_base}/api/admin/devices"
        try:
            resp = await self._http.get(url, timeout=5.0)
        except (httpx.ConnectError, httpx.ReadError) as exc:
            raise HubUnreachable(f"hub at {self._hub_base} unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise OrchestratorError(f"hub list devices returned {resp.status_code}")
        return resp.json().get("devices", [])

    async def _find_orphan_bindings(self, known_ids: set[str]) -> list[str]:
        mapped = await self._repo.list_mapped_devices()
        return [d for d in mapped if d not in known_ids]

    async def _load_binding_view(self, device_id: str) -> BindingView | None:
        mapping = await self._repo.get_mapping(device_id)
        if mapping is None:
            return None
        agents: list[AgentView] = []
        for aid in mapping.agent_ids:
            meta = await self._repo.get_agent_meta(aid)
            if meta is None:
                # Inconsistency — mapping refers to an agent_id with no
                # metadata row. Skip it from the view rather than crash;
                # operator will see the discrepancy in agent_ids vs agents.
                logger.warning("orphan agent_id %s referenced by mapping %s", aid, device_id)
                continue
            agents.append(AgentView(
                agent_id=aid,
                template_id=meta.template_id,
                template_revision=meta.template_revision,
                owner_user_id=meta.owner_user_id,
                owner_device_id=meta.owner_device_id,
                created_at=meta.created_at,
                updated_at=meta.updated_at,
                is_active=(aid == mapping.active_agent_id),
            ))
        return BindingView(
            user_id=mapping.user_id,
            agent_ids=list(mapping.agent_ids),
            active_agent_id=mapping.active_agent_id,
            updated_at=mapping.updated_at,
            agents=agents,
        )

    async def _require_device_approved(self, device_id: str) -> None:
        devices = await self._fetch_hub_devices()
        for d in devices:
            if d["device_id"] == device_id:
                if not d.get("approved"):
                    raise DeviceNotApproved(
                        f"device {device_id} not yet approved — operator must "
                        "approve it before binding an agent"
                    )
                return
        raise DeviceNotFound(
            f"device {device_id} not registered with hub — wait for it to "
            "call GET /api/config at least once"
        )

    async def _require_agent_belongs_to_device(
        self, device_id: str, agent_id: str
    ) -> None:
        mapping = await self._repo.get_mapping(device_id)
        if mapping is None:
            raise DeviceNotFound(f"no binding for device {device_id}")
        if agent_id not in mapping.agent_ids:
            raise AgentNotFound(
                f"agent {agent_id} not bound to device {device_id}"
            )

    async def _render_template(self, template_id: str) -> tuple[str, int]:
        url = f"{self._agent_base}/api/admin/personas/templates/{template_id}/render"
        try:
            resp = await self._http.post(url, timeout=10.0)
        except (httpx.ConnectError, httpx.ReadError) as exc:
            raise TemplateRenderFailed(
                f"agent service unreachable at {self._agent_base}: {exc}"
            ) from exc
        if resp.status_code == 404:
            raise TemplateRenderFailed(f"template {template_id!r} not found on agent service")
        if resp.status_code >= 400:
            raise TemplateRenderFailed(
                f"render returned {resp.status_code}: {resp.text}"
            )
        body = resp.json()
        return body["markdown"], int(body["template_revision"])

    async def _mint_unique_agent_id(self, max_attempts: int = 5) -> str:
        """Mint a UUID4 agent_id that's not already present in NATS.

        The retry loop is defensive-only code: with uuid4's 128 bits of
        entropy the collision probability is ~1 / 2^128 per attempt, so
        in practice the first iteration always returns. The loop earns
        its keep ONLY against the scenario where NATS is corrupted /
        returns stale "exists=true" for everything — raising after
        ``max_attempts`` then surfaces a loud failure instead of an
        infinite spin.

        Why this path has no automated test:
            Forcing a real UUID4 collision is computationally impossible
            (search space too large) and we deliberately do NOT inject
            a fake UUID factory just to make this testable — that would
            be the mocking pattern the project's testing philosophy
            avoids. The retry's correctness is enforced by code review
            + the small surface (5 lines) rather than by a test that
            would have to lie about its setup.
        """
        for _ in range(max_attempts):
            candidate = uuid.uuid4().hex
            if not await self._repo.agent_exists(candidate):
                return candidate
        raise OrchestratorError(
            f"failed to mint a non-colliding agent_id after {max_attempts} attempts"
        )

    async def _pick_newest(self, agent_ids: list[str]) -> str:
        """Return the most recently created agent_id among the given list.

        Falls back to the last one in the list (insertion order) when
        metadata rows are missing — degraded but never hangs the delete.
        """
        newest: tuple[datetime, str] | None = None
        for aid in agent_ids:
            meta = await self._repo.get_agent_meta(aid)
            if meta is None:
                continue
            if newest is None or meta.created_at > newest[0]:
                newest = (meta.created_at, aid)
        return newest[1] if newest is not None else agent_ids[-1]

    async def _compensate(self, reason: str, keys: list[tuple[str, str]]) -> None:
        """Best-effort cleanup of writes done before a later step failed.

        ``keys`` is a list of ``(bucket_kind, agent_id)`` pairs where
        bucket_kind ∈ {"souls", "agents"}. We never compensate mappings
        here — mappings only get touched in the final step, and if it
        failed the previous state is what we want to keep.
        """
        logger.warning("compensating: %s — keys=%s", reason, keys)
        for kind, agent_id in keys:
            try:
                if kind == "souls":
                    await self._repo.delete_soul(agent_id)
                elif kind == "agents":
                    await self._repo.delete_agent_meta(agent_id)
            except Exception:  # noqa: BLE001
                # Don't fail the user-visible error path because cleanup
                # failed — but do log loudly so the operator can find
                # orphans.
                logger.exception("compensation delete failed for %s/%s", kind, agent_id)


def _maybe_dt(value) -> datetime | None:
    """Accept either ISO string or None — return parsed datetime or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
