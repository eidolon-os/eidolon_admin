"""Keeps Owner voices and their provider voices converging, without anyone watching.

Every 15 seconds: ask the provider about bindings still under review. Every 10
minutes: expire unsaved drafts, delete tombstoned provider voices, and make
missing bindings for voices a Companion uses after this Host changed model.
Every hour: delete provider voices this Host made that Data never recorded —
only once they have been seen unrecorded twice, an hour apart, so a voice whose
creation is still in flight is never mistaken for one.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import UTC, datetime, timedelta

from eidolon_sdk.biz.speech_voice import (
    OwnerVoice,
    OwnerVoiceBinding,
    ProviderVoiceTombstone,
)

from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

from .speech_voices import ChannelVoices, SpeechVoices, host_model

logger = logging.getLogger(__name__)

PENDING_EVERY_S, UPKEEP_EVERY_S, SWEEP_EVERY_S = 15, 600, 3600
REVIEW_LIMIT = timedelta(minutes=10)


class OwnerVoiceReconciler:
    def __init__(self, *, data, channel: ChannelVoices, clock=time.monotonic) -> None:
        self._data, self._channel, self._clock = data, channel, clock
        self._voices = SpeechVoices(data=data, roster=data, channel=channel)
        self._next_upkeep = self._next_sweep = 0.0
        self._suspects: set[str] = set()

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(PENDING_EVERY_S)

    async def tick(self) -> None:
        now = self._clock()
        await self._step(self.advance)
        # A round skipped because Data or the voice service was not up yet is
        # asked again on the next tick, not ten minutes later: a Host start,
        # which every model change is, would otherwise leave custom voices
        # waiting that long to be made again.
        if now >= self._next_upkeep:
            ran = [await self._step(s) for s in (self.expire_drafts, self.bury, self.re_enroll)]
            self._next_upkeep = now + (UPKEEP_EVERY_S if all(ran) else PENDING_EVERY_S)
        if now >= self._next_sweep:
            ran = await self._step(self.sweep)
            self._next_sweep = now + (SWEEP_EVERY_S if ran else PENDING_EVERY_S)

    @staticmethod
    async def _step(step) -> bool:
        """Whether the step got to run. One failing step must not stop the others."""
        try:
            await step()
        except AuthorityFailure as exc:
            # Data or the voice service not answering — at Host start Admin is up
            # before them. A known state; the next round asks again.
            logger.warning("owner voice upkeep %s skipped: %s", step.__name__, exc)
            return False
        except Exception:
            logger.warning("owner voice upkeep %s failed", step.__name__, exc_info=True)
        return True

    async def advance(self) -> None:
        for voice in await self._data.speech_voice_call("GET", "/owner-voices/pending", list[OwnerVoice]):
            for binding in voice.bindings:
                if binding.status != "deploying":
                    continue
                if binding.created_at and datetime.now(UTC) - binding.created_at > REVIEW_LIMIT:
                    await self._data.speech_voice_call(
                        "PUT", f"/owners/{voice.owner_id}/voices/{voice.voice_asset_id}/binding", OwnerVoice,
                        json=binding.model_copy(update={"status": "failed", "status_detail": "服务商审核超时"})
                        .model_dump(mode="json"))
                else:
                    await self._voices.record_status(voice, binding)

    async def expire_drafts(self) -> None:
        await self._data.speech_voice_call("POST", "/owner-voices/expire-drafts", dict)

    async def bury(self) -> None:
        for tombstone in await self._data.speech_voice_call(
                "GET", "/provider-voice-tombstones", list[ProviderVoiceTombstone]):
            try:
                await self._channel.delete(tombstone.provider, tombstone.provider_voice_id)
            except AuthorityFailure:
                continue  # that provider is not this Host's now, or is down: kept for later
            await self._data.speech_voice_call(
                "DELETE", f"/provider-voice-tombstones/{tombstone.provider}/{tombstone.provider_voice_id}")

    async def re_enroll(self) -> None:
        catalog, _ = await self._channel.catalog()
        current = host_model(catalog) if catalog else None
        if current is None or not catalog.enrollment.clone:
            return
        voices = await self._data.speech_voice_call(
            "GET", "/owner-voices/needing-binding", list[OwnerVoice],
            params={"provider": current.provider, "model": current.model})
        for voice in voices:
            if not voice.has_reference or (voice.consent and not voice.consent.allow_re_enrollment):
                continue  # the Owner sees it as unavailable and can record or design again
            reference = await self._data.speech_voice_call(
                "GET", f"/owners/{voice.owner_id}/voices/{voice.voice_asset_id}/reference", bytes)
            made = await self._channel.clone(base64.b64encode(reference).decode())
            await self._bind(voice, made.binding)

    async def _bind(self, voice: OwnerVoice, binding: OwnerVoiceBinding) -> None:
        await self._data.speech_voice_call(
            "PUT", f"/owners/{voice.owner_id}/voices/{voice.voice_asset_id}/binding", OwnerVoice,
            json=binding.model_dump(mode="json"))

    async def sweep(self) -> None:
        catalog, _ = await self._channel.catalog()
        current = host_model(catalog) if catalog else None
        if current is None or not (catalog.enrollment.clone or catalog.enrollment.design):
            return
        own = set(await self._channel.list_own(current.provider))
        known = set(await self._data.speech_voice_call(
            "GET", f"/provider-voices/{current.provider}", list[str]))
        orphans = own - known
        for voice_id in orphans & self._suspects:
            await self._channel.delete(current.provider, voice_id)
            logger.info("deleted orphan provider voice %s", voice_id)
        self._suspects = orphans - self._suspects
