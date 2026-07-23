"""Linux platform and package-manager metadata.

The module deliberately contains no installer side effects.  It converts
``/etc/os-release`` data into a conservative platform family and exposes
argv builders which callers may hand to their own executor.

Support metadata describes implemented knowledge, not proof that an install
works.  The project currently has no verified platform combinations, so all
built-in platform providers remain experimental.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import platform as stdlib_platform
import shlex
import shutil
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


VERIFIED = "verified"
EXPERIMENTAL = "experimental"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PackageManagerSpec:
    """Side-effect-free command templates for a native package manager.

    Package names are appended as separate argv elements.  Shell strings are
    intentionally not returned, which avoids quoting and injection mistakes.
    """

    name: str
    executable: str
    update_command: Tuple[str, ...]
    install_command: Tuple[str, ...]
    probe_command: Tuple[str, ...]
    update_is_full_upgrade: bool = False
    notes: Tuple[str, ...] = ()

    def update_argv(self) -> Tuple[str, ...]:
        if not self.update_command:
            raise ValueError("package manager %s has no update command" % self.name)
        return self.update_command

    def install_argv(self, packages: Iterable[str]) -> Tuple[str, ...]:
        resolved = _clean_packages(packages)
        if not self.install_command:
            raise ValueError("package manager %s has no install command" % self.name)
        if not resolved:
            raise ValueError("at least one package is required")
        return self.install_command + resolved

    def probe_argv(self, package: str) -> Tuple[str, ...]:
        resolved = _clean_packages((package,))
        if not self.probe_command:
            raise ValueError("package manager %s has no probe command" % self.name)
        return self.probe_command + resolved


def _clean_packages(packages: Iterable[str]) -> Tuple[str, ...]:
    result = []
    for package in packages:
        if not isinstance(package, str) or not package.strip():
            raise ValueError("package names must be non-empty strings")
        candidate = package.strip()
        if candidate.startswith("-"):
            raise ValueError("package names may not begin with '-': %s" % candidate)
        result.append(candidate)
    return tuple(result)


PACKAGE_MANAGERS: Dict[str, PackageManagerSpec] = {
    "apt": PackageManagerSpec(
        name="apt",
        executable="apt-get",
        update_command=("apt-get", "update"),
        install_command=(
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
        ),
        probe_command=("apt-cache", "show"),
        notes=(
            "Set DEBIAN_FRONTEND=noninteractive in the executor environment when requested.",
            "Do not mix packages from a different Debian or Ubuntu release.",
        ),
    ),
    "dnf": PackageManagerSpec(
        name="dnf",
        executable="dnf",
        update_command=("dnf", "-y", "makecache"),
        install_command=("dnf", "-y", "install"),
        probe_command=("dnf", "--quiet", "list", "--showduplicates"),
        notes=(
            "RHEL subscriptions and optional CRB/EPEL repositories are external prerequisites.",
            "A provider must not enable third-party repositories silently.",
        ),
    ),
    "pacman": PackageManagerSpec(
        name="pacman",
        executable="pacman",
        update_command=("pacman", "-Syu", "--noconfirm"),
        install_command=("pacman", "-S", "--needed", "--noconfirm"),
        probe_command=("pacman", "-Si"),
        update_is_full_upgrade=True,
        notes=(
            "Arch partial upgrades are unsupported; never replace -Syu with -Sy.",
        ),
    ),
    "zypper": PackageManagerSpec(
        name="zypper",
        executable="zypper",
        update_command=("zypper", "--non-interactive", "refresh"),
        install_command=(
            "zypper",
            "--non-interactive",
            "install",
            "--no-recommends",
        ),
        probe_command=(
            "zypper",
            "--non-interactive",
            "search",
            "--match-exact",
            "--type",
            "package",
        ),
        notes=(
            "Leap and Tumbleweed can use different package and pattern names; probe first.",
        ),
    ),
    "apk": PackageManagerSpec(
        name="apk",
        executable="apk",
        update_command=("apk", "update"),
        install_command=("apk", "add", "--no-cache"),
        probe_command=("apk", "search", "--exact"),
        notes=(
            "Desktop, Java, and Guacamole paths on musl/OpenRC are not end-to-end tested.",
        ),
    ),
    "custom": PackageManagerSpec(
        name="custom",
        executable="",
        update_command=(),
        install_command=(),
        probe_command=(),
        notes=("Commands must be supplied explicitly by the operator.",),
    ),
}


@dataclass(frozen=True)
class InitCapability:
    name: str
    system_services: bool
    user_services: bool
    template_units: bool
    notes: Tuple[str, ...] = ()


INIT_CAPABILITIES: Dict[str, InitCapability] = {
    "systemd": InitCapability(
        name="systemd",
        system_services=True,
        user_services=True,
        template_units=True,
    ),
    "openrc": InitCapability(
        name="openrc",
        system_services=True,
        user_services=False,
        template_units=False,
        notes=("A dedicated OpenRC service script is required for each VNC instance.",),
    ),
    "runit": InitCapability(
        name="runit",
        system_services=True,
        user_services=False,
        template_units=False,
        notes=("No built-in runit service renderer is currently verified.",),
    ),
    "sysv": InitCapability(
        name="sysv",
        system_services=True,
        user_services=False,
        template_units=False,
        notes=("Legacy init scripts require a separate provider.",),
    ),
    "unknown": InitCapability(
        name="unknown",
        system_services=False,
        user_services=False,
        template_units=False,
        notes=("Service management cannot be selected until init is detected.",),
    ),
}


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    display_name: str
    os_ids: Tuple[str, ...]
    id_like_tokens: Tuple[str, ...]
    package_manager: str
    init_systems: Tuple[str, ...]
    tier: str
    reason: str
    notes: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.key

    @property
    def family(self) -> str:
        return self.key

    @property
    def package_manager_spec(self) -> PackageManagerSpec:
        return PACKAGE_MANAGERS[self.package_manager]

    def supports_init(self, init_system: str) -> bool:
        return init_system.lower() in self.init_systems


PLATFORMS: Dict[str, PlatformSpec] = {
    "debian": PlatformSpec(
        key="debian",
        display_name="Debian / Ubuntu family",
        os_ids=(
            "debian",
            "ubuntu",
            "linuxmint",
            "pop",
            "elementary",
            "raspbian",
            "kali",
        ),
        id_like_tokens=("debian", "ubuntu"),
        package_manager="apt",
        init_systems=("systemd",),
        tier=EXPERIMENTAL,
        reason="Provider metadata exists, but no clean-VM end-to-end tuple is verified yet.",
        notes=(
            "Derivatives are detected as this family but are not automatically supported.",
            "Tomcat package versions and webapp paths vary by release.",
        ),
    ),
    "rhel": PlatformSpec(
        key="rhel",
        display_name="RHEL-compatible family",
        os_ids=(
            "rhel",
            "centos",
            "rocky",
            "almalinux",
            "ol",
            "amzn",
            "scientific",
            "eurolinux",
        ),
        id_like_tokens=("rhel", "centos"),
        package_manager="dnf",
        init_systems=("systemd",),
        tier=EXPERIMENTAL,
        reason="Desktop and VNC packages can require subscription, CRB, or EPEL repositories.",
        notes=(
            "SELinux policy must be preserved and adjusted narrowly rather than disabled.",
        ),
    ),
    "fedora": PlatformSpec(
        key="fedora",
        display_name="Fedora",
        os_ids=("fedora",),
        id_like_tokens=("fedora",),
        package_manager="dnf",
        init_systems=("systemd",),
        tier=EXPERIMENTAL,
        reason="Rolling package changes and desktop X11 splits require per-release tests.",
        notes=(
            "DNF desktop group identifiers are not stable enough to be the only package source.",
        ),
    ),
    "arch": PlatformSpec(
        key="arch",
        display_name="Arch Linux family",
        os_ids=("arch", "manjaro", "endeavouros", "garuda"),
        id_like_tokens=("arch",),
        package_manager="pacman",
        init_systems=("systemd",),
        tier=EXPERIMENTAL,
        reason="The rolling release model requires continuously refreshed integration tests.",
        notes=("AUR packages are never treated as supported native dependencies.",),
    ),
    "suse": PlatformSpec(
        key="suse",
        display_name="openSUSE / SLES family",
        os_ids=(
            "opensuse",
            "opensuse-leap",
            "opensuse-tumbleweed",
            "sles",
            "sled",
        ),
        id_like_tokens=("suse", "opensuse"),
        package_manager="zypper",
        init_systems=("systemd",),
        tier=EXPERIMENTAL,
        reason="Leap, Tumbleweed, and SLES differ in package patterns and VNC service layout.",
        notes=(
            "The display-manager.service alias and vhosts.d nginx layout must be probed.",
        ),
    ),
    "alpine": PlatformSpec(
        key="alpine",
        display_name="Alpine Linux",
        os_ids=("alpine",),
        id_like_tokens=("alpine",),
        package_manager="apk",
        init_systems=("openrc",),
        tier=EXPERIMENTAL,
        reason="musl, OpenRC, and repository differences have not been tested end to end.",
        notes=(
            "A systemd unit cannot be installed on the default Alpine configuration.",
        ),
    ),
    "custom": PlatformSpec(
        key="custom",
        display_name="Custom Linux platform",
        os_ids=(),
        id_like_tokens=(),
        package_manager="custom",
        init_systems=("systemd", "openrc", "runit", "sysv", "unknown"),
        tier=UNSUPPORTED,
        reason="Package and service commands must be supplied and verified by the operator.",
    ),
}


@dataclass(frozen=True)
class DetectedPlatform:
    os_id: str
    os_name: str
    version_id: str
    os_like: Tuple[str, ...]
    architecture: str
    init_system: str
    package_managers: Tuple[str, ...]
    primary_package_manager: str
    platform_key: str
    os_release: Mapping[str, str] = field(default_factory=dict)

    @property
    def spec(self) -> PlatformSpec:
        return PLATFORMS[self.platform_key]

    @property
    def family(self) -> str:
        return self.platform_key


def parse_os_release(content: str) -> Dict[str, str]:
    """Parse os-release without sourcing it as shell code."""

    result: Dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        try:
            parsed = shlex.split(raw_value, comments=False, posix=True)
        except ValueError:
            parsed = []
        if not parsed:
            value = raw_value.strip().strip("\"'")
        elif len(parsed) == 1:
            value = parsed[0]
        else:
            value = " ".join(parsed)
        result[key] = value
    return result


def read_os_release(path: str = "/etc/os-release") -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as handle:
        return parse_os_release(handle.read())


def resolve_platform(os_id: str, os_like: Sequence[str] = ()) -> PlatformSpec:
    """Resolve exact ID first, then ordered ID_LIKE tokens."""

    normalized_id = (os_id or "").strip().lower()
    for spec in PLATFORMS.values():
        if normalized_id and normalized_id in spec.os_ids:
            return spec

    normalized_like = tuple(str(item).strip().lower() for item in os_like if str(item).strip())
    # RHEL-like must win over the broader Fedora ancestry used by some clones.
    precedence = ("debian", "rhel", "fedora", "arch", "suse", "alpine")
    for key in precedence:
        spec = PLATFORMS[key]
        if any(token in spec.id_like_tokens for token in normalized_like):
            return spec
    return PLATFORMS["custom"]


def detect_init_system(proc1_comm: Optional[str] = None) -> str:
    if proc1_comm is None:
        try:
            with open("/proc/1/comm", "r", encoding="utf-8") as handle:
                proc1_comm = handle.read().strip()
        except OSError:
            proc1_comm = ""
    normalized = (proc1_comm or "").strip().lower()
    if normalized == "systemd" or os.path.isdir("/run/systemd/system"):
        return "systemd"
    if normalized in ("openrc", "openrc-init") or os.path.isdir("/run/openrc"):
        return "openrc"
    if normalized == "runit":
        return "runit"
    if normalized in ("init", "sysvinit"):
        return "sysv"
    return "unknown"


def detect_package_managers(
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Tuple[str, ...]:
    found = []
    for name in ("apt", "dnf", "pacman", "zypper", "apk"):
        if which(PACKAGE_MANAGERS[name].executable):
            found.append(name)
    return tuple(found)


def detect_platform(
    os_release_path: str = "/etc/os-release",
    which: Callable[[str], Optional[str]] = shutil.which,
) -> DetectedPlatform:
    release = read_os_release(os_release_path)
    os_id = release.get("ID", "").lower()
    os_like = tuple(release.get("ID_LIKE", "").lower().split())
    spec = resolve_platform(os_id, os_like)
    managers = detect_package_managers(which)
    primary = spec.package_manager if spec.package_manager in managers else (managers[0] if managers else "custom")
    return DetectedPlatform(
        os_id=os_id,
        os_name=release.get("PRETTY_NAME", release.get("NAME", os_id)),
        version_id=release.get("VERSION_ID", ""),
        os_like=os_like,
        architecture=stdlib_platform.machine(),
        init_system=detect_init_system(),
        package_managers=managers,
        primary_package_manager=primary,
        platform_key=spec.key,
        os_release=release,
    )


def platform_from_facts(facts: Any) -> PlatformSpec:
    explicit = _fact_value(facts, "platform_key", "platform_family", "os_family", "family")
    if explicit:
        key = str(explicit).strip().lower()
        if key in PLATFORMS:
            return PLATFORMS[key]
    os_id = str(_fact_value(facts, "os_id", default="") or "")
    os_like_value = _fact_value(facts, "os_like", "id_like", default=())
    if isinstance(os_like_value, str):
        os_like = tuple(os_like_value.split())
    else:
        os_like = tuple(os_like_value or ())
    return resolve_platform(os_id, os_like)


def _fact_value(facts: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(facts, Mapping) and name in facts:
            return facts[name]
        if hasattr(facts, name):
            return getattr(facts, name)
    return default


def get_package_manager(name: str) -> PackageManagerSpec:
    try:
        return PACKAGE_MANAGERS[name.lower()]
    except KeyError:
        raise KeyError("unknown package manager: %s" % name)


def list_platforms() -> Tuple[PlatformSpec, ...]:
    return tuple(PLATFORMS[key] for key in PLATFORMS)


__all__ = [
    "DetectedPlatform",
    "EXPERIMENTAL",
    "INIT_CAPABILITIES",
    "InitCapability",
    "PACKAGE_MANAGERS",
    "PLATFORMS",
    "PackageManagerSpec",
    "PlatformSpec",
    "UNSUPPORTED",
    "VERIFIED",
    "detect_init_system",
    "detect_package_managers",
    "detect_platform",
    "get_package_manager",
    "list_platforms",
    "parse_os_release",
    "platform_from_facts",
    "read_os_release",
    "resolve_platform",
]
