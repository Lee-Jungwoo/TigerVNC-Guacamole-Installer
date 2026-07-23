"""VNC provider metadata and cross-provider compatibility validation.

TigerVNC/TightVNC create isolated virtual X11 displays.  x11vnc and wayvnc
share an already running graphical session.  Treating these modes as one
service lifecycle is a common source of broken and insecure installations,
so the distinction is represented explicitly here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .desktops import (
    DesktopSpec,
    detect_desktop_from_xsessions,
    match_installed_xsession,
    resolve_desktop,
)
from .platforms import (
    EXPERIMENTAL,
    UNSUPPORTED,
    VERIFIED,
    PlatformSpec,
    platform_from_facts,
)


PackageCandidates = Mapping[str, Tuple[Tuple[str, ...], ...]]


@dataclass(frozen=True)
class VncSpec:
    key: str
    display_name: str
    mode: str
    package_candidates_by_family: PackageCandidates
    server_binaries: Tuple[str, ...]
    password_binaries: Tuple[str, ...]
    display_servers: Tuple[str, ...]
    tier: str
    reason: str
    creates_display: bool
    requires_active_session: bool
    default_display: Optional[int]
    default_port: Optional[int]
    native_unit_candidates: Tuple[str, ...] = ()
    unsupported_families: Mapping[str, str] = field(default_factory=dict)
    family_notes: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.key

    @property
    def implementation(self) -> str:
        return self.key

    def package_candidates(self, family: str) -> Tuple[Tuple[str, ...], ...]:
        return self.package_candidates_by_family.get(family, ())

    def port_for_display(self, display: Optional[int] = None) -> Optional[int]:
        if self.mode == "virtual":
            resolved_display = self.default_display if display is None else display
            if resolved_display is None or resolved_display < 0:
                raise ValueError("a non-negative display number is required")
            return 5900 + resolved_display
        return self.default_port


VNCS: Dict[str, VncSpec] = {
    "tigervnc": VncSpec(
        key="tigervnc",
        display_name="TigerVNC virtual desktop",
        mode="virtual",
        package_candidates_by_family={
            "debian": (
                ("tigervnc-standalone-server", "tigervnc-tools"),
                ("tigervnc-standalone-server",),
            ),
            "rhel": (("tigervnc-server",),),
            "fedora": (("tigervnc-server",),),
            "arch": (("tigervnc",),),
            "suse": (
                ("tigervnc", "xorg-x11-Xvnc"),
                ("tigervnc",),
                ("xorg-x11-Xvnc",),
            ),
            "alpine": (("tigervnc",),),
        },
        server_binaries=("tigervncserver", "vncserver", "Xvnc"),
        password_binaries=("tigervncpasswd", "vncpasswd"),
        display_servers=("x11",),
        tier=EXPERIMENTAL,
        reason="The preferred virtual desktop backend has no verified OS/desktop tuple yet.",
        creates_display=True,
        requires_active_session=False,
        default_display=1,
        default_port=5901,
        native_unit_candidates=("vncserver@.service", "tigervncserver@.service"),
        family_notes={
            "suse": (
                "Probe whether the release uses tigervnc, xorg-x11-Xvnc, a systemd template, or an xinetd layout.",
            ),
            "alpine": ("An OpenRC service renderer is required; systemd units are not usable.",),
        },
        notes=(
            "Resolve tigervncserver/vncserver and tigervncpasswd/vncpasswd after installation.",
            "Use localhost-only mode and verify the listener rather than assuming option syntax.",
        ),
    ),
    "tightvnc": VncSpec(
        key="tightvnc",
        display_name="TightVNC virtual desktop",
        mode="virtual",
        package_candidates_by_family={
            "debian": (("tightvncserver",),),
            "suse": (("tightvnc",),),
        },
        server_binaries=("tightvncserver", "vncserver", "Xvnc"),
        password_binaries=("tightvncpasswd", "vncpasswd"),
        display_servers=("x11",),
        tier=EXPERIMENTAL,
        reason="This legacy backend has limited repository availability and modern desktop compatibility.",
        creates_display=True,
        requires_active_session=False,
        default_display=1,
        default_port=5901,
        unsupported_families={
            "rhel": "TightVNC server is not assumed available from supported RHEL repositories.",
            "fedora": "TightVNC server is not assumed available from Fedora repositories.",
            "arch": "The commonly used TightVNC package is from AUR, which is not a supported dependency source.",
            "alpine": "No maintained native TightVNC provider has been validated for Alpine.",
        },
        family_notes={
            "suse": ("The tightvnc package name and server contents must be probed for the exact release.",),
        },
        notes=(
            "Classic VNC authentication has an effective eight-character password limit.",
            "No consistent native systemd service is assumed; bind it to localhost only.",
        ),
    ),
    "x11vnc": VncSpec(
        key="x11vnc",
        display_name="x11vnc shared Xorg session",
        mode="shared",
        package_candidates_by_family={
            "debian": (("x11vnc",),),
            "rhel": (("x11vnc",),),
            "fedora": (("x11vnc",),),
            "arch": (("x11vnc",),),
            "suse": (("x11vnc",),),
            "alpine": (("x11vnc",),),
        },
        server_binaries=("x11vnc",),
        password_binaries=("x11vnc",),
        display_servers=("x11",),
        tier=EXPERIMENTAL,
        reason="It shares a real Xorg login and needs display-manager-specific authentication tests.",
        creates_display=False,
        requires_active_session=True,
        default_display=0,
        default_port=5900,
        notes=(
            "It does not create a desktop; start it after display-manager.service/graphical.target.",
            "-auth guess is a last-resort experimental fallback, not a portable authentication contract.",
            "It cannot capture a complete GNOME/KDE Wayland desktop through XWayland.",
        ),
    ),
    "wayvnc": VncSpec(
        key="wayvnc",
        display_name="wayvnc shared Wayland session",
        mode="shared",
        package_candidates_by_family={
            "debian": (("wayvnc",),),
            "rhel": (("wayvnc",),),
            "fedora": (("wayvnc",),),
            "arch": (("wayvnc",),),
            "suse": (("wayvnc",),),
            "alpine": (("wayvnc",),),
        },
        server_binaries=("wayvnc",),
        password_binaries=(),
        display_servers=("wayland",),
        tier=EXPERIMENTAL,
        reason="wayvnc requires an active wlroots-compatible compositor and is not a generic Wayland server.",
        creates_display=False,
        requires_active_session=True,
        default_display=None,
        default_port=5900,
        notes=(
            "GNOME Mutter and KDE KWin are not treated as compatible wlroots compositors.",
            "The built-in desktop launch commands in desktops.py are X11 commands.",
        ),
    ),
    "external": VncSpec(
        key="external",
        display_name="Existing external VNC endpoint",
        mode="external",
        package_candidates_by_family={},
        server_binaries=(),
        password_binaries=(),
        display_servers=("x11", "wayland", "external"),
        tier=EXPERIMENTAL,
        reason="The installer does not own or verify the remote server lifecycle.",
        creates_display=False,
        requires_active_session=False,
        default_display=None,
        default_port=None,
        notes=("An explicit host and TCP port are required.",),
    ),
    "realvnc": VncSpec(
        key="realvnc",
        display_name="RealVNC (reserved commercial provider)",
        mode="custom",
        package_candidates_by_family={},
        server_binaries=(),
        password_binaries=(),
        display_servers=("x11", "wayland"),
        tier=UNSUPPORTED,
        reason="RealVNC requires operator-provided licensed packages, repository policy, and vendor-specific service configuration.",
        creates_display=False,
        requires_active_session=False,
        default_display=None,
        default_port=5900,
    ),
    "gnome-remote-desktop": VncSpec(
        key="gnome-remote-desktop",
        display_name="GNOME Remote Desktop (reserved)",
        mode="custom",
        package_candidates_by_family={},
        server_binaries=(),
        password_binaries=(),
        display_servers=("wayland", "x11"),
        tier=UNSUPPORTED,
        reason="Modern GNOME Remote Desktop is primarily an RDP/session service and cannot be treated as a generic VNC backend.",
        creates_display=False,
        requires_active_session=True,
        default_display=None,
        default_port=None,
    ),
    "krfb": VncSpec(
        key="krfb",
        display_name="KDE Desktop Sharing / krfb (reserved)",
        mode="custom",
        package_candidates_by_family={},
        server_binaries=(),
        password_binaries=(),
        display_servers=("x11", "wayland"),
        tier=UNSUPPORTED,
        reason="krfb is tied to an active Plasma session and requires a release-specific user-session provider.",
        creates_display=False,
        requires_active_session=True,
        default_display=None,
        default_port=5900,
    ),
    "custom": VncSpec(
        key="custom",
        display_name="Custom VNC provider",
        mode="custom",
        package_candidates_by_family={},
        server_binaries=(),
        password_binaries=(),
        display_servers=("custom",),
        tier=UNSUPPORTED,
        reason="Commands, lifecycle, authentication, and display compatibility are operator-defined.",
        creates_display=False,
        requires_active_session=False,
        default_display=None,
        default_port=None,
    ),
}


VNC_ALIASES: Dict[str, str] = {
    "tiger": "tigervnc",
    "tight": "tightvnc",
    "x11": "x11vnc",
    "wayland": "wayvnc",
    "remote": "external",
}


@dataclass
class ValidationResult:
    """Result of validating one exact platform/desktop/VNC selection."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tier: str = VERIFIED
    reasons: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        _append_unique(self.errors, message)
        self.tier = UNSUPPORTED

    def add_warning(self, message: str) -> None:
        _append_unique(self.warnings, message)

    def include_tier(self, tier: str, reason: str = "") -> None:
        if _TIER_ORDER.get(tier, 2) > _TIER_ORDER.get(self.tier, 2):
            self.tier = tier
        if reason:
            _append_unique(self.reasons, reason)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "tier": self.tier,
            "reasons": list(self.reasons),
        }


