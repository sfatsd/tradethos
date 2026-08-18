"""Check that no real value from a private list reached a tracked file.

The problem this solves is specific. A repository cannot carry a list of the
values it must never contain, because that list would be the leak. So the
list lives in `.private-values`, which is git-ignored, and this script reads
it and searches the tracked content for anything on it.

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
    python -m evals.check_no_real_data --staged   # the staged blobs

**`--staged` reads the blob, not the file.** The first version of this script
took the file names from `git diff --cached` and then opened those paths from
disk. That is the wrong content: a secret can be staged and then edited out of
the working tree, and the scan would read the clean version and report clean
while the secret sat in the index waiting to be committed. The two sources
usually agree, which is what makes the bug so quiet. `git show :<path>` reads
what git will actually commit.

**A git failure is never reported as clean.** The same first version discarded
git's exit code, so running outside a repository produced an empty file list
and a confident "clean". A guard whose failure mode is silent success has the
one failure mode it cannot have.

Exit code 0 means nothing on the list was found. Exit code 1 means it was, and
the offending file and line are printed. Exit code 2 means the check could not
run, which is not the same as passing.

Short values are rejected rather than matched. `42` appears in almost any
file, and a guard that cries wolf is a guard people learn to skip.
"""

import argparse
import os
import re
import subprocess
import sys


DEFAULT_LIST = ".private-values"

# Below this length a value matches too much to be useful. A four-character
# account fragment or a bare "590" would fire on line numbers, quantities and
# timestamps until nobody reads the output.
MIN_VALUE_LENGTH = 5


class CheckError(Exception):
    """The check could not run. Not the same as finding nothing."""


def read_values(path):
    """Return the values to search for, or None when the list is absent."""
    if not os.path.exists(path):
        return None
    values, short = [], []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if len(line) < MIN_VALUE_LENGTH:
                short.append(line)
                continue
            values.append(line)
    if short:
        print("ignoring %d value(s) shorter than %d characters: %s"
              % (len(short), MIN_VALUE_LENGTH, ", ".join(short)),
              file=sys.stderr)
    return values


def _git(*args):
    """Run git and raise when it fails, rather than returning nothing."""
    result = subprocess.run(("git",) + args, capture_output=True, text=True)
    if result.returncode != 0:
        raise CheckError("git %s failed: %s"
                         % (" ".join(args),
                            result.stderr.strip() or "no output"))
    return result.stdout


def tracked_files(staged=False):
    if staged:
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    else:
        out = _git("ls-files")
    return [f for f in out.splitlines() if f.strip()]


def read_content(path, staged=False):
    """Return the text to scan: the staged blob, or the file on disk.

    Returns None when there is nothing readable, so a deleted path or a
    binary blob is skipped rather than stopping the scan.
    """
    if staged:
        result = subprocess.run(["git", "show", ":%s" % path],
                                capture_output=True, text=True,
                                errors="ignore")
        return result.stdout if result.returncode == 0 else None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return None


def scan(paths, values, staged=False):
    """Return every (path, line number, value) match.

    A word boundary keeps a listed number from matching inside a longer one,
    so `590.05` on the list does not fire on `1590.055`.
    """
    patterns = [(v, re.compile(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])"
                               % re.escape(v))) for v in values]
    hits = []
    for path in paths:
        content = read_content(path, staged)
        if content is None:
            continue
        for number, line in enumerate(content.splitlines(), 1):
            for value, pattern in patterns:
                if pattern.search(line):
                    hits.append((path, number, value))
    return hits


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true",
                        help="Scan the staged blobs, not the working tree")
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
        print("%s has no usable values, nothing to check" % args.list)
        return 0

    try:
        hits = scan(tracked_files(args.staged), values, args.staged)
    except CheckError as error:
        print("check could not run: %s" % error, file=sys.stderr)
        return 2

    if not hits:
        print("clean: %d value(s) checked against %s"
              % (len(values),
                 "the staged blobs" if args.staged else "all tracked files"))
        return 0

    print("Found %d occurrence(s) of a private value in %s:"
          % (len(hits), "staged content" if args.staged else "tracked files"),
          file=sys.stderr)
    for path, number, value in hits:
        print("  %s:%d  contains %r" % (path, number, value), file=sys.stderr)
    print("\nRemove these before committing. If a value is already pushed, "
          "rewriting the branch is the only way to take it back.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
