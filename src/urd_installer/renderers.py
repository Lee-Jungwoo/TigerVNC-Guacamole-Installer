"""Pure configuration renderers used by the installation planner."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Mapping, Sequence


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name, {})
    return value if isinstance(value, Mapping) else {}


def render_xstartup(session_argv: Sequence[str]) -> str:
    if not session_argv:
        raise ValueError("a desktop session command is required")
    command = " ".join(shlex.quote(str(part)) for part in session_argv)
    return f"""#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP="${{XDG_CURRENT_DESKTOP:-URD}}"

if command -v dbus-run-session >/dev/null 2>&1; then
    exec dbus-run-session -- {command}
fi
exec {command}
"""


def render_vnc_config(config: Mapping[str, Any], home: Path) -> str:
    vnc = _section(config, "vnc")
    provider = str(vnc.get("implementation", vnc.get("provider", "tigervnc")))
    display = int(vnc.get("display_number", vnc.get("display", 1)))
    default_port = 5900 + display if provider in ("tigervnc", "tightvnc") else 5900
    port = int(vnc.get("port", default_port))
    listen = str(vnc.get("bind_address", vnc.get("listen", "127.0.0.1")))
    geometry = str(vnc.get("geometry", "1920x1080"))
    depth = int(vnc.get("depth", 24))
    password_file = str(vnc.get("password_file") or home / ".config" / "urd" / "vnc.passwd")
    display_target = str(vnc.get("shared_display", ":0"))
    values = {
        "PROVIDER": provider,
        "AUTHENTICATION": str(vnc.get("authentication", "password")),
        "DISPLAY_NUMBER": str(display),
        "PORT": str(port),
        "LISTEN": listen,
        "GEOMETRY": geometry,
        "DEPTH": str(depth),
        "PASSWORD_FILE": password_file,
        "SHARED_DISPLAY": display_target,
    }
    for key, value in values.items():
        if "\n" in value or "\r" in value or "=" in value:
            raise ValueError(f"invalid character in VNC setting {key}")
    return "".join(f"{key}={value}\n" for key, value in values.items())


def render_vnc_launcher() -> str:
    """Render a provider-neutral launcher without evaluating its config file."""

    return r'''#!/bin/sh
set -eu

action=${1:-run}
config=${2:-}
[ -r "$config" ] || { printf '%s\n' "cannot read VNC config: $config" >&2; exit 66; }

PROVIDER=
AUTHENTICATION=
DISPLAY_NUMBER=
PORT=
LISTEN=
GEOMETRY=
DEPTH=
PASSWORD_FILE=
SHARED_DISPLAY=
while IFS='=' read -r key value; do
    case "$key" in
        PROVIDER) PROVIDER=$value ;;
        AUTHENTICATION) AUTHENTICATION=$value ;;
        DISPLAY_NUMBER) DISPLAY_NUMBER=$value ;;
        PORT) PORT=$value ;;
        LISTEN) LISTEN=$value ;;
        GEOMETRY) GEOMETRY=$value ;;
        DEPTH) DEPTH=$value ;;
        PASSWORD_FILE) PASSWORD_FILE=$value ;;
        SHARED_DISPLAY) SHARED_DISPLAY=$value ;;
        ''|'#'*) ;;
        *) printf '%s\n' "unknown VNC config key: $key" >&2; exit 65 ;;
    esac
done < "$config"

find_command() {
    for candidate in "$@"; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

case "$PROVIDER" in
    tigervnc)
        server=$(find_command tigervncserver vncserver) || {
            printf '%s\n' 'TigerVNC server executable not found' >&2; exit 69;
        }
        if [ "$action" = stop ]; then
            exec "$server" -kill ":$DISPLAY_NUMBER"
        fi
        localhost=no
        if [ "$LISTEN" = 127.0.0.1 ] || [ "$LISTEN" = ::1 ]; then
            localhost=yes
        fi
        exec "$server" ":$DISPLAY_NUMBER" -fg -localhost "$localhost" \
            -geometry "$GEOMETRY" -depth "$DEPTH" -PasswordFile "$PASSWORD_FILE"
        ;;
    tightvnc)
        server=$(find_command tightvncserver vncserver) || {
            printf '%s\n' 'TightVNC server executable not found' >&2; exit 69;
        }
        if [ "$action" = stop ]; then
            exec "$server" -kill ":$DISPLAY_NUMBER"
        fi
        exec "$server" ":$DISPLAY_NUMBER" -localhost \
            -geometry "$GEOMETRY" -depth "$DEPTH" -rfbauth "$PASSWORD_FILE"
        ;;
    x11vnc)
        [ "$action" = stop ] && exit 0
        server=$(find_command x11vnc) || {
            printf '%s\n' 'x11vnc executable not found' >&2; exit 69;
        }
        listen_args=
        if [ "$LISTEN" = 127.0.0.1 ] || [ "$LISTEN" = ::1 ]; then
            listen_args=-localhost
        fi
        if [ "$AUTHENTICATION" = password ]; then
            # Word splitting is intentional only for the fixed empty/-localhost flag.
            # shellcheck disable=SC2086
            exec "$server" -display "$SHARED_DISPLAY" -auth guess -forever -shared \
                -rfbport "$PORT" -rfbauth "$PASSWORD_FILE" $listen_args
        fi
        # shellcheck disable=SC2086
        exec "$server" -display "$SHARED_DISPLAY" -auth guess -forever -shared \
            -rfbport "$PORT" $listen_args
        ;;
    wayvnc)
        printf '%s\n' 'wayvnc must run inside the active wlroots user session' >&2
        exit 78
        ;;
    *)
        printf '%s\n' "unsupported VNC provider: $PROVIDER" >&2
        exit 64
        ;;
esac
'''


def render_systemd_vnc_unit(user: str, home: Path, provider: str) -> str:
    if not user or any(char in user for char in "\n\r/ "):
        raise ValueError("invalid service user")
    if (
        not home.is_absolute()
        or any(char.isspace() for char in str(home))
        or any(char in str(home) for char in '\\"')
    ):
        raise ValueError("invalid service home")
    if provider not in {"tigervnc", "tightvnc", "x11vnc"}:
        raise ValueError("invalid system service VNC provider")
    service_type = "forking" if provider == "tightvnc" else "simple"
    after = "network.target"
    if provider == "x11vnc":
        after = "display-manager.service graphical.target"
    return f"""# Managed by Universal Remote Desktop Installer
[Unit]
Description=URD {provider} session for {user}
After={after}

[Service]
Type={service_type}
User={user}
WorkingDirectory={home}
Environment=HOME={home}
UMask=0077
ExecStart=/usr/local/libexec/urd-vnc-session run {home}/.config/urd/vnc.conf
ExecStop=-/usr/local/libexec/urd-vnc-session stop {home}/.config/urd/vnc.conf
Restart=on-failure
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false

[Install]
WantedBy=multi-user.target
"""


def render_openrc_vnc_service(user: str, home: Path, provider: str) -> str:
    if not user or any(char in user for char in "\n\r/ "):
        raise ValueError("invalid service user")
    if (
        not home.is_absolute()
        or any(char.isspace() for char in str(home))
        or any(char in str(home) for char in '\\"')
    ):
        raise ValueError("invalid service home")
    if provider not in {"tigervnc", "tightvnc", "x11vnc"}:
        raise ValueError("invalid OpenRC VNC provider")
    return f"""#!/sbin/openrc-run
description="URD {provider} session for {user}"
command="/usr/local/libexec/urd-vnc-session"
command_args="run {home}/.config/urd/vnc.conf"
command_user="{user}"
command_background="no"
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=0

depend() {{
    need net
    after display-manager
}}
"""
