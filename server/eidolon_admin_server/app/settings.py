"""Gateway settings — loaded from environment + services.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseModel):
    type: Literal["none", "bearer"] = "none"
    token_env: str | None = None


class SupervisorRef(BaseModel):
    """Link from a service to its supervisord config + program names.

    Lets the UI group supervisor programs under their owning service card.
    """
    config_file: str | None = None
    group: str | None = None
    programs: list[str] = Field(default_factory=list)


class FeatureEntry(BaseModel):
    key: str
    label: str
    stream: bool = False
    route: str | None = None


class ServiceConfig(BaseModel):
    id: str
    name: str
    # How this gateway integrates with the sub-project. Drives UI affordances
    # (e.g. "proxy" services may show a generic API console; "native" services
    # don't) and documents intent. See services.yaml header for the full
    # taxonomy. ``proxy`` is the default for backwards-compat with services
    # that pre-date this field.
    integration: Literal["native", "proxy", "process", "infra"] = "proxy"
    base_url: str = ""
    upstream_prefix: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)
    health: str | None = None
    supervisor: SupervisorRef | None = None
    features: list[FeatureEntry] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _strip_trailing(cls, v: str) -> str:
        return v.rstrip("/")


class AdminBindConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9000
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:9001",
            "http://localhost:9001",
        ]
    )


class GatewayConfig(BaseModel):
    admin: AdminBindConfig = Field(default_factory=AdminBindConfig)
    services: list[ServiceConfig] = Field(default_factory=list)

    def find(self, service_id: str) -> ServiceConfig | None:
        for s in self.services:
            if s.id == service_id:
                return s
        return None


_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EIDOLON_ADMIN_", extra="ignore")

    services_file: Path = _REPO_ROOT / "config" / "services.yaml"
    # supervisord wiring — these defaults match what deploy/dev/supervisord.conf
    # writes when run from the project root.
    supervisor_socket: Path = _REPO_ROOT / "var" / "supervisor.sock"
    supervisor_available_dir: Path = _REPO_ROOT / "deploy" / "supervisor" / "available"
    supervisor_enabled_dir: Path = _REPO_ROOT / "deploy" / "supervisor" / "enabled"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_gateway_config(path: Path | None = None) -> GatewayConfig:
    target = path or get_settings().services_file
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return GatewayConfig.model_validate(raw)
