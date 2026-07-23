"""Capability-aware plan/apply layer for the universal installer.

The planner is intentionally conservative: it supports a useful hybrid path
today while keeping unverified combinations visible instead of pretending that
the Cartesian product of Linux distributions, desktops, and VNC servers works.
"""

from __future__ import annotations

import copy
import hashlib
import os
import pwd
import secrets
import shutil
import socket
import stat
import string
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Sequence

from .model import (
    ExecutionError,
    InstallerConfig,
    OperationResult,
    Plan,
    PlanStep,
    SupportLevel,
    SystemFacts,
)
from .providers.deployment import (
    DEPLOYMENTS,
    render_apache,
    render_caddy,
    render_compose,
    render_guacamole_http_bind,
    render_nginx,
)
from .providers.desktops import DESKTOPS, resolve_desktop, xstartup_argv
from .providers.platforms import PACKAGE_MANAGERS, PLATFORMS, platform_from_facts
from .providers.vnc import VNCS, resolve_vnc, validate_combination
from .renderers import (
    render_openrc_vnc_service,
    render_systemd_vnc_unit,
    render_vnc_config,
    render_vnc_launcher,
    render_xstartup,
)


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "profile": "hybrid",
    "target": {
        "desktop_user": "",
        "create_user": False,
        "container_runtime": "auto",
        "install_root": "/opt/urd",
        "os_family": "auto",
        "distribution": "auto",
    },
    "desktop": {
        "enabled": True,
        "environment": "xfce",
        "display_server": "x11",
        "display_manager": "none",
        "session": "xfce",
    },
    "vnc": {
        "enabled": True,
        "implementation": "tigervnc",
        "mode": "virtual-session",
        "bind_address": "127.0.0.1",
        "port": 5901,
        "display_number": 1,
        "geometry": "1920x1080",
        "depth": 24,
        "authentication": "password",
        "password_file": "",
        "service_scope": "system",
    },
    "guacamole": {
        "enabled": True,
        "version": "1.6.0",
        "deployment": "compose",
        "web_bind_address": "127.0.0.1",
        "web_port": 8080,
        "guacd_bind_address": "127.0.0.1",
        "guacd_port": 4822,
        "context_path": "/guacamole/",
        "admin_user": "guacadmin",
        "admin_password_file": "",
    },
    "database": {
        "engine": "postgresql",
        "deployment": "compose",
        "host": "127.0.0.1",
        "port": 54321,
        "name": "guacamole_db",
        "username": "guacamole_user",
        "password_file": "",
    },
    "proxy": {"provider": "none", "deployment": "native", "server_name": "localhost"},
    "tls": {"mode": "off"},
    "firewall": {"mode": "none", "provider": "auto", "allow_vnc": False},
    "features": {},
    "custom": {"enabled": False, "packages": {}, "desktop_session_command": []},
}


BASE_PACKAGES: dict[str, tuple[str, ...]] = {
    "debian": ("ca-certificates", "dbus-x11"),
    "rhel": ("ca-certificates", "dbus-x11"),
    "fedora": ("ca-certificates", "dbus-x11"),
    "arch": ("ca-certificates", "dbus"),
    "suse": ("ca-certificates", "dbus-1-x11"),
    "alpine": ("ca-certificates", "dbus-x11"),
}


CONTAINER_PACKAGES: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "docker": {
        "debian": (
            ("docker.io", "docker-compose-v2"),
            ("docker.io", "docker-compose-plugin"),
            ("docker.io", "docker-compose"),
        ),
        "arch": (("docker", "docker-compose"),),
        "alpine": (("docker", "docker-cli-compose"),),
    },
    "podman": {
        "debian": (("podman", "podman-compose"),),
        "rhel": (("podman", "podman-compose"),),
        "fedora": (("podman", "podman-compose"),),
        "arch": (("podman", "podman-compose"),),
        "suse": (("podman", "podman-compose"),),
        "alpine": (("podman", "podman-compose"),),
    },
}


PROXY_PACKAGES: dict[str, dict[str, str]] = {
    "nginx": {key: "nginx" for key in ("debian", "rhel", "fedora", "arch", "suse", "alpine")},
    "apache": {
        "debian": "apache2",
        "rhel": "httpd",
        "fedora": "httpd",
        "arch": "apache",
        "suse": "apache2",
        "alpine": "apache2",
    },
}


def _merge(base: MutableMapping[str, Any], override: Mapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), MutableMapping):
            _merge(base[key], value)  # type: ignore[index]
        else:
            base[key] = copy.deepcopy(value)
    return base


def _normalized(facts: SystemFacts, config: InstallerConfig) -> dict[str, Any]:
    data = _merge(copy.deepcopy(DEFAULT_CONFIG), config.to_dict())
    target = data["target"]
    user = str(target.get("desktop_user") or data.get("target_user") or "").strip()
    if not user:
        sudo_user = os.environ.get("SUDO_USER", "")
        if sudo_user and sudo_user != "root":
            user = sudo_user
        elif facts.current_user and facts.current_user != "root":
            user = facts.current_user
        else:
            user = "guacdesk"
            target["create_user"] = True
    target["desktop_user"] = user

    profile = str(data.get("profile", "hybrid"))
    if profile == "vnc-only":
        data["guacamole"].update({"enabled": False, "deployment": "none"})
        data["database"].update({"engine": "none", "deployment": "none"})
        data["proxy"].update({"provider": "none", "deployment": "none"})
    elif profile == "external":
        data["guacamole"].update({"enabled": False, "deployment": "external"})
        data["database"]["deployment"] = "external"
        data["proxy"].update({"provider": "external", "deployment": "external"})
    elif profile == "native":
        data["guacamole"]["deployment"] = "native"
        data["database"]["deployment"] = "native"
    if data["guacamole"].get("deployment") == "container":
        data["guacamole"]["deployment"] = "compose"
    if data["database"].get("deployment") == "container":
        data["database"]["deployment"] = "compose"
    return data


def _support_level(value: str) -> SupportLevel:
    try:
        return SupportLevel(value)
    except ValueError:
        return SupportLevel.UNSUPPORTED


def list_supported() -> Mapping[str, Any]:
    return {
        "policy": "No exact tuple is verified until it passes clean-VM install-twice, reboot, and end-to-end tests.",
        "platforms": [
            {"id": item.key, "tier": item.tier, "reason": item.reason}
            for item in PLATFORMS.values()
        ],
        "desktops": [
            {"id": item.key, "tier": item.tier, "reason": item.reason}
            for item in DESKTOPS.values()
        ],
        "vnc": [
            {"id": item.key, "mode": item.mode, "tier": item.tier, "reason": item.reason}
            for item in VNCS.values()
        ],
        "deployments": [
            {"id": item.name, "tier": item.tier, "description": item.description}
            for item in DEPLOYMENTS.values()
        ],
    }


