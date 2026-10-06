#!/bin/bash
# Runs every suite. Stdlib Python 3 only — no installs.
cd "$(dirname "$0")"; fail=0
for t in tests/test_*.py; do
  if PYTHONPATH=. python3 "$t" >/tmp/mfe_$$.txt 2>&1; then echo "PASS  $t"; else echo "FAIL  $t"; tail -5 /tmp/mfe_$$.txt; fail=1; fi
done; rm -f /tmp/mfe_$$.txt; exit $fail