_TIER_ORDER = {VERIFIED: 0, EXPERIMENTAL: 1, UNSUPPORTED: 2}


def _append_unique(target: List[str], message: str) -> None:
    if message and message not in target:
        target.append(message)


def resolve_vnc(name: str) -> VncSpec:
    normalized = str(name or "tigervnc").strip().lower()
    normalized = VNC_ALIASES.get(normalized, normalized)
    try:
        return VNCS[normalized]
    except KeyError:
        raise KeyError("unknown VNC provider: %s" % name)


def vnc_package_candidates(vnc: str, family: str) -> Tuple[Tuple[str, ...], ...]:
    return resolve_vnc(vnc).package_candidates(family)


def validate_combination(facts: Any, config: Any) -> ValidationResult:
    """Validate an exact provider combination without changing the system.

    ``facts`` may be the project's ``SystemFacts``, a mapping, or another
    attribute object.  ``config`` may be ``InstallerConfig`` or a mapping.
    The return value always separates hard errors from actionable warnings and
    carries the lowest support tier of all selected providers.
    """

    result = ValidationResult()
    platform = platform_from_facts(facts)
    result.include_tier(platform.tier, platform.reason)

    desktop_enabled = _as_bool(
        _config_value(config, "desktop_enabled", "enabled", section="desktop", default=True)
    )
    vnc_enabled = _as_bool(
        _config_value(config, "vnc_enabled", "enabled", section="vnc", default=True)
    )
    desktop_name = (
        _selection(config, "desktop", ("desktop_type", "desktop_provider"), "auto")
        if desktop_enabled
        else "none"
    )
    vnc_name = (
        _selection(config, "vnc", ("vnc_type", "vnc_provider", "server"), "tigervnc")
        if vnc_enabled
        else "external"
    )

    try:
        desktop = resolve_desktop(desktop_name)
    except KeyError as exc:
        result.add_error(str(exc))
        desktop = resolve_desktop("custom")
    try:
        vnc = resolve_vnc(vnc_name)
    except KeyError as exc:
        result.add_error(str(exc))
        vnc = resolve_vnc("custom")

    result.include_tier(desktop.tier, desktop.reason)
    if vnc_enabled:
        result.include_tier(vnc.tier, vnc.reason)

    _validate_platform(platform, facts, config, result)
    _validate_packages(platform, desktop, vnc, result)
    resolved_desktop = _validate_desktop_selection(desktop, facts, config, vnc, result)
    if vnc_enabled:
        _validate_vnc_mode(platform, resolved_desktop, vnc, facts, config, result)
    else:
        result.add_warning("VNC management is disabled; no local VNC endpoint will be installed or verified.")
    _add_provider_notes(platform, resolved_desktop, vnc, result, include_vnc=vnc_enabled)

    if _as_bool(_config_value(config, "require_verified", default=False)) and result.tier != VERIFIED:
        result.add_error(
            "require_verified is enabled, but this exact combination is %s; no combination is currently verified."
            % result.tier
        )
    return result