def build_plan(facts: SystemFacts, config: InstallerConfig) -> Plan:
    data = _normalized(facts, config)
    effective = InstallerConfig(1, data, config.source)
    validation = validate_combination(facts, effective)
    reasons = list(validation.reasons)
    warnings = list(validation.warnings)
    errors = list(validation.errors)

    profile = str(data["profile"])
    guac_deployment = str(data["guacamole"].get("deployment", "compose"))
    if profile == "native" or guac_deployment == "native":
        errors.append(
            "Native Guacamole is intentionally gated until an exact distro/version Tomcat and build adapter is selected; use hybrid/container or external."
        )
    if profile not in {"hybrid", "native", "vnc-only", "external"}:
        errors.append(f"unknown profile: {profile}")

    platform = platform_from_facts(facts)
    if platform.package_manager != "custom" and platform.package_manager not in facts.package_managers:
        errors.append(
            f"platform {platform.key} requires {platform.package_manager}, but detected package managers are {', '.join(facts.package_managers) or 'none'}"
        )
    requested_pm = str(data["target"].get("package_manager", "auto"))
    if requested_pm not in {"auto", platform.package_manager}:
        errors.append(
            f"target.package_manager={requested_pm} conflicts with platform provider {platform.package_manager}"
        )
    requested_init = str(data["target"].get("init_system", "auto"))
    if requested_init not in {"auto", facts.init_system}:
        errors.append(
            f"target.init_system={requested_init} conflicts with detected init system {facts.init_system}"
        )
    requested_arch = str(data["target"].get("architecture", "auto"))
    if requested_arch not in {"auto", facts.architecture}:
        errors.append(
            f"target.architecture={requested_arch} conflicts with detected architecture {facts.architecture}"
        )
    desktop_name = str(data["desktop"].get("environment", "xfce"))
    vnc_name = str(data["vnc"].get("implementation", "tigervnc"))
    try:
        desktop = resolve_desktop(desktop_name)
        vnc = resolve_vnc(vnc_name)
    except KeyError as exc:
        errors.append(str(exc))
        desktop = resolve_desktop("custom")
        vnc = resolve_vnc("custom")

    runtime = _resolve_runtime(str(data["target"].get("container_runtime", "auto")), platform.key)
    data["target"]["container_runtime"] = runtime
    if guac_deployment == "compose" and runtime == "none":
        errors.append("container Guacamole requires Docker or Podman Compose")
    if guac_deployment == "compose":
        warnings.append(
            "Guacamole container tags are version-pinned but not yet pinned to independently reviewed per-architecture OCI digests; production promotion requires digest locking"
        )

    proxy = str(data["proxy"].get("provider", "none"))
    if proxy not in {"none", "external", "nginx", "apache", "caddy"}:
        errors.append(f"unsupported proxy provider: {proxy}")
    tls_mode = str(data["tls"].get("mode", "off"))
    if tls_mode not in {"off", "external"}:
        errors.append(
            f"TLS mode {tls_mode!r} is represented by the schema but not safely automated yet; use an external ACME/TLS provider."
        )
    guacd_bind = str(data["guacamole"].get("guacd_bind_address", "127.0.0.1"))
    if guac_deployment == "compose" and guacd_bind not in {"127.0.0.1", "::1"}:
        errors.append("guacd has no authentication and must remain bound to loopback")
    web_bind = str(data["guacamole"].get("web_bind_address", "127.0.0.1"))
    proxy_bind = str(data["proxy"].get("listen_address", "127.0.0.1"))
    if web_bind not in {"127.0.0.1", "::1"} and tls_mode == "off":
        errors.append("a non-loopback Guacamole listener requires externally managed TLS")
    if proxy not in {"none", "external"} and proxy_bind not in {"127.0.0.1", "::1"} and tls_mode == "off":
        errors.append("a non-loopback reverse proxy listener requires TLS; use tls.mode=external with a reviewed terminator")
    firewall_mode = str(data["firewall"].get("mode", "none"))
    if firewall_mode not in {"none", "report-only"}:
        errors.append(
            "Automatic firewall mutation is gated; choose firewall.mode=none and apply the reported 80/443 rules in the active policy manager."
        )
    requested_features = [
        str(name)
        for name, enabled in data.get("features", {}).items()
        if bool(enabled)
    ] if isinstance(data.get("features", {}), Mapping) else []
    if requested_features:
        errors.append(
            "optional feature providers are not implemented for: "
            + ", ".join(sorted(requested_features))
        )
    display_manager = str(data["desktop"].get("display_manager", "none"))
    if display_manager not in {"none", "auto"}:
        warnings.append(
            f"display manager {display_manager} is treated as an existing external prerequisite and will not be replaced or reconfigured"
        )

    install_root = Path(str(data["target"].get("install_root", "/opt/urd")))
    if not install_root.is_absolute() or ".." in install_root.parts:
        errors.append("target.install_root must be an absolute normalized path")
    user = str(data["target"]["desktop_user"])
    if not _valid_user(user):
        errors.append("target.desktop_user is not a valid Linux user name")

    if str(data["vnc"].get("bind_address")) not in {"127.0.0.1", "::1"}:
        if not bool(data["firewall"].get("allow_vnc", False)):
            errors.append("non-loopback VNC requires firewall.allow_vnc=true and an explicitly reviewed network policy")
    if vnc.key in {"tigervnc", "tightvnc"}:
        expected_port = 5900 + int(data["vnc"].get("display_number", 1))
        if int(data["vnc"].get("port", expected_port)) != expected_port:
            errors.append(
                f"{vnc.key} display :{data['vnc'].get('display_number', 1)} requires vnc.port={expected_port}"
            )
    scope = str(data["vnc"].get("service_scope", "system"))
    if bool(data["vnc"].get("enabled", True)) and vnc.key not in {"external", "custom"} and scope != "system":
        errors.append("the current service provider manages a hardened system unit running as the desktop user; select vnc.service_scope=system")
    if vnc.key == "wayvnc":
        errors.append("wayvnc requires an active wlroots user-session provider, which is reserved but not safely automated")
    if vnc.key == "x11vnc" and str(data["vnc"].get("authentication", "password")) != "none":
        errors.append("x11vnc password creation cannot be automated without exposing the secret in argv; use loopback-only authentication=none or a reviewed custom rfbauth provider")
    if vnc.key in {"tigervnc", "tightvnc"} and str(data["vnc"].get("authentication", "password")) != "password":
        errors.append(f"{vnc.key} built-in virtual-session provider requires password authentication")
    if str(data["database"].get("engine", "postgresql")) != "postgresql" and guac_deployment == "compose":
        errors.append("the built-in Compose provider currently supports PostgreSQL; use an external database provider for other engines")
    if str(data["guacamole"].get("admin_user", "guacadmin")) != "guacadmin" and guac_deployment == "compose":
        errors.append("the Compose bootstrap can rotate only the schema-created guacadmin account; create additional administrators through Guacamole")
    if proxy == "caddy":
        errors.append("Caddy rendering is available for external integration, but the built-in provider does not modify the operator-owned Caddyfile import graph")

    steps: list[PlanStep] = []
    if not errors:
        home = _home_for(user)
        packages = _package_candidates(platform.key, desktop, vnc, data, runtime, proxy)
        steps.extend(
            [
                PlanStep(
                    "target-user",
                    f"Ensure desktop account {user} exists without changing its password",
                    "platform",
                    "ensure-user",
                    details={"user": user, "home": str(home), "create": bool(data["target"].get("create_user"))},
                ),
                PlanStep(
                    "packages",
                    f"Install packages using {platform.package_manager}",
                    platform.key,
                    "ensure-packages",
                    details={"candidate_groups": packages},
                ),
            ]
        )
        if bool(data["vnc"].get("enabled", True)) and vnc.key not in {"external", "custom"}:
            steps.extend(
                [
                    PlanStep(
                        "vnc-config",
                        f"Configure {vnc.key} for {user} on loopback",
                        vnc.key,
                        "configure",
                        details={"home": str(home)},
                    ),
                    PlanStep(
                        "vnc-service",
                        f"Install and enable the managed {vnc.key} service",
                        facts.init_system,
                        "ensure-service",
                        details={"service": f"urd-vnc-{user}"},
                    ),
                ]
            )
        if guac_deployment == "compose":
            steps.extend(
                [
                    PlanStep(
                        "guacamole-secrets",
                        "Create or import root-only Guacamole and PostgreSQL secrets",
                        "secrets",
                        "ensure-files",
                    ),
                    PlanStep(
                        "guacamole-schema",
                        "Generate the PostgreSQL schema from the pinned Guacamole image",
                        "guacamole",
                        "generate-schema",
                    ),
                    PlanStep(
                        "guacamole-compose",
                        f"Validate and start the Guacamole stack with {runtime} Compose",
                        "compose",
                        "ensure-stack",
                    ),
                    PlanStep(
                        "database-password",
                        "Reconcile the PostgreSQL role password when its secret changes",
                        "postgresql",
                        "rotate-database-password",
                    ),
                    PlanStep(
                        "guacamole-admin",
                        "Replace the upstream default administrator password with the configured secret",
                        "postgresql",
                        "rotate-admin-password",
                    ),
                ]
            )
            if bool(data["vnc"].get("enabled", True)) and vnc.key not in {"external", "custom", "wayvnc"}:
                steps.append(
                    PlanStep(
                        "guacamole-connection",
                        "Provision an idempotent local VNC connection for the administrator",
                        "postgresql",
                        "ensure-connection",
                    )
                )
        if proxy not in {"none", "external"}:
            steps.append(
                PlanStep(
                    "reverse-proxy",
                    f"Install, validate, and enable an isolated {proxy} site",
                    proxy,
                    "ensure-proxy",
                )
            )

    support = SupportLevel.UNSUPPORTED if errors else _support_level(validation.tier)
    summary = (
        f"{platform.key}/{desktop.key}/{vnc.key}/{profile} plan with {len(steps)} steps"
        if not errors
        else "The selected combination is not safely applicable"
    )
    return Plan(
        steps=tuple(steps),
        support_level=support,
        summary=summary,
        warnings=tuple(dict.fromkeys(warnings)),
        reasons=tuple(dict.fromkeys(reasons + errors)),
        metadata={
            "config": data,
            "platform": platform.key,
            "desktop": desktop.key,
            "vnc": vnc.key,
            "runtime": runtime,
            "install_root": str(install_root),
        },
    )


