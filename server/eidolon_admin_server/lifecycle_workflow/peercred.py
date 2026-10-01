"""Kernel-backed workload authentication, independent of the host driver."""

from __future__ import annotations

import socket
import struct
import ctypes
import sys
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


class DarwinPeerCredentialAdapter:
    """Read XNU's peer PID and effective UID/GID, never process/request claims.

    SOL_LOCAL and LOCAL_PEERPID are defined in macOS sys/un.h. getpeereid
    supplies the credentials captured by the kernel for the Unix connection.
    Both calls work with asyncio's TransportSocket as well as socket.socket.
    """

    def read(self, connection: socket.socket) -> UnixPeerCredential:
        library = ctypes.CDLL(None, use_errno=True)
        getpeereid = library.getpeereid
        getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        getpeereid.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise OSError(ctypes.get_errno(), "getpeereid failed")
        raw = connection.getsockopt(0, 2, struct.calcsize("i"))
        if len(raw) != struct.calcsize("i"):
            raise RuntimeError("LOCAL_PEERPID returned an invalid value")
        pid = struct.unpack("i", raw)[0]
        if pid <= 0:
            raise RuntimeError("LOCAL_PEERPID returned an invalid PID")
        return UnixPeerCredential(pid=pid, uid=uid.value, gid=gid.value)


def native_peer_credentials() -> PeerCredentialPort:
    """Select the native adapter at composition; unsupported hosts fail closed."""

    if sys.platform == "linux":
        return LinuxSoPeerCredentialAdapter()
    if sys.platform == "darwin":
        return DarwinPeerCredentialAdapter()
    raise RuntimeError(f"Unix peer authentication is unsupported on {sys.platform}")


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