def _validate_platform(
    platform: PlatformSpec,
    facts: Any,
    config: Any,
    result: ValidationResult,
) -> None:
    if platform.key == "custom":
        result.add_error(
            "The operating system is not mapped to a built-in platform provider; "
            "select and define a custom provider explicitly."
        )

    init_system = str(_value(facts, "init_system", default="unknown") or "unknown").lower()
    if init_system == "unknown":
        result.add_warning("Init system is unknown; service installation cannot be planned safely.")
    elif not platform.supports_init(init_system):
        result.add_error(
            "Platform %s expects init %s, but facts report %s."
            % (platform.key, "/".join(platform.init_systems), init_system)
        )
    elif init_system != "systemd":
        result.add_warning(
            "Init system %s requires a dedicated service renderer; systemd units cannot be reused."
            % init_system
        )

    primary_pm = str(
        _value(facts, "primary_package_manager", "package_manager", default="") or ""
    ).lower()
    if primary_pm and primary_pm not in (platform.package_manager, "custom"):
        result.add_warning(
            "Detected package manager %s differs from the %s platform default %s."
            % (primary_pm, platform.key, platform.package_manager)
        )

    configured_family = str(
        _config_value(config, "os_family", section="target", default="auto") or "auto"
    ).lower()
    if configured_family not in ("", "auto", platform.key):
        result.add_error(
            "Configured target.os_family=%s conflicts with detected platform family %s."
            % (configured_family, platform.key)
        )

    configured_distribution = str(
        _config_value(config, "distribution", section="target", default="auto") or "auto"
    ).lower()
    actual_id = str(_value(facts, "os_id", default="") or "").lower()
    if configured_distribution not in ("", "auto", actual_id):
        result.add_error(
            "Configured target.distribution=%s conflicts with detected os_id=%s."
            % (configured_distribution, actual_id or "unknown")
        )


