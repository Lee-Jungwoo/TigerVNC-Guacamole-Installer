"""Small dependency-free validator for the bundled configuration schema.

Only JSON Schema keywords used by ``schemas/config-v1.schema.json`` are
implemented.  Unknown assertion keywords fail closed so the schema cannot grow
silently beyond what the installer actually validates.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .model import ConfigError


class _Mismatch(Exception):
    pass


_KNOWN_ANNOTATIONS = {
    "$schema", "$id", "$defs", "title", "description", "default", "examples",
    "deprecated", "readOnly", "writeOnly", "$comment",
}
_KNOWN_ASSERTIONS = {
    "$ref", "type", "const", "enum", "properties", "required",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "format", "minimum", "maximum",
    "anyOf", "allOf", "if", "then", "else",
}


def load_bundled_schema() -> Mapping[str, Any]:
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / "schemas" / "config-v1.schema.json",
        Path(sys.prefix) / "share" / "urd-installer" / "config-v1.schema.json",
    )
    path = next((item for item in candidates if item.is_file()), candidates[0])
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load bundled configuration schema {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ConfigError("bundled configuration schema is not an object")
    return value


def validate_against_schema(
    instance: Any, schema: Optional[Mapping[str, Any]] = None
) -> None:
    selected = schema or load_bundled_schema()
    try:
        _validate(instance, selected, selected, "$")
    except _Mismatch as exc:
        raise ConfigError(str(exc)) from exc


def _fail(path: str, message: str) -> None:
    raise _Mismatch(f"{path}: {message}")


def _resolve_ref(reference: str, root: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        _fail(path, f"only local schema references are supported: {reference}")
    current: Any = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or token not in current:
            _fail(path, f"unresolvable schema reference: {reference}")
        current = current[token]
    if not isinstance(current, Mapping):
        _fail(path, f"schema reference does not point to an object: {reference}")
    return current


def _validate(instance: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str) -> None:
    unknown = set(schema) - _KNOWN_ANNOTATIONS - _KNOWN_ASSERTIONS
    if unknown:
        _fail(path, "validator does not implement schema keyword(s): " + ", ".join(sorted(unknown)))

    if "$ref" in schema:
        _validate(instance, _resolve_ref(str(schema["$ref"]), root, path), root, path)

    if "type" in schema and not _matches_type(instance, schema["type"]):
        _fail(path, f"expected type {schema['type']}, got {_json_type(instance)}")
    if "const" in schema and not _json_equal(instance, schema["const"]):
        _fail(path, f"must equal {schema['const']!r}")
    if "enum" in schema and not any(_json_equal(instance, item) for item in schema["enum"]):
        _fail(path, "must be one of " + ", ".join(repr(item) for item in schema["enum"]))

    if "anyOf" in schema:
        matched = False
        for candidate in schema["anyOf"]:
            try:
                _validate(instance, candidate, root, path)
                matched = True
                break
            except _Mismatch:
                pass
        if not matched:
            _fail(path, "does not match any allowed schema")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                _fail(path, f"missing required property {key!r}")
        properties = schema.get("properties", {})
        if properties:
            for key, value in instance.items():
                child = f"{path}.{key}"
                if key in properties:
                    _validate(value, properties[key], root, child)
                elif schema.get("additionalProperties") is False:
                    _fail(child, "unknown property")
                elif isinstance(schema.get("additionalProperties"), Mapping):
                    _validate(value, schema["additionalProperties"], root, child)
        elif schema.get("additionalProperties") is False and instance:
            _fail(path, "object may not contain properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            _fail(path, f"must contain at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            _fail(path, f"must contain at most {schema['maxItems']} item(s)")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                _fail(path, "array items must be unique")
        if isinstance(schema.get("items"), Mapping):
            for index, value in enumerate(instance):
                _validate(value, schema["items"], root, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            _fail(path, f"must be at least {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            _fail(path, f"must be at most {schema['maxLength']} characters")
        if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
            _fail(path, "does not match the required pattern")
        if "format" in schema and not _matches_format(instance, str(schema["format"])):
            _fail(path, f"is not a valid {schema['format']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            _fail(path, f"must be at least {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            _fail(path, f"must be at most {schema['maximum']}")

    for item in schema.get("allOf", []):
        if "if" in item:
            try:
                _validate(instance, item["if"], root, path)
                condition = True
            except _Mismatch:
                condition = False
            branch = item.get("then") if condition else item.get("else")
            if isinstance(branch, Mapping):
                _validate(instance, branch, root, path)
        else:
            _validate(instance, item, root, path)


def _json_equal(left: Any, right: Any) -> bool:
    return _json_type(left) == _json_type(right) and left == right


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: Any, expected: Any) -> bool:
    types: Sequence[str] = (expected,) if isinstance(expected, str) else expected
    actual = _json_type(value)
    return actual in types or (actual == "integer" and "number" in types)


def _matches_format(value: str, name: str) -> bool:
    if name in {"ipv4", "ipv6"}:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return address.version == (4 if name == "ipv4" else 6)
    if name == "hostname":
        if value == "localhost":
            return True
        candidate = value[:-1] if value.endswith(".") else value
        if not candidate or len(candidate) > 253:
            return False
        label = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
        return all(label.fullmatch(part) for part in candidate.split("."))
    if name == "email":
        if len(value) > 254 or value.count("@") != 1 or any(char.isspace() for char in value):
            return False
        local, domain = value.rsplit("@", 1)
        return bool(local) and _matches_format(domain, "hostname")
    _fail("$", f"validator does not implement format {name!r}")
    return False


__all__ = ["load_bundled_schema", "validate_against_schema"]
