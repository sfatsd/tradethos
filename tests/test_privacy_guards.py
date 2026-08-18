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



class StagedBlobTest(unittest.TestCase):
    """The bug that made the pre-commit use case useless.

    The first version took file names from `git diff --cached` and then
    opened those paths from disk. A secret staged and then edited out of the
    working tree read as clean, while the index still held it. The two
    sources usually agree, which is exactly why it stayed quiet: the demo
    that "proved" the guard worked had matching content in both.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(("git",) + args, cwd=str(self.repo),
                              capture_output=True, text=True)

    def run_guard(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "evals.check_no_real_data"] + list(args),
            cwd=str(self.repo), capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(ROOT)))

    def test_a_secret_staged_then_edited_out_is_still_caught(self):
        (self.repo / "leak.py").write_text('SECRET = "999888777"\n')
        self.git("add", "leak.py")
        (self.repo / "leak.py").write_text('SECRET = "clean"\n')
        (self.repo / ".private-values").write_text("999888777\n")

        result = self.run_guard("--staged")
        self.assertEqual(result.returncode, 1,
                         "the staged blob still holds the secret\n"
                         + result.stdout + result.stderr)
        self.assertIn("leak.py", result.stderr)

    def test_a_clean_staged_blob_passes(self):
        (self.repo / "ok.py").write_text('VALUE = "nothing here"\n')
        self.git("add", "ok.py")
        (self.repo / ".private-values").write_text("999888777\n")
        self.assertEqual(self.run_guard("--staged").returncode, 0)

    def test_a_git_failure_is_not_reported_as_clean(self):
        # Outside a repository the first version produced an empty file
        # list and a confident "clean". A guard whose failure mode is
        # silent success has the one failure mode it cannot have.
        #
        # The directory has to sit outside self.repo, not under it: git
        # walks upward, so a subdirectory of a repository is still in that
        # repository and the call would succeed.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        outside = Path(other.name)
        (outside / ".private-values").write_text("999888777\n")
        result = subprocess.run(
            [sys.executable, "-m", "evals.check_no_real_data"],
            cwd=str(outside), capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(ROOT)))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("could not run", result.stderr)


class MatchingTest(unittest.TestCase):

    def test_short_values_are_rejected_not_matched(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as handle:
            handle.write("# short\n42\n999888777\n")
            path = handle.name
        self.addCleanup(os.remove, path)
        self.assertEqual(guard.read_values(path), ["999888777"])

    def test_a_listed_number_does_not_match_inside_a_longer_one(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.txt")
            with open(path, "w") as handle:
                handle.write("total 1590.055 here\n")
            self.assertEqual(guard.scan([path], ["590.05"]), [])

    def test_a_listed_number_matches_when_it_stands_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.txt")
            with open(path, "w") as handle:
                handle.write("total 590.05 here\n")
            self.assertEqual(len(guard.scan([path], ["590.05"])), 1)


class MaskingBreadthTest(unittest.TestCase):

    def test_an_account_inside_a_url_is_rewritten(self):
        payload = {"order": {
            "account": "https://api.robinhood.com/accounts/998877665/",
            "account_id": "998877665"}}
        masked = cf.mask_accounts(payload, {})
        blob = str(masked)
        self.assertNotIn("998877665", blob)
        self.assertIn("accounts/", masked["order"]["account"])

    def test_three_accounts_get_three_placeholders(self):
        payload = {"a": [{"account_number": "111111111"},
                         {"account_number": "222222222"},
                         {"account_number": "333333333"}]}
        masked = cf.mask_accounts(payload, {})
        values = [x["account_number"] for x in masked["a"]]
        self.assertEqual(len(set(values)), 3, values)

    def test_the_same_account_keeps_one_placeholder(self):
        payload = {"a": [{"account_number": "111111111"},
                         {"account_number": "111111111"}]}
        masked = cf.mask_accounts(payload, {})
        values = [x["account_number"] for x in masked["a"]]
        self.assertEqual(len(set(values)), 1)

    def test_the_sweep_catches_a_value_under_an_unlisted_key(self):
        # An allowlist of key names only covers the shapes someone thought
        # of. The sweep is what covers the rest.
        payload = {"account_number": "998877665",
                   "some_unlisted_key": "998877665"}
        masked = cf.mask_accounts(payload, {})
        with self.assertRaises(SystemExit) as caught:
            cf.verify_masked(payload, masked)
        self.assertIn("998877665", str(caught.exception))




class StoreRedirectTest(unittest.TestCase):
    """The eval must not be able to reach the user's real ledger.

    The skill tells the agent never to pass `--data-dir`, so an eval cannot
    redirect the store by instructing the agent without changing the command
    it is measuring. The environment variable moves the store instead, and
    the agent runs exactly what the skill documents.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cli = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"

    def run_cli(self, *args, **kwargs):
        env = dict(os.environ)
        env.pop("TRADETHOS_DATA_DIR", None)
        env.update(kwargs.get("env") or {})
        return subprocess.run([sys.executable, str(self.cli)] + list(args),
                              capture_output=True, text=True, env=env)

    def test_the_env_var_redirects_the_store(self):
        result = self.run_cli("create", "Probe", "--symbols", "WDC:100",
                              "--account", "123456789",
                              env={"TRADETHOS_DATA_DIR": str(self.dir)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.dir / "events.log.jsonl").exists(),
                        "the store did not land in the redirected directory")

    def test_an_explicit_data_dir_still_wins(self):
        other = self.dir / "explicit"
        other.mkdir()
        result = self.run_cli("--data-dir", str(other), "create", "Probe",
                              "--symbols", "WDC:100", "--account", "123456789",
                              env={"TRADETHOS_DATA_DIR": str(self.dir)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((other / "events.log.jsonl").exists())
        self.assertFalse((self.dir / "events.log.jsonl").exists())

    def test_the_real_store_is_untouched_by_a_redirected_write(self):
        from evals.run_case import fingerprint, REAL_STORE
        before = fingerprint(REAL_STORE)
        self.run_cli("create", "Probe", "--symbols", "WDC:100",
                     "--account", "123456789",
                     env={"TRADETHOS_DATA_DIR": str(self.dir)})
        self.assertEqual(fingerprint(REAL_STORE), before,
                         "a redirected write reached the real ledger")

    def test_the_tripwire_notices_a_change(self):
        # The tripwire is only worth having if it can fail. A guard that
        # cannot detect the thing it guards against is decoration.
        from evals.run_case import fingerprint
        watched = self.dir / "watched"
        watched.mkdir()
        (watched / "a.jsonl").write_text("one\n")
        before = fingerprint(watched)
        (watched / "a.jsonl").write_text("one\ntwo\n")
        self.assertNotEqual(fingerprint(watched), before)

    def test_the_tripwire_is_quiet_when_nothing_changes(self):
        from evals.run_case import fingerprint
        watched = self.dir / "steady"
        watched.mkdir()
        (watched / "a.jsonl").write_text("one\n")
        self.assertEqual(fingerprint(watched), fingerprint(watched))

if __name__ == "__main__":
    unittest.main()