def _validate_packages(
    platform: PlatformSpec,
    desktop: DesktopSpec,
    vnc: VncSpec,
    result: ValidationResult,
) -> None:
    family = platform.key
    if vnc.mode not in ("external", "custom"):
        if family in vnc.unsupported_families:
            result.add_error(vnc.unsupported_families[family])
        elif not vnc.package_candidates(family):
            result.add_error(
                "No native %s package candidate is registered for platform family %s."
                % (vnc.key, family)
            )

    if desktop.key not in ("auto", "none", "custom") and not desktop.package_candidates(family):
        result.add_error(
            "No %s desktop package candidate is registered for platform family %s."
            % (desktop.key, family)
        )


def _validate_desktop_selection(
    desktop: DesktopSpec,
    facts: Any,
    config: Any,
    vnc: VncSpec,
    result: ValidationResult,
) -> DesktopSpec:
    available = tuple(_value(facts, "available_xsessions", default=()) or ())
    if desktop.key == "auto":
        detected = detect_desktop_from_xsessions(available)
        if detected is None:
            if not available:
                result.add_error(
                    "desktop=auto requires at least one discovered file in /usr/share/xsessions."
                )
            else:
                result.add_error(
                    "desktop=auto is ambiguous or has no recognized X11 session: %s"
                    % ", ".join(str(item) for item in available)
                )
            return desktop
        result.add_warning("desktop=auto resolved provisionally to %s." % detected.key)
        result.include_tier(detected.tier, detected.reason)
        desktop = detected

    if desktop.key == "custom":
        command = _config_value(
            config,
            "desktop_session_command",
            "session_command",
            section="desktop",
            default=None,
        )
        if not command:
            result.add_error("desktop=custom requires desktop.session_command argv.")

    if vnc.mode == "virtual" and desktop.key == "none":
        result.add_error("A virtual VNC server requires a desktop session or a custom session command.")

    if desktop.key not in ("auto", "none", "custom") and available:
        if not match_installed_xsession(desktop.key, available):
            result.add_warning(
                "No matching installed X11 session was found for %s; verify again after package installation."
                % desktop.key
            )

    return desktop


