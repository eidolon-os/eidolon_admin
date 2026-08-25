#!/usr/bin/env python3
"""Emit the TypeScript types for Management v1 from the committed contract.

Follows the pattern this workspace already uses for contract bindings — a
generator plus a ``--check`` drift gate, run from a test — rather than adding an
npm codegen dependency. The document is small and we produce it ourselves, so a
general-purpose generator would be a large dependency for a narrow job, and it
would still need this same gate to be trustworthy.

The property that makes a hand-rolled emitter safe here is that it **refuses
what it does not understand**. Every construct it can translate is listed; a
schema shape outside that list stops generation with the offending path named,
rather than producing a type that compiles and lies.

Why TypeScript at all while Web has no management pages: a second consumer is
the only outside pressure keeping this ABI from quietly taking the shape of one
client (plan §1.4). The types plus a consumer test are that pressure, and they
cost nothing to keep current.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE / "management-v1.openapi.json"
#: ``contracts/management/v1`` → repository root is three parents up.
OUTPUT = HERE.parents[2] / "web/src/management/generated/management-v1.ts"

_PRIMITIVES = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}


class UnsupportedSchema(Exception):
    """A shape this emitter will not guess at."""


def _type_of(schema: dict, *, where: str) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "anyOf" in schema:
        return " | ".join(
            _type_of(option, where=f"{where}.anyOf[{index}]")
            for index, option in enumerate(schema["anyOf"])
        )
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "null":
        return "null"
    if kind in _PRIMITIVES:
        return _PRIMITIVES[kind]
    if kind == "array":
        return f"Array<{_type_of(schema.get('items', {}), where=f'{where}[]')}>"
    if kind == "object":
        values = schema.get("additionalProperties")
        if isinstance(values, dict):
            return f"Record<string, {_type_of(values, where=f'{where}{{}}')}>"
        return "Record<string, unknown>"
    if not schema:
        return "unknown"
    if set(schema) <= {"title", "description"}:
        # A schema with a name and nothing else says "any value" — FastAPI emits
        # this for the offending input inside a validation error. Translating it
        # to ``unknown`` is exact, not a guess: ``unknown`` is the type that
        # forces a caller to narrow before use.
        return "unknown"
    raise UnsupportedSchema(
        f"{where}: this emitter does not translate {json.dumps(schema, sort_keys=True)}"
    )


def _interface(name: str, schema: dict) -> str:
    if schema.get("type") != "object":
        raise UnsupportedSchema(f"{name}: only object schemas become interfaces")
    required = set(schema.get("required", []))
    lines = [f"export interface {name} {{"]
    for field, definition in schema.get("properties", {}).items():
        optional = "" if field in required else "?"
        description = definition.get("description")
        if description:
            lines.append(f"  /** {description.replace(chr(10), ' ')} */")
        lines.append(f"  {field}{optional}: {_type_of(definition, where=f'{name}.{field}')}")
    lines.append("}")
    return "\n".join(lines)


def build_output() -> bytes:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    operations: list[str] = []
    for path, methods in sorted(document["paths"].items()):
        for method, operation in sorted(methods.items()):
            content = operation["responses"]["200"].get("content", {})
            if "application/json" in content:
                emitted = _type_of(content["application/json"]["schema"], where=path)
            else:
                # A photograph, not a document. Typed as bytes rather than as
                # some object with a base64 field inside it: every layer that
                # encoded it would be spending a megabyte to say what the bytes
                # already say, and no client here has any use for what is inside.
                emitted = "Blob"
            operations.append(f"  '{method.upper()} {path}': {emitted}")

    body = [
        "// Generated from management-v1.openapi.json. Do not edit.",
        "//",
        "// Regenerate with contracts/management/v1/generate_typescript.py; a test",
        "// runs it with --check, so an edit here fails rather than surviving.",
        "//",
        "// No operation takes an owner_id: the Owner comes from the authenticated",
        "// Controller session, so it is not expressible from a client.",
        "",
    ]
    for name in sorted(schemas):
        body.append(_interface(name, schemas[name]))
        body.append("")
    body.append("/** Response type per operation, keyed as it is called. */")
    body.append("export interface ManagementResponses {")
    body.extend(operations)
    body.append("}")
    body.append("")
    return "\n".join(body).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    try:
        expected = build_output()
    except UnsupportedSchema as exc:
        print(f"management TypeScript generation refused: {exc}", file=sys.stderr)
        return 1

    if arguments.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != expected:
            print(
                f"management TypeScript drift: {OUTPUT} no longer matches the contract",
                file=sys.stderr,
            )
            return 1
        print(f"management TypeScript clean: {OUTPUT}")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(expected)
    print(f"generated {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
