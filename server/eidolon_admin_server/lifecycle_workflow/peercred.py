"""Kernel-backed workload authentication for Linux Unix sockets."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UnixPeerCredential:
    pid: int
    uid: int
    gid: int


@dataclass(frozen=True, slots=True)
class AuthenticatedWorkload:
    principal_id: str
    credential: UnixPeerCredential


class PeerCredentialPort(Protocol):
    def read(self, connection: socket.socket) -> UnixPeerCredential: ...


class LinuxSoPeerCredentialAdapter:
    """Read credentials supplied by the Linux kernel, never request data."""

    _STRUCT = struct.Struct("3i")

    def read(self, connection: socket.socket) -> UnixPeerCredential:
        option = getattr(socket, "SO_PEERCRED", None)
        if option is None:
            raise RuntimeError("Linux SO_PEERCRED is unavailable")
        raw = connection.getsockopt(socket.SOL_SOCKET, option, self._STRUCT.size)
        if len(raw) != self._STRUCT.size:
            raise RuntimeError("Linux SO_PEERCRED returned an invalid value")
        pid, uid, gid = self._STRUCT.unpack(raw)
        if pid <= 0 or uid < 0 or gid < 0:
            raise RuntimeError("Linux SO_PEERCRED returned invalid credentials")
        return UnixPeerCredential(pid=pid, uid=uid, gid=gid)


@dataclass(frozen=True, slots=True)
class ExactUidWorkloadAuthorizer:
    expected_uid: int
    principal_id: str = "eidolon-local-api"

    def authorize(self, credential: UnixPeerCredential) -> AuthenticatedWorkload:
        if credential.uid != self.expected_uid:
            raise PermissionError("Unix peer UID is not authorized")
        return AuthenticatedWorkload(
            principal_id=self.principal_id,
            credential=credential,
        )
