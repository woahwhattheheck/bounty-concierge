import io
import json
import types
import unittest
from unittest import mock

from concierge import revenue_cli


class RevenueCliTests(unittest.TestCase):
    def test_registry_is_fixed_sorted_safe_and_unique(self):
        names = list(revenue_cli.COMMANDS)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 12)
        self.assertEqual(
            revenue_cli.COMMANDS["payout-delivery"].module,
            "concierge.payout_delivery_gate",
        )
        for name, command in revenue_cli.COMMANDS.items():
            self.assertRegex(name, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertRegex(command.module, r"^concierge\.[a-z0-9_]+$")
            self.assertTrue(command.summary.endswith("."))

    def test_exported_registry_rejects_item_add_replace_and_delete(self):
        injected = revenue_cli.RevenueCommand("concierge.attacker", "Injected route.")
        with self.assertRaises(TypeError):
            revenue_cli.COMMANDS["injected"] = injected
        with self.assertRaises(TypeError):
            revenue_cli.COMMANDS["cash-cycle"] = injected
        with self.assertRaises(TypeError):
            del revenue_cli.COMMANDS["cash-cycle"]

    def test_exported_command_value_cannot_redirect_routing_or_discovery(self):
        command = revenue_cli.COMMANDS["cash-cycle"]
        canonical_module = command.module
        with self.assertRaises(AttributeError):
            object.__setattr__(command, "module", "os")
        self.assertEqual(command.module, canonical_module)

        seen = []
        fake = types.SimpleNamespace(main=lambda argv: seen.append(argv) or 7)
        listed_json = io.StringIO()
        with mock.patch.object(
            revenue_cli.importlib,
            "import_module",
            return_value=fake,
        ) as importer:
            self.assertEqual(revenue_cli.run(["cash-cycle", "verify"]), 7)
            self.assertEqual(
                revenue_cli.run(["--list-json"], stdout=listed_json),
                0,
            )

        importer.assert_called_once_with(canonical_module)
        self.assertEqual(seen, [["verify"]])
        payload = json.loads(listed_json.getvalue())
        cash_cycle = next(
            row for row in payload["targets"] if row["target"] == "cash-cycle"
        )
        self.assertEqual(cash_cycle["module"], canonical_module)

    def test_exported_registry_rebind_cannot_change_dispatch_or_discovery(self):
        canonical = revenue_cli.COMMANDS["cash-cycle"]
        rebound = {
            "cash-cycle": revenue_cli.RevenueCommand(
                "concierge.attacker",
                "Rebound route.",
            ),
            "injected": revenue_cli.RevenueCommand(
                "concierge.attacker",
                "Injected route.",
            ),
        }
        seen = []
        fake = types.SimpleNamespace(main=lambda argv: seen.append(argv) or 7)
        listed = io.StringIO()
        listed_json = io.StringIO()
        err = io.StringIO()

        with mock.patch.object(revenue_cli, "COMMANDS", rebound):
            with mock.patch.object(
                revenue_cli.importlib,
                "import_module",
                return_value=fake,
            ) as importer:
                code = revenue_cli.run(["cash-cycle", "verify", "receipt.json"])
                unknown = revenue_cli.run(["injected"], stderr=err)
                self.assertEqual(revenue_cli.run(["--list"], stdout=listed), 0)
                self.assertEqual(
                    revenue_cli.run(["--list-json"], stdout=listed_json),
                    0,
                )

        self.assertEqual(code, 7)
        self.assertEqual(unknown, 2)
        self.assertEqual(seen, [["verify", "receipt.json"]])
        importer.assert_called_once_with(canonical.module)
        self.assertIn("cash-cycle", listed.getvalue())
        self.assertNotIn("injected", listed.getvalue())
        payload = json.loads(listed_json.getvalue())
        self.assertEqual(
            [row["target"] for row in payload["targets"]],
            sorted(revenue_cli.COMMANDS),
        )
        self.assertNotIn("injected", [row["target"] for row in payload["targets"]])

    def test_library_only_custody_modules_are_not_advertised_as_cli_targets(self):
        self.assertNotIn("collection-custody", revenue_cli.COMMANDS)
        self.assertNotIn("submission-custody", revenue_cli.COMMANDS)

    def test_help_is_side_effect_free_and_lists_every_target(self):
        out = io.StringIO()
        with mock.patch.object(revenue_cli.importlib, "import_module") as importer:
            code = revenue_cli.run(["--help"], stdout=out)
        self.assertEqual(code, 0)
        importer.assert_not_called()
        for name in revenue_cli.COMMANDS:
            self.assertIn(name, out.getvalue())
        self.assertIn("performs no provider action", out.getvalue())

    def test_list_json_is_machine_readable_and_import_free(self):
        out = io.StringIO()
        with mock.patch.object(revenue_cli.importlib, "import_module") as importer:
            code = revenue_cli.run(["--list-json"], stdout=out)
        self.assertEqual(code, 0)
        importer.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertEqual(
            [row["target"] for row in payload["targets"]],
            sorted(revenue_cli.COMMANDS),
        )

    def test_unknown_target_fails_closed_without_import(self):
        err = io.StringIO()
        with mock.patch.object(revenue_cli.importlib, "import_module") as importer:
            code = revenue_cli.run(["os", "system", "anything"], stderr=err)
        self.assertEqual(code, 2)
        importer.assert_not_called()
        self.assertIn("unknown revenue target", err.getvalue())

    def test_only_selected_fixed_module_is_imported_and_argv_is_exact(self):
        seen = []
        fake = types.SimpleNamespace(main=lambda argv: seen.append(argv) or 7)
        command = revenue_cli.COMMANDS["cash-cycle"]
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake) as importer:
            code = revenue_cli.run(["cash-cycle", "verify", "--weird=1", "a b.json"])
        self.assertEqual(code, 7)
        self.assertEqual(seen, [["verify", "--weird=1", "a b.json"]])
        importer.assert_called_once_with(command.module)

    def test_none_return_normalizes_to_success(self):
        fake = types.SimpleNamespace(main=lambda argv: None)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            self.assertEqual(revenue_cli.run(["settlement"]), 0)

    def test_missing_module_fails_closed_without_exception_text(self):
        err = io.StringIO()
        secret = "SECRET_TOKEN_SHOULD_NOT_LEAK"
        with mock.patch.object(
            revenue_cli.importlib,
            "import_module",
            side_effect=ImportError(secret),
        ):
            code = revenue_cli.run(["receivables-aging"], stderr=err)
        self.assertEqual(code, 2)
        self.assertNotIn(secret, err.getvalue())
        self.assertIn("revenue target unavailable", err.getvalue())

    def test_ordinary_import_exceptions_are_redacted(self):
        secret = "SECRET_PATH_OR_TOKEN_SHOULD_NOT_LEAK"
        for error_type in (RuntimeError, ValueError, OSError):
            with self.subTest(error_type=error_type):
                err = io.StringIO()
                with mock.patch.object(
                    revenue_cli.importlib,
                    "import_module",
                    side_effect=error_type(secret),
                ):
                    code = revenue_cli.run(["receivables-aging"], stderr=err)
                self.assertEqual(code, 2)
                self.assertNotIn(secret, err.getvalue())
                self.assertIn("revenue target unavailable", err.getvalue())

    def test_entrypoint_resolution_exception_is_redacted(self):
        secret = "SECRET_FROM_MODULE_GETATTR_SHOULD_NOT_LEAK"

        class ExplosiveModule:
            @property
            def main(self):
                raise RuntimeError(secret)

        err = io.StringIO()
        with mock.patch.object(
            revenue_cli.importlib,
            "import_module",
            return_value=ExplosiveModule(),
        ):
            code = revenue_cli.run(["cash-cycle"], stderr=err)
        self.assertEqual(code, 2)
        self.assertNotIn(secret, err.getvalue())
        self.assertIn("revenue target unavailable", err.getvalue())

    def test_import_phase_system_exit_is_not_swallowed(self):
        with mock.patch.object(
            revenue_cli.importlib,
            "import_module",
            side_effect=SystemExit(23),
        ):
            with self.assertRaisesRegex(SystemExit, "23"):
                revenue_cli.run(["cash-cycle"])

    def test_missing_callable_main_fails_closed(self):
        err = io.StringIO()
        with mock.patch.object(
            revenue_cli.importlib,
            "import_module",
            return_value=types.SimpleNamespace(main="not callable"),
        ):
            code = revenue_cli.run(["collection-request"], stderr=err)
        self.assertEqual(code, 2)
        self.assertIn("unavailable", err.getvalue())

    def test_invalid_return_contract_fails_closed(self):
        err = io.StringIO()
        fake = types.SimpleNamespace(main=lambda argv: {"not": "an exit code"})
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            code = revenue_cli.run(["payoff-path"])
        self.assertEqual(code, 2)
        self.assertIn("invalid return contract", err.getvalue())

    def test_bool_return_is_not_accepted_as_integer(self):
        err = io.StringIO()
        fake = types.SimpleNamespace(main=lambda argv: True)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            code = revenue_cli.run(["payout-dispute"], stderr=err)
        self.assertEqual(code, 2)

    def test_downstream_system_exit_propagates(self):
        def downstream(_argv):
            raise SystemExit(19)

        fake = types.SimpleNamespace(main=downstream)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(SystemExit, "19"):
                revenue_cli.run(["cash-cycle", "--help"])

    def test_downstream_runtime_error_propagates_with_original_text(self):
        secret = "DOWNSTREAM_RUNTIME_ERROR_MUST_PROPAGATE"

        def downstream(_argv):
            raise RuntimeError(secret)

        fake = types.SimpleNamespace(main=downstream)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, secret):
                revenue_cli.run(["cash-cycle"])

    def test_unrelated_downstream_typeerror_propagates(self):
        def downstream(_argv):
            raise TypeError("downstream bug")

        fake = types.SimpleNamespace(main=downstream)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(TypeError, "downstream bug"):
                revenue_cli.run(["closeout"])

    def test_downstream_typeerror_matching_launcher_text_still_propagates(self):
        message = "target main(argv) must return int or None"

        def downstream(_argv):
            raise TypeError(message)

        fake = types.SimpleNamespace(main=downstream)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(TypeError, r"target main\(argv\) must return int or None"):
                revenue_cli.run(["closeout"])


if __name__ == "__main__":
    unittest.main()
