"""Check that no real value from a private list reached a tracked file.

The problem this solves is specific. A repository cannot carry a list of the
values it must never contain, because that list would be the leak. So the
list lives in `.private-values`, which is git-ignored, and this script reads
it and searches the tracked files for anything on it.

That inverts the usual approach. A pattern-based scanner has to guess what a
real account number looks like, and it either misses the ones that look
ordinary or drowns the output in false positives from test fixtures. Matching
against values the user already knows are real gives an exact answer.

`.private-values` holds one value per line. Blank lines and lines starting
with `#` are ignored. The values below are invented, for the same reason this
file exists:

    # brokerage
    999888777
    111222333
    # balances that appeared in a portfolio call
    1234.56

Two modes:

    python -m evals.check_no_real_data            # every tracked file
    python -m evals.check_no_real_data --staged   # only what is staged

The staged mode is the one worth putting in a pre-commit hook, because it
answers the question at the moment it still costs nothing to fix. Run it
without arguments after a rewrite to confirm the history is actually clean.

Exit code 0 means nothing on the list was found. Exit code 1 means it was,
and the offending file and line are printed.
"""

import argparse
import os
import subprocess
import sys


DEFAULT_LIST = ".private-values"


def read_values(path):
    if not os.path.exists(path):
        return None
    values = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                values.append(line)
    return values


def tracked_files(staged=False):
    if staged:
        command = ["git", "diff", "--cached", "--name-only",
                   "--diff-filter=ACM"]
    else:
        command = ["git", "ls-files"]
    out = subprocess.run(command, capture_output=True, text=True)
    return [f for f in out.stdout.splitlines() if f.strip()]


def scan(paths, values):
    """Return every (path, line number, value) match.

    Binary files and files that have gone away are skipped rather than
    raising: a scan that stops early is a scan that reports clean for the
    wrong reason.
    """
    hits = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", errors="ignore") as handle:
                for number, line in enumerate(handle, 1):
                    for value in values:
                        if value in line:
                            hits.append((path, number, value))
        except OSError:
            continue
    return hits


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true",
                        help="Scan only the staged changes")
    parser.add_argument("--list", default=DEFAULT_LIST,
                        help="Path to the private value list "
                             "(default: %s)" % DEFAULT_LIST)
    args = parser.parse_args(argv)

    values = read_values(args.list)
    if values is None:
        # Not an error. Most checkouts will not have the list, and failing
        # the build for its absence would train people to delete the hook.
        print("no %s found, nothing to check" % args.list)
        return 0
    if not values:
        print("%s is empty, nothing to check" % args.list)
        return 0

    hits = scan(tracked_files(args.staged), values)
    if not hits:
        print("clean: %d value(s) checked against %s files"
              % (len(values),
                 "staged" if args.staged else "all tracked"))
        return 0

    print("Found %d occurrence(s) of a private value in tracked content:"
          % len(hits), file=sys.stderr)
    for path, number, value in hits:
        print("  %s:%d  contains %r" % (path, number, value), file=sys.stderr)
    print("\nRemove these before committing. If a value is already pushed, "
          "rewriting the branch is the only way to take it back.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
