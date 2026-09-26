# EOS plugin evals

`claude plugin eval` cases for what the plugin adds to a session, each run
with and without the plugin (the Δ arm):

- `wrapper-first` — a project declares its tracker wrapper in
  `capabilities.toml`; the question is which command to run. With the plugin
  the task brief names the wrapper before the first call.
- `last-lesson` — the project's last deploy failed and left a lesson in the
  run ledger; the question is what went wrong. With the plugin the lesson
  arrives with the task.

Both scaffold a project with `eos` (so `eos` must be on PATH) and grant only
read-only tools: a Bash-granting eval refuses to run on a machine whose Docker
credential store contains a symbolic link, and these cases do not need Bash to
show the difference.

```bash
claude plugin eval plugin --scaffold --trust-plugin --runs 3 --model sonnet \
  --max-cost-usd 3 --no-publish --output-dir /tmp/eos-plugin-eval
```

Keep `--output-dir` outside the repository: the reports carry absolute paths,
which `tools/check-clean.sh` refuses. If `ANTHROPIC_API_KEY` in your shell is
stale, the child sessions fail with 401 — run with `env -u ANTHROPIC_API_KEY`.

First measurement (2026-09-26, one run per arm, sonnet): both arms answer both
cases correctly (Δ score 0 — in a two-file fixture the answer is easy to find),
but with the plugin each case took 1 turn against 7–8 without it;
`wrapper-first` cost $0.039 against $0.091, `last-lesson` $0.156 against $0.099
(the brief and the plugin's listing cost more than eight cheap reads in a tiny
project). The difference that matters is expected where finding the answer is
not cheap, which these fixtures do not model.
