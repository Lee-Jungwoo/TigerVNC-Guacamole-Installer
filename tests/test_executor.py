from __future__ import annotations

import io
import json
import logging
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from urd_installer.executor import (
    AtomicFileInstaller,
    CommandRunner,
    Executor,
    SecretRedactor,
    StateStore,
)
from urd_installer.model import ExecutionError

from tests.support import ROOT


class DryRunTests(unittest.TestCase):
    def test_dry_run_never_executes_or_changes_filesystem(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            target = root / "nested" / "managed.conf"
            ensured = root / "another" / "directory"
            executor = Executor(dry_run=True, state_path=root / "state.json")
            with mock.patch.object(
                subprocess, "run", side_effect=AssertionError("dry-run executed a subprocess")
            ) as run:
                command = executor.run(("definitely-not-a-real-command",), changed=True)
                installed = executor.install_file(target, "desired\n", mode=0o600)
                created = executor.ensure_directory(ensured, mode=0o700)
            run.assert_not_called()
            self.assertTrue(command.changed)
            self.assertTrue(installed.changed)
            self.assertTrue(created.changed)
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())
            self.assertFalse(ensured.exists())
            self.assertFalse((root / "state.json").exists())

    def test_dry_run_still_validates_environment_values(self) -> None:
        runner = CommandRunner(dry_run=True)
        with self.assertRaises(ValueError):
            runner.run(("tool",), env={"SAFE": "bad\x00value"})
        with self.assertRaises(ValueError):
            runner.run(("tool",), env={"SAFE": 123})  # type: ignore[dict-item]

    def test_command_strings_are_never_accepted_as_shell_commands(self) -> None:
        runner = CommandRunner(dry_run=True)
        with self.assertRaises(TypeError):
            runner.run("touch /tmp/owned")  # type: ignore[arg-type]


class AtomicFileTests(unittest.TestCase):
    def test_install_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            target = Path(directory) / "managed.conf"
            installer = AtomicFileInstaller(CommandRunner())

            first = installer.install(target, "first\n", mode=0o600)
            second = installer.install(target, "first\n", mode=0o600)
            third = installer.install(target, "second\n", mode=0o600)

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertTrue(third.changed)
            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertIsNotNone(third.backup_path)
            assert third.backup_path is not None
            self.assertEqual(third.backup_path.read_text(encoding="utf-8"), "first\n")
            self.assertEqual(list(Path(directory).glob(".managed.conf.urd-*.tmp")), [])

    def test_install_refuses_to_replace_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            victim = root / "victim"
            victim.write_text("untouched\n", encoding="utf-8")
            link = root / "managed.conf"
            link.symlink_to(victim)
            installer = AtomicFileInstaller(CommandRunner())
            with self.assertRaises(ExecutionError):
                installer.install(link, "replacement\n", mode=0o600)
            self.assertEqual(victim.read_text(encoding="utf-8"), "untouched\n")


class RedactionAndStateTests(unittest.TestCase):
    def test_command_logs_and_errors_redact_known_secret(self) -> None:
        secret = "canary-secret-20c8"
        output = io.StringIO()
        logger = logging.getLogger("urd-tests-redaction")
        logger.handlers = []
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.StreamHandler(output))
        runner = CommandRunner(
            dry_run=True,
            redactor=SecretRedactor((secret,)),
            logger=logger,
        )
        runner.run(("tool", "--password={}".format(secret)))
        self.assertNotIn(secret, output.getvalue())
        self.assertIn("<redacted>", output.getvalue())

        failed = subprocess.CompletedProcess(
            args=["tool"], returncode=1, stdout="", stderr="token={}".format(secret)
        )
        real_runner = CommandRunner(redactor=SecretRedactor((secret,)))
        with mock.patch.object(subprocess, "run", return_value=failed), self.assertRaises(
            ExecutionError
        ) as raised:
            real_runner.run(("tool",))
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("<redacted>", str(raised.exception))

    def test_state_is_mode_0600_redacted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            secret = "state-canary-7331"
            runner = CommandRunner(redactor=SecretRedactor((secret,)))
            store = StateStore(runner, path=root / "state.json", lock_path=root / "state.lock")
            state = {
                "schema_version": 1,
                "resources": {},
                "database_password": secret,
            }
            first = store.save(state)
            second = store.save(state)
            rendered = (root / "state.json").read_text(encoding="utf-8")
            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertNotIn(secret, rendered)
            self.assertIn("<redacted>", rendered)
            self.assertEqual(stat.S_IMODE((root / "state.json").stat().st_mode), 0o600)
            self.assertEqual(json.loads(rendered)["schema_version"], 1)

    def test_state_load_refuses_symlink(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            victim = root / "untrusted-state.json"
            victim.write_text('{"schema_version":1,"resources":{}}\n', encoding="utf-8")
            link = root / "state.json"
            link.symlink_to(victim)
            store = StateStore(
                CommandRunner(), path=link, lock_path=root / "state.lock"
            )
            with self.assertRaises(ExecutionError):
                store.load()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
