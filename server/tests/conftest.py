"""Shared test fixtures."""

from __future__ import annotations


import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    ServiceConfig,
)


@pytest.fixture
def gateway_config() -> GatewayConfig:
    return GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="agent",
                name="Agent",
                base_url="http://agent.test",
                upstream_prefix="/api/admin",
                auth=AuthConfig(type="none"),
                health="/api/admin/personas/templates",
                features=[],
            ),
            ServiceConfig(
                id="memory",
                name="Memory",
                base_url="http://memory.test",
                upstream_prefix="/api",
                auth=AuthConfig(type="bearer", token_env="TEST_MEMORY_TOKEN"),
                health="/api/health",
                features=[],
            ),
        ],
    )


@pytest.fixture
def app(gateway_config):
    return create_app(gateway_config)
