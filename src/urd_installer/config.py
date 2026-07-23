"""Versioned JSON configuration loading and secret discovery."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import ConfigError, InstallerConfig
from .schema import validate_against_schema


CURRENT_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 1024 * 1024
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|passphrase|token|secret|api[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
_REFERENCE_SUFFIX = re.compile(r"(?:_file|_path|_env|_command)$", re.IGNORECASE)


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("duplicate JSON key: {!r}".format(key))
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ConfigError("non-standard JSON constant: {}".format(value))


def _validate_json_tree(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > 64:
        raise ConfigError("configuration nesting exceeds 64 levels at {}".format(path))
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError("non-finite number is not valid JSON at {}".format(path))
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_tree(item, "{}[{}]".format(path, index), depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigError("configuration key at {} is not a string".format(path))
            _validate_json_tree(item, "{}.{}".format(path, key), depth + 1)
        return
    raise ConfigError("unsupported value at {}: {}".format(path, type(value).__name__))


def validate_config(
    data: Any, source: Optional[Path] = None, *, strict: Optional[bool] = None
) -> InstallerConfig:
    if not isinstance(data, dict):
        raise ConfigError("configuration root must be a JSON object")
    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ConfigError("schema_version must be the integer 1")
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise ConfigError(
            "unsupported schema_version {}; this build supports only {}".format(
                schema_version, CURRENT_SCHEMA_VERSION
            )
        )
    _validate_json_tree(data)
    if strict is None:
        strict = source is not None or set(data) != {"schema_version"}
    if strict:
        validate_against_schema(data)

    target_user = data.get("target_user")
    if target_user is not None and (not isinstance(target_user, str) or not target_user.strip()):
        raise ConfigError("target_user must be a non-empty string")
    for section in ("desktop", "vnc", "guacamole", "database", "proxy", "tls", "firewall"):
        if section in data and not isinstance(data[section], dict):
            raise ConfigError("{} must be a JSON object".format(section))

    return InstallerConfig(
        schema_version=schema_version,
        data=copy.deepcopy(data),
        source=source,
    )


def load_config(path: Optional[str] = None) -> InstallerConfig:
    """Load JSON config, or return the minimal versioned config when omitted."""

    if path is None:
        return validate_config({"schema_version": CURRENT_SCHEMA_VERSION})
    config_path = Path(path).expanduser()
    try:
        stat = config_path.stat()
        if stat.st_size > MAX_CONFIG_BYTES:
            raise ConfigError(
                "configuration is larger than {} bytes: {}".format(
                    MAX_CONFIG_BYTES, config_path
                )
            )
        text = config_path.read_text(encoding="utf-8")
    except ConfigError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ConfigError("cannot read configuration {}: {}".format(config_path, exc)) from exc
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ConfigError:
        raise
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "invalid JSON in {} at line {}, column {}: {}".format(
                config_path, exc.lineno, exc.colno, exc.msg
            )
        ) from exc
    return validate_config(data, source=config_path.resolve(), strict=True)


def secret_values(config: InstallerConfig) -> Tuple[str, ...]:
    """Return inline secret values that must be redacted from diagnostics.

    References such as ``password_file`` are intentionally not opened and are
    not treated as secret values.  Providers may register additional runtime
    values with :class:`urd_installer.executor.SecretRedactor`.
    """

    found: List[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif (
            isinstance(value, (str, int, float))
            and _SENSITIVE_KEY.search(key)
            and not _REFERENCE_SUFFIX.search(key)
        ):
            rendered = str(value)
            if rendered:
                found.append(rendered)

    walk(config.data)
    return tuple(dict.fromkeys(found))


def config_permission_warnings(config: InstallerConfig) -> Tuple[str, ...]:
    if config.source is None or os.name == "nt":
        return ()
    try:
        mode = config.source.stat().st_mode & 0o777
    except OSError:
        return ()
    if secret_values(config) and mode & 0o077:
        return (
            "configuration {} contains inline secrets but mode is {:04o}; use 0600 or secret files".format(
                config.source, mode
            ),
        )
    return ()
