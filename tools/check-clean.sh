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
DENY='etiya|FEMBS|BSTP|etiyalabs|/Volumes/Data/workspace|fm-(crm|cpq|pcm|rim|ntf|user|document|domainconfig|common)'

hits=$(grep -rEin "$DENY" "$TARGET" \
    --exclude-dir=.git \
    --exclude-dir=node_modules \
    --exclude-dir=__pycache__ \
    --exclude-dir=.pytest_cache \
    --exclude-dir=.eos \
    --exclude-dir=static \
    --exclude-dir=.superpowers \
    --exclude='check-clean.sh' \
    --exclude='test_check_clean.py' \
    2>/dev/null || true)

if [ -n "$hits" ]; then
    echo "check-clean: internal identifiers found:" >&2
    printf '%s\n' "$hits" | head -50 >&2
    count=$(printf '%s\n' "$hits" | wc -l | tr -d ' ')
    echo "check-clean: $count match(es). Remove them before publishing." >&2
    exit 1
fi

echo "check-clean: clean"
exit 0
