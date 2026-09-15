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

Every address *of the product's*, since 2026-09-15. There is one kind of link
the Host is in a position to rule out, and only because Ops tells it: the cable
to an operator's workstation, which no phone is ever on. That is not a guess
about the phone's network — it is a fact about this one, declared where it is
known. Nothing else narrows: an interface that is down is still offered, and so
is a link-local address, for the reasons each says below.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Sequence

import psutil
from eidolon_sdk.system import on_product_link

__all__ = ["local_api_base_urls", "reachable_ipv4_addresses"]

logger = logging.getLogger(__name__)

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def local_api_base_urls(port: int, management_networks: Sequence[IPNetwork] = ()) -> list[str]:
    """Every address a Controller could reach this Host's Local API on."""

    return [
        f"https://{address}:{port}"
        for address in reachable_ipv4_addresses(management_networks)
    ]


def reachable_ipv4_addresses(management_networks: Sequence[IPNetwork] = ()) -> list[str]:
    """This Host's own IPv4 addresses, most routable first.

    Link-local addresses come last rather than being dropped: a phone is not
    normally on one, but a Host reachable only over a direct cable is still
    reachable, and the Host is in no position to decide which network the phone
    is on.

    The links Ops keeps for itself are dropped, which is the one exception and
    is not the same judgement: those are addresses this Host knows no phone can
    be on, because the workstation at the other end of that cable is the only
    thing there. They are not free to carry either — the endpoint they go into
    is signed into a single 512-byte characteristic read, and the addresses
    that do not fit are dropped from the end, so a cable near the front of the
    list costs a real address at the back of it.
    """

    addresses: list[str] = []
    for raw in _kernel_reported_addresses():
        try:
            parsed = ipaddress.IPv4Address(raw)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_multicast or parsed.is_unspecified:
            continue
        if not on_product_link(parsed, management_networks):
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
