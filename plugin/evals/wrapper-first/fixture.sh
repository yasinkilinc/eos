#!/usr/bin/env bash
# A project with one declared wrapper (capabilities.toml) for its issue tracker.
set -euo pipefail
eos init . --no-ai >/dev/null
mkdir -p .eos/knowledge scripts
printf '%s\n' '#!/bin/sh' \
  '# The project tracker wrapper: one compact line, never raw JSON.' \
  'case "$1" in issue) echo "DEMO-1  In Review  assignee: sam" ;; *) echo "usage: tracker.sh issue <KEY>" >&2; exit 2 ;; esac' \
  > scripts/tracker.sh
chmod +x scripts/tracker.sh
printf '%s\n' '[[capability]]' 'name = "tracker"' 'run = "scripts/tracker.sh"' \
  'does = "issue <KEY> (one compact status line)"' 'words = ["tracker", "ticket"]' \
  "hint = ['curl\\s[^|;&]*tracker\\.example']" > .eos/knowledge/capabilities.toml
