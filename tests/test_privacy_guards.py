#!/usr/bin/env python3
"""Tests for the two guards that keep real data out of the repository.

Both exist because of a specific failure, and the tests reproduce that
failure rather than testing the happy path around it.

`verify_masked` exists because masking silently did nothing. The placeholder
held a real account number at the time, so the substitution mapped that
number to itself: the code did exactly what it claimed, and the output was
still unmasked. Nothing in the run said so.

`check_no_real_data` exists because the first guard only covers captures. A
real value typed straight into source - a constructor default, a fixture in a
docstring - never passes through the masker at all.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import capture_fixtures as cf                 # noqa: E402
from evals import check_no_real_data as guard            # noqa: E402


class MaskingTest(unittest.TestCase):

    def payload(self, account="999888777"):
        return {"data": {"accounts": [
            {"account_number": account, "rhs_account_number": "111222333",
             "nickname": "Agentic"}]}}

    def test_masking_replaces_every_account_key(self):
        masked = cf.mask_accounts(self.payload(), {})
        account = masked["data"]["accounts"][0]
        self.assertNotEqual(account["account_number"], "999888777")
        self.assertNotEqual(account["rhs_account_number"], "111222333")

    def test_a_sibling_account_key_is_not_missed(self):
        # rhs_account_number identifies the same account as account_number.
        # Masking one and not the other is no masking at all, and that is
        # exactly what shipped in the first draft of the fake broker.
        masked = cf.mask_accounts(self.payload(), {})
        values = cf.account_values(masked)
        self.assertNotIn("111222333", values)

    def test_verify_passes_when_masking_actually_changed_something(self):
        payload = self.payload()
        cf.verify_masked(payload, cf.mask_accounts(payload, {}))

    def test_verify_catches_a_placeholder_that_is_a_real_number(self):
        # The original bug, reproduced. The placeholder equals the real
        # account number, so the substitution is a no-op and the "masked"
        # payload still carries it.
        payload = self.payload(account=cf.AGENTIC_PLACEHOLDER)
        masked = cf.mask_accounts(payload, {})
        with self.assertRaises(SystemExit) as caught:
            cf.verify_masked(payload, masked)
        self.assertIn(cf.AGENTIC_PLACEHOLDER, str(caught.exception))

    def test_capture_refuses_to_write_an_unmasked_file(self):
        payload = self.payload(account=cf.AGENTIC_PLACEHOLDER)
        with self.assertRaises(SystemExit):
            cf.capture("get_accounts_guard_probe", payload)
        written = cf.FIXTURES / "get_accounts_guard_probe.json"
        self.addCleanup(lambda: written.exists() and os.remove(str(written)))
        self.assertFalse(written.exists(),
                         "a capture that failed verification must not land")

    def test_account_values_walks_nested_structures(self):
        deep = {"a": [{"b": {"account_number": "555"}}]}
        self.assertEqual(cf.account_values(deep), {"555"})


class ScannerTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return str(path)

    def test_a_listed_value_is_found(self):
        path = self.write("code.py", 'ACCOUNT = "999888777"\n')
        hits = guard.scan([path], ["999888777"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 1)

    def test_a_clean_file_reports_nothing(self):
        path = self.write("code.py", 'ACCOUNT = "123456789"\n')
        self.assertEqual(guard.scan([path], ["999888777"]), [])

    def test_the_line_number_is_right(self):
        path = self.write("code.py", "one\ntwo\nsecret 42\n")
        hits = guard.scan([path], ["42"])
        self.assertEqual(hits[0][1], 3)

    def test_a_missing_file_does_not_stop_the_scan(self):
        # A scan that raises half way reports clean for the wrong reason.
        present = self.write("code.py", "999888777\n")
        hits = guard.scan([str(self.dir / "gone.py"), present], ["999888777"])
        self.assertEqual(len(hits), 1)

    def test_comments_and_blanks_are_ignored_in_the_list(self):
        path = self.write(".private-values",
                          "# brokerage\n999888777\n\n  \n# balances\n590.0\n")
        self.assertEqual(guard.read_values(path), ["999888777", "590.0"])

    def test_a_missing_list_is_not_an_error(self):
        # Failing the build because the list is absent would train people
        # to delete the hook, which is the opposite of the goal.
        self.assertIsNone(guard.read_values(str(self.dir / "nope")))
        self.assertEqual(guard.main(["--list", str(self.dir / "nope")]), 0)

    def test_the_repository_itself_is_clean(self):
        # The real check, run against this checkout. It uses whatever
        # .private-values holds locally, and passes trivially when the file
        # is absent - which is correct for a fresh clone.
        result = subprocess.run(
            [sys.executable, "-m", "evals.check_no_real_data"],
            cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
