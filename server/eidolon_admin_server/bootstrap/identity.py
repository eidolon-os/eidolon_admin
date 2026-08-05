"""Host identity lifecycle.

Development may create a per-host identity on first boot. Production fails
closed when manufacturing has not provisioned one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from .config import BootstrapMode
from .domain import HostIdentity


class HostIdentityError(RuntimeError):
    """Base error for missing or unsafe host identity material."""


class HostIdentityProvisioningRequired(HostIdentityError):
    """Production started without a manufacturing-provisioned identity."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class HostIdentityManager:
    def __init__(self, key_path: Path, mode: BootstrapMode) -> None:
        self._key_path = key_path
        self._mode = mode
        self._private_key: Ed25519PrivateKey | None = None
        self._identity: HostIdentity | None = None

    def load(self) -> HostIdentity:
        if not self._key_path.exists():
            if self._mode is BootstrapMode.PRODUCTION:
                raise HostIdentityProvisioningRequired(
                    f"production host identity is missing: {self._key_path}"
                )
            self._generate_development_identity()

        link_stat = self._key_path.lstat()
        if stat.S_ISLNK(link_stat.st_mode):
            raise HostIdentityError("host identity key must not be a symbolic link")
        key_stat = self._key_path.stat()
        if not stat.S_ISREG(key_stat.st_mode):
            raise HostIdentityError("host identity path is not a regular file")
        if key_stat.st_mode & 0o077:
            raise HostIdentityError("host identity key must have mode 0600")
        if (
            self._mode is BootstrapMode.PRODUCTION
            and key_stat.st_uid != os.geteuid()
        ):
            raise HostIdentityError(
                "production host identity key must be owned by bootstrapd"
            )

        raw_private = self._key_path.read_bytes()
        if len(raw_private) != 32:
            raise HostIdentityError("host identity key must be raw Ed25519 private bytes")
        try:
            private_key = Ed25519PrivateKey.from_private_bytes(raw_private)
        except ValueError as exc:
            raise HostIdentityError("host identity key is invalid") from exc

        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        digest = hashlib.sha256(public_raw).digest()
        identity = HostIdentity(
            host_id=f"ehost-{digest.hex()[:20]}",
            public_key=_b64url(public_raw),
            public_key_fingerprint=f"sha256:{_b64url(digest)}",
        )
        self._private_key = private_key
        self._identity = identity
        return identity

    def sign_mapping(self, value: dict[str, Any]) -> str:
        if self._private_key is None:
            self.load()
        assert self._private_key is not None
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _b64url(self._private_key.sign(canonical))

    @property
    def identity(self) -> HostIdentity:
        if self._identity is None:
            return self.load()
        return self._identity

    def _generate_development_identity(self) -> None:
        self._key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        raw_private = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            descriptor = os.open(
                self._key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw_private)
            output.flush()
            os.fsync(output.fileno())
