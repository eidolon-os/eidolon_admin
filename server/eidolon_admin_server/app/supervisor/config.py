"""Filesystem-level management of supervisor configs.

Layout (sites-available / sites-enabled pattern):

    deploy/supervisor/
      available/<name>.conf       # canonical home for every project's config
      enabled/<name>.conf -> ../available/<name>.conf

Enable  = create the symlink in enabled/.
Disable = remove the symlink in enabled/.

The supervisord master config [include]s enabled/*.conf, so disabling is a
file-system action that takes effect on the next reloadConfig().
"""
from __future__ import annotations

import asyncio
import configparser
import re
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised for invalid names, missing files, traversal attempts, etc."""


_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_FILE_LOCK = asyncio.Lock()


@dataclass
class ConfigEntry:
    name: str                              # filename without .conf
    available_path: Path
    enabled_path: Path
    enabled: bool
    programs: list[str]                    # parsed [program:X] section names
    groups: list[str]                      # parsed [group:Y] section names


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ConfigError(
            f"invalid name {name!r}: must match {_NAME_RE.pattern}"
        )


def _parse_sections(text: str) -> tuple[list[str], list[str]]:
    """Return (program_names, group_names) declared in an ini text."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)
    programs: list[str] = []
    groups: list[str] = []
    for section in parser.sections():
        if section.startswith("program:"):
            programs.append(section.split(":", 1)[1].strip())
        elif section.startswith("group:"):
            groups.append(section.split(":", 1)[1].strip())
    return programs, groups


class ConfigStore:
    """Encapsulates available/ and enabled/ directories."""

    def __init__(self, available: Path, enabled: Path) -> None:
        self._available = Path(available)
        self._enabled = Path(enabled)
        self._available.mkdir(parents=True, exist_ok=True)
        self._enabled.mkdir(parents=True, exist_ok=True)

    @property
    def available_dir(self) -> Path:
        return self._available

    @property
    def enabled_dir(self) -> Path:
        return self._enabled

    def _available_path(self, name: str) -> Path:
        _validate_name(name)
        return self._available / f"{name}.conf"

    def _enabled_path(self, name: str) -> Path:
        _validate_name(name)
        return self._enabled / f"{name}.conf"

    # ---- queries ------------------------------------------------------------

    def list(self) -> list[ConfigEntry]:
        entries: list[ConfigEntry] = []
        for path in sorted(self._available.glob("*.conf")):
            name = path.stem
            try:
                text = path.read_text(encoding="utf-8")
                programs, groups = _parse_sections(text)
            except (OSError, configparser.Error):
                programs, groups = [], []
            enabled_path = self._enabled / path.name
            entries.append(
                ConfigEntry(
                    name=name,
                    available_path=path,
                    enabled_path=enabled_path,
                    enabled=self._is_enabled(enabled_path, path),
                    programs=programs,
                    groups=groups,
                )
            )
        return entries

    def get(self, name: str) -> ConfigEntry:
        path = self._available_path(name)
        if not path.exists():
            raise ConfigError(f"no such config: {name}")
        text = path.read_text(encoding="utf-8")
        programs, groups = _parse_sections(text)
        enabled_path = self._enabled_path(name)
        return ConfigEntry(
            name=name,
            available_path=path,
            enabled_path=enabled_path,
            enabled=self._is_enabled(enabled_path, path),
            programs=programs,
            groups=groups,
        )

    def read_text(self, name: str) -> str:
        path = self._available_path(name)
        if not path.exists():
            raise ConfigError(f"no such config: {name}")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _is_enabled(enabled_path: Path, available_path: Path) -> bool:
        if not enabled_path.exists() and not enabled_path.is_symlink():
            return False
        # Accept symlinks pointing to the matching available/ file. Also accept
        # plain files (user copy-pasted) as enabled.
        try:
            resolved = enabled_path.resolve(strict=False)
        except OSError:
            return False
        return resolved == available_path.resolve(strict=False)

    # ---- mutations ----------------------------------------------------------

    async def write_text(self, name: str, content: str) -> ConfigEntry:
        async with _FILE_LOCK:
            path = self._available_path(name)
            # Validate ini parses before persisting.
            try:
                configparser.ConfigParser(interpolation=None, strict=False).read_string(content)
            except configparser.Error as exc:
                raise ConfigError(f"invalid ini: {exc}") from exc
            path.write_text(content, encoding="utf-8")
        return self.get(name)

    async def enable(self, name: str) -> ConfigEntry:
        async with _FILE_LOCK:
            available = self._available_path(name)
            if not available.exists():
                raise ConfigError(f"no such config: {name}")
            link = self._enabled_path(name)
            if link.is_symlink() or link.exists():
                link.unlink()
            # Use a relative target so the tree is portable.
            relative = Path("..") / "available" / available.name
            link.symlink_to(relative)
        return self.get(name)

    async def disable(self, name: str) -> ConfigEntry:
        async with _FILE_LOCK:
            link = self._enabled_path(name)
            if link.is_symlink() or link.exists():
                link.unlink()
        return self.get(name)
