from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest import mock

from urd_installer import cli

from tests.support import load_example_config, make_facts


class CliJsonTests(unittest.TestCase):
    def _run(self, arguments: List[str]) -> Tuple[int, Dict[str, Any], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            cli, "detect_facts", return_value=make_facts()
        ), mock.patch(
            "urd_installer.executor.subprocess.run",
            side_effect=AssertionError("CLI read-only command executed a subprocess"),
        ), mock.patch(
            "urd_installer.planner.socket.create_connection",
            side_effect=AssertionError("CLI read-only command opened a network connection"),
        ), mock.patch(
            "urd_installer.planner.pwd.getpwnam", side_effect=KeyError("fixture user")
        ), mock.patch.dict(
            os.environ,
            {
                "URD_STATE_PATH": str(Path(directory) / "state.json"),
                "URD_LOCK_PATH": str(Path(directory) / "state.lock"),
            },
            clear=False,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(arguments)
        rendered = stdout.getvalue().strip()
        self.assertTrue(rendered, "CLI produced no JSON output; stderr={!r}".format(stderr.getvalue()))
        return code, json.loads(rendered), stderr.getvalue()

    def test_detect_json(self) -> None:
        code, payload, stderr = self._run(["detect", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "detect")
        self.assertEqual(payload["facts"]["os_id"], "ubuntu")
        self.assertEqual(payload["facts"]["architecture"], "amd64")
        self.assertEqual(stderr, "")

    def test_list_supported_json(self) -> None:
        code, payload, _ = self._run(["list-supported", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        supported = payload["supported"]
        self.assertTrue(supported["platforms"])
        self.assertTrue(supported["desktops"])
        self.assertTrue(supported["vnc"])
        self.assertTrue(supported["deployments"])

    def test_plan_json_uses_fixture_config_without_applying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_example_config()
            config["target"]["container_runtime"] = "docker"
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            code, payload, _ = self._run(
                ["plan", "--config", str(path), "--json", "--non-interactive"]
            )
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "plan")
        plan = payload["plan"]
        self.assertEqual(plan["support_level"], "experimental")
        self.assertEqual(plan["metadata"]["platform"], "debian")
        self.assertIn("vnc-config", [step["id"] for step in plan["steps"]])
        self.assertIn("guacamole-compose", [step["id"] for step in plan["steps"]])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