def _validate_vnc_mode(
    platform: PlatformSpec,
    desktop: DesktopSpec,
    vnc: VncSpec,
    facts: Any,
    config: Any,
    result: ValidationResult,
) -> None:
    host_display = str(_value(facts, "display_server", default="unknown") or "unknown").lower()
    configured_mode = str(
        _config_value(config, "mode", section="vnc", default="") or ""
    ).lower()
    expected_modes = {
        "tigervnc": "virtual-session",
        "tightvnc": "virtual-session",
        "x11vnc": "existing-session",
        "wayvnc": "wayland-session",
    }
    expected_mode = expected_modes.get(vnc.key)
    if expected_mode and configured_mode and configured_mode != expected_mode:
        result.add_error(
            "vnc.implementation=%s requires vnc.mode=%s, not %s."
            % (vnc.key, expected_mode, configured_mode)
        )

    bind_address = str(
        _config_value(config, "bind_address", section="vnc", default="127.0.0.1")
        or "127.0.0.1"
    ).strip().lower()
    if vnc.mode not in ("external", "custom") and bind_address not in (
        "127.0.0.1",
        "::1",
        "localhost",
    ):
        result.add_warning(
            "%s is configured to listen on %s; direct VNC exposure is outside the safe localhost-only default."
            % (vnc.key, bind_address)
        )
    authentication = str(
        _config_value(config, "authentication", section="vnc", default="password")
        or "password"
    ).lower()
    if vnc.mode not in ("external", "custom") and authentication == "none":
        result.add_warning(
            "%s authentication is disabled; even a loopback listener permits access by other local users."
            % vnc.key
        )

    configured_session_display = str(
        _config_value(
            config,
            "session_display_server",
            "display_server",
            section="desktop",
            default="",
        )
        or ""
    ).lower()

    if vnc.mode == "virtual":
        if configured_session_display not in ("", "auto") and configured_session_display not in vnc.display_servers:
            result.add_error(
                "%s creates an X11 display and cannot launch a %s virtual session."
                % (vnc.key, configured_session_display)
            )
        if desktop.display_server not in ("x11", "custom", "auto", "none"):
            result.add_error(
                "%s desktop is not compatible with the X11 display created by %s."
                % (desktop.key, vnc.key)
            )
        if desktop.compositor:
            result.add_warning(
                "%s uses a compositor; verify D-Bus, user services, X11 packages, and software rendering under %s."
                % (desktop.key, vnc.key)
            )
        if vnc.key == "tightvnc" and desktop.key in ("gnome", "kde", "cinnamon"):
            result.add_error(
                "TightVNC with %s is unsupported because the legacy X server is not a reliable compositor target."
                % desktop.key
            )

    elif vnc.key == "x11vnc":
        if host_display == "wayland":
            result.add_error(
                "x11vnc cannot capture the complete active Wayland desktop; select an Xorg login session."
            )
        elif host_display not in ("x11", "xorg"):
            result.add_warning(
                "The active display server is unknown; x11vnc requires a running Xorg display and valid Xauthority."
            )
        if desktop.key not in ("none", "auto", "custom"):
            result.add_warning(
                "x11vnc shares the existing login session and will not launch the selected %s desktop."
                % desktop.key
            )
        if desktop.key in ("gnome", "kde"):
            result.add_warning(
                "%s must be logged in through its explicit Xorg/X11 session for x11vnc."
                % desktop.key
            )

    elif vnc.key == "wayvnc":
        if host_display in ("x11", "xorg"):
            result.add_error("wayvnc requires an active compatible Wayland compositor, not Xorg.")
        elif host_display != "wayland":
            result.add_warning(
                "The active display server is unknown; wayvnc requires a running Wayland compositor."
            )
        if desktop.key not in ("none", "auto", "custom"):
            result.add_error(
                "The built-in %s session is X11-oriented and is not a managed wayvnc/wlroots session."
                % desktop.key
            )
        compositor = str(_value(facts, "wayland_compositor", "compositor", default="") or "").lower()
        if compositor and compositor not in ("sway", "wayfire", "labwc", "river"):
            result.add_error(
                "Wayland compositor %s is not in the conservatively supported wlroots-compatible set."
                % compositor
            )
        elif not compositor:
            result.add_warning(
                "wayvnc compatibility cannot be established without compositor facts "
                "(for example sway/wayfire/labwc/river)."
            )

    elif vnc.mode == "external":
        host = _config_value(
            config,
            "vnc_host",
            "host",
            "hostname",
            section="vnc",
            default=None,
        )
        port = _config_value(config, "vnc_port", "port", section="vnc", default=None)
        if not host or port in (None, ""):
            result.add_error("vnc=external requires an explicit vnc.host and vnc.port.")
        else:
            try:
                numeric_port = int(port)
            except (TypeError, ValueError):
                numeric_port = 0
            if not 1 <= numeric_port <= 65535:
                result.add_error("External VNC port must be an integer from 1 through 65535.")
        if desktop.key != "none":
            result.add_warning("An external VNC endpoint is not managed by the selected local desktop provider.")

    elif vnc.mode == "custom":
        command = _config_value(
            config,
            "vnc_server_command",
            "server_command",
            section="vnc",
            default=None,
        )
        if not command:
            result.add_error("vnc=custom requires vnc.server_command argv or a custom planner provider.")

    if vnc.requires_active_session:
        result.add_warning(
            "%s requires an already running graphical login and must not be treated as a virtual desktop service."
            % vnc.key
        )

    if platform.key == "alpine" and vnc.mode != "external":
        result.add_warning("Alpine requires an OpenRC-specific VNC service implementation.")


