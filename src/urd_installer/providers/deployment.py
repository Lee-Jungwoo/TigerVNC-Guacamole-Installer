"""Guacamole deployment and reverse-proxy renderers.

The module deliberately contains no subprocess calls.  The planner is responsible
for turning these pure renderers into checked, idempotent tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DeploymentSpec:
    name: str
    description: str
    tier: str
    requires: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


DEPLOYMENTS: dict[str, DeploymentSpec] = {
    "compose": DeploymentSpec(
        "compose",
        "Guacamole, guacd and PostgreSQL managed with Compose",
        "experimental",
        ("docker or podman", "compose frontend"),
        ("Recommended portable Guacamole deployment",),
    ),
    "native": DeploymentSpec(
        "native",
        "Distribution-native Guacamole build and servlet deployment",
        "experimental",
        ("systemd", "supported build dependency provider"),
        ("Requires a distro/version-specific adapter",),
    ),
    "external": DeploymentSpec(
        "external",
        "Use an existing Guacamole endpoint",
        "experimental",
        (),
        ("The installer manages only the local desktop/VNC endpoint",),
    ),
    "none": DeploymentSpec(
        "none",
        "Do not deploy Guacamole (VNC-only profile)",
        "experimental",
    ),
}


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name, {})
    return value if isinstance(value, Mapping) else {}


def _string(value: Any, default: str) -> str:
    return str(value) if value not in (None, "") else default


def compose_frontend(runtime: str, available: Sequence[str] = ()) -> tuple[str, ...]:
    """Return the argv prefix for a Compose implementation.

    ``available`` is injectable so detection can be tested without examining the
    real PATH.  An empty sequence means "use the conventional modern command".
    """

    found = set(available)
    if runtime == "docker":
        if not found or "docker" in found:
            return ("docker", "compose")
        if "docker-compose" in found:
            return ("docker-compose",)
    if runtime == "podman":
        if not found or "podman" in found:
            return ("podman", "compose")
        if "podman-compose" in found:
            return ("podman-compose",)
    raise ValueError(f"no Compose frontend is available for runtime {runtime!r}")


def render_compose(config: Mapping[str, Any], install_dir: Path) -> str:
    """Render a Linux Compose stack using loopback-only infrastructure ports.

    Host networking is intentional for the hybrid profile: it lets containerized
    guacd reach a VNC server which is safely bound to host loopback.  guacd and
    PostgreSQL are explicitly bound to 127.0.0.1.  Tomcat itself listens on the
    configured HTTP port; the planner warns when a host firewall/reverse proxy is
    required because the upstream image cannot change its bind address through a
    supported environment variable.
    """

    guac = _section(config, "guacamole")
    database = _section(config, "database")

    version = _string(guac.get("version"), "1.6.0")
    web_port = int(guac.get("web_port", guac.get("port", 8080)))
    guacd_bind = _string(guac.get("guacd_bind_address"), "127.0.0.1")
    guacd_port = int(guac.get("guacd_port", 4822))
    context = _string(guac.get("context_path", guac.get("context")), "/guacamole/").strip("/") or "ROOT"
    db_name = _string(database.get("name"), "guacamole_db")
    db_user = _string(database.get("username", database.get("user")), "guacamole_user")
    db_port = int(database.get("port", 54321))
    postgres_image = _string(database.get("image"), "postgres:16-alpine")
    secret_path = install_dir / "secrets" / "postgresql_password"
    init_path = install_dir / "initdb"
    entrypoint_patch = install_dir / "entrypoint.d" / "90-urd-http-bind.sh"

    # Values reaching this renderer have already passed schema validation.  JSON
    # quoting is also valid YAML quoting and avoids accidental YAML scalars.
    import json

    q = json.dumps
    lines = [
        "services:",
        "  postgres:",
        f"    image: {q(postgres_image)}",
        "    restart: unless-stopped",
        "    network_mode: host",
        "    command:",
        "      - postgres",
        "      - -c",
        "      - listen_addresses=127.0.0.1",
        "      - -c",
        f"      - port={db_port}",
        "    environment:",
        f"      POSTGRES_DB: {q(db_name)}",
        f"      POSTGRES_USER: {q(db_user)}",
        "      POSTGRES_PASSWORD_FILE: /run/secrets/postgresql_password",
        f"      PGPORT: {q(str(db_port))}",
        "    secrets:",
        "      - postgresql_password",
        "    volumes:",
        "      - postgresql_data:/var/lib/postgresql/data",
        f"      - {q(str(init_path))}:/docker-entrypoint-initdb.d:ro",
        "    healthcheck:",
        f"      test: [\"CMD-SHELL\", \"pg_isready -h 127.0.0.1 -p {db_port} -U {db_user} -d {db_name}\"]",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 12",
        "      start_period: 20s",
        "  guacd:",
        f"    image: {q('guacamole/guacd:' + version)}",
        "    restart: unless-stopped",
        "    network_mode: host",
        f"    command: [\"/opt/guacamole/sbin/guacd\", \"-b\", {q(guacd_bind)}, \"-l\", {q(str(guacd_port))}, \"-f\"]",
        "    security_opt:",
        "      - no-new-privileges:true",
        "  guacamole:",
        f"    image: {q('guacamole/guacamole:' + version)}",
        "    restart: unless-stopped",
        "    network_mode: host",
        "    depends_on:",
        "      postgres:",
        "        condition: service_healthy",
        "      guacd:",
        "        condition: service_started",
        "    environment:",
        f"      GUACD_HOSTNAME: {q(guacd_bind)}",
        f"      GUACD_PORT: {q(str(guacd_port))}",
        "      POSTGRESQL_ENABLED: \"true\"",
        "      POSTGRESQL_HOSTNAME: 127.0.0.1",
        f"      POSTGRESQL_PORT: {q(str(db_port))}",
        f"      POSTGRESQL_DATABASE: {q(db_name)}",
        f"      POSTGRESQL_USER: {q(db_user)}",
        f"      POSTGRESQL_USERNAME: {q(db_user)}",
        "      POSTGRESQL_PASSWORD_FILE: /run/secrets/postgresql_password",
        f"      WEBAPP_CONTEXT: {q(context)}",
        "      REMOTE_IP_VALVE_ENABLED: \"true\"",
        "    secrets:",
        "      - postgresql_password",
        "    volumes:",
        f"      - {q(str(entrypoint_patch))}:/opt/guacamole/entrypoint.d/90-urd-http-bind.sh:ro",
        "    security_opt:",
        "      - no-new-privileges:true",
        "secrets:",
        "  postgresql_password:",
        f"    file: {q(str(secret_path))}",
        "volumes:",
        "  postgresql_data:",
        "",
        f"# The managed entrypoint extension binds Tomcat to the configured host TCP/{web_port} address.",
    ]
    return "\n".join(lines) + "\n"


def render_guacamole_http_bind(config: Mapping[str, Any]) -> str:
    """Render an official entrypoint extension which narrows Tomcat's listener.

    The Guacamole 1.6.0 image explicitly supports extra ``entrypoint.d`` scripts.
    This script edits only the container's ephemeral server.xml before Tomcat is
    started; no host or image file is modified.
    """

    guac = _section(config, "guacamole")
    bind = _string(guac.get("web_bind_address"), "127.0.0.1")
    port = int(guac.get("web_port", 8080))
    if bind not in {"127.0.0.1", "::1", "0.0.0.0", "::"}:
        # IP literals are schema-validated, but sed replacement deliberately has
        # a smaller allowlist to keep this generated shell script injection-free.
        import ipaddress

        try:
            bind = str(ipaddress.ip_address(bind))
        except ValueError as exc:
            raise ValueError("guacamole.web_bind_address must be an IP literal") from exc
    return f'''#!/bin/bash
set -e
server_xml=/opt/tomcat/conf/server.xml
if [ ! -f "$server_xml" ]; then
    server_xml=/usr/local/tomcat/conf/server.xml
fi
[ -f "$server_xml" ] || {{ echo "Tomcat server.xml was not found" >&2; return 1; }}
sed -i -E '0,/<Connector port="8080"/s//<Connector address="{bind}" port="{port}"/' "$server_xml"
'''


def render_nginx(config: Mapping[str, Any]) -> str:
    proxy = _section(config, "proxy")
    guac = _section(config, "guacamole")
    domain = _string(proxy.get("domain", proxy.get("server_name")), "_")
    listen = _string(proxy.get("listen_address"), "127.0.0.1")
    http_port = int(proxy.get("http_port", 80))
    web_port = int(guac.get("web_port", guac.get("port", 8080)))
    context = _string(guac.get("context_path", guac.get("context")), "/guacamole/").strip("/")
    prefix = "/" if context in ("", "ROOT") else f"/{context}/"
    upstream = "/" if context in ("", "ROOT") else f"/{context}/"
    return f"""# Managed by Universal Remote Desktop Installer
