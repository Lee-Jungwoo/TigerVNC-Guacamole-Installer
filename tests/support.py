"""Dependency-free fixtures and JSON Schema checks used by the test suite."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from urd_installer.model import InstallerConfig, SystemFacts


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = ROOT / "schemas" / "config-v1.schema.json"
EXAMPLE_CONFIG_PATH = ROOT / "examples" / "config.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_example_config() -> Dict[str, Any]:
    return copy.deepcopy(load_json(EXAMPLE_CONFIG_PATH))


def installer_config(data: Optional[Mapping[str, Any]] = None) -> InstallerConfig:
    selected = load_example_config() if data is None else copy.deepcopy(dict(data))
    return InstallerConfig(schema_version=1, data=selected)


def make_facts(**overrides: Any) -> SystemFacts:
    values: Dict[str, Any] = {
        "os_id": "ubuntu",
        "os_name": "Ubuntu 24.04.2 LTS",
        "version_id": "24.04",
        "os_like": ("debian",),
        "architecture": "amd64",
        "kernel": "Linux test",
        "hostname": "fixture-host",
        "init_system": "systemd",
        "package_managers": ("apt",),
        "primary_package_manager": "apt",
        "security_modules": (),
        "active_firewall": None,
        "display_server": "x11",
        "available_xsessions": ("xfce",),
        "service_units": (),
        "is_container": False,
        "euid": 1000,
        "is_root": False,
        "sudo_available": False,
        "current_user": "fixture-user",
        "python_version": "3.9.0",
        "os_release_path": str(FIXTURES / "os-release" / "ubuntu-24.04"),
        "os_release": {
            "ID": "ubuntu",
            "ID_LIKE": "debian",
            "VERSION_ID": "24.04",
            "PRETTY_NAME": "Ubuntu 24.04.2 LTS",
        },
    }
    values.update(overrides)
    return SystemFacts(**values)


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _resolve_pointer(root: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        raise AssertionError("test validator supports only local JSON pointers: {}".format(reference))
    value: Any = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    if not isinstance(value, Mapping):
        raise AssertionError("JSON pointer does not reference a schema object: {}".format(reference))
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise AssertionError("unsupported JSON Schema type in test validator: {}".format(expected))


def _valid_hostname(value: str) -> bool:
    if not value or len(value) > 253 or value.endswith("."):
        return False
    label = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
    return all(label.fullmatch(item) is not None for item in value.split("."))


def _valid_format(value: str, name: str) -> bool:
    if name in ("ipv4", "ipv6"):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return address.version == (4 if name == "ipv4" else 6)
    if name == "hostname":
        return _valid_hostname(value)
    if name == "email":
        if value.count("@") != 1 or any(character.isspace() for character in value):
            return False
        local, domain = value.rsplit("@", 1)
        return bool(local) and len(local) <= 64 and _valid_hostname(domain)
    return True


def schema_errors(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    root: Optional[Mapping[str, Any]] = None,
    path: str = "$",
) -> List[str]:
    """Validate the schema subset used by config-v1 with the Python stdlib only."""

    root = schema if root is None else root
    errors: List[str] = []

    reference = schema.get("$ref")
    if isinstance(reference, str):
        errors.extend(schema_errors(instance, _resolve_pointer(root, reference), root=root, path=path))
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        if siblings:
            errors.extend(schema_errors(instance, siblings, root=root, path=path))
        return errors

    expected_type = schema.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_matches_type(instance, item) for item in types):
            return ["{}: expected type {}, got {}".format(path, types, type(instance).__name__)]

    if "const" in schema and not _json_equal(instance, schema["const"]):
        errors.append("{}: value does not match const".format(path))
    if "enum" in schema and not any(_json_equal(instance, item) for item in schema["enum"]):
        errors.append("{}: value is not in enum".format(path))

    for sub_schema in schema.get("allOf", []):
        errors.extend(schema_errors(instance, sub_schema, root=root, path=path))
    if "anyOf" in schema:
        branches = [schema_errors(instance, item, root=root, path=path) for item in schema["anyOf"]]
        if not any(not branch for branch in branches):
            errors.append("{}: no anyOf branch matched".format(path))
    if "oneOf" in schema:
        branches = [schema_errors(instance, item, root=root, path=path) for item in schema["oneOf"]]
        if sum(not branch for branch in branches) != 1:
            errors.append("{}: exactly one oneOf branch must match".format(path))

    condition = schema.get("if")
    if isinstance(condition, Mapping):
        condition_matches = not schema_errors(instance, condition, root=root, path=path)
        branch = schema.get("then") if condition_matches else schema.get("else")
        if isinstance(branch, Mapping):
            errors.extend(schema_errors(instance, branch, root=root, path=path))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append("{}: missing required property {!r}".format(path, key))
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = "{}.{}".format(path, key)
            if key in properties:
                errors.extend(schema_errors(value, properties[key], root=root, path=child_path))
            elif schema.get("additionalProperties") is False:
                errors.append("{}: additional property is forbidden".format(child_path))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append("{}: too few array items".format(path))
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append("{}: too many array items".format(path))
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(rendered) != len(set(rendered)):
                errors.append("{}: array items must be unique".format(path))
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(instance):
                errors.extend(
                    schema_errors(item, item_schema, root=root, path="{}[{}]".format(path, index))
                )

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append("{}: string is too short".format(path))
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append("{}: string is too long".format(path))
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            errors.append("{}: string does not match pattern".format(path))
        format_name = schema.get("format")
        if isinstance(format_name, str) and not _valid_format(instance, format_name):
            errors.append("{}: invalid {} format".format(path, format_name))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("{}: number is below minimum".format(path))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("{}: number is above maximum".format(path))

    return errors

