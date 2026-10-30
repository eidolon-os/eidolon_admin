"""Saved revision to existing runtime; uncertain outcomes retain start intent."""
import logging
from types import SimpleNamespace
from eidolon_sdk.biz.control.coordination import RoleGroupStatus
from eidolon_sdk.biz.control.teams import (TeamRevision, TeamStartSnapshot, TeamPage, TeamStarts,
    TeamConflict, TeamConflicts)
from .router import ManagementBackendError

logger = logging.getLogger(__name__)


class SavedTeamRuns:
    def __init__(self, assets, devices):
        self.assets, self.devices = assets, devices

    async def conflicts(self, *, session, host_id, team_id):
        import asyncio
        # This is an explanation, never a substitute for Provider admission.
        async with asyncio.timeout(15):
            target = TeamRevision.model_validate(await self.assets.teams(
                owner_id=session.owner_id, host_id=host_id, team_id=team_id))
            companions = {m.companion_id for m in target.configuration.members}
            devices = {target.configuration.input_device_id,
                       *(m.output_device_id for m in target.configuration.members)}
            results, unresolved, cursor, seen = [], 0, "", set()
            while True:
                page = TeamPage.model_validate(await self.assets.teams(
                    owner_id=session.owner_id, host_id=host_id, after=cursor, limit=100))
                for other in page.teams:
                    active = TeamStarts.model_validate(await self.assets.team_starts(
                        owner_id=session.owner_id, host_id=host_id,
                        team_id=other.team_id, action="list"))
                    for run in active.starts:
                        if run.closed:
                            continue
                        record = TeamStartSnapshot.model_validate(await self.assets.team_starts(
                            owner_id=session.owner_id, host_id=host_id, team_id=other.team_id,
                            session_id=run.session_id, action="get"))
                        if record.closed:
                            continue
                        selection = record.selection
                        if selection is None:
                            unresolved += 1
                            continue
                        shared_companions = companions & {m.companion_id for m in selection.members}
                        shared_devices = devices & {d.device_instance_id for d in selection.devices}
                        if not shared_companions and not shared_devices:
                            continue
                        # A stale Data intent is not evidence that resources are occupied.
                        status = await self.inspect(session=session, host_id=host_id,
                            team_id=other.team_id, session_id=run.session_id)
                        if status.state != "closed":
                            results.append(TeamConflict(team_id=other.team_id,
                                name=other.configuration.name, session_id=run.session_id,
                                revision=run.revision, companion_ids=tuple(sorted(shared_companions)),
                                device_ids=tuple(sorted(shared_devices))))
                if not page.next_cursor:
                    return TeamConflicts(conflicts=tuple(results), unresolved_runs=unresolved)
                if page.next_cursor in seen:
                    raise ValueError("TEAM_PAGE_CURSOR_REPEATED")
                cursor = page.next_cursor
                seen.add(cursor)

    async def start(self, *, session, host_id, team_id, request):
        saved = TeamRevision.model_validate(await self.assets.team_starts(
            owner_id=session.owner_id, host_id=host_id, team_id=team_id,
            action="reserve", payload=request.model_dump(mode="json")))
        if saved.team_id != team_id or saved.revision != request.expected_revision:
            raise ValueError("TEAM_REVISION_RESPONSE_MISMATCH")
        try:
            record = TeamStartSnapshot.model_validate(await self.assets.team_starts(
                owner_id=session.owner_id, host_id=host_id, team_id=team_id,
                session_id=request.session_id, action="get"))
            if record.selection is None:
                selection = await self.devices.resolve_team(session=session,
                    configuration=saved.configuration, session_id=request.session_id)
                record = TeamStartSnapshot.model_validate(await self.assets.team_starts(
                    owner_id=session.owner_id, host_id=host_id, team_id=team_id,
                    session_id=request.session_id, action="snapshot",
                    payload=selection.model_dump(mode="json")))
            if (record.team_id != team_id or record.session_id != request.session_id
                    or record.revision != request.expected_revision or record.closed
                    or record.selection is None or record.selection.session_id != request.session_id):
                raise ValueError("TEAM_SNAPSHOT_RESPONSE_MISMATCH")
            result = await self.devices.role_group(session=session, action="open",
                payload=SimpleNamespace(session_id=request.session_id), selection=record.selection)
        except ManagementBackendError as exc:
            if exc.status_code in {400, 403, 404, 409, 422}:
                # A refusal is not proof of release. Ask Provider to close/fence
                # this exact identity, then reconcile only its terminal evidence.
                # Transport errors retain the intent for explicit recovery.
                try:
                    closed = await self.devices.role_group(session=session, action="close",
                        payload=SimpleNamespace(session_id=request.session_id))
                    closed = RoleGroupStatus.model_validate(closed)
                    if closed.state == "closed" and not closed.error:
                        closed = closed.model_copy(update={"error": "TEAM_START_REFUSED"})
                    await self._reconcile(closed, session=session, host_id=host_id,
                        team_id=team_id, session_id=request.session_id)
                except Exception:
                    logger.warning("Team start refusal cleanup remains unconfirmed", exc_info=True)
            raise
        return await self._reconcile(result, session=session, host_id=host_id,
                                     team_id=team_id, session_id=request.session_id)

    async def inspect(self, *, session, host_id, team_id, session_id, close=False):
        record = TeamStartSnapshot.model_validate(await self.assets.team_starts(
            owner_id=session.owner_id, host_id=host_id, team_id=team_id,
            session_id=session_id, action="get"))
        if record.team_id != team_id or record.session_id != session_id:
            raise ValueError("TEAM_RUN_RESPONSE_MISMATCH")
        if record.closed:
            if record.terminal_status is not None:
                return record.terminal_status
            return RoleGroupStatus(session_id=session_id, state="closed",
                scenario="ip_role_group", completion_basis="native_playout")
        result = await self.devices.role_group(session=session,
            action="close" if close else "status", payload=SimpleNamespace(session_id=session_id))
        return await self._reconcile(result, session=session, host_id=host_id,
                                     team_id=team_id, session_id=session_id)

    async def _reconcile(self, result, *, session, host_id, team_id, session_id):
        status = result if isinstance(result, RoleGroupStatus) else RoleGroupStatus.model_validate(result)
        if status.session_id != session_id:
            raise ValueError("TEAM_RUN_RESPONSE_MISMATCH")
        if status.state == "closed":
            await self.assets.team_starts(owner_id=session.owner_id, host_id=host_id,
                team_id=team_id, session_id=session_id, action="confirmed-closed",
                payload=status.model_dump(mode="json"))
        return status
