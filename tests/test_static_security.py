from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from tests.support import ROOT


class ForbiddenPatternTests(unittest.TestCase):
    @classmethod
    def production_sources(cls):
        yield ROOT / "init.sh"
        yield ROOT / "bootstrap.sh"
        yield from sorted((ROOT / "src").rglob("*.py"))

    def test_no_dangerous_shell_patterns_in_production_sources(self) -> None:
        patterns = {
            "password deletion": re.compile(r"\bpasswd\s+(?:[^\n]*\s)?-d(?:\s|$)"),
            "world-writable chmod": re.compile(
                r"\bchmod\s+(?:-R\s+)?(?:0?777|a\+rwx)(?:\s|$)"
            ),
            "database password in argv": re.compile(
                r"\bmysql\b[^\n]*(?:\s-p\S+|\s--password(?:=|\s)\S+)"
            ),
            "plaintext HTTP download": re.compile(
                r"\b(?:curl|wget)\b[^\n]*\bhttp://", re.IGNORECASE
            ),
            "shell eval": re.compile(r"(?m)^[ \t]*eval(?:[ \t]|$)"),
        }
        failures = []
        for path in self.production_sources():
            text = path.read_text(encoding="utf-8")
            for name, pattern in patterns.items():
                match = pattern.search(text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    failures.append("{}:{}: {}".format(path.relative_to(ROOT), line, name))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_python_never_uses_eval_os_system_or_shell_true(self) -> None:
        failures = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "eval":
                    failures.append("{}:{}: eval()".format(path.relative_to(ROOT), node.lineno))
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr == "system"
                ):
                    failures.append("{}:{}: os.system()".format(path.relative_to(ROOT), node.lineno))
                for keyword in node.keywords:
                    if (
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        failures.append("{}:{}: shell=True".format(path.relative_to(ROOT), node.lineno))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_locked_downloads_are_https_and_have_reviewed_sha256(self) -> None:
        lock_path = ROOT / "versions" / "lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertTrue(lock["verification"]["required"])
        for name, artifact in lock["guacamole"]["artifacts"].items():
            with self.subTest(artifact=name):
                self.assertTrue(artifact["url"].startswith("https://"))
                self.assertTrue(artifact["signature_url"].startswith("https://"))
                self.assertTrue(artifact["sha256_manifest_url"].startswith("https://"))
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
