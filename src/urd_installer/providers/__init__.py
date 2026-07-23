"""Public provider registry API.

All registries are metadata-only and safe to import during CLI discovery.
Commands are returned as argv tuples; this package never invokes a shell or
changes the host by itself.
"""

from __future__ import annotations

from typing import Any, Dict

from . import deployment
from .deployment import DEPLOYMENTS
from .desktops import (
    DESKTOPS,
    DesktopSpec,
    desktop_package_candidates,
    detect_desktop_from_xsessions,
    list_desktops,
    match_installed_xsession,
    resolve_desktop,
    xstartup_argv,
)
from .platforms import (
    EXPERIMENTAL,
    INIT_CAPABILITIES,
    PACKAGE_MANAGERS,
    PLATFORMS,
    UNSUPPORTED,
    VERIFIED,
    DetectedPlatform,
    InitCapability,
    PackageManagerSpec,
    PlatformSpec,
    detect_init_system,
    detect_package_managers,
    detect_platform,
    get_package_manager,
    list_platforms,
    parse_os_release,
    platform_from_facts,
    resolve_platform,
)
from .vnc import (
    VNCS,
    ValidationResult,
    VncSpec,
    list_vncs,
    resolve_vnc,
    validate_combination,
    vnc_package_candidates,
)


def registry_snapshot() -> Dict[str, Any]:
    """Return a serialization-friendly overview for ``list-supported`` UIs."""

    return {
        "verified_combinations": [],
        "platforms": {
            key: {
                "tier": spec.tier,
                "reason": spec.reason,
                "package_manager": spec.package_manager,
                "init_systems": list(spec.init_systems),
            }
            for key, spec in PLATFORMS.items()
        },
        "desktops": {
            key: {
                "tier": spec.tier,
                "reason": spec.reason,
                "display_server": spec.display_server,
                "session_argv": list(spec.session_argv),
            }
            for key, spec in DESKTOPS.items()
        },
        "vnc": {
            key: {
                "tier": spec.tier,
                "reason": spec.reason,
                "mode": spec.mode,
                "display_servers": list(spec.display_servers),
            }
            for key, spec in VNCS.items()
        },
        "deployments": {
            key: {
                "tier": spec.tier,
                "description": spec.description,
                "requires": list(spec.requires),
            }
            for key, spec in DEPLOYMENTS.items()
        },
    }


def list_supported() -> Dict[str, Any]:
    """Compatibility alias for CLI/planner discovery.

    The name does not imply that every listed provider is verified; each entry
    includes its explicit tier and reason, and ``verified_combinations`` is
    currently empty.
    """

    return registry_snapshot()


__all__ = [
    "DESKTOPS",
    "DEPLOYMENTS",
    "DetectedPlatform",
    "DesktopSpec",
    "EXPERIMENTAL",
    "INIT_CAPABILITIES",
    "InitCapability",
    "PACKAGE_MANAGERS",
    "PLATFORMS",
    "PackageManagerSpec",
    "PlatformSpec",
    "UNSUPPORTED",
    "VERIFIED",
    "VNCS",
    "ValidationResult",
    "VncSpec",
    "desktop_package_candidates",
    "deployment",
    "detect_desktop_from_xsessions",
    "detect_init_system",
    "detect_package_managers",
    "detect_platform",
    "get_package_manager",
    "list_desktops",
    "list_platforms",
    "list_supported",
    "list_vncs",
    "match_installed_xsession",
    "parse_os_release",
    "platform_from_facts",
    "registry_snapshot",
    "resolve_desktop",
    "resolve_platform",
    "resolve_vnc",
    "validate_combination",
    "vnc_package_candidates",
    "xstartup_argv",
]
