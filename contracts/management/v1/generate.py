#!/usr/bin/env python3
"""Emit the Management v1 OpenAPI artifact, or prove the committed one is current.

The artifact is the contract source two clients are built from, so it is
generated from the routes rather than written by hand — a hand-written document
drifts from the code the first time someone is in a hurry, and it drifts
silently.

Committed rather than produced at build time, for two reasons: a reviewer sees
the contract change in the diff of the change that caused it, and a client
repository can build offline without standing up this app.

``--check`` is the drift gate. It regenerates into memory and compares; it never
writes. A test runs it, so "the document no longer describes the routes" fails
in CI rather than at the moment a generated client turns out to be wrong.

Only the public management surface is included. This app also serves the Local
API's other routes, and putting them in a document that Web and Mobile generate
from would hand two clients a surface the plan says they must not use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE / "management-v1.openapi.json"
SERVER_ROOT = HERE.parents[3] / "server"

#: What belongs in the document a client is generated from. Everything the app
#: mounts outside this prefix is deliberately excluded, not accidentally.
PUBLIC_PREFIX = "/api/management/v1"


def _load_app():
    if str(SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(SERVER_ROOT))

    from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
    from eidolon_admin_server.local_api.app import create_app
    from eidolon_admin_server.local_api.config import LocalApiSettings

    class _Absent:
        """Composition needs ports; describing routes never calls them."""

        def __getattr__(self, name):
            raise AssertionError(f"generation must not call {name}")

        async def close(self) -> None:
            return None

    absent = _Absent()
    settings = LocalApiSettings(
        bootstrap=BootstrapSettings(
            mode=BootstrapMode.DEVELOPMENT,
            state_dir=HERE / ".generation-state",
            runtime_dir=HERE / ".generation-run",
            control_socket=HERE / ".generation-run/control.sock",
            ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        )
    )
    return create_app(
        settings,
        workspace_client=absent,
        runtime_client=absent,
        devices_client=absent,
        device_admission_client=absent,
        host_services_client=absent,
        management_backend=absent,
    )


def build_document() -> dict:
    document = _load_app().openapi()
    paths = {
        path: operations
        for path, operations in document["paths"].items()
        if path.startswith(PUBLIC_PREFIX)
    }
    if not paths:
        raise SystemExit("no management routes found; refusing to emit an empty contract")

    #: Only the schemas the kept paths actually reach. A document carrying every
    #: model this app defines would generate clients with types for surfaces they
    #: are not allowed to call.
    wanted = _referenced_schemas(paths, document.get("components", {}).get("schemas", {}))
    return {
        "openapi": document["openapi"],
        "info": {
            "title": "Eidolon Owner Management",
            "version": "1",
            "description": (
                "The single Owner management ABI. Both management clients are "
                "generated from this document; no route here accepts an owner_id, "
                "because the Owner comes from the authenticated Controller session."
            ),
        },
        "paths": paths,
        "components": {"schemas": wanted},
    }


def _referenced_schemas(paths: dict, schemas: dict) -> dict:
    """Transitively collect the schemas the kept paths refer to."""
    pending = _refs_in(paths)
    kept: dict[str, dict] = {}
    while pending:
        name = pending.pop()
        if name in kept or name not in schemas:
            continue
        kept[name] = schemas[name]
        pending |= _refs_in(schemas[name])
    return dict(sorted(kept.items()))


def _refs_in(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            found.add(reference.rsplit("/", 1)[1])
        for nested in value.values():
            found |= _refs_in(nested)
    elif isinstance(value, list):
        for nested in value:
            found |= _refs_in(nested)
    return found


def _serialise(document: dict) -> bytes:
    body = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return body.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    expected = _serialise(build_document())
    if arguments.check:
        if not ARTIFACT.exists() or ARTIFACT.read_bytes() != expected:
            print(
                f"management contract drift: {ARTIFACT} no longer matches the routes",
                file=sys.stderr,
            )
            return 1
        digest = hashlib.sha256(expected).hexdigest()
        print(f"management contract clean: sha256:{digest}")
        return 0

    ARTIFACT.write_bytes(expected)
    print(f"generated {ARTIFACT} (sha256:{hashlib.sha256(expected).hexdigest()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
