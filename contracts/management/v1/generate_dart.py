#!/usr/bin/env python3
"""Emit the Dart types for Management v1 from the committed contract.

Same shape as the TypeScript emitter and for the same reasons: a generator plus
a ``--check`` gate run from a test, refusing any schema construct it does not
understand rather than producing a type that compiles and lies.

Why Dart is generated here rather than hand-written in the app: two clients
generated from one document is the mechanism that keeps this ABI from taking the
shape of whichever client was written first (plan §1.4). A hand-written DTO in
the app would be a second, quietly diverging opinion about the same wire.

Output lands in the mobile repository, following the precedent set by
``eidolon_sdk/contracts/device_foundation/v1/generation/generate.py``, which
writes its Dart and C++ bindings into the client repos that consume them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE / "management-v1.openapi.json"
#: ``eidolon_admin/contracts/management/v1`` → workspace root is four up.
WORKSPACE = HERE.parents[3]
OUTPUT = WORKSPACE / "eidolon_client_mobile/lib/src/generated/management_v1.dart"

_PRIMITIVES = {"string": "String", "integer": "int", "number": "double", "boolean": "bool"}


class UnsupportedSchema(Exception):
    """A shape this emitter will not guess at."""


def _dart_name(field: str) -> str:
    head, *rest = field.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _type_of(schema: dict, *, where: str) -> tuple[str, bool]:
    """Return the Dart type and whether it admits null."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1], False
    if "anyOf" in schema:
        options = [
            _type_of(option, where=f"{where}.anyOf[{index}]")
            for index, option in enumerate(schema["anyOf"])
        ]
        concrete = [name for name, _ in options if name != "Null"]
        nullable = any(name == "Null" for name, _ in options)
        if len(set(concrete)) != 1:
            if set(concrete) <= set(_PRIMITIVES.values()):
                # Dart has no untagged union. A union of primitives becomes
                # ``Object``, which is exact in the same sense TypeScript's
                # ``unknown`` is: it carries the value and forces the caller to
                # test before using it. Anything wider than primitives is
                # refused rather than flattened, because flattening an object
                # union produces a type that compiles and lies.
                return "Object", nullable
            raise UnsupportedSchema(
                f"{where}: Dart has no untagged union; got {sorted(set(concrete))}"
            )
        return concrete[0], nullable
    if "const" in schema:
        # A single permitted value. Typed as its primitive and asserted at parse
        # time, because Dart has no literal type to carry the constraint.
        return _PRIMITIVES[_json_kind(schema["const"])], False
    if "enum" in schema:
        kinds = {_json_kind(value) for value in schema["enum"]}
        if len(kinds) != 1:
            raise UnsupportedSchema(f"{where}: mixed-type enum")
        return _PRIMITIVES[kinds.pop()], False
    kind = schema.get("type")
    if kind == "null":
        return "Null", True
    if kind in _PRIMITIVES:
        return _PRIMITIVES[kind], False
    if kind == "array":
        return f"List<{_element_of(schema.get('items', {}), where=f'{where}[]')}>", False
    if kind == "object":
        values = schema.get("additionalProperties")
        if isinstance(values, dict):
            element = _element_of(values, where=f"{where}{{}}")
            return f"Map<String, {element}>", False
        return "Map<String, Object?>", False
    if not schema or set(schema) <= {"title", "description"}:
        # "Any value" — FastAPI emits this for the offending input inside a
        # validation error. ``Object?`` is exact rather than a guess: it forces
        # a caller to narrow before use.
        return "Object?", True
    raise UnsupportedSchema(
        f"{where}: this emitter does not translate {json.dumps(schema, sort_keys=True)}"
    )


