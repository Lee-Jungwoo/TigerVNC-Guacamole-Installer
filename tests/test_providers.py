from __future__ import annotations

import unittest

from urd_installer.providers.desktops import resolve_desktop, xstartup_argv
from urd_installer.providers.platforms import PACKAGE_MANAGERS, resolve_platform
from urd_installer.providers.vnc import resolve_vnc, validate_combination

from tests.support import installer_config, load_example_config, make_facts


class PlatformProviderTests(unittest.TestCase):
    def test_exact_id_and_id_like_resolution(self) -> None:
        self.assertEqual(resolve_platform("ubuntu", ("debian",)).key, "debian")
        self.assertEqual(resolve_platform("rocky", ("rhel", "fedora")).key, "rhel")
        self.assertEqual(resolve_platform("unknown-child", ("suse",)).key, "suse")
        self.assertEqual(resolve_platform("unknown", ()).key, "custom")

    def test_package_commands_are_argv_and_reject_option_injection(self) -> None:
        apt = PACKAGE_MANAGERS["apt"]
        self.assertEqual(
            apt.install_argv(("tigervnc-standalone-server",))[-1],
            "tigervnc-standalone-server",
        )
        with self.assertRaises(ValueError):
            apt.install_argv(("--allow-unauthenticated",))


class DesktopAndVncProviderTests(unittest.TestCase):
    def test_desktop_and_vnc_aliases(self) -> None:
        self.assertEqual(resolve_desktop("xfce4").key, "xfce")
        self.assertEqual(resolve_desktop("kde-plasma").key, "kde")
        self.assertEqual(xstartup_argv("xfce"), ("startxfce4",))
        self.assertEqual(resolve_vnc("tiger").key, "tigervnc")
        self.assertEqual(resolve_vnc("x11").key, "x11vnc")

    def test_ubuntu_xfce_tigervnc_is_plannable_but_not_verified(self) -> None:
        result = validate_combination(make_facts(), installer_config())
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.tier, "experimental")

    def test_fedora_tightvnc_is_rejected(self) -> None:
        data = load_example_config()
        data["vnc"].update(
            {"implementation": "tightvnc", "mode": "virtual-session"}
        )
        facts = make_facts(
            os_id="fedora",
            os_name="Fedora Linux 41",
            version_id="41",
            os_like=(),
            package_managers=("dnf",),
            primary_package_manager="dnf",
            os_release={"ID": "fedora", "VERSION_ID": "41"},
        )
        result = validate_combination(facts, installer_config(data))
        self.assertFalse(result.ok)
        self.assertTrue(any("TightVNC" in error for error in result.errors), result.errors)

    def test_x11vnc_cannot_capture_wayland(self) -> None:
        data = load_example_config()
        data["desktop"].update({"environment": "none", "display_server": "wayland"})
        data["vnc"].update(
            {
                "implementation": "x11vnc",
                "mode": "existing-session",
                "display_number": 0,
                "port": 5900,
            }
        )
        result = validate_combination(
            make_facts(display_server="wayland", available_xsessions=()),
            installer_config(data),
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("Wayland" in error for error in result.errors), result.errors)

    def test_wayvnc_requires_a_compatible_wayland_session(self) -> None:
        data = load_example_config()
        data["desktop"].update({"environment": "none", "display_server": "wayland"})
        data["vnc"].update(
            {
                "implementation": "wayvnc",
                "mode": "wayland-session",
                "authentication": "none",
                "display_number": 0,
                "port": 5900,
            }
        )
        facts = make_facts(display_server="wayland", available_xsessions=()).to_dict()
        facts["wayland_compositor"] = "sway"
        result = validate_combination(facts, installer_config(data))
        self.assertTrue(result.ok, result.errors)

    def test_configured_mode_must_match_implementation(self) -> None:
        data = load_example_config()
        data["vnc"]["mode"] = "existing-session"
        result = validate_combination(make_facts(), installer_config(data))
        self.assertFalse(result.ok)
        self.assertTrue(any("requires vnc.mode" in error for error in result.errors))

    def test_alpine_reports_openrc_as_experimental_not_systemd(self) -> None:
        facts = make_facts(
            os_id="alpine",
            os_name="Alpine Linux v3.20",
            version_id="3.20.3",
            os_like=(),
            init_system="openrc",
            package_managers=("apk",),
            primary_package_manager="apk",
            os_release={"ID": "alpine", "VERSION_ID": "3.20.3"},
        )
        result = validate_combination(facts, installer_config())
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.tier, "experimental")
        self.assertTrue(any("OpenRC" in warning or "openrc" in warning for warning in result.warnings))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