def _resolve_runtime(requested: str, family: str) -> str:
    if requested in {"docker", "podman", "none"}:
        return requested
    if shutil.which("docker"):
        return "docker"
    if shutil.which("podman"):
        return "podman"
    return "docker" if family in {"debian", "arch", "alpine"} else "podman"


def _package_candidates(platform: str, desktop: Any, vnc: Any, data: Mapping[str, Any], runtime: str, proxy: str) -> list[list[list[str]]]:
    groups: list[list[list[str]]] = []
    base = BASE_PACKAGES.get(platform, ())
    if base:
        groups.append([list(base)])
    if bool(data["desktop"].get("enabled", True)) and desktop.key not in {"none", "auto", "custom"}:
        groups.append([list(item) for item in desktop.package_candidates(platform)])
    if bool(data["vnc"].get("enabled", True)) and vnc.key not in {"external", "custom"}:
        groups.append([list(item) for item in vnc.package_candidates(platform)])
    if str(data["guacamole"].get("deployment")) == "compose":
        packages = CONTAINER_PACKAGES.get(runtime, {}).get(platform)
        if packages:
            groups.append([list(candidate) for candidate in packages])
    if proxy in PROXY_PACKAGES and platform in PROXY_PACKAGES[proxy]:
        groups.append([[PROXY_PACKAGES[proxy][platform]]])
    custom = data.get("custom", {})
    if isinstance(custom, Mapping) and bool(custom.get("enabled")):
        package_map = custom.get("packages", {})
        if isinstance(package_map, Mapping):
            extra = [str(item) for values in package_map.values() if isinstance(values, list) for item in values]
            if extra:
                groups.append([extra])
    return groups


def _valid_user(value: str) -> bool:
    if not value or value in {".", ".."} or len(value) > 32 or not value.isascii():
        return False
    if not (value[0].islower() or value[0] == "_"):
        return False
    return all(char.islower() or char.isdigit() or char in "_.-" for char in value[1:])


def _home_for(user: str) -> Path:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return Path("/home") / user


def apply_plan(plan: Plan, executor: Any) -> OperationResult:
    if plan.support_level is SupportLevel.UNSUPPORTED:
        raise ExecutionError("refusing to apply an unsupported plan: " + "; ".join(plan.reasons))
    data = copy.deepcopy(dict(plan.metadata["config"]))
    platform_key = str(plan.metadata["platform"])
    runtime = str(plan.metadata["runtime"])
    install_root = Path(str(plan.metadata["install_root"]))
    user = str(data["target"]["desktop_user"])
    home = _home_for(user)
    changed = False
    completed: list[str] = []

    for step in plan.steps:
        if step.id == "target-user":
            changed |= _ensure_user(executor, user, home, bool(step.details.get("create")))
            home = _home_for(user)
        elif step.id == "packages":
            changed |= _ensure_packages(executor, platform_key, step.details["candidate_groups"])
        elif step.id == "vnc-config":
            changed |= _ensure_vnc(executor, data, user, home)
        elif step.id == "vnc-service":
            changed |= _ensure_vnc_service(executor, data, user, home, str(step.provider))
        elif step.id == "guacamole-secrets":
            changed |= _ensure_secrets(executor, data, install_root)
        elif step.id == "guacamole-schema":
            changed |= _ensure_schema(executor, data, install_root, runtime)
        elif step.id == "guacamole-compose":
            changed |= _ensure_compose(executor, data, install_root, runtime)
        elif step.id == "database-password":
            changed |= _ensure_database_password(executor, data, install_root, runtime)
        elif step.id == "guacamole-admin":
            changed |= _ensure_admin_password(executor, data, install_root, runtime)
        elif step.id == "guacamole-connection":
            changed |= _ensure_connection(executor, data, install_root, runtime)
        elif step.id == "reverse-proxy":
            changed |= _ensure_proxy(executor, data, platform_key)
        else:
            raise ExecutionError(f"unknown plan step: {step.id}")
        completed.append(step.id)

    if changed:
        _save_state(executor, plan, completed)
    return OperationResult(
        ok=True,
        changed=changed,
        summary="Applied {} provider tasks".format(len(completed)),
        details={"completed": completed, "install_root": str(install_root)},
        warnings=plan.warnings,
    )


