from pathlib import Path
from types import SimpleNamespace

from eidolon_admin_server.local_api import cli


def test_local_api_terminates_tls_with_the_bootstrap_commissioning_identity(
    monkeypatch,
) -> None:
    pem_path = Path("/var/lib/eidolon-bootstrap/commissioning_tls.pem")
    settings = SimpleNamespace(
        host="0.0.0.0",
        port=9002,
        bootstrap=SimpleNamespace(commissioning_tls_pem_path=pem_path),
    )
    app = object()
    captured: dict = {}

    monkeypatch.setattr(cli, "load_local_api_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_app", lambda value: app)
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda target, **kwargs: captured.update(target=target, **kwargs),
    )

    cli.main()

    assert captured == {
        "target": app,
        "host": "0.0.0.0",
        "port": 9002,
        "log_level": "info",
        "ssl_certfile": str(pem_path),
        "ssl_keyfile": str(pem_path),
    }
