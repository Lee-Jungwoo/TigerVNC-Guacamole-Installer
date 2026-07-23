from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from urd_installer import facts
from urd_installer.model import FactsError

from tests.support import FIXTURES


class OsReleaseTests(unittest.TestCase):
    def test_distribution_fixtures_parse(self) -> None:
        expected = {
            "ubuntu-24.04": ("ubuntu", "24.04"),
            "fedora-41": ("fedora", "41"),
            "alpine-3.20": ("alpine", "3.20.3"),
        }
        for filename, values in expected.items():
            with self.subTest(fixture=filename):
                release, _ = facts.read_os_release(
                    str(FIXTURES / "os-release" / filename)
                )
                self.assertEqual((release["ID"], release["VERSION_ID"]), values)

    def test_reads_ubuntu_fixture_without_sourcing_it(self) -> None:
        fixture = FIXTURES / "os-release" / "ubuntu-24.04"
        release, selected_path = facts.read_os_release(str(fixture))
        self.assertEqual(release["ID"], "ubuntu")
        self.assertEqual(release["ID_LIKE"], "debian")
        self.assertEqual(release["VERSION_ID"], "24.04")
        self.assertEqual(selected_path, str(fixture))

    def test_parser_treats_shell_syntax_as_literal_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            text = 'ID="ubuntu$(touch {})"\nNAME="Fixture Linux"\n'.format(marker)
            release = facts.parse_os_release(text)
            self.assertIn("$(touch", release["ID"])
            self.assertFalse(marker.exists())

    def test_parser_rejects_invalid_key_and_unquoted_whitespace(self) -> None:
        with self.assertRaises(FactsError):
            facts.parse_os_release("bad-key=value\n")
        with self.assertRaises(FactsError):
            facts.parse_os_release("NAME=two words\n")

    def test_architecture_aliases_are_stable(self) -> None:
        self.assertEqual(facts.normalize_architecture("x86_64"), "amd64")
        self.assertEqual(facts.normalize_architecture("aarch64"), "arm64")
        self.assertEqual(facts.normalize_architecture("i686"), "386")


class FactDetectionTests(unittest.TestCase):
    def test_detect_uses_fixture_and_mocked_host_probes_only(self) -> None:
        fixture = FIXTURES / "os-release" / "ubuntu-24.04"
        with mock.patch.object(facts.platform, "system", return_value="Linux"), mock.patch.object(
            facts.platform, "machine", return_value="x86_64"
        ), mock.patch.object(facts.platform, "release", return_value="fixture-kernel"), mock.patch.object(
            facts.socket, "gethostname", return_value="fixture-host"
        ), mock.patch.object(facts.os, "geteuid", return_value=1000), mock.patch.object(
            facts.getpass, "getuser", return_value="fixture-user"
        ), mock.patch.object(
            facts, "_detect_init_system", return_value="systemd"
        ), mock.patch.object(
            facts, "_detect_package_managers", return_value=(("apt",), "apt")
        ), mock.patch.object(
            facts, "_detect_security_modules", return_value=("apparmor",)
        ), mock.patch.object(
            facts, "_detect_active_firewall", return_value=None
        ), mock.patch.object(
            facts, "_detect_display_server", return_value=None
        ), mock.patch.object(
            facts, "_discover_session_names", return_value=("xfce",)
        ), mock.patch.object(
            facts, "_discover_service_units", return_value=("ssh.service",)
        ), mock.patch.object(
            facts, "_detect_container", return_value=False
        ), mock.patch.object(
            facts.shutil, "which", return_value=None
        ), mock.patch.object(
            facts.subprocess, "run", side_effect=AssertionError("subprocess probe is forbidden")
        ):
            detected = facts.detect_facts(str(fixture))

        self.assertEqual(detected.os_id, "ubuntu")
        self.assertEqual(detected.version_id, "24.04")
        self.assertEqual(detected.os_like, ("debian",))
        self.assertEqual(detected.architecture, "amd64")
        self.assertEqual(detected.init_system, "systemd")
        self.assertEqual(detected.primary_package_manager, "apt")
        self.assertEqual(detected.available_xsessions, ("xfce",))
        self.assertFalse(detected.is_root)
        self.assertFalse(detected.sudo_available)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