map $http_upgrade $urd_connection_upgrade {{
    default upgrade;
    ''      close;
}}

server {{
    listen {listen}:{http_port};
    server_name {domain};

    location {prefix} {{
        proxy_pass http://127.0.0.1:{web_port}{upstream};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $urd_connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
}}
"""


def render_caddy(config: Mapping[str, Any]) -> str:
    proxy = _section(config, "proxy")
    guac = _section(config, "guacamole")
    domain = _string(proxy.get("domain", proxy.get("server_name")), "localhost")
    web_port = int(guac.get("web_port", guac.get("port", 8080)))
    return f"""# Managed by Universal Remote Desktop Installer
{domain} {{
    reverse_proxy 127.0.0.1:{web_port}
}}
"""


def render_apache(config: Mapping[str, Any]) -> str:
    proxy = _section(config, "proxy")
    guac = _section(config, "guacamole")
    domain = _string(proxy.get("domain", proxy.get("server_name")), "localhost")
    listen = _string(proxy.get("listen_address"), "127.0.0.1")
    http_port = int(proxy.get("http_port", 80))
    web_port = int(guac.get("web_port", guac.get("port", 8080)))
    context = _string(guac.get("context_path", guac.get("context")), "/guacamole/").strip("/")
    prefix = "/" if context in ("", "ROOT") else f"/{context}/"
    target = "/" if context in ("", "ROOT") else f"/{context}/"
    return f"""# Managed by Universal Remote Desktop Installer
<VirtualHost {listen}:{http_port}>
    ServerName {domain}
    ProxyPreserveHost On
    ProxyPass {prefix}websocket-tunnel ws://127.0.0.1:{web_port}{target}websocket-tunnel
    ProxyPass {prefix} http://127.0.0.1:{web_port}{target} retry=0 timeout=3600
    ProxyPassReverse {prefix} http://127.0.0.1:{web_port}{target}
    RequestHeader set X-Forwarded-Proto expr=%{{REQUEST_SCHEME}}
</VirtualHost>
"""


PROXY_RENDERERS = {
    "nginx": render_nginx,
    "caddy": render_caddy,
    "apache": render_apache,
}
