#!/usr/bin/env bash
# Fail if any internal identifier survived the extraction from its origin
# workspace. Run in CI on every push and by hand before publishing.
#
# A one-off manual scrub leaks: someone pastes a real ticket key into a
# comment six months from now and nothing notices. This is the gate that
# notices.
set -uo pipefail

TARGET="${1:-.}"

# Case-insensitive. Each entry is an ERE alternative.
DENY='/Volumes/Data/workspace'

# The origin workspace's own names must not be published either, so they are
# not listed here: EOS_CLEAN_DENY (a CI secret) or the untracked
# .check-clean-deny at the repo root holds them, one ERE per line, # comments.
if [ -n "${EOS_CLEAN_DENY:-}" ]; then
    private="$EOS_CLEAN_DENY"
elif [ -f "$(dirname "$0")/../.check-clean-deny" ]; then
    private=$(cat "$(dirname "$0")/../.check-clean-deny")
else
    private=""
    echo "check-clean: no private denylist (EOS_CLEAN_DENY or .check-clean-deny); generic checks only" >&2
fi
private=$(printf '%s\n' "$private" | grep -vE '^[[:space:]]*(#|$)' | paste -sd'|' -)
[ -n "$private" ] && DENY="$DENY|$private"

# grep exits 2 on a pattern it cannot compile, and a search that errors finds
# nothing: neither may read as clean. The pattern is not echoed -- it holds
# the private names.
grep -Ei -e "$DENY" </dev/null >/dev/null 2>&1
if [ $? -eq 2 ]; then
    echo "check-clean: invalid deny pattern (not an ERE grep accepts); check EOS_CLEAN_DENY or .check-clean-deny" >&2
    exit 2
fi

errors=$(mktemp)
trap 'rm -f "$errors"' EXIT
hits=$(grep -rEin -e "$DENY" "$TARGET" \
    --exclude-dir=.git \
    --exclude=.git \
    --exclude-dir=node_modules \
    --exclude-dir=__pycache__ \
    --exclude-dir=.pytest_cache \
    --exclude-dir=.eos \
    --exclude-dir=static \
    --exclude-dir=.superpowers \
    --exclude='check-clean.sh' \
    --exclude='.check-clean-deny' \
    --exclude='test_check_clean.py' \
    2>"$errors")
if [ $? -eq 2 ]; then
    echo "check-clean: the search failed, so nothing was checked:" >&2
    head -5 "$errors" >&2
    exit 2
fi

if [ -n "$hits" ]; then
    echo "check-clean: internal identifiers found:" >&2
    printf '%s\n' "$hits" | head -50 >&2
    count=$(printf '%s\n' "$hits" | wc -l | tr -d ' ')
    echo "check-clean: $count match(es). Remove them before publishing." >&2
    exit 1
fi

echo "check-clean: clean"
exit 0