def _path_member(path: str) -> str:
    """A constant for a fixed path, a function for a templated one.

    A templated path cannot be a constant a caller pastes into: they would
    concatenate and, sooner or later, forget to percent-encode an id. The name
    is built from every segment — template segments contributing the parameter
    they stand for — so ``/companions`` and ``/companions/{companion_id}``
    cannot collide.
    """

    #: Everything after the version prefix identifies the operation.
    segments = [segment for segment in path.split("/") if segment][3:]
    words: list[str] = []
    parameters: list[str] = []
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            parameter = _dart_name(segment[1:-1])
            parameters.append(parameter)
            words.extend(["by", parameter])
        else:
            words.append(segment.replace("-", "_"))
    name = _dart_name("_".join(words) + "_path")
    if not parameters:
        return f"static const String {name} = '{path}';"

    interpolated = path
    for parameter, segment in zip(
        parameters, [s for s in segments if s.startswith("{")]
    ):
        interpolated = interpolated.replace(
            segment, f"${{Uri.encodeComponent({parameter})}}"
        )
    arguments = ", ".join(f"String {parameter}" for parameter in parameters)
    return f"static String {name}({arguments}) => '{interpolated}';"


def _element_of(schema: dict, *, where: str) -> str:
    """The Dart type of a container's element, nullability included.

    Kept separate because a container has to carry its element's nullability in
    the type itself — Dart has no "nullable somewhere else" — and dropping it
    produced ``Map<String, int>`` for a limit whose whole point is that it may
    be null. That compiled, and threw at the first response.
    """

    name, nullable = _type_of(schema, where=where)
    if not nullable or name.endswith("?"):
        return name
    return f"{name}?"


