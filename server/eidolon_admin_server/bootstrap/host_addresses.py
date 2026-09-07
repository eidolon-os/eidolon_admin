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
import logging
import socket

import psutil

__all__ = ["local_api_base_urls", "reachable_ipv4_addresses"]

logger = logging.getLogger(__name__)


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
    """Every IPv4 address the OS reports on any interface, in its own order.

    Asked of ``psutil`` rather than of a command. This shelled out to
    ``ip -json -4 addr show``, which is iproute2 and therefore Linux — so on
    macOS the command did not exist, the ``OSError`` was logged and swallowed,
    and the signed endpoint published an empty address list. Not a failure
    anybody would see: the fallback exists for the case where announcements do
    not reach the phone, so it is silent exactly until the day it is needed,
    and only then is it discovered to have been empty all along on that
    platform.

    ``psutil`` is already this repository's answer to the same question —
    ``app.system_health.probe`` uses it and says why: it works the same on
    macOS and Linux with no knowledge of either. A second parser here would
    have been a second thing to keep right.

    Interfaces that are down are still reported, as they were before. The Host
    does not know which network the phone is on, and a phone that tries an
    address nothing answers on has lost a round trip; a phone offered no
    address at all has lost the Host.
    """

    try:
        interfaces = psutil.net_if_addrs()
    except OSError as exc:
        logger.warning("could not read this Host's addresses: %s", exc)
        return []
    return [
        entry.address
        for entries in interfaces.values()
        for entry in entries
        if entry.family is socket.AF_INET and isinstance(entry.address, str)
    ]
