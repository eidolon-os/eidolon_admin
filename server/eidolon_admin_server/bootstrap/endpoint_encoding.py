"""How big the endpoint is, said once, for everyone who has to care.

The endpoint is read from a single GATT characteristic, and a GATT attribute
value stops at 512 bytes: a phone that asks for more gets the first 512 and no
indication that anything is missing. Truncated JSON does not parse, so the
phone reports a Host that returned invalid identity data — which is true, and
says nothing about why.

Nothing here decides what goes in the endpoint. It exists so that the side
building it and the side serving it measure the same bytes: the limit is only
meaningful against the exact encoding that reaches the wire, and two places
spelling out `separators` and `sort_keys` is two places for them to drift.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = ["GATT_MAX_ATTRIBUTE_BYTES", "encode_endpoint", "endpoint_size"]

#: Bluetooth core specification, attribute value length. Not a tuning knob.
GATT_MAX_ATTRIBUTE_BYTES = 512


def encode_endpoint(endpoint: Mapping[str, Any]) -> bytes:
    """The exact bytes a Controller reads off the characteristic."""

    return json.dumps(
        endpoint,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def endpoint_size(endpoint: Mapping[str, Any]) -> int:
    return len(encode_endpoint(endpoint))
