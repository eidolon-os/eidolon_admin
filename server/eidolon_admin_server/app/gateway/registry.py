"""Service registry — wraps GatewayConfig with cached lookups."""
from __future__ import annotations

from ..settings import GatewayConfig, ServiceConfig


class ServiceRegistry:
    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._by_id = {s.id: s for s in config.services}

    @property
    def config(self) -> GatewayConfig:
        return self._config

    @property
    def services(self) -> list[ServiceConfig]:
        return list(self._config.services)

    def get(self, service_id: str) -> ServiceConfig | None:
        return self._by_id.get(service_id)

    def require(self, service_id: str) -> ServiceConfig:
        svc = self.get(service_id)
        if svc is None:
            raise KeyError(f"unknown service_id: {service_id}")
        return svc
