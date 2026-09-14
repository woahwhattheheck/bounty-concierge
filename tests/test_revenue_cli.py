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
        self.assertEqual(len(names), 11)
        for name, command in revenue_cli.COMMANDS.items():
            self.assertRegex(name, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertRegex(command.module, r"^concierge\.[a-z0-9_]+$")
            self.assertTrue(command.summary.endswith("."))

    def test_public_registry_is_read_only(self):
        original = revenue_cli.COMMANDS["cash-cycle"]
        with self.assertRaises(TypeError):
            revenue_cli.COMMANDS["evil"] = revenue_cli.RevenueCommand("os", "Nope.")
        with self.assertRaises(TypeError):
            del revenue_cli.COMMANDS["cash-cycle"]
        self.assertIs(revenue_cli.COMMANDS["cash-cycle"], original)

    def test_public_record_object_setattr_cannot_mutate_routing(self):
        public = revenue_cli.COMMANDS["cash-cycle"]
        with self.assertRaises(AttributeError):
            object.__setattr__(public, "module", "os")
        private = revenue_cli._lookup_command("cash-cycle")
        self.assertIsNot(private, public)
        self.assertEqual(private.module, "concierge.cash_cycle_review")

        fake = types.SimpleNamespace(main=lambda argv: 0)
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=fake) as importer:
            self.assertEqual(revenue_cli.run(["cash-cycle"]), 0)
        importer.assert_called_once_with("concierge.cash_cycle_review")

    def test_rebinding_public_discovery_view_cannot_inject_route(self):
        injected = {"evil": revenue_cli.RevenueCommand("os", "Nope.")}
        err = io.StringIO()
        with mock.patch.object(revenue_cli, "COMMANDS", injected):
            with mock.patch.object(revenue_cli.importlib, "import_module") as importer:
                code = revenue_cli.run(["evil"], stderr=err)
        self.assertEqual(code, 2)
        importer.assert_not_called()
        self.assertIn("unknown revenue target", err.getvalue())

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

    def test_ordinary_import_phase_exceptions_are_sanitized(self):
        for exc_type in (RuntimeError, OSError, ValueError):
            with self.subTest(exc_type=exc_type.__name__):
                err = io.StringIO()
                secret = f"SECRET_{exc_type.__name__}_SHOULD_NOT_LEAK"
                with mock.patch.object(
                    revenue_cli.importlib,
                    "import_module",
                    side_effect=exc_type(secret),
                ):
                    code = revenue_cli.run(["settlement"], stderr=err)
                self.assertEqual(code, 2)
                self.assertNotIn(secret, err.getvalue())
                self.assertIn("revenue target unavailable", err.getvalue())

    def test_entrypoint_resolution_exception_is_sanitized(self):
        class Trap:
            @property
            def main(self):
                raise RuntimeError("SECRET_PROPERTY_SHOULD_NOT_LEAK")

        err = io.StringIO()
        with mock.patch.object(revenue_cli.importlib, "import_module", return_value=Trap()):
            code = revenue_cli.run(["closeout"], stderr=err)
        self.assertEqual(code, 2)
        self.assertNotIn("SECRET_PROPERTY_SHOULD_NOT_LEAK", err.getvalue())
        self.assertIn("revenue target unavailable", err.getvalue())

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
            code = revenue_cli.run(["payoff-path"], stderr=err)
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
