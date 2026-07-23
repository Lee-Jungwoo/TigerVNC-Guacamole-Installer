"""Shared, dependency-free data models for the installer core.

Provider modules should depend on these small value objects rather than on the
CLI.  All fields intentionally contain JSON-compatible values (after paths and
enums are converted by ``to_dict``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


class InstallerError(RuntimeError):
    """Base class for expected, user-facing installer failures."""


class ConfigError(InstallerError):
    """A configuration file is missing, malformed, or unsupported."""


class FactsError(InstallerError):
    """Host fact discovery failed."""


class ExecutionError(InstallerError):
    """A command or filesystem operation failed."""


class IntegrationError(InstallerError):
    """The planner/provider layer is absent or violates its interface."""


class SupportLevel(str, Enum):
    VERIFIED = "verified"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class InstallerConfig:
    """Validated version-1 JSON configuration.

    ``data`` retains provider-specific keys so providers can evolve without a
    core release.  The core validates the envelope and JSON value types.
    """

    schema_version: int
    data: Mapping[str, Any]
    source: Optional[Path] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Return a top-level or dotted configuration value."""

        if key in self.data:
            return self.data[key]
        current: Any = self.data
        for component in key.split("."):
            if not isinstance(current, Mapping) or component not in current:
                return default
            current = current[component]
        return current

    def section(self, key: str) -> Mapping[str, Any]:
        value = self.get(key, {})
        return value if isinstance(value, Mapping) else {}

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class SystemFacts:
    os_id: str
    os_name: str
    version_id: str
    os_like: Tuple[str, ...]
    architecture: str
    kernel: str
    hostname: str
    init_system: str
    package_managers: Tuple[str, ...]
    primary_package_manager: Optional[str]
    security_modules: Tuple[str, ...]
    active_firewall: Optional[str]
    display_server: Optional[str]
    available_xsessions: Tuple[str, ...]
    service_units: Tuple[str, ...]
    is_container: bool
    euid: int
    is_root: bool
    sudo_available: bool
    current_user: str
    python_version: str
    os_release_path: Optional[str]
    os_release: Mapping[str, str] = field(repr=False)

    @property
    def arch(self) -> str:
        return self.architecture

    @property
    def package_manager(self) -> Optional[str]:
        return self.primary_package_manager

    @property
    def os_version_id(self) -> str:
        return self.version_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "os_id": self.os_id,
            "os_name": self.os_name,
            "version_id": self.version_id,
            "os_like": list(self.os_like),
            "architecture": self.architecture,
            "kernel": self.kernel,
            "hostname": self.hostname,
            "init_system": self.init_system,
            "package_managers": list(self.package_managers),
            "primary_package_manager": self.primary_package_manager,
            "security_modules": list(self.security_modules),
            "active_firewall": self.active_firewall,
            "display_server": self.display_server,
            "available_xsessions": list(self.available_xsessions),
            "service_units": list(self.service_units),
            "is_container": self.is_container,
            "euid": self.euid,
            "is_root": self.is_root,
            "sudo_available": self.sudo_available,
            "current_user": self.current_user,
            "python_version": self.python_version,
            "os_release_path": self.os_release_path,
            "os_release": dict(self.os_release),
        }


@dataclass(frozen=True)
class PlanStep:
    id: str
    description: str
    provider: str = "core"
    action: str = "configure"
    destructive: bool = False
    requires_root: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "provider": self.provider,
            "action": self.action,
            "destructive": self.destructive,
            "requires_root": self.requires_root,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class Plan:
    steps: Tuple[PlanStep, ...] = ()
    support_level: SupportLevel = SupportLevel.UNSUPPORTED
    summary: str = ""
    warnings: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def destructive(self) -> bool:
        return any(step.destructive for step in self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "support_level": self.support_level.value,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "warnings": list(self.warnings),
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CommandResult:
    argv: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    changed: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class FileInstallResult:
    path: Path
    changed: bool
    backup_path: Optional[Path] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "changed": self.changed,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DirectoryEnsureResult:
    path: Path
    changed: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "changed": self.changed, "reason": self.reason}


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    changed: bool = False
    summary: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[str] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "summary": self.summary,
            "details": dict(self.details),
            "warnings": list(self.warnings),
        }
