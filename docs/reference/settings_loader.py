"""Reference settings loader — copy into each sub-project (no shared package).

Synced convention: eidolon_admin/docs/config-convention.md
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class ConfigNotFoundError(FileNotFoundError):
    """Raised when a required settings or env file is missing."""


def resolve_settings_path(
    *,
    env_var: str,
    repo_root: Path,
    local_relative: str = "config/settings.yaml",
    legacy_paths: tuple[str, ...] = (),
) -> Path:
    """Return existing settings YAML path; raise if none found."""
    explicit = os.environ.get(env_var, "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigNotFoundError(
                f"{env_var} points to missing file: {p}"
            )
        return p.resolve()

    candidates = [repo_root / local_relative, *(repo_root / leg for leg in legacy_paths)]
    for p in candidates:
        if p.is_file():
            return p.resolve()

    hint = ", ".join(str(c) for c in candidates)
    raise ConfigNotFoundError(
        f"settings file not found (tried: {hint}). "
        f"Run ./deploy/dev/init.sh in {repo_root}"
    )


def resolve_env_file(
    *,
    env_var: str,
    repo_root: Path,
    local_relative: str = "config/.env",
    legacy_paths: tuple[str, ...] = (),
) -> Path:
    """Return existing dotenv path; raise if none found."""
    explicit = os.environ.get(env_var, "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigNotFoundError(f"{env_var} points to missing file: {p}")
        return p.resolve()

    candidates = [repo_root / local_relative, *(repo_root / leg for leg in legacy_paths)]
    for p in candidates:
        if p.is_file():
            return p.resolve()

    hint = ", ".join(str(c) for c in candidates)
    raise ConfigNotFoundError(
        f"env file not found (tried: {hint}). "
        f"Run ./deploy/dev/init.sh in {repo_root}"
    )


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Load nested dict from a YAML file for pydantic-settings."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._yaml_path = yaml_path

    def get_field_value(self, field, field_name: str, value_is_complex: bool):
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"expected mapping in {self._yaml_path}")
        return data


def reject_inline_secrets(data: Any, *, path: str = "") -> None:
    """Raise if yaml contains non-empty secret-like keys."""
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{path}.{k}" if path else k
            if k.lower() in ("api_key", "secret", "token") and isinstance(v, str) and v.strip():
                raise ValueError(
                    f"inline secret not allowed at {p}; use config/.env and SecretStr"
                )
            reject_inline_secrets(v, path=p)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            reject_inline_secrets(item, path=f"{path}[{i}]")


def settings_sources_with_yaml(
    settings_cls: type[BaseSettings],
    *,
    yaml_path: Path,
    init_settings,
    env_settings,
    dotenv_settings,
    file_secret_settings,
):
    """Standard source tuple: init > env > dotenv > yaml."""
    return (
        init_settings,
        env_settings,
        dotenv_settings,
        YamlSettingsSource(settings_cls, yaml_path),
    )
