"""Command-line interface and the stable planner integration boundary.

The planner module is imported lazily.  It is expected to expose:

``list_supported()``, ``build_plan(facts, config)``,
``apply_plan(plan, executor)``, ``verify(facts, config, executor)``,
``doctor(facts, config, executor)``, and
``uninstall(facts, config, executor)``.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .config import config_permission_warnings, load_config, secret_values
from .executor import Executor, SecretRedactor, lock_path_from_env, state_path_from_env
from .facts import detect_facts
from .model import ConfigError, InstallerError, IntegrationError, SupportLevel
from . import __version__


PLANNER_FUNCTIONS = (
    "list_supported",
    "build_plan",
    "apply_plan",
    "verify",
    "doctor",
    "uninstall",
)


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    parser.add_argument("--config", metavar="PATH", help="version-1 JSON configuration")
    parser.add_argument("--dry-run", action="store_true", help="report changes without performing them")
    parser.add_argument("--yes", action="store_true", help="approve the displayed operation")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; fail if explicit approval is required",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="emit machine-readable JSON")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="urd-installer",
        description="Universal Remote Desktop installer core",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "detect": "show read-only host facts",
        "list-supported": "show provider capabilities",
        "plan": "resolve configuration into a change plan",
        "apply": "apply the resolved desired state",
        "verify": "verify installed services and configuration",
        "doctor": "run read-only core and provider diagnostics",
        "uninstall": "stop managed services while preserving packages, configuration, and data",
    }
    for name, description in descriptions.items():
        subparsers.add_parser(name, help=description, description=description, parents=[common])
    return parser


def _load_planner(required: bool = True) -> Optional[Any]:
    module_name = "{}.planner".format(__package__)
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise IntegrationError("planner import failed because dependency is missing: {}".format(exc.name)) from exc
        if required:
            raise IntegrationError(
                "planner integration is unavailable; expected {} with functions: {}".format(
                    module_name, ", ".join(PLANNER_FUNCTIONS)
                )
            ) from exc
        return None
    except Exception as exc:
        raise IntegrationError("planner import failed: {}".format(exc)) from exc


def _planner_function(planner: Any, name: str) -> Callable[..., Any]:
    function = getattr(planner, name, None)
    if not callable(function):
        raise IntegrationError("planner must define callable {}()".format(name))
    return function


def _to_primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _to_primitive(value.to_dict())
    if dataclasses.is_dataclass(value):
        return {field.name: _to_primitive(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_primitive(item) for item in value]
    return str(value)


def _emit(payload: Any, *, json_output: bool, redactor: SecretRedactor, error: bool = False) -> None:
    safe = redactor.redact_object(_to_primitive(payload))
    stream = sys.stderr if error and not json_output else sys.stdout
    if json_output:
        print(json.dumps(safe, sort_keys=True, ensure_ascii=False), file=stream)
    elif isinstance(safe, str):
        print(safe, file=stream)
    else:
        print(json.dumps(safe, indent=2, sort_keys=True, ensure_ascii=False), file=stream)


def _support_level(plan: Any) -> Optional[str]:
    if isinstance(plan, Mapping):
        value = plan.get("support_level", plan.get("tier"))
    else:
        value = getattr(plan, "support_level", getattr(plan, "tier", None))
    if isinstance(value, Enum):
        return str(value.value)
    return str(value) if value is not None else None


def _is_ok(result: Any) -> bool:
    if isinstance(result, Mapping) and "ok" in result:
        return bool(result["ok"])
    if hasattr(result, "ok"):
        return bool(result.ok)
    return True


def _confirm(args: argparse.Namespace, prompt: str) -> None:
    if getattr(args, "yes", False):
        return
    if getattr(args, "non_interactive", False) or not sys.stdin.isatty():
        raise InstallerError("explicit approval is required; rerun with --yes")
    answer = input("{} [y/N] ".format(prompt)).strip().lower()
    if answer not in ("y", "yes"):
        raise InstallerError("operation cancelled")


def _fallback_supported() -> Mapping[str, Any]:
    try:
        providers = importlib.import_module("{}.providers".format(__package__))
    except (ImportError, AttributeError) as exc:
        return {
            "planner_available": False,
            "providers_available": False,
            "message": "provider registry is not installed: {}".format(exc),
        }
    snapshot = getattr(providers, "registry_snapshot", None)
    if not callable(snapshot):
        return {
            "planner_available": False,
            "providers_available": True,
            "message": "provider registry has no registry_snapshot()",
        }
    result = dict(snapshot())
    result["planner_available"] = False
    result["providers_available"] = True
    return result


def _core_doctor(facts: Any, config: Any, planner: Optional[Any]) -> Dict[str, Any]:
    checks = []
    checks.append(
        {
            "id": "python-version",
            "ok": sys.version_info >= (3, 9),
            "detail": "{}.{}.{}".format(*sys.version_info[:3]),
        }
    )
    checks.append(
        {
            "id": "platform-detected",
            "ok": facts.os_id != "unknown",
            "detail": "{} {}".format(facts.os_id, facts.version_id).strip(),
        }
    )
    checks.append(
        {
            "id": "privilege-path",
            "ok": facts.is_root or facts.sudo_available,
            "detail": "root" if facts.is_root else ("sudo" if facts.sudo_available else "unavailable"),
        }
    )
    checks.append(
        {
            "id": "planner-integration",
            "ok": planner is not None,
            "detail": "available" if planner is not None else "not installed",
        }
    )
    return {
        "ok": all(check["ok"] for check in checks if check["id"] != "planner-integration"),
        "checks": checks,
        "warnings": list(config_permission_warnings(config)),
        "state_path": str(state_path_from_env()),
        "lock_path": str(lock_path_from_env(state_path_from_env())),
    }


def _run_command(args: argparse.Namespace) -> Tuple[Any, int, SecretRedactor]:
    config = load_config(getattr(args, "config", None))
    redactor = SecretRedactor(secret_values(config))
    command = args.command
    executor = Executor(
        dry_run=getattr(args, "dry_run", False),
        non_interactive=getattr(args, "non_interactive", False),
        secrets=secret_values(config),
    )
    redactor = executor.redactor

    if command == "list-supported":
        planner = _load_planner(required=False)
        supported = (
            _planner_function(planner, "list_supported")() if planner is not None else _fallback_supported()
        )
        return {"ok": True, "command": command, "supported": supported}, 0, redactor

    facts = detect_facts()

    if command == "detect":
        return {"ok": True, "command": command, "facts": facts}, 0, redactor

    if command == "doctor":
        planner = _load_planner(required=False)
        core = _core_doctor(facts, config, planner)
        provider = None
        if planner is not None:
            provider = _planner_function(planner, "doctor")(facts, config, executor)
        ok = bool(core["ok"]) and (provider is None or _is_ok(provider))
        return {
            "ok": ok,
            "command": command,
            "core": core,
            "provider": provider,
        }, (0 if ok else 1), redactor

    planner = _load_planner(required=True)
    assert planner is not None

    if command == "plan":
        plan = _planner_function(planner, "build_plan")(facts, config)
        return {"ok": True, "command": command, "plan": plan}, 0, redactor

    if command == "apply":
        plan = _planner_function(planner, "build_plan")(facts, config)
        level = _support_level(plan)
        if level == SupportLevel.UNSUPPORTED.value:
            raise InstallerError("the resolved provider combination is unsupported; inspect `plan` reasons")
        if not getattr(args, "dry_run", False):
            qualifier = "experimental " if level == SupportLevel.EXPERIMENTAL.value else ""
            _confirm(args, "Apply the {}installation plan?".format(qualifier))
        with executor.locked():
            result = _planner_function(planner, "apply_plan")(plan, executor)
        ok = _is_ok(result)
        return {
            "ok": ok,
            "command": command,
            "dry_run": executor.dry_run,
            "plan": plan,
            "result": result,
        }, (0 if ok else 1), redactor

    if command == "verify":
        result = _planner_function(planner, "verify")(facts, config, executor)
        ok = _is_ok(result)
        return {"ok": ok, "command": command, "result": result}, (0 if ok else 1), redactor

    if command == "uninstall":
        if not getattr(args, "dry_run", False):
            _confirm(args, "Remove installer-managed resources?")
        with executor.locked():
            result = _planner_function(planner, "uninstall")(facts, config, executor)
        ok = _is_ok(result)
        return {
            "ok": ok,
            "command": command,
            "dry_run": executor.dry_run,
            "result": result,
        }, (0 if ok else 1), redactor

    raise IntegrationError("unhandled command: {}".format(command))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    json_output = getattr(args, "json_output", False)
    redactor = SecretRedactor()
    try:
        payload, exit_code, redactor = _run_command(args)
        _emit(payload, json_output=json_output, redactor=redactor)
        return exit_code
    except (ConfigError, InstallerError) as exc:
        # `_run_command` may fail after loading the configuration but before it
        # can return its redactor.  Reconstruct only the redaction set so a
        # provider exception can never echo an inline credential.
        try:
            failed_config = load_config(getattr(args, "config", None))
            redactor = SecretRedactor(secret_values(failed_config))
        except ConfigError:
            pass
        payload = {"ok": False, "command": getattr(args, "command", None), "error": redactor.redact(exc)}
        _emit(payload, json_output=json_output, redactor=redactor, error=True)
        return 2
    except KeyboardInterrupt:
        _emit(
            {"ok": False, "command": getattr(args, "command", None), "error": "interrupted"},
            json_output=json_output,
            redactor=redactor,
            error=True,
        )
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
