#!/usr/bin/env bash
# A project whose last deploy failed and left a lesson in the run ledger.
set -euo pipefail
eos init . --no-ai >/dev/null
export EOS_SESSION=fixture-session EOS_STATE_DIR="$PWD/.eos/fixture-state"
run_id="$(eos run start . --title "deploy the billing service to staging" 2>/dev/null)"
eos run finish . "$run_id" --outcome failed \
  --lesson "the deploy failed because the staging config key BILLING_URL was missing; set BILLING_URL before deploying" >/dev/null