def _json_kind(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    raise UnsupportedSchema(f"unsupported literal {value!r}")


def _class(name: str, schema: dict) -> str:
    if schema.get("type") != "object":
        raise UnsupportedSchema(f"{name}: only object schemas become classes")
    required = set(schema.get("required", []))
    fields = []
    for field, definition in schema.get("properties", {}).items():
        dart_type, nullable = _type_of(definition, where=f"{name}.{field}")
        # Optional on the wire is nullable in Dart: a client must be able to
        # hold "the Host did not say" without inventing a value for it.
        optional = field not in required or nullable
        fields.append(
            {
                "wire": field,
                "name": _dart_name(field),
                "type": f"{dart_type}?" if optional and dart_type != "Object?" else dart_type,
                "optional": optional,
                "raw_type": dart_type,
                "doc": definition.get("description"),
            }
        )

    lines = [f"class {name} {{", f"  const {name}({{"]
    for field in fields:
        prefix = "" if field["optional"] else "required "
        lines.append(f"    {prefix}this.{field['name']},")
    lines.append("  });")
    lines.append("")
    for field in fields:
        if field["doc"]:
            for paragraph in field["doc"].split("\n"):
                lines.append(f"  /// {paragraph.strip()}" if paragraph.strip() else "  ///")
        lines.append(f"  final {field['type']} {field['name']};")
        lines.append("")
    lines.append(f"  factory {name}.fromJson(Map<String, dynamic> value) {{")
    lines.append(f"    return {name}(")
    for field in fields:
        accessor = f"value['{field['wire']}']"
        if field["raw_type"] == "Object?":
            expression = accessor
        elif field["raw_type"].startswith("List<"):
            inner = field["raw_type"][5:-1]
            #: ``Object``/``Object?`` are Dart's own top types, not schemas —
            #: casting is the whole translation. Only a named schema gets a
            #: ``fromJson``.
            opaque = {"Object", "Object?"} | set(_PRIMITIVES.values())
            element = (
                f"{inner}.fromJson(entry as Map<String, dynamic>)"
                if inner[:1].isupper() and inner not in opaque
                else f"entry as {inner}"
            )
            listing = f"(({accessor} as List<dynamic>).map((entry) => {element}).toList())"
            expression = (
                f"{accessor} == null ? null : {listing}" if field["optional"] else listing
            )
        elif field["raw_type"].startswith("Map<"):
            inner = field["raw_type"].split(", ", 1)[1][:-1]
            mapping = (
                f"(({accessor} as Map<String, dynamic>)"
                f".map((key, entry) => MapEntry(key, entry as {inner})))"
            )
            expression = (
                f"{accessor} == null ? null : {mapping}" if field["optional"] else mapping
            )
        elif field["raw_type"] in _PRIMITIVES.values():
            expression = f"{accessor} as {field['type']}"
        else:
            nested = f"{field['raw_type']}.fromJson({accessor} as Map<String, dynamic>)"
            expression = (
                f"{accessor} == null ? null : {nested}" if field["optional"] else nested
            )
        lines.append(f"      {field['name']}: {expression},")
    lines.append("    );")
    lines.append("  }")
    lines.append("")
    lines.extend(_to_json(fields))
    lines.append("}")
    return "\n".join(lines)


def _to_json(fields: list[dict]) -> list[str]:
    """The way back out, generated for the same reason the way in is.

    A request body assembled by hand is a second, silent copy of the contract:
    add a field to a schema and the hand-written map keeps compiling while the
    field never leaves the phone. Nothing says so — the request succeeds, and
    whatever the person typed into that field is simply gone.

    Absent fields are omitted rather than sent as null. On these contracts "the
    client did not say" and "the client said nothing is there" are different
    requests — one of them replays on retry and the other can conflict — so the
    difference has to survive serialisation.
    """

    lines = ["  Map<String, dynamic> toJson() {", "    return {"]
    for field in fields:
        value = field["name"]
        if field["raw_type"].startswith("List<"):
            inner = field["raw_type"][5:-1]
            opaque = {"Object", "Object?"} | set(_PRIMITIVES.values())
            if inner[:1].isupper() and inner not in opaque:
                value = (
                    f"{field['name']}{'?' if field['optional'] else ''}"
                    ".map((entry) => entry.toJson()).toList()"
                )
        elif field["raw_type"].startswith("Map<"):
            inner = field["raw_type"].split(", ", 1)[1][:-1]
            opaque = {"Object", "Object?"} | set(_PRIMITIVES.values())
            if inner[:1].isupper() and inner not in opaque:
                value = (
                    f"{field['name']}{'?' if field['optional'] else ''}"
                    ".map((key, entry) => MapEntry(key, entry.toJson()))"
                )
        elif field["raw_type"] not in ({"Object?"} | set(_PRIMITIVES.values())):
            value = f"{field['name']}{'?' if field['optional'] else ''}.toJson()"
        if field["optional"]:
            lines.append(
                f"      if ({field['name']} != null) '{field['wire']}': {value},"
            )
        else:
            lines.append(f"      '{field['wire']}': {value},")
    lines.append("    };")
    lines.append("  }")
    return lines


def build_output() -> bytes:
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    body = [
        "// Generated from eidolon_admin/contracts/management/v1/"
        "management-v1.openapi.json.",
        "// Do not edit by hand; contracts/management/v1/generate_dart.py owns this file",
        "// and a test runs it with --check, so an edit here fails rather than surviving.",
        "//",
        "// No operation takes an ownerId: the Owner comes from the authenticated",
        "// Controller session, so it is not expressible from a client.",
        "",
        "/// The paths this contract describes, so a caller does not spell one.",
        "class ManagementV1 {",
        "  const ManagementV1._();",
        "",
    ]
    for path in sorted(document["paths"]):
        body.append("  " + _path_member(path))
    body.append("}")
    body.append("")
    for name in sorted(schemas):
        body.append(_class(name, schemas[name]))
        body.append("")
    return "\n".join(body).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    try:
        expected = build_output()
    except UnsupportedSchema as exc:
        print(f"management Dart generation refused: {exc}", file=sys.stderr)
        return 1

    if arguments.check:
        if not OUTPUT.parent.parent.parent.exists():
            print(f"management Dart skipped: {OUTPUT.parents[3]} is not checked out")
            return 0
        if not OUTPUT.exists() or OUTPUT.read_bytes() != expected:
            print(
                f"management Dart drift: {OUTPUT} no longer matches the contract",
                file=sys.stderr,
            )
            return 1
        print(f"management Dart clean: {OUTPUT}")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(expected)
    print(f"generated {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
