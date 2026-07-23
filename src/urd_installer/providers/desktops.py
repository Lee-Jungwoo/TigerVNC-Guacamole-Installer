"""Desktop-environment provider registry.

Package entries are ordered alternatives.  Each inner tuple is one complete
candidate set and must be probed before it is selected.  This is necessary
because even distributions in the same family split desktop metapackages
differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .platforms import EXPERIMENTAL, UNSUPPORTED


PackageCandidates = Mapping[str, Tuple[Tuple[str, ...], ...]]


@dataclass(frozen=True)
class DesktopSpec:
    key: str
    display_name: str
    package_candidates_by_family: PackageCandidates
    session_argv: Tuple[str, ...]
    xsession_names: Tuple[str, ...]
    display_server: str
    tier: str
    reason: str
    compositor: bool = False
    family_notes: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.key

    def package_candidates(self, family: str) -> Tuple[Tuple[str, ...], ...]:
        return self.package_candidates_by_family.get(family, ())

    def notes_for_family(self, family: str) -> Tuple[str, ...]:
        return self.family_notes.get(family, ())


_XFCE_PACKAGES: PackageCandidates = {
    "debian": (("xfce4", "xfce4-goodies"), ("xfce4",)),
    "rhel": (("xfce4-session", "xfce4-panel", "xfwm4", "xfdesktop"),),
    "fedora": (("xfce4-session", "xfce4-panel", "xfwm4", "xfdesktop"),),
    "arch": (("xfce4", "xfce4-goodies"), ("xfce4",)),
    "suse": (
        ("patterns-xfce-xfce",),
        ("xfce4-session", "xfce4-panel", "xfwm4", "xfdesktop"),
    ),
    "alpine": (("xfce4", "xfce4-terminal"), ("xfce4",)),
}

_GNOME_PACKAGES: PackageCandidates = {
    "debian": (("gnome-core",), ("gnome-session", "gnome-shell")),
    "rhel": (("gnome-session", "gnome-shell"),),
    "fedora": (("gnome-session", "gnome-shell"),),
    "arch": (("gnome",), ("gnome-session", "gnome-shell")),
    "suse": (
        ("patterns-gnome-gnome",),
        ("gnome-session", "gnome-shell"),
    ),
    "alpine": (("gnome",), ("gnome-session", "gnome-shell")),
}

_KDE_PACKAGES: PackageCandidates = {
    "debian": (("kde-plasma-desktop",), ("plasma-desktop", "plasma-workspace")),
    "rhel": (
        ("plasma-desktop", "plasma-workspace", "plasma-workspace-x11"),
        ("plasma-desktop", "plasma-workspace"),
    ),
    "fedora": (
        ("plasma-desktop", "plasma-workspace", "plasma-workspace-x11"),
        ("plasma-desktop", "plasma-workspace"),
    ),
    "arch": (
        ("plasma", "plasma-x11-session"),
        ("plasma",),
    ),
    "suse": (
        ("patterns-kde-kde_plasma",),
        ("plasma6-session", "plasma6-workspace"),
        ("plasma5-session", "plasma5-workspace"),
    ),
    "alpine": (("plasma",), ("plasma-desktop", "plasma-workspace")),
}

_MATE_PACKAGES: PackageCandidates = {
    "debian": (("mate-desktop-environment-core",), ("mate-session-manager", "mate-panel", "marco", "caja")),
    "rhel": (("mate-session-manager", "mate-panel", "marco", "caja"),),
    "fedora": (("mate-session-manager", "mate-panel", "marco", "caja"),),
    "arch": (("mate",), ("mate-session-manager", "mate-panel", "marco", "caja")),
    "suse": (
        ("patterns-mate-mate",),
        ("mate-session-manager", "mate-panel", "marco", "caja"),
    ),
    "alpine": (("mate",), ("mate-session-manager", "mate-panel", "marco", "caja")),
}

_LXQT_PACKAGES: PackageCandidates = {
    "debian": (("lxqt-core", "openbox"), ("lxqt", "openbox")),
    "rhel": (("lxqt-session", "lxqt-panel", "pcmanfm-qt", "openbox"),),
    "fedora": (("lxqt-session", "lxqt-panel", "pcmanfm-qt", "openbox"),),
    "arch": (("lxqt", "openbox"),),
    "suse": (
        ("patterns-lxqt-lxqt",),
        ("lxqt-session", "lxqt-panel", "pcmanfm-qt", "openbox"),
    ),
    "alpine": (("lxqt", "openbox"), ("lxqt-session", "lxqt-panel", "openbox")),
}

_LXDE_PACKAGES: PackageCandidates = {
    "debian": (("lxde-core",), ("lxsession", "lxpanel", "openbox", "pcmanfm")),
    "rhel": (("lxsession", "lxpanel", "openbox", "pcmanfm"),),
    "fedora": (("lxsession", "lxpanel", "openbox", "pcmanfm"),),
    "arch": (("lxde",), ("lxsession", "lxpanel", "openbox", "pcmanfm")),
    "suse": (
        ("patterns-lxde-lxde",),
        ("lxsession", "lxpanel", "openbox", "pcmanfm"),
    ),
    "alpine": (("lxde",), ("lxsession", "lxpanel", "openbox", "pcmanfm")),
}

_CINNAMON_PACKAGES: PackageCandidates = {
    "debian": (("cinnamon-core",), ("cinnamon-desktop-environment",), ("cinnamon",)),
    "rhel": (("cinnamon",),),
    "fedora": (("cinnamon",),),
    "arch": (("cinnamon",),),
    "suse": (("cinnamon",),),
    "alpine": (("cinnamon",),),
}


DESKTOPS: Dict[str, DesktopSpec] = {
    "auto": DesktopSpec(
        key="auto",
        display_name="Auto-detected installed X11 desktop",
        package_candidates_by_family={},
        session_argv=(),
        xsession_names=(),
        display_server="auto",
        tier=EXPERIMENTAL,
        reason="Auto-selection is safe only when exactly one usable X11 session is discovered.",
        notes=("Inspect /usr/share/xsessions; do not select a Wayland session file.",),
    ),
    "none": DesktopSpec(
        key="none",
        display_name="No managed desktop",
        package_candidates_by_family={},
        session_argv=(),
        xsession_names=(),
        display_server="none",
        tier=EXPERIMENTAL,
        reason="Useful for an external/shared server, but not a complete virtual desktop session.",
    ),
    "xfce": DesktopSpec(
        key="xfce",
        display_name="XFCE",
        package_candidates_by_family=_XFCE_PACKAGES,
        session_argv=("startxfce4",),
        xsession_names=("xfce", "xfce4"),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="This is the lowest-risk virtual VNC target, but no tuple is verified yet.",
        family_notes={
            "rhel": ("Packages commonly require EPEL on RHEL-compatible systems.",),
        },
    ),
    "gnome": DesktopSpec(
        key="gnome",
        display_name="GNOME",
        package_candidates_by_family=_GNOME_PACKAGES,
        session_argv=("gnome-session", "--session=gnome"),
        xsession_names=("gnome-xorg", "gnome"),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="GNOME Shell needs an Xorg session, D-Bus/user services, and working software rendering.",
        compositor=True,
        notes=("Never assume the default GNOME login is Xorg; many releases default to Wayland.",),
    ),
    "kde": DesktopSpec(
        key="kde",
        display_name="KDE Plasma",
        package_candidates_by_family=_KDE_PACKAGES,
        session_argv=("startplasma-x11",),
        xsession_names=("plasma-x11", "plasma5", "plasma"),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="Plasma requires an explicitly installed X11 session and compositor validation.",
        compositor=True,
        family_notes={
            "rhel": ("KDE and its X11 session can require EPEL or be absent for a release.",),
            "fedora": ("Probe the release-specific Plasma X11 session split package.",),
            "arch": ("Probe plasma-x11-session; do not assume the Wayland default is usable.",),
        },
    ),
    "mate": DesktopSpec(
        key="mate",
        display_name="MATE",
        package_candidates_by_family=_MATE_PACKAGES,
        session_argv=("mate-session",),
        xsession_names=("mate",),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="The session is a plausible virtual-X11 target but still needs per-family tests.",
        family_notes={
            "rhel": ("Packages commonly require EPEL on RHEL-compatible systems.",),
        },
    ),
    "lxqt": DesktopSpec(
        key="lxqt",
        display_name="LXQt",
        package_candidates_by_family=_LXQT_PACKAGES,
        session_argv=("startlxqt",),
        xsession_names=("lxqt",),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="LXQt needs a separately available X11 window manager on several families.",
        notes=("The package candidate includes Openbox where a default window manager is not guaranteed.",),
    ),
    "lxde": DesktopSpec(
        key="lxde",
        display_name="LXDE",
        package_candidates_by_family=_LXDE_PACKAGES,
        session_argv=("startlxde",),
        xsession_names=("lxde",),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="LXDE packaging is increasingly optional and must be probed per release.",
    ),
    "cinnamon": DesktopSpec(
        key="cinnamon",
        display_name="Cinnamon",
        package_candidates_by_family=_CINNAMON_PACKAGES,
        session_argv=("cinnamon-session",),
        xsession_names=("cinnamon", "cinnamon2d"),
        display_server="x11",
        tier=EXPERIMENTAL,
        reason="Muffin composition and software-rendering behavior vary under virtual X servers.",
        compositor=True,
        family_notes={
            "rhel": ("Cinnamon generally requires EPEL and may be unavailable.",),
            "suse": ("Repository availability differs between Leap and Tumbleweed.",),
        },
    ),
    "budgie": DesktopSpec(
        key="budgie",
        display_name="Budgie (reserved provider)",
        package_candidates_by_family={},
        session_argv=(),
        xsession_names=("budgie-desktop", "budgie"),
        display_server="x11",
        tier=UNSUPPORTED,
        reason="Budgie packaging and its GNOME service dependencies require a dedicated, tested provider.",
        compositor=True,
        notes=("No package repository or display manager is enabled automatically.",),
    ),
    "custom": DesktopSpec(
        key="custom",
        display_name="Custom desktop session",
        package_candidates_by_family={},
        session_argv=(),
        xsession_names=(),
        display_server="custom",
        tier=UNSUPPORTED,
        reason="A launch argv and package/service ownership must be supplied by the operator.",
    ),
}


DESKTOP_ALIASES: Dict[str, str] = {
    "plasma": "kde",
    "kde-plasma": "kde",
    "plasma-x11": "kde",
    "xfce4": "xfce",
    "gnome-xorg": "gnome",
    "gnome-classic": "gnome",
    "cinnamon2d": "cinnamon",
    "disabled": "none",
}


def resolve_desktop(name: str) -> DesktopSpec:
    normalized = str(name or "auto").strip().lower()
    normalized = DESKTOP_ALIASES.get(normalized, normalized)
    try:
        return DESKTOPS[normalized]
    except KeyError:
        raise KeyError("unknown desktop provider: %s" % name)


def desktop_package_candidates(desktop: str, family: str) -> Tuple[Tuple[str, ...], ...]:
    return resolve_desktop(desktop).package_candidates(family)


def normalize_xsession_name(name: str) -> str:
    normalized = str(name).strip().lower().rsplit("/", 1)[-1]
    if normalized.endswith(".desktop"):
        normalized = normalized[:-8]
    return normalized


def match_installed_xsession(
    desktop: str,
    available_xsessions: Sequence[str],
) -> Optional[str]:
    """Return the preferred installed X11 session basename, if present."""

    spec = resolve_desktop(desktop)
    available = {
        normalize_xsession_name(item): item
        for item in available_xsessions
        if str(item).strip()
    }
    for preferred in spec.xsession_names:
        if preferred in available:
            return preferred
    return None


def detect_desktop_from_xsessions(available_xsessions: Sequence[str]) -> Optional[DesktopSpec]:
    """Auto-detect only when exactly one canonical desktop matches."""

    matches = []
    for key in ("xfce", "mate", "lxqt", "lxde", "kde", "gnome", "cinnamon"):
        if match_installed_xsession(key, available_xsessions):
            matches.append(DESKTOPS[key])
    if len(matches) == 1:
        return matches[0]
    return None


def xstartup_argv(desktop: str) -> Tuple[str, ...]:
    """Return argv to place after ``exec dbus-run-session --``."""

    spec = resolve_desktop(desktop)
    if not spec.session_argv:
        raise ValueError("desktop %s has no built-in session command" % spec.key)
    return spec.session_argv


def list_desktops() -> Tuple[DesktopSpec, ...]:
    return tuple(DESKTOPS[key] for key in DESKTOPS)


__all__ = [
    "DESKTOPS",
    "DESKTOP_ALIASES",
    "DesktopSpec",
    "PackageCandidates",
    "desktop_package_candidates",
    "detect_desktop_from_xsessions",
    "list_desktops",
    "match_installed_xsession",
    "normalize_xsession_name",
    "resolve_desktop",
    "xstartup_argv",
]