def _ensure_user(executor: Any, user: str, home: Path, create: bool) -> bool:
    try:
        pwd.getpwnam(user)
        return False
    except KeyError:
        pass
    if not create:
        raise ExecutionError(f"desktop user {user!r} does not exist and target.create_user=false")
    if executor.dry_run:
        executor.run(("useradd", "--create-home", "--home-dir", str(home), "--shell", "/bin/bash", user), require_root=True, changed=True)
    elif shutil.which("useradd"):
        executor.run(("useradd", "--create-home", "--home-dir", str(home), "--shell", "/bin/bash", user), require_root=True, changed=True)
    elif shutil.which("adduser"):
        executor.run(("adduser", "-D", "-h", str(home), user), require_root=True, changed=True)
    else:
        raise ExecutionError("neither useradd nor adduser is available")
    return True


def _ensure_packages(executor: Any, platform_key: str, groups: Sequence[Sequence[Sequence[str]]]) -> bool:
    manager = PACKAGE_MANAGERS[PLATFORMS[platform_key].package_manager]
    selected: list[str] = []
    for alternatives in groups:
        candidate = _select_package_candidate(executor, manager, alternatives)
        selected.extend(candidate)
    selected = list(dict.fromkeys(selected))
    missing = [name for name in selected if not _package_installed(executor, manager.name, name)]
    if missing:
        executor.run(manager.update_argv(), require_root=True, changed=True)
        env = {"DEBIAN_FRONTEND": "noninteractive"} if manager.name == "apt" else None
        executor.run(manager.install_argv(missing), require_root=True, env=env, changed=True)
    return bool(missing)


def _package_installed(executor: Any, manager: str, package: str) -> bool:
    if executor.dry_run:
        return False
    commands = {
        "apt": ("dpkg-query", "-W", "-f=${Status}", package),
        "dnf": ("rpm", "-q", package),
        "pacman": ("pacman", "-Q", package),
        "zypper": ("rpm", "-q", package),
        "apk": ("apk", "info", "-e", package),
    }
    argv = commands.get(manager)
    if not argv:
        return False
    result = executor.run(argv, check=False)
    if manager == "apt":
        return result.returncode == 0 and "install ok installed" in result.stdout
    return result.returncode == 0


def _select_package_candidate(executor: Any, manager: Any, alternatives: Sequence[Sequence[str]]) -> tuple[str, ...]:
    for candidate in alternatives:
        if all(executor.run(manager.probe_argv(name), check=False).returncode == 0 for name in candidate):
            return tuple(candidate)
    rendered = " or ".join(" ".join(item) for item in alternatives)
    raise ExecutionError(f"no registered package candidate is available: {rendered}")


def _ensure_vnc(executor: Any, data: MutableMapping[str, Any], user: str, home: Path) -> bool:
    vnc = data["vnc"]
    provider = str(vnc["implementation"])
    source_password_file = vnc.get("password_file")
    desktop = resolve_desktop(str(data["desktop"]["environment"]))
    config_dir = home / ".config" / "urd"
    auth_file = config_dir / "vnc.passwd"
    vnc["password_file"] = str(auth_file)
    uid, gid = _user_identity(user)
    owner: Any = user if uid is not None else None
    group: Any = gid
    changed = executor.ensure_directory(config_dir, mode=0o700, owner=owner, group=group).changed
    changed |= executor.install_file(config_dir / "vnc.conf", render_vnc_config(data, home), mode=0o600, owner=owner, group=group).changed
    changed |= executor.install_file(Path("/usr/local/libexec/urd-vnc-session"), render_vnc_launcher(), mode=0o755, owner=0, group=0).changed

    if provider in {"tigervnc", "tightvnc"}:
        changed |= executor.ensure_directory(home / ".vnc", mode=0o700, owner=owner, group=group).changed
        changed |= executor.install_file(home / ".vnc" / "xstartup", render_xstartup(xstartup_argv(desktop.key)), mode=0o700, owner=owner, group=group).changed
    elif provider == "wayvnc":
        raise ExecutionError("wayvnc requires an active wlroots user session and is not automatically service-managed")

    if str(vnc.get("authentication", "password")) == "password":
        managed_secret = Path(str(data["target"]["install_root"])) / "secrets" / "vnc_password"
        changed |= executor.ensure_directory(managed_secret.parent, mode=0o700, owner=0, group=0).changed
        secret = _read_or_create_secret(executor, source_password_file, managed_secret, length=8, alphabet=string.ascii_letters + string.digits)
        if not secret.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in secret) or not 6 <= len(secret) <= 8:
            raise ExecutionError("VNC password must be 6-8 printable ASCII characters (legacy VNC uses at most 8)")
        executor.register_secret(secret)
        data.setdefault("_runtime", {})["vnc_password"] = secret
        changed |= executor.install_file(
            managed_secret,
            secret + "\n",
            mode=0o600,
            owner=0,
            group=0,
            backup=False,
        ).changed
        fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        fingerprint_path = config_dir / "vnc.passwd.sha256"
        existing_fingerprint = ""
        if fingerprint_path.exists():
            existing_fingerprint = _read_managed_text(executor, fingerprint_path).strip()
        if auth_file.exists() and secrets.compare_digest(existing_fingerprint, fingerprint):
            data.setdefault("_runtime", {})["vnc_restart_required"] = changed
            return changed
        passwd_tool = _find_binary(resolve_vnc(provider).password_binaries)
        if executor.dry_run and not passwd_tool:
            binaries = resolve_vnc(provider).password_binaries
            passwd_tool = binaries[0] if binaries else None
        if provider == "x11vnc" and passwd_tool and Path(passwd_tool).name == "x11vnc":
            raise ExecutionError("x11vnc password generation would expose the secret in argv; provide a prebuilt rfbauth file through a custom provider")
        if not passwd_tool:
            raise ExecutionError(f"no password utility found for {provider}")
        result = executor.run((passwd_tool, str(auth_file)), require_root=True, input_text=f"{secret}\n{secret}\nn\n", changed=True)
        if not executor.dry_run:
            executor.run(("chmod", "0600", str(auth_file)), require_root=True, changed=True)
            executor.run(("chown", f"{user}:{gid if gid is not None else user}", str(auth_file)), require_root=True, changed=True)
        changed |= executor.install_file(fingerprint_path, fingerprint + "\n", mode=0o600, owner=owner, group=group, backup=False).changed
        changed = changed or result.changed
    data.setdefault("_runtime", {})["vnc_restart_required"] = changed
    return changed


