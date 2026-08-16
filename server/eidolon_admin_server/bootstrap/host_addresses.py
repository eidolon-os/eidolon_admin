"""Where this Host can be reached, as the Host itself sees it.

A Controller finds a Host by listening for its announcement. That works until
it does not: same Wi-Fi, same subnet, the Host announcing correctly and a
phone three feet away hearing nothing. The phone then has a Host it has
claimed, whose identity it holds, at an address it could reach — and no way to
learn that address.

So the Host says it, over the channel that is already how a phone and a Host
talk when the network cannot be relied on. Every address is offered because
the Host does not know which network the phone is on; the phone tries them and
proves the identity at whichever answers.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import subprocess

__all__ = ["local_api_base_urls", "reachable_ipv4_addresses"]

logger = logging.getLogger(__name__)

#: Reading the kernel's own view costs a few milliseconds and is asked for
#: rarely; a Host with a wedged network must not wedge the endpoint too.
_TIMEOUT_SECONDS = 5


def local_api_base_urls(port: int) -> list[str]:
    """Every address a Controller could reach this Host's Local API on."""

    return [f"https://{address}:{port}" for address in reachable_ipv4_addresses()]


def reachable_ipv4_addresses() -> list[str]:
    """This Host's own IPv4 addresses, most routable first.

    Link-local addresses come last rather than being dropped: a phone is not
    normally on one, but a Host reachable only over a direct cable is still
    reachable, and the Host is in no position to decide which network the phone
    is on.
    """

    addresses: list[str] = []
    for raw in _kernel_reported_addresses():
        try:
            parsed = ipaddress.IPv4Address(raw)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_multicast or parsed.is_unspecified:
            continue
        if raw not in addresses:
            addresses.append(raw)
    addresses.sort(key=lambda value: ipaddress.IPv4Address(value).is_link_local)
    return addresses


def _kernel_reported_addresses() -> list[str]:
    try:
        completed = subprocess.run(
            ("ip", "-json", "-4", "addr", "show"),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not read this Host's addresses: %s", exc)
        return []
    if completed.returncode != 0:
        logger.warning(
            "could not read this Host's addresses: ip exited %s", completed.returncode
        )
        return []
    try:
        interfaces = json.loads(completed.stdout or "[]")
    except ValueError as exc:
        logger.warning("could not parse this Host's addresses: %s", exc)
        return []
    found: list[str] = []
    for interface in interfaces if isinstance(interfaces, list) else []:
        if not isinstance(interface, dict):
            continue
        for entry in interface.get("addr_info") or []:
            address = entry.get("local") if isinstance(entry, dict) else None
            if isinstance(address, str):
                found.append(address)
    return found
