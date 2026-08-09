"""Gateway settings — loaded from environment + services.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseModel):
    type: Literal["none", "bearer", "passthrough"] = "none"
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


class PortsDecl(BaseModel):
    """Ports a service is expected to listen on.

    Drives the System Health audit: admin enumerates these and checks
    (a) is something listening? (b) is that something one of the
    service's supervised processes? Discrepancies (no listener, or a
    listener that supervisord doesn't know about) become observable
    orphan / down signals.

    Kept optional so services with no network surface (e.g. memory
    internals that talk only over NATS) don't need to declare anything.
    """

    declared: list[int] = Field(default_factory=list)


class ConfigEntry(BaseModel):
    """A single editable config file declared by a service.

    The {svc}/{cfg} URL pair is just a lookup key; the actual filesystem path
    is resolved server-side from this declaration. ``path`` may contain ``~``
    and ``$VAR`` for portability between machines.
    """

    id: str
    label: str | None = None
    path: str
    format: Literal["yaml", "dotenv", "ini"] = "yaml"
    reload: Literal["sighup_program", "restart_program", "restart_group", "none"] = (
        "none"
    )
    reload_target: str | None = None
    template: str | None = None


class ServiceConfig(BaseModel):
    id: str
    name: str
    # How this gateway integrates with the sub-project. Drives UI affordances
    # (e.g. "proxy" services may show a generic API console; "native" services
    # don't) and documents intent. See services.yaml header for the full
    # taxonomy. ``proxy`` is the default for backwards-compat with services
    # that pre-date this field.
    integration: Literal["native", "proxy", "process", "infra"] = "proxy"
    # ``optional=true`` marks a service the operator may run independently
    # outside supervisord (e.g. mementos: operator opens the Electron
    # app manually, supervisord just tries to coordinate). When true:
    #
    #   * pre-flight port audit doesn't refuse the cold start if this
    #     service's declared port is already bound — it logs a notice
    #     and lets the rest of the stack come up.
    #   * supervisord's spawn-error for this service is downgraded from
    #     "stack broken" to "side-project unavailable".
    #
    # All other services default to ``optional=false`` — required-by-
    # default keeps "杜绝 silent skip" honest for the core stack.
    optional: bool = False
    base_url: str = ""
    upstream_prefix: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)
    health: str | None = None
    supervisor: SupervisorRef | None = None
    features: list[FeatureEntry] = Field(default_factory=list)
    configs: list[ConfigEntry] = Field(default_factory=list)
    ports: PortsDecl = Field(default_factory=PortsDecl)

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
    esp32_tools_file: Path = _REPO_ROOT / "config" / "esp32_tools.yaml"
    state_dir: Path = _REPO_ROOT / "var"
    system_directory_url: str = "http://127.0.0.1:8090"
    system_directory_uds: Path | None = None
    directory_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    authority_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    # A distinct Admin service credential. The producer currently supports one
    # opaque token only; production must not silently reuse Kernel's env var.
    data_authority_token: str = ""
    # Separate write credential for Data's narrow workspace authority.
    data_workspace_authority_token: str = ""
    # Loopback ingress credential used only by eidolon-local-api.
    local_api_service_token: str = ""
    # Installation secret shared only with Hub's Owner management verifier.
    # It is used to mint short-lived Admin Device-approval credentials and is
    # never exposed through Admin or the Local API.
    hub_management_jwt_secret: SecretStr = SecretStr("")
    hub_management_jwt_ttl_seconds: int = Field(default=60, ge=30, le=300)
    # supervisord wiring — these defaults match what deploy/dev/supervisord.conf
    # writes when run from the project root.
    supervisor_socket: Path = _REPO_ROOT / "var" / "supervisor.sock"
    supervisor_available_dir: Path = _REPO_ROOT / "deploy" / "supervisor" / "available"
    supervisor_enabled_dir: Path = _REPO_ROOT / "deploy" / "supervisor" / "enabled"

    @field_validator("system_directory_uds", mode="before")
    @classmethod
    def _blank_uds_is_disabled(cls, value: object) -> object:
        return None if value is None or not str(value).strip() else value


@lru_cache
def get_settings() -> Settings:
    return Settings()


def default_eidolon_root() -> Path:
    """Monorepo root containing eidolon_admin, eidolon_agent, etc."""
    env = os.environ.get("EIDOLON_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # A linked Git worktree has a .git *file* pointing into the original
    # repository. Resolve that location so sibling repositories do not become
    # incorrectly relative to .codex/worktrees/<id>.
    git_pointer = _REPO_ROOT / ".git"
    if git_pointer.is_file():
        lines = git_pointer.read_text(encoding="utf-8").splitlines()
        first_line = lines[0] if lines else ""
        prefix = "gitdir:"
        if first_line.lower().startswith(prefix):
            git_dir = Path(first_line[len(prefix) :].strip()).expanduser()
            if not git_dir.is_absolute():
                git_dir = _REPO_ROOT / git_dir
            git_dir = git_dir.resolve()
            for parent in git_dir.parents:
                if parent.name == ".git":
                    return parent.parent.parent.resolve()
    return (_REPO_ROOT.parent).resolve()


def load_gateway_config(path: Path | None = None) -> GatewayConfig:
    from .ports import apply_ports_to_environ

    apply_ports_to_environ()
    target = path or get_settings().services_file
    text = target.read_text(encoding="utf-8")
    if "EIDOLON_ROOT" in text and not os.environ.get("EIDOLON_ROOT"):
        os.environ.setdefault("EIDOLON_ROOT", str(default_eidolon_root()))
    raw = yaml.safe_load(os.path.expandvars(text)) or {}
    return GatewayConfig.model_validate(raw)