def _user_identity(user: str) -> tuple[Optional[int], Optional[int]]:
    try:
        entry = pwd.getpwnam(user)
        return entry.pw_uid, entry.pw_gid
    except KeyError:
        return None, None


def _find_binary(names: Iterable[str]) -> Optional[str]:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _ensure_vnc_service(executor: Any, data: Mapping[str, Any], user: str, home: Path, init_system: str) -> bool:
    provider = str(data["vnc"]["implementation"])
    runtime_data = data.get("_runtime", {}) if isinstance(data.get("_runtime", {}), Mapping) else {}
    restart_required = bool(runtime_data.get("vnc_restart_required"))
    service = f"urd-vnc-{user}"
    if init_system == "systemd":
        result = executor.install_file(Path("/etc/systemd/system") / f"{service}.service", render_systemd_vnc_unit(user, home, provider), mode=0o644, owner=0, group=0)
        enabled = executor.run(("systemctl", "is-enabled", "--quiet", f"{service}.service"), require_root=True, check=False).returncode == 0
        active = executor.run(("systemctl", "is-active", "--quiet", f"{service}.service"), require_root=True, check=False).returncode == 0
        try:
            if result.changed:
                executor.run(("systemctl", "daemon-reload"), require_root=True, changed=True)
            if not enabled:
                executor.run(("systemctl", "enable", f"{service}.service"), require_root=True, changed=True)
            if (result.changed or restart_required) and active:
                executor.run(("systemctl", "restart", f"{service}.service"), require_root=True, changed=True)
            elif not active:
                executor.run(("systemctl", "start", f"{service}.service"), require_root=True, changed=True)
        except ExecutionError:
            if not executor.dry_run:
                _restore_file(executor, result)
                executor.run(("systemctl", "daemon-reload"), require_root=True, check=False, changed=True)
                if active:
                    executor.run(("systemctl", "restart", f"{service}.service"), require_root=True, check=False, changed=True)
            raise
        return result.changed or restart_required or not enabled or not active
    if init_system == "openrc":
        result = executor.install_file(Path("/etc/init.d") / service, render_openrc_vnc_service(user, home, provider), mode=0o755, owner=0, group=0)
        enabled = executor.run(("rc-update", "show", "default"), require_root=True, check=False).stdout.find(service) >= 0
        active = executor.run(("rc-service", service, "status"), require_root=True, check=False).returncode == 0
        if not enabled:
            executor.run(("rc-update", "add", service, "default"), require_root=True, changed=True)
        if (result.changed or restart_required) and active:
            executor.run(("rc-service", service, "restart"), require_root=True, changed=True)
        elif not active:
            executor.run(("rc-service", service, "start"), require_root=True, changed=True)
        return result.changed or restart_required or not enabled or not active
    raise ExecutionError(f"init system {init_system!r} has no managed VNC service renderer")


def _read_or_create_secret(executor: Any, source: Any, managed: Path, length: int = 32, alphabet: str = string.ascii_letters + string.digits + "-_") -> str:
    if executor.dry_run:
        value = alphabet[0] * length
        executor.register_secret(value)
        return value
    source_path = Path(str(source)) if source else None
    if source_path and source_path != managed:
        return _read_secret_file(source_path, executor)
    if managed.exists():
        return _read_secret_file(managed, executor)
    value = "".join(secrets.choice(alphabet) for _ in range(length))
    executor.register_secret(value)
    executor.install_file(managed, value + "\n", mode=0o600, owner=0, group=0, backup=False)
    return value