def _add_provider_notes(
    platform: PlatformSpec,
    desktop: DesktopSpec,
    vnc: VncSpec,
    result: ValidationResult,
    include_vnc: bool = True,
) -> None:
    for note in platform.notes:
        result.add_warning(note)
    for note in desktop.notes:
        result.add_warning(note)
    for note in desktop.notes_for_family(platform.key):
        result.add_warning(note)
    if include_vnc:
        for note in vnc.notes:
            result.add_warning(note)
        for note in vnc.family_notes.get(platform.key, ()):
            result.add_warning(note)


def _selection(
    config: Any,
    section: str,
    aliases: Sequence[str],
    default: str,
) -> str:
    value = _config_value(config, section, *aliases, default=None)
    section_keys = {
        "desktop": ("environment", "session", "type", "provider", "name", "id"),
        "vnc": ("implementation", "type", "provider", "name", "id"),
    }.get(section, ("type", "provider", "name", "id"))
    if isinstance(value, Mapping):
        for key in section_keys:
            if value.get(key):
                return str(value[key])
    elif value is not None and not hasattr(value, "data"):
        if hasattr(value, "key"):
            return str(value.key)
        if hasattr(value, "id"):
            return str(value.id)
        return str(value)

    nested = _config_section(config, section)
    if nested is not None:
        for key in section_keys:
            nested_value = _value(nested, key, default=None)
            if nested_value:
                return str(nested_value)
    return default


def _config_value(
    config: Any,
    *names: str,
    section: Optional[str] = None,
    default: Any = None,
) -> Any:
    for name in names:
        value = _value(config, name, default=_MISSING)
        if value is not _MISSING:
            return value
    if section:
        nested = _config_section(config, section)
        if nested is not None:
            for name in names:
                value = _value(nested, name, default=_MISSING)
                if value is not _MISSING:
                    return value
    return default


def _config_section(config: Any, section: str) -> Any:
    if hasattr(config, "section") and callable(config.section):
        try:
            value = config.section(section)
        except (KeyError, TypeError, ValueError):
            value = None
        if value:
            return value
    return _value(config, section, default=None)


_MISSING = object()


def _value(source: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        if hasattr(source, name):
            return getattr(source, name)
        if hasattr(source, "get") and callable(source.get):
            try:
                value = source.get(name, _MISSING)
            except (KeyError, TypeError, ValueError):
                value = _MISSING
            if value is not _MISSING:
                return value
    return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def list_vncs() -> Tuple[VncSpec, ...]:
    return tuple(VNCS[key] for key in VNCS)


__all__ = [
    "VNCS",
    "VNC_ALIASES",
    "ValidationResult",
    "VncSpec",
    "list_vncs",
    "resolve_vnc",
    "validate_combination",
    "vnc_package_candidates",
]
