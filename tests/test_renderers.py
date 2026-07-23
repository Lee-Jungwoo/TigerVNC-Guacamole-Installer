from __future__ import annotations

import copy
import unittest
from pathlib import Path

from urd_installer.providers.deployment import (
    render_apache,
    render_compose,
    render_guacamole_http_bind,
    render_nginx,
)
from urd_installer.renderers import (
    render_systemd_vnc_unit,
    render_vnc_config,
    render_vnc_launcher,
    render_xstartup,
)

from tests.support import load_example_config


class NetworkRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_example_config()

    def test_vnc_configuration_is_loopback_only(self) -> None:
        rendered = render_vnc_config(self.config, Path("/home/guacdesk"))
        self.assertIn("PROVIDER=tigervnc\n", rendered)
        self.assertIn("LISTEN=127.0.0.1\n", rendered)
        self.assertIn("PORT=5901\n", rendered)
        self.assertNotIn("0.0.0.0", rendered)

        launcher = render_vnc_launcher()
        self.assertIn("-localhost", launcher)
        self.assertNotIn("\neval ", launcher)

    def test_compose_binds_database_guacd_and_web_to_loopback(self) -> None:
        rendered = render_compose(self.config, Path("/opt/urd"))
        self.assertIn("listen_addresses=127.0.0.1", rendered)
        self.assertIn('guacd", "-b", "127.0.0.1"', rendered)
        self.assertIn("POSTGRESQL_HOSTNAME: 127.0.0.1", rendered)
        self.assertIn("90-urd-http-bind.sh", rendered)
        self.assertNotIn("0.0.0.0", rendered)

        bind_script = render_guacamole_http_bind(self.config)
        self.assertIn('address="127.0.0.1"', bind_script)
        self.assertIn('port="8080"', bind_script)

    def test_reverse_proxies_honor_loopback_listener(self) -> None:
        nginx = render_nginx(self.config)
        self.assertIn("listen 127.0.0.1:80;", nginx)
        self.assertIn("proxy_pass http://127.0.0.1:8080/guacamole/;", nginx)
        self.assertNotIn("listen 0.0.0.0", nginx)

        apache = render_apache(self.config)
        self.assertIn("<VirtualHost 127.0.0.1:80>", apache)
        self.assertIn("http://127.0.0.1:8080/guacamole/", apache)

    def test_rendered_configs_never_embed_plaintext_secrets(self) -> None:
        canary = "plain-secret-canary-7f3c"
        config = copy.deepcopy(self.config)
        config["database"]["password"] = canary
        config["guacamole"]["admin_password"] = canary
        compose = render_compose(config, Path("/opt/urd"))
        nginx = render_nginx(config)
        vnc = render_vnc_config(config, Path("/home/guacdesk"))
        for rendered in (compose, nginx, vnc):
            self.assertNotIn(canary, rendered)
        self.assertIn("POSTGRES_PASSWORD_FILE", compose)
        self.assertIn("POSTGRESQL_PASSWORD_FILE", compose)

    def test_http_bind_rejects_shell_injection(self) -> None:
        config = copy.deepcopy(self.config)
        config["guacamole"]["web_bind_address"] = '127.0.0.1"; touch /tmp/owned; #'
        with self.assertRaises(ValueError):
            render_guacamole_http_bind(config)


class ServiceRendererTests(unittest.TestCase):
    def test_systemd_unit_has_baseline_hardening(self) -> None:
        rendered = render_systemd_vnc_unit(
            "guacdesk", Path("/home/guacdesk"), "tigervnc"
        )
        self.assertIn("User=guacdesk", rendered)
        self.assertIn("UMask=0077", rendered)
        self.assertIn("NoNewPrivileges=true", rendered)
        self.assertIn("PrivateTmp=true", rendered)
        self.assertNotIn("Password=", rendered)

    def test_service_renderer_rejects_newline_injection(self) -> None:
        with self.assertRaises(ValueError):
            render_systemd_vnc_unit(
                "guacdesk",
                Path("/home/guacdesk\nExecStart=/tmp/owned"),
                "tigervnc",
            )
        with self.assertRaises(ValueError):
            render_systemd_vnc_unit(
                "guacdesk", Path("/home/guacdesk"), "tigervnc\nExecStart=/tmp/owned"
            )

    def test_vnc_renderer_rejects_newline_values(self) -> None:
        config = load_example_config()
        config["vnc"]["geometry"] = "1920x1080\nOWNED=yes"
        with self.assertRaises(ValueError):
            render_vnc_config(config, Path("/home/guacdesk"))

    def test_xstartup_has_real_shebang_and_execs_session(self) -> None:
        rendered = render_xstartup(("startxfce4",))
        self.assertTrue(rendered.startswith("#!/bin/sh\n"))
        self.assertIn("exec dbus-run-session -- startxfce4", rendered)
        self.assertNotIn("DISPLAY=:1", rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
