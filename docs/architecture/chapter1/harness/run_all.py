"""run_all.py — the one integrated acceptance gate (completion criterion 7).

Order: (1) generate or re-verify the golden vectors, (2) run every unittest
suite (canonicalization, schemas, semantic rules, vectors, cross-process,
isolation, checker negatives), (3) run check_chapter1's registry checks.
Writes a full report and exits 0 iff everything passed.

Usage: python run_all.py [--repo <swarm-coordinator root>] [--report <file>]
"""

import argparse
import io
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    out = io.StringIO()

    def say(line=""):
        print(line)
        out.write(line + "\n")

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    say("Chapter 1 acceptance run, %s" % started)
    say("workspace: %s" % ROOT)
    ok = True

    # 1. goldens
    import gen_goldens
    try:
        status, path = gen_goldens.write_or_verify()
        say("[1] goldens %s (%s)" % (status, path.name))
    except Exception as exc:
        ok = False
        say("[1] goldens FAILED: %r" % exc)

    # 2. unittest suites
    loader = unittest.TestLoader()
    suite = loader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    buffer = io.StringIO()
    runner = unittest.TextTestRunner(stream=buffer, verbosity=2)
    result = runner.run(suite)
    say("[2] unittest: %d tests, %d failures, %d errors"
        % (result.testsRun, len(result.failures), len(result.errors)))
    if result.failures or result.errors:
        ok = False
        say(buffer.getvalue())
    else:
        for line in buffer.getvalue().splitlines():
            if " ... " in line:
                say("    " + line)

    # 3. registry checks
    import check_chapter1
    say("[3] check_chapter1:")
    import contextlib
    checker_out = io.StringIO()
    with contextlib.redirect_stdout(checker_out):
        code = check_chapter1.run(ROOT, Path(args.repo) if args.repo else None)
    for line in checker_out.getvalue().splitlines():
        say("    " + line)
    if code != 0:
        ok = False

    say()
    say("RESULT: %s" % ("PASS" if ok else "FAIL"))
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(out.getvalue())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
