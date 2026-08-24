"""The contract two clients are generated from, and the gate that keeps it true.

A hand-written API document drifts from the code the first time someone is in a
hurry, and it drifts silently — the next person to notice is whoever generated a
client from it. So the document is generated from the routes and committed, and
this is the gate that fails when the two disagree.

What is asserted here is not the document's current contents (that would restate
the artifact) but the properties a generated client depends on.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
CONTRACT = SERVER.parent / "contracts/management/v1"
GENERATOR = CONTRACT / "generate.py"
TYPESCRIPT_GENERATOR = CONTRACT / "generate_typescript.py"
DART_GENERATOR = CONTRACT / "generate_dart.py"
ARTIFACT = CONTRACT / "management-v1.openapi.json"


def _document() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_the_committed_contract_still_describes_the_routes() -> None:
    """The drift gate. Fails in CI rather than at the moment a client is wrong."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=SERVER,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_generated_typescript_still_matches_the_contract() -> None:
    """The second consumer's types, gated the same way as the document.

    Web has no management pages yet and still carries these types, because a
    second consumer is the only outside pressure keeping this ABI from taking
    the shape of one client (plan §1.4). Pressure that silently goes stale is
    not pressure, so the drift is a test failure here rather than a surprise
    whenever Web is finally built.
    """
    result = subprocess.run(
        [sys.executable, str(TYPESCRIPT_GENERATOR), "--check"],
        cwd=SERVER,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_generated_dart_still_matches_the_contract() -> None:
    """The mobile client's types, gated like the other two artifacts.

    Emitted into the mobile repository, following the precedent of the Device
    Foundation generator, which writes its Dart and C++ bindings into the client
    repos that consume them. When that checkout is absent the generator says so
    and passes: a repository cannot verify a file it does not contain, and
    failing here would only teach people to ignore this test.
    """
    result = subprocess.run(
        [sys.executable, str(DART_GENERATOR), "--check"],
        cwd=SERVER,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_contract_carries_only_the_management_surface() -> None:
    """This app also serves Local API's other routes, deliberately excluded.

    Including them would hand two generated clients a surface the plan says they
    must not call — and the guard against calling it would then be a habit
    rather than an absence.
    """
    paths = _document()["paths"]
    assert paths
    assert all(path.startswith("/api/management/v1") for path in paths)


#: Query parameters this ABI may offer. Not a style rule — every name here is a
#: value the Host itself handed the client (a cursor), so the list is closed and
#: an addition is a decision. It replaces an earlier blanket "no query
#: parameters at all", which was too tight: it would have rejected pagination,
#: and a rule that has to be relaxed the first time it is inconvenient teaches
#: nothing about what actually matters — which is that a client cannot name a
#: subject it was not given.
ALLOWED_QUERY_PARAMETERS = {
    "cursor",
    # An *audience*, not a subject. Memory belongs to the Owner and every one of
    # their Companions reads it, so naming one adds a layer and cannot widen
    # what the space holds — a foreign id simply matches nothing. The value also
    # came from the roster this Host served, which is the property that keeps
    # this list closed: every name here is something the Host handed the client.
    "companion_id",
}


def test_no_operation_accepts_an_owner(  # noqa: D401 - the name is the assertion
) -> None:
    """A parameter in this document is a parameter two clients can send.

    The Owner comes from the authenticated Controller session, so it must not be
    expressible in a generated client at all.
    """
    for path, operations in _document()["paths"].items():
        for method, operation in operations.items():
            parameters = operation.get("parameters", [])
            names = {parameter["name"] for parameter in parameters}
            assert "owner_id" not in names, f"{method.upper()} {path} takes an owner_id"
            queries = {
                parameter["name"]
                for parameter in parameters
                if parameter["in"] == "query"
            }
            assert queries <= ALLOWED_QUERY_PARAMETERS, (
                f"{method.upper()} {path} declares {queries - ALLOWED_QUERY_PARAMETERS}"
            )


def test_the_contract_carries_only_the_schemas_its_paths_reach() -> None:
    """Otherwise a client gets types for surfaces it may not call."""
    document = _document()
    referenced = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                referenced.add(reference.rsplit("/", 1)[1])
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(document["paths"])
    walk(document["components"]["schemas"])
    assert set(document["components"]["schemas"]) == referenced


def test_the_context_response_names_the_owner_once() -> None:
    """The single-adjudication rule, asserted where a client would read it."""
    schemas = _document()["components"]["schemas"]
    context = schemas["ManagementContextView"]["properties"]
    assert "owner_id" not in context, "owner_id belongs under owner, named once"
    assert "default_companion_id" in context
    assert set(schemas["OwnerContextView"]["properties"]) == {
        "owner_id",
        "display_name",
        "revision",
    }


def test_the_document_declares_its_version_rather_than_a_build_number() -> None:
    """A client pins a major version, not a build.

    The artifact changes with every additive route; the version does not, and a
    breaking change is a new path rather than a bumped number here.
    """
    assert _document()["info"]["version"] == "1"
