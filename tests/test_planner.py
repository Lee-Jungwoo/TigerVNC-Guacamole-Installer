from __future__ import annotations

import copy
import unittest
from unittest import mock

from urd_installer.model import SupportLevel
from urd_installer.planner import _guacamole_password_hash, build_plan, list_supported

from tests.support import installer_config, load_example_config, make_facts


class PlannerTests(unittest.TestCase):
    def _plan(self, data, facts=None):
        selected_facts = make_facts() if facts is None else facts
        with mock.patch(
            "urd_installer.planner.pwd.getpwnam", side_effect=KeyError("fixture user")
        ):
            return build_plan(selected_facts, installer_config(data))

    def test_debian_fixture_builds_expected_hybrid_plan(self) -> None:
        data = load_example_config()
        data["target"]["container_runtime"] = "docker"
        plan = self._plan(data)
        self.assertEqual(plan.support_level, SupportLevel.EXPERIMENTAL)
        self.assertEqual(plan.metadata["platform"], "debian")
        self.assertEqual(plan.metadata["desktop"], "xfce")
        self.assertEqual(plan.metadata["vnc"], "tigervnc")
        self.assertEqual(plan.metadata["runtime"], "docker")
        step_ids = [step.id for step in plan.steps]
        self.assertEqual(
            step_ids,
            [
                "target-user",
                "packages",
                "vnc-config",
                "vnc-service",
                "guacamole-secrets",
                "guacamole-schema",
                "guacamole-compose",
                "database-password",
                "guacamole-admin",
                "guacamole-connection",
                "reverse-proxy",
            ],
        )
        self.assertFalse(any(step.destructive for step in plan.steps))

    def test_vnc_disabled_omits_vnc_owned_steps(self) -> None:
        data = load_example_config()
        data["target"]["container_runtime"] = "docker"
        data["vnc"]["enabled"] = False
        plan = self._plan(data)
        self.assertNotEqual(plan.support_level, SupportLevel.UNSUPPORTED, plan.reasons)
        step_ids = [step.id for step in plan.steps]
        self.assertNotIn("vnc-config", step_ids)
        self.assertNotIn("vnc-service", step_ids)
        self.assertIn("guacamole-compose", step_ids)

    def test_fedora_tightvnc_is_an_unsupported_empty_plan(self) -> None:
        data = load_example_config()
        data["target"]["container_runtime"] = "podman"
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
        plan = self._plan(data, facts)
        self.assertEqual(plan.support_level, SupportLevel.UNSUPPORTED)
        self.assertEqual(plan.steps, ())
        self.assertTrue(any("TightVNC" in reason for reason in plan.reasons), plan.reasons)

    def test_native_guacamole_is_explicitly_gated(self) -> None:
        data = load_example_config()
        data["profile"] = "native"
        data["guacamole"]["deployment"] = "native"
        data["database"]["deployment"] = "native"
        plan = self._plan(data)
        self.assertEqual(plan.support_level, SupportLevel.UNSUPPORTED)
        self.assertTrue(any("Native Guacamole" in reason for reason in plan.reasons))

    def test_virtual_vnc_port_and_service_scope_must_match_provider(self) -> None:
        data = load_example_config()
        data["vnc"]["port"] = 5999
        data["vnc"]["service_scope"] = "user"
        plan = self._plan(data)
        self.assertEqual(plan.support_level, SupportLevel.UNSUPPORTED)
        self.assertTrue(any("requires vnc.port=5901" in reason for reason in plan.reasons))
        self.assertTrue(any("service_scope=system" in reason for reason in plan.reasons))

    def test_guacamole_hash_matches_official_schema_known_value(self) -> None:
        salt = bytes.fromhex(
            "FE24ADC5E11E2B25288D1704ABE67A79"
            "E342ECC26064CE69C5B3177795A82264"
        )
        self.assertEqual(
            _guacamole_password_hash("guacadmin", salt).upper(),
            "CA458A7D494E3BE824F5E1E175A1556C"
            "0F8EEF2C2D7DF3633BEC4A29C4411960",
        )

    def test_unknown_platform_is_unsupported(self) -> None:
        data = load_example_config()
        data["target"]["container_runtime"] = "docker"
        facts = make_facts(
            os_id="unknown-fixture",
            os_name="Unknown Fixture Linux",
            version_id="1",
            os_like=(),
            package_managers=(),
            primary_package_manager=None,
            os_release={"ID": "unknown-fixture", "VERSION_ID": "1"},
        )
        plan = self._plan(data, facts)
        self.assertEqual(plan.support_level, SupportLevel.UNSUPPORTED)
        self.assertEqual(plan.steps, ())

    def test_dangerous_desktop_user_is_rejected_defense_in_depth(self) -> None:
        data = load_example_config()
        data["target"]["container_runtime"] = "docker"
        data["target"]["desktop_user"] = ".."
        plan = self._plan(data)
        self.assertEqual(plan.support_level, SupportLevel.UNSUPPORTED)
        self.assertEqual(plan.steps, ())
        self.assertTrue(any("desktop_user" in reason for reason in plan.reasons))

    def test_supported_catalog_is_honest_about_verification(self) -> None:
        supported = list_supported()
        self.assertTrue(supported["platforms"])
        self.assertTrue(supported["desktops"])
        self.assertTrue(supported["vnc"])
        tiers = {
            item["tier"]
            for section in ("platforms", "desktops", "vnc", "deployments")
            for item in supported[section]
        }
        self.assertNotIn("verified", tiers)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
