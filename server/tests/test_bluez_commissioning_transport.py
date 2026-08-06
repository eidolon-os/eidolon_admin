from __future__ import annotations

import pytest

from eidolon_admin_server.bootstrap.adapters.commissioning.bluez import (
    _BlueZCommissioningLink,
)


class _RecordingTx:
    def __init__(self) -> None:
        self.values: list[bytes] = []

    async def indicate(self, value: bytes) -> None:
        self.values.append(value)


@pytest.mark.asyncio
async def test_link_never_exceeds_gatt_attribute_value_limit() -> None:
    tx = _RecordingTx()
    link = _BlueZCommissioningLink(tx, "/org/bluez/hci0/dev_test")  # type: ignore[arg-type]
    link.mtu = 517

    payload = bytes(1025)
    await link.send(payload)

    assert [len(value) for value in tx.values] == [512, 512, 1]
    assert b"".join(tx.values) == payload


@pytest.mark.asyncio
async def test_link_uses_att_payload_for_default_mtu() -> None:
    tx = _RecordingTx()
    link = _BlueZCommissioningLink(tx, "/org/bluez/hci0/dev_test")  # type: ignore[arg-type]

    payload = bytes(41)
    await link.send(payload)

    assert [len(value) for value in tx.values] == [20, 20, 1]
    assert b"".join(tx.values) == payload