def _read_secret_file(path: Path, executor: Any = None) -> str:
    try:
        info = path.lstat()
    except PermissionError:
        if executor is None:
            raise ExecutionError(f"cannot inspect permission-restricted secret file {path}")
        raw = executor.read_file(path, require_regular=True, no_symlink=True)
        mode_result = executor.run(("stat", "-c", "%a", str(path)), require_root=True)
        try:
            mode = int(mode_result.stdout.strip(), 8)
        except ValueError as exc:
            raise ExecutionError(f"cannot determine permissions of secret file {path}") from exc
        if mode & 0o077:
            raise ExecutionError(f"secret file must not be accessible by group/others: {path}")
        try:
            value = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise ExecutionError(f"secret file is not valid UTF-8: {path}") from exc
        if not value:
            raise ExecutionError(f"secret file is empty: {path}")
        return value
    except OSError as exc:
        raise ExecutionError(f"cannot read secret file {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExecutionError(f"secret path must be a regular non-symlink file: {path}")
    if info.st_mode & 0o077:
        raise ExecutionError(f"secret file must not be accessible by group/others: {path}")
    value = _read_managed_text(executor, path).rstrip("\r\n")
    if not value:
        raise ExecutionError(f"secret file is empty: {path}")
    return value


def _read_managed_text(executor: Any, path: Path) -> str:
    if executor is not None and hasattr(executor, "read_file"):
        value = executor.read_file(path)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
    try:
        return path.read_text(encoding="utf-8")
    except PermissionError:
        if executor is None:
            raise
        result = executor.run(("base64", str(path)), require_root=True)
        import base64

        return base64.b64decode(result.stdout.encode("ascii")).decode("utf-8")


def _ensure_secrets(executor: Any, data: MutableMapping[str, Any], install_root: Path) -> bool:
    secret_dir = install_root / "secrets"
    directory_result = executor.ensure_directory(secret_dir, mode=0o700, owner=0, group=0)
    db_source = data["database"].get("password_file")
    db_value = _read_or_create_secret(executor, db_source, secret_dir / "postgresql_password", 40)
    _validate_service_secret(db_value, "database", 16)
    executor.register_secret(db_value)
    result = executor.install_file(secret_dir / "postgresql_password", db_value + "\n", mode=0o600, owner=0, group=0, backup=False)
    data["database"]["password_file"] = str(secret_dir / "postgresql_password")
    admin_source = data["guacamole"].get("admin_password_file")
    admin_value = _read_or_create_secret(executor, admin_source, secret_dir / "guacamole_admin_password", 24)
    _validate_service_secret(admin_value, "Guacamole administrator", 16)
    executor.register_secret(admin_value)
    admin_result = executor.install_file(secret_dir / "guacamole_admin_password", admin_value + "\n", mode=0o600, owner=0, group=0, backup=False)
    data["guacamole"]["admin_password_file"] = str(secret_dir / "guacamole_admin_password")
    salt_path = secret_dir / "guacamole_admin_salt"
    if salt_path.exists():
        salt_hex = _read_managed_text(executor, salt_path).strip()
    else:
        salt_hex = secrets.token_bytes(32).hex()
    salt_result = executor.install_file(salt_path, salt_hex + "\n", mode=0o600, owner=0, group=0, backup=False)
    data.setdefault("_runtime", {})["database_password"] = db_value
    data.setdefault("_runtime", {})["database_password_changed"] = result.changed
    data.setdefault("_runtime", {})["admin_password"] = admin_value
    data.setdefault("_runtime", {})["admin_salt"] = salt_hex
    return directory_result.changed or result.changed or admin_result.changed or salt_result.changed


def _validate_service_secret(value: str, label: str, minimum: int) -> None:
    if len(value) < minimum:
        raise ExecutionError(f"{label} secret must contain at least {minimum} characters")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ExecutionError(f"{label} secret must be a single NUL-free line")


def _runtime_argv(runtime: str) -> tuple[str, ...]:
    if runtime not in {"docker", "podman"}:
        raise ExecutionError(f"unsupported container runtime: {runtime}")
    return (runtime,)


def _ensure_schema(executor: Any, data: Mapping[str, Any], install_root: Path, runtime: str) -> bool:
    schema_path = install_root / "initdb" / "001-guacamole-schema.sql"
    changed = False
    if not schema_path.exists():
        version = str(data["guacamole"]["version"])
        result = executor.run(
            _runtime_argv(runtime) + ("run", "--rm", f"guacamole/guacamole:{version}", "/opt/guacamole/bin/initdb.sh", "--postgresql"),
            require_root=True,
            changed=True,
            timeout=300,
        )
        if executor.dry_run:
            changed = True
        else:
            if "CREATE TABLE" not in result.stdout:
                raise ExecutionError("Guacamole image did not produce a PostgreSQL schema")
            changed |= executor.install_file(schema_path, result.stdout, mode=0o644, owner=0, group=0, backup=False).changed
    runtime_data = data.get("_runtime", {}) if isinstance(data.get("_runtime", {}), Mapping) else {}
    password = str(runtime_data.get("admin_password") or _read_secret_file(install_root / "secrets" / "guacamole_admin_password", executor))
    salt_hex = str(runtime_data.get("admin_salt") or _read_managed_text(executor, install_root / "secrets" / "guacamole_admin_salt").strip())
    admin_sql = _admin_password_sql(data, password, salt_hex)
    changed |= executor.install_file(
        install_root / "initdb" / "999-guacamole-admin-password.sql",
        admin_sql,
        mode=0o644,
        owner=0,
        group=0,
        backup=False,
    ).changed
    return changed


def _compose_command(runtime: str, executor: Any = None) -> tuple[str, ...]:
    if executor is not None and shutil.which(runtime):
        modern = executor.run((runtime, "compose", "version"), check=False)
        if modern.returncode == 0:
            return (runtime, "compose")
    legacy = f"{runtime}-compose"
    if shutil.which(legacy):
        return (legacy,)
    if runtime == "docker" and shutil.which("docker-compose"):
        return ("docker-compose",)
    if executor is not None and executor.dry_run:
        return (runtime, "compose")
    raise ExecutionError(f"no Compose frontend was found for {runtime}")


def _ensure_compose(executor: Any, data: Mapping[str, Any], install_root: Path, runtime: str) -> bool:
    compose_path = install_root / "compose.yaml"
    bind_script = install_root / "entrypoint.d" / "90-urd-http-bind.sh"
    compose_result = executor.install_file(compose_path, render_compose(data, install_root), mode=0o600, owner=0, group=0)
    bind_result = executor.install_file(bind_script, render_guacamole_http_bind(data), mode=0o555, owner=0, group=0)
    changed = compose_result.changed or bind_result.changed
    if runtime == "docker" and shutil.which("systemctl"):
        active = executor.run(("systemctl", "is-active", "--quiet", "docker"), require_root=True, check=False).returncode == 0
        if not active:
            executor.run(("systemctl", "enable", "--now", "docker"), require_root=True, changed=True)
            changed = True
    elif runtime == "docker" and shutil.which("rc-service"):
        active = executor.run(("rc-service", "docker", "status"), require_root=True, check=False).returncode == 0
        if not active:
            executor.run(("rc-update", "add", "docker", "default"), require_root=True, check=False, changed=True)
            executor.run(("rc-service", "docker", "start"), require_root=True, changed=True)
            changed = True
    command = _compose_command(runtime, executor)
    prefix = command + ("-p", "urd-guacamole", "-f", str(compose_path))
    try:
        executor.run(prefix + ("config", "--quiet"), require_root=True)
    except ExecutionError:
        if not executor.dry_run:
            _restore_file(executor, bind_result)
            _restore_file(executor, compose_result)
        raise
    running = executor.run(prefix + ("ps", "--status", "running", "--services"), require_root=True, check=False)
    services = {line.strip() for line in running.stdout.splitlines() if line.strip()}
    if changed or not {"postgres", "guacd", "guacamole"}.issubset(services):
        executor.run(prefix + ("up", "-d"), require_root=True, changed=True, timeout=600)
        return True
    return False


def _ensure_admin_password(executor: Any, data: Mapping[str, Any], install_root: Path, runtime: str) -> bool:
    runtime_data = data.get("_runtime", {}) if isinstance(data.get("_runtime", {}), Mapping) else {}
    password = str(runtime_data.get("admin_password") or _read_secret_file(install_root / "secrets" / "guacamole_admin_password", executor))
    executor.register_secret(password)
    salt_hex = str(runtime_data.get("admin_salt") or _read_managed_text(executor, install_root / "secrets" / "guacamole_admin_salt").strip())
    sql = _admin_password_sql(data, password, salt_hex)
    database = data["database"]
    compose = _compose_command(runtime, executor)
    compose_file = str(install_root / "compose.yaml")
    argv = compose + (
        "-p", "urd-guacamole", "-f", compose_file, "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
        "-U", str(database["username"]), "-d", str(database["name"]),
    )
    if executor.dry_run:
        executor.run(argv, require_root=True, input_text=sql, changed=True)
        return True
    last_error: Optional[Exception] = None
    for _ in range(12):
        try:
            result = executor.run(argv, require_root=True, input_text=sql, changed=True)
            return "UPDATE 0" not in result.stdout
        except ExecutionError as exc:
            last_error = exc
            time.sleep(2)
    raise ExecutionError(f"could not update the Guacamole administrator password: {last_error}")


def _ensure_database_password(
    executor: Any,
    data: Mapping[str, Any],
    install_root: Path,
    runtime: str,
) -> bool:
    runtime_data = data.get("_runtime", {}) if isinstance(data.get("_runtime", {}), Mapping) else {}
    if not bool(runtime_data.get("database_password_changed")):
        return False
    password = str(runtime_data.get("database_password") or _read_secret_file(install_root / "secrets" / "postgresql_password", executor))
    executor.register_secret(password)
    database = data["database"]
    username = str(database["username"])
    if not _valid_identifier(username):
        raise ExecutionError("database.username contains unsupported characters")
    sql = (
        "ALTER ROLE "
        + _sql_identifier(username)
        + " WITH PASSWORD "
        + _sql_literal(password)
        + ";\n"
    )
    compose = _compose_command(runtime, executor)
    prefix = compose + (
        "-p", "urd-guacamole", "-f", str(install_root / "compose.yaml")
    )
    argv = prefix + (
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
        "-U", username, "-d", str(database["name"]),
    )
    last_error: Optional[Exception] = None
    for _ in range(12 if not executor.dry_run else 1):
        try:
            executor.run(argv, require_root=True, input_text=sql, changed=True)
            executor.run(prefix + ("restart", "guacamole"), require_root=True, changed=True)
            return True
        except ExecutionError as exc:
            last_error = exc
            if not executor.dry_run:
                time.sleep(2)
    raise ExecutionError(f"could not reconcile the PostgreSQL role password: {last_error}")


def _valid_identifier(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char in "_.-@" for char in value)


def _admin_password_sql(data: Mapping[str, Any], password: str, salt_hex: str) -> str:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise ExecutionError("managed Guacamole administrator salt is invalid") from exc
    if len(salt) != 32:
        raise ExecutionError("managed Guacamole administrator salt must be 32 bytes")
    digest = _guacamole_password_hash(password, salt)
    user = str(data["guacamole"].get("admin_user", "guacadmin"))
    if not _valid_identifier(user):
        raise ExecutionError("guacamole.admin_user contains unsupported characters")
    return f"""UPDATE guacamole_user
SET password_salt = decode('{salt.hex()}', 'hex'),
    password_hash = decode('{digest}', 'hex'),
    password_date = CURRENT_TIMESTAMP,
    disabled = FALSE,
    expired = FALSE
WHERE entity_id = (
    SELECT entity_id FROM guacamole_entity WHERE name = '{user}' AND type = 'USER'
)
AND (
    password_salt IS DISTINCT FROM decode('{salt.hex()}', 'hex')
    OR password_hash IS DISTINCT FROM decode('{digest}', 'hex')
    OR disabled IS DISTINCT FROM FALSE
    OR expired IS DISTINCT FROM FALSE
);
"""


def _guacamole_password_hash(password: str, salt: bytes) -> str:
    if len(salt) != 32:
        raise ValueError("Guacamole password salts must contain 32 bytes")
    return hashlib.sha256(
        password.encode("utf-8") + salt.hex().upper().encode("ascii")
    ).hexdigest()


def _sql_literal(value: str) -> str:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ExecutionError("SQL-provisioned values must be single NUL-free lines")
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    if not _valid_identifier(value):
        raise ExecutionError("invalid SQL identifier")
    return '"' + value.replace('"', '""') + '"'


def _ensure_connection(
    executor: Any,
    data: Mapping[str, Any],
    install_root: Path,
    runtime: str,
) -> bool:
    vnc = data["vnc"]
    target = data["target"]
    admin = str(data["guacamole"].get("admin_user", "guacadmin"))
    connection_name = f"Local desktop ({target['desktop_user']})"
    hostname = "127.0.0.1"
    port = str(vnc["port"])
    password_sql = ""
    if str(vnc.get("authentication", "password")) == "password":
        runtime_data = data.get("_runtime", {}) if isinstance(data.get("_runtime", {}), Mapping) else {}
        password = str(runtime_data.get("vnc_password") or _read_secret_file(install_root / "secrets" / "vnc_password", executor))
        executor.register_secret(password)
        password_sql = f"""
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'password', {_sql_literal(password)} FROM selected
ON CONFLICT (connection_id, parameter_name) DO UPDATE
SET parameter_value = EXCLUDED.parameter_value
WHERE guacamole_connection_parameter.parameter_value IS DISTINCT FROM EXCLUDED.parameter_value;
"""
    else:
        password_sql = """
DELETE FROM guacamole_connection_parameter
WHERE parameter_name = 'password'
  AND connection_id IN (SELECT connection_id FROM selected);
"""
    sql = f"""BEGIN;
INSERT INTO guacamole_connection (connection_name, protocol)
SELECT {_sql_literal(connection_name)}, 'vnc'
WHERE NOT EXISTS (
    SELECT 1 FROM guacamole_connection
    WHERE connection_name = {_sql_literal(connection_name)} AND parent_id IS NULL
);

CREATE TEMP TABLE selected ON COMMIT DROP AS
SELECT connection_id FROM guacamole_connection
WHERE connection_name = {_sql_literal(connection_name)} AND parent_id IS NULL
ORDER BY connection_id LIMIT 1;

INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, parameter_name, parameter_value
FROM selected CROSS JOIN (VALUES
    ('hostname', {_sql_literal(hostname)}),
    ('port', {_sql_literal(port)})
) AS desired(parameter_name, parameter_value)
ON CONFLICT (connection_id, parameter_name) DO UPDATE
SET parameter_value = EXCLUDED.parameter_value
WHERE guacamole_connection_parameter.parameter_value IS DISTINCT FROM EXCLUDED.parameter_value;
{password_sql}
INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)
SELECT entity.entity_id, selected.connection_id, 'READ'
FROM selected
JOIN guacamole_entity entity ON entity.name = {_sql_literal(admin)} AND entity.type = 'USER'
ON CONFLICT DO NOTHING;
COMMIT;
"""
    database = data["database"]
    compose = _compose_command(runtime, executor)
    argv = compose + (
        "-p", "urd-guacamole", "-f", str(install_root / "compose.yaml"), "exec", "-T", "postgres",
        "psql", "-v", "ON_ERROR_STOP=1", "-U", str(database["username"]),
        "-d", str(database["name"]),
    )
    result = executor.run(
        argv,
        require_root=True,
        input_text=sql,
        changed=True,
    )
    changed_markers = ("INSERT 0 1", "UPDATE 1", "DELETE 1")
    return executor.dry_run or any(marker in result.stdout for marker in changed_markers)


def _ensure_proxy(executor: Any, data: Mapping[str, Any], platform_key: str) -> bool:
    provider = str(data["proxy"]["provider"])
    if provider == "nginx":
        path = Path("/etc/nginx/vhosts.d/urd-guacamole.conf") if platform_key == "suse" else Path("/etc/nginx/conf.d/urd-guacamole.conf")
        result = executor.install_file(path, render_nginx(data), mode=0o644, owner=0, group=0)
        _validate_or_restore(executor, result, ("nginx", "-t"))
        _reload_service(executor, "nginx", result.changed)
        return result.changed
    if provider == "apache":
        if platform_key == "debian":
            path = Path("/etc/apache2/conf-enabled/urd-guacamole.conf")
        elif platform_key == "suse":
            path = Path("/etc/apache2/conf.d/urd-guacamole.conf")
        else:
            path = Path("/etc/httpd/conf.d/urd-guacamole.conf")
        result = executor.install_file(path, render_apache(data), mode=0o644, owner=0, group=0)
        if platform_key == "debian":
            executor.run(("a2enmod", "proxy", "proxy_http", "proxy_wstunnel", "headers"), require_root=True, changed=True)
        validator = ("apache2ctl", "configtest") if platform_key in {"debian", "suse"} else ("apachectl", "configtest")
        _validate_or_restore(executor, result, validator)
        _reload_service(executor, "apache2" if platform_key in {"debian", "suse"} else "httpd", result.changed)
        return result.changed
    if provider == "caddy":
        path = Path("/etc/caddy/conf.d/urd-guacamole.caddy")
        result = executor.install_file(path, render_caddy(data), mode=0o644, owner=0, group=0)
        _validate_or_restore(executor, result, ("caddy", "validate", "--config", str(path)))
        _reload_service(executor, "caddy", result.changed)
        return result.changed
    return False


def _validate_or_restore(executor: Any, result: Any, validator: Sequence[str]) -> None:
    try:
        executor.run(tuple(validator), require_root=True)
    except ExecutionError:
        if not executor.dry_run:
            _restore_file(executor, result)
        raise


def _restore_file(executor: Any, result: Any) -> None:
    if result.backup_path:
        executor.run(("mv", "-f", str(result.backup_path), str(result.path)), require_root=True, changed=True)
    elif result.changed:
        executor.run(("rm", "-f", str(result.path)), require_root=True, changed=True)


def _reload_service(executor: Any, service: str, changed: bool) -> None:
    if changed:
        if shutil.which("systemctl") or executor.dry_run:
            executor.run(("systemctl", "enable", "--now", service), require_root=True, changed=True)
            executor.run(("systemctl", "reload", service), require_root=True, changed=True)
        elif shutil.which("rc-service"):
            executor.run(("rc-update", "add", service, "default"), require_root=True, check=False, changed=True)
            executor.run(("rc-service", service, "restart"), require_root=True, changed=True)
        else:
            raise ExecutionError(f"no service manager is available for {service}")


def _save_state(executor: Any, plan: Plan, completed: Sequence[str]) -> None:
    state = {
        "schema_version": 1,
        "completed": list(completed),
        "plan": {
            "platform": plan.metadata.get("platform"),
            "desktop": plan.metadata.get("desktop"),
            "vnc": plan.metadata.get("vnc"),
            "runtime": plan.metadata.get("runtime"),
            "install_root": plan.metadata.get("install_root"),
        },
        "updated_at": int(time.time()),
    }
    try:
        executor.state.save(state)
    except (AttributeError, TypeError):
        pass


def verify(facts: SystemFacts, config: InstallerConfig, executor: Any) -> OperationResult:
    plan = build_plan(facts, config)
    if plan.support_level is SupportLevel.UNSUPPORTED:
        return OperationResult(False, summary="Configuration is unsupported", details={"reasons": list(plan.reasons)})
    data = plan.metadata["config"]
    user = str(data["target"]["desktop_user"])
    checks: dict[str, bool] = {}
    checks["user"] = executor.run(("getent", "passwd", user), check=False).returncode == 0
    service = f"urd-vnc-{user}.service"
    managed_vnc = bool(data["vnc"].get("enabled", True)) and str(data["vnc"].get("implementation")) not in {"external", "custom"}
    if managed_vnc:
        checks["vnc_port"] = _tcp_open(
            str(data["vnc"].get("bind_address", "127.0.0.1")),
            int(data["vnc"].get("port", 5901)),
        )
    if facts.init_system == "systemd" and managed_vnc:
        checks["vnc_service"] = executor.run(("systemctl", "is-active", "--quiet", service), check=False).returncode == 0
    elif facts.init_system == "openrc" and managed_vnc:
        checks["vnc_service"] = executor.run(("rc-service", service.removesuffix(".service"), "status"), check=False).returncode == 0
    install_root = Path(str(plan.metadata["install_root"]))
    if str(data["guacamole"].get("deployment")) == "compose":
        runtime = str(plan.metadata["runtime"])
        command = _compose_command(runtime, executor)
        checks["compose"] = executor.run(command + ("-p", "urd-guacamole", "-f", str(install_root / "compose.yaml"), "ps", "--status", "running"), require_root=True, check=False).returncode == 0
        checks["http"] = _tcp_open(str(data["guacamole"]["web_bind_address"]), int(data["guacamole"]["web_port"]))
        checks["guacd"] = _tcp_open(str(data["guacamole"]["guacd_bind_address"]), int(data["guacamole"]["guacd_port"]))
        checks["postgresql"] = _tcp_open("127.0.0.1", int(data["database"]["port"]))
    ok = all(checks.values()) if checks else False
    return OperationResult(ok, summary="All managed services are healthy" if ok else "One or more health checks failed", details={"checks": checks})


def doctor(facts: SystemFacts, config: InstallerConfig, executor: Any) -> OperationResult:
    plan = build_plan(facts, config)
    ports = {}
    if plan.metadata.get("config"):
        data = plan.metadata["config"]
        for label, section, key, default in (
            ("vnc", "vnc", "port", 5901),
            ("guacd", "guacamole", "guacd_port", 4822),
            ("web", "guacamole", "web_port", 8080),
            ("postgresql", "database", "port", 54321),
        ):
            port = int(data[section].get(key, default))
            ports[label] = {"port": port, "in_use": _tcp_open("127.0.0.1", port)}
    tools = {name: bool(shutil.which(name)) for name in ("sudo", "systemctl", "docker", "podman", "nginx", "apachectl", "caddy")}
    return OperationResult(
        ok=plan.support_level is not SupportLevel.UNSUPPORTED,
        summary=plan.summary,
        details={"support_level": plan.support_level.value, "reasons": list(plan.reasons), "warnings": list(plan.warnings), "ports": ports, "tools": tools},
    )


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def uninstall(facts: SystemFacts, config: InstallerConfig, executor: Any) -> OperationResult:
    plan = build_plan(facts, config)
    data = plan.metadata.get("config", _normalized(facts, config))
    user = str(data["target"]["desktop_user"])
    install_root = Path(str(data["target"].get("install_root", "/opt/urd")))
    changed = False
    warnings = ["Desktop and VNC packages are preserved because they may be used by other software.", "PostgreSQL data is preserved by default."]
    if str(data["guacamole"].get("deployment")) == "compose" and (install_root / "compose.yaml").exists():
        runtime = str(data["target"].get("container_runtime", "auto"))
        runtime = _resolve_runtime(runtime, str(plan.metadata.get("platform", "custom")))
        command = _compose_command(runtime, executor)
        executor.run(command + ("-p", "urd-guacamole", "-f", str(install_root / "compose.yaml"), "down"), require_root=True, changed=True)
        changed = True
    service = f"urd-vnc-{user}.service"
    if facts.init_system == "systemd":
        executor.run(("systemctl", "disable", "--now", service), require_root=True, check=False, changed=True)
        changed = True
    elif facts.init_system == "openrc":
        executor.run(("rc-service", service.removesuffix(".service"), "stop"), require_root=True, check=False, changed=True)
        executor.run(("rc-update", "del", service.removesuffix(".service"), "default"), require_root=True, check=False, changed=True)
        changed = True
    # Managed configuration is intentionally retained for audit/reinstall.  The
    # user can delete it after reviewing state; uninstall never recursively
    # removes a home directory or database volume.
    return OperationResult(True, changed, "Services stopped; data and managed configuration retained", {"install_root": str(install_root)}, warnings)


__all__ = ["apply_plan", "build_plan", "doctor", "list_supported", "uninstall", "verify"]
