# The prompt

Fill `{PROJECT}` and `{SCENARIO}`, start a session that has none of your
context, and paste the whole thing. Do not add hints. A hint you add here is a
hint the next real reader will not get.

---

You are evaluating a tool's documentation by using it for real work. Your job
is not to finish the task at any cost — it is to report honestly what using
the tool was like, including everything that went wrong.

**The project:** `{PROJECT}`

**Your task:** {SCENARIO}

**What you may use:**

- The skill document at `{PROJECT}/.eos/` or wherever your platform loaded it.
  Read it once, as you normally would.
- The `eos` command line.
- `Read`, `Grep` and `Glob` on the project's own source.

**What you may not use:** the EOS source code, `eos --help` as a first resort,
any wrapper script, and anything you happen to know about this project from
elsewhere. If you use one of these anyway, say so in your report rather than
hiding it — the fact that you needed it is the finding.

**Be unsparing.** A dead end is the most valuable thing you can report. If the
document said something that turned out to be wrong, quote it. If an answer
was empty and you could not tell whether the tool had looked, say so. If you
wanted to ask a question the tool does not have, describe the question. Do not
smooth any of this over, and do not thank the tool for working.

**Report back, in this order:**

1. **Commands, in order.** Every one you ran, including the ones that went
   nowhere. Mark the first one that was actually useful.
2. **How you found them.** Did the document tell you, or did you fall back to
   `--help`, source, or guessing?
3. **Wrong turns.** For each: what you believed, what made you believe it, and
   what corrected you. This is the section that matters most.
4. **What you wanted and could not ask for.**
5. **Numbers.** Every count, tally or measurement you saw, verbatim.
6. **The answer to the task**, last and briefly.

If you finished the task without difficulty and have nothing for sections 3
and 4, say exactly that. Do not invent findings to fill them.
