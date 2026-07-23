"""Read-only host fact discovery that also works on macOS test hosts."""

from __future__ import annotations

import getpass
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import FactsError, SystemFacts


OS_RELEASE_ENV = "URD_OS_RELEASE"
_OS_RELEASE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PACKAGE_MANAGER_COMMANDS = (
    ("apt", "apt-get"),
    ("dnf", "dnf"),
    ("yum", "yum"),
    ("zypper", "zypper"),
    ("pacman", "pacman"),
    ("apk", "apk"),
    ("xbps", "xbps-install"),
    ("emerge", "emerge"),
    ("brew", "brew"),
)


def parse_os_release(text: str) -> Dict[str, str]:
    """Parse os-release without sourcing it or expanding shell expressions."""

    result: Dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise FactsError("invalid os-release line {}: missing '='".format(line_number))
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _OS_RELEASE_KEY.match(key):
            raise FactsError("invalid os-release key at line {}: {!r}".format(line_number, key))
        try:
            values = shlex.split(raw_value, comments=False, posix=True)
        except ValueError as exc:
            raise FactsError("invalid os-release value at line {}: {}".format(line_number, exc)) from exc
        if not values:
            value = ""
        elif len(values) == 1:
            value = values[0]
        else:
            raise FactsError("unquoted whitespace in os-release line {}".format(line_number))
        result[key] = value
    return result


def read_os_release(path: Optional[str] = None) -> Tuple[Dict[str, str], Optional[str]]:
    override = path if path is not None else os.environ.get(OS_RELEASE_ENV)
    candidates: Sequence[Path]
    if override:
        candidates = (Path(override).expanduser(),)
    else:
        candidates = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as exc:
            raise FactsError("cannot read os-release {}: {}".format(candidate, exc)) from exc
        return parse_os_release(text), str(candidate)

    if override:
        raise FactsError("URD_OS_RELEASE does not exist: {}".format(candidates[0]))
    if platform.system() == "Darwin":
        version = platform.mac_ver()[0]
        return {
            "ID": "macos",
            "NAME": "macOS",
            "PRETTY_NAME": "macOS {}".format(version).strip(),
            "VERSION_ID": version,
        }, None
    return {
        "ID": platform.system().lower() or "unknown",
        "NAME": platform.system() or "Unknown",
        "VERSION_ID": platform.release(),
    }, None


def normalize_architecture(machine: str) -> str:
    value = machine.strip().lower()
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armv7",
        "armv6l": "armv6",
        "i386": "386",
        "i486": "386",
        "i586": "386",
        "i686": "386",
        "ppc64le": "ppc64le",
        "s390x": "s390x",
        "riscv64": "riscv64",
    }
    return aliases.get(value, value or "unknown")


def _detect_init_system(system_name: str) -> str:
    if system_name == "Darwin":
        return "launchd"
    pid_one = _read_first(Path("/proc/1/comm")).lower()
    if Path("/run/systemd/system").is_dir() or pid_one == "systemd":
        return "systemd"
    if shutil.which("rc-service") or Path("/run/openrc").exists():
        return "openrc"
    if shutil.which("runit") or Path("/run/runit").exists():
        return "runit"
    if shutil.which("s6-svscan"):
        return "s6"
    return "none"


def _detect_package_managers() -> Tuple[Tuple[str, ...], Optional[str]]:
    found = tuple(name for name, command in _PACKAGE_MANAGER_COMMANDS if shutil.which(command))
    return found, found[0] if found else None


def _read_first(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _detect_security_modules() -> Tuple[str, ...]:
    modules: List[str] = []
    if Path("/sys/fs/selinux").exists() or shutil.which("getenforce"):
        modules.append("selinux")
    apparmor = _read_first(Path("/sys/module/apparmor/parameters/enabled")).lower()
    if apparmor.startswith("y") or Path("/sys/kernel/security/apparmor").exists():
        modules.append("apparmor")
    return tuple(modules)


def _quiet_command(argv: Sequence[str]) -> bool:
    try:
        return subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _detect_active_firewall(init_system: str) -> Optional[str]:
    if init_system == "systemd" and shutil.which("systemctl"):
        for service, name in (("firewalld", "firewalld"), ("ufw", "ufw"), ("nftables", "nftables")):
            if _quiet_command(("systemctl", "is-active", "--quiet", service)):
                return name
    if shutil.which("firewall-cmd") and _quiet_command(("firewall-cmd", "--state")):
        return "firewalld"
    return None


def _detect_display_server() -> Optional[str]:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type in ("x11", "wayland"):
        return session_type
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return None


def _discover_session_names() -> Tuple[str, ...]:
    names = set()
    # This field is deliberately X11-only.  Merging Wayland desktop files can
    # make a virtual-X VNC provider auto-select an incompatible session.
    for directory in (Path("/usr/share/xsessions"),):
        try:
            for entry in directory.iterdir():
                if entry.is_file() and entry.suffix == ".desktop":
                    names.add(entry.stem)
        except OSError:
            continue
    return tuple(sorted(names))


def _discover_service_units() -> Tuple[str, ...]:
    names = set()
    for directory in (
        Path("/etc/systemd/system"),
        Path("/usr/lib/systemd/system"),
        Path("/lib/systemd/system"),
    ):
        try:
            for entry in directory.iterdir():
                if entry.is_file() or entry.is_symlink():
                    if entry.suffix in (".service", ".socket", ".target"):
                        names.add(entry.name)
        except OSError:
            continue
    return tuple(sorted(names))


def _detect_container(system_name: str) -> bool:
    if system_name != "Linux":
        return False
    if os.environ.get("container") or Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    cgroup = _read_first(Path("/proc/1/cgroup")).lower()
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "podman", "lxc"))


def detect_facts(os_release_path: Optional[str] = None) -> SystemFacts:
    release, release_path = read_os_release(os_release_path)
    system_name = platform.system()
    managers, primary_manager = _detect_package_managers()
    init_system = _detect_init_system(system_name)
    euid = os.geteuid() if hasattr(os, "geteuid") else -1
    like = tuple(part for part in release.get("ID_LIKE", "").lower().split() if part)
    return SystemFacts(
        os_id=release.get("ID", system_name.lower() or "unknown").lower(),
        os_name=release.get("PRETTY_NAME") or release.get("NAME") or system_name or "Unknown",
        version_id=release.get("VERSION_ID", ""),
        os_like=like,
        architecture=normalize_architecture(platform.machine()),
        kernel="{} {}".format(system_name, platform.release()).strip(),
        hostname=socket.gethostname(),
        init_system=init_system,
        package_managers=managers,
        primary_package_manager=primary_manager,
        security_modules=_detect_security_modules(),
        active_firewall=_detect_active_firewall(init_system),
        display_server=_detect_display_server(),
        available_xsessions=_discover_session_names(),
        service_units=_discover_service_units(),
        is_container=_detect_container(system_name),
        euid=euid,
        is_root=euid == 0,
        sudo_available=shutil.which("sudo") is not None,
        current_user=getpass.getuser(),
        python_version="{}.{}.{}".format(*sys.version_info[:3]),
        os_release_path=release_path,
        os_release=release,
    )


# Stable short alias for provider code and tests.
detect = detect_facts
