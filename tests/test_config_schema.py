from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from urd_installer.config import load_config, secret_values, validate_config
from urd_installer.model import ConfigError

from tests.support import SCHEMA_PATH, load_example_config, load_json, schema_errors


class SchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)
        cls.example = load_example_config()

    def assertSchemaValid(self, value: object) -> None:
        errors = schema_errors(value, self.schema)
        self.assertEqual(errors, [], "\n".join(errors))

    def assertSchemaInvalid(self, value: object, fragment: str = "") -> None:
        errors = schema_errors(value, self.schema)
        self.assertTrue(errors, "configuration unexpectedly matched the schema")
        if fragment:
            self.assertTrue(
                any(fragment in error for error in errors),
                "expected {!r} in schema errors:\n{}".format(fragment, "\n".join(errors)),
            )

    def test_recommended_example_is_valid(self) -> None:
        self.assertSchemaValid(self.example)
        loaded = load_config(str(Path(__file__).resolve().parents[1] / "examples" / "config.json"))
        self.assertEqual(loaded.schema_version, 1)
        self.assertEqual(self.example["profile"], "hybrid")
        self.assertEqual(self.example["desktop"]["environment"], "xfce")
        self.assertEqual(self.example["vnc"]["implementation"], "tigervnc")
        self.assertEqual(self.example["vnc"]["bind_address"], "127.0.0.1")
        self.assertEqual(self.example["guacamole"]["web_bind_address"], "127.0.0.1")
        self.assertEqual(self.example["guacamole"]["version"], "1.6.0")
        self.assertEqual(self.example["database"]["engine"], "postgresql")
        self.assertEqual(self.example["firewall"]["mode"], "none")

    def test_unknown_top_level_and_nested_properties_are_rejected(self) -> None:
        value = copy.deepcopy(self.example)
        value["run_as_root_without_prompt"] = True
        self.assertSchemaInvalid(value, "additional property")

        value = copy.deepcopy(self.example)
        value["vnc"]["password"] = "plaintext"
        self.assertSchemaInvalid(value, "additional property")

    def test_enum_format_and_conditional_errors_are_rejected(self) -> None:
        value = copy.deepcopy(self.example)
        value["profile"] = "everything"
        self.assertSchemaInvalid(value, "enum")

        value = copy.deepcopy(self.example)
        value["vnc"]["bind_address"] = "not-an-ip"
        self.assertSchemaInvalid(value, "anyOf")

        value = copy.deepcopy(self.example)
        value["vnc"]["mode"] = "existing-session"
        self.assertSchemaInvalid(value, "const")

        value = copy.deepcopy(self.example)
        value["proxy"]["trust_forwarded_headers"] = True
        value["proxy"]["trusted_proxy_cidrs"] = []
        self.assertSchemaInvalid(value, "too few")

    def test_acme_requires_domain_email_terms_and_dns_secret(self) -> None:
        value = copy.deepcopy(self.example)
        value["tls"].update(
            {
                "mode": "acme-dns-01",
                "domains": ["desktop.example.test"],
                "agree_to_terms": False,
            }
        )
        self.assertSchemaInvalid(value)

        value["tls"].update(
            {
                "email": "admin@example.test",
                "agree_to_terms": True,
                "dns_provider": "rfc2136",
                "credentials_file": "/run/secrets/acme_dns",
            }
        )
        self.assertSchemaValid(value)

    def test_command_and_path_injection_strings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owned"
            value = copy.deepcopy(self.example)
            value["target"]["desktop_user"] = "$(touch {})".format(marker)
            self.assertSchemaInvalid(value, "pattern")
            self.assertFalse(marker.exists())

            value = copy.deepcopy(self.example)
            value["database"]["password_file"] = "/run/secrets/db\n--help"
            self.assertSchemaInvalid(value, "pattern")
            self.assertFalse(marker.exists())

    def test_every_declared_object_disallows_unknown_properties(self) -> None:
        missing = []

        def walk(value: object, path: str = "$") -> None:
            if isinstance(value, dict):
                if value.get("type") == "object" and value.get("additionalProperties") is not False:
                    missing.append(path)
                for key, child in value.items():
                    walk(child, "{}.{}".format(path, key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, "{}[{}]".format(path, index))

        walk(self.schema)
        self.assertEqual(missing, [])


class ConfigLoaderTests(unittest.TestCase):
    def _load_text(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(text, encoding="utf-8")
            return load_config(str(path))

    def test_duplicate_keys_non_finite_numbers_and_wrong_sections_fail(self) -> None:
        with self.assertRaises(ConfigError):
            self._load_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ConfigError):
            self._load_text('{"schema_version":1,"value":NaN}')
        with self.assertRaises(ConfigError):
            self._load_text('{"schema_version":1,"vnc":"tigervnc"}')
        with self.assertRaises(ConfigError):
            validate_config({"schema_version": True})

    def test_loader_rejects_and_never_executes_injection_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "not-created"
            payload = load_example_config()
            payload["target"]["desktop_user"] = "$(touch {})".format(marker)
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(str(path))
            self.assertFalse(marker.exists())

    def test_secret_discovery_ignores_file_references(self) -> None:
        config = validate_config(
            {
                "schema_version": 1,
                "database": {
                    "password": "inline-canary",
                    "password_file": "/run/secrets/database",
                },
            },
            strict=False,
        )
        self.assertEqual(secret_values(config), ("inline-canary",))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
