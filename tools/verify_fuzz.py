#!/usr/bin/env python3
"""Differential fuzzer: core.verify._runs(command, regex) vs real bash.

For each generated shell command (grammar of realistic agent-written forms),
compare what _runs() predicts against what bash actually does when CHECK is
defined once to pass and once to fail. A "false clear" is a case where _runs
says True but bash's real exit status did not, in fact, depend exclusively on
CHECK having passed.

    python3 tools/verify_fuzz.py [N] [--out results.jsonl]

tests/test_verify_fuzz.py runs a small sample on every suite run (ADR-028).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor

EOS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EOS_ROOT)
from core import verify  # noqa: E402

REGEX = r"CHECK\b"
BASH = "/bin/bash"
TIMEOUT = 2.0

PASS_DEF = "CHECK() { touch ran_ok; return 0; }\nexport -f CHECK\n"
FAIL_DEF = "CHECK() { touch ran_fail; return 7; }\nexport -f CHECK\n"

NEUTRAL = ["true", "false", ":", "echo x", "printf y", "cd .", "pwd >/dev/null",
           "exit 0", "exit 3", "ls >/dev/null 2>&1"]
HARMLESS_TAIL = ["echo PASS", "echo done", "true", ":", "printf ok"]
NONHARMLESS_TAIL = ["cd .", "pwd >/dev/null", "exit 0", "date >/dev/null"]


def neutral(rng):
    return rng.choice(NEUTRAL)


# --- Template library -------------------------------------------------
# Each template is a function(rng) -> str containing exactly one (usually)
# literal "CHECK" token representing an invocation of the check function.
# Templates are tagged by name for shape-bucketing / dedup.

TEMPLATES = {}


def template(name):
    def deco(fn):
        TEMPLATES[name] = fn
        return fn
    return deco


@template("bare")
def t_bare(rng):
    return "CHECK"


@template("seq_semicolon_tail")
def t_seq_semi(rng):
    return f"CHECK; {neutral(rng)}"


@template("seq_semicolon_head")
def t_seq_semi_head(rng):
    return f"{neutral(rng)}; CHECK"


@template("seq_newline_tail")
def t_seq_nl(rng):
    return f"CHECK\n{neutral(rng)}"


@template("and_harmless_tail")
def t_and_harmless(rng):
    n = rng.randint(1, 3)
    tail = " && ".join(rng.choice(HARMLESS_TAIL) for _ in range(n))
    return f"CHECK && {tail}"


@template("and_nonharmless_tail")
def t_and_nonharmless(rng):
    return f"CHECK && {rng.choice(NONHARMLESS_TAIL)}"


@template("or_tail")
def t_or_tail(rng):
    return f"CHECK || {rng.choice(HARMLESS_TAIL)}"


@template("head_or_check")
def t_head_or_check(rng):
    # `true || CHECK` : check never runs (short-circuit)
    return f"true || CHECK"


@template("head_and_check_fail")
def t_head_and_fail(rng):
    # `false && CHECK` : check never runs (short-circuit)
    return f"false && CHECK"


@template("pipe_tail_notrust")
def t_pipe_tail(rng):
    return f"CHECK | tail -3"


@template("pipe_cat_notrust")
def t_pipe_cat(rng):
    return f"CHECK | cat"


@template("pipe_amp_notrust")
def t_pipe_amp(rng):
    # `|&` : bash 3.2 (this system) does not support it (added bash 4.0).
    return f"CHECK |& cat"


@template("pipefail_then_pipe")
def t_pipefail_pipe(rng):
    return f"set -o pipefail\nCHECK | tail -3"


@template("pipefail_inline_and")
def t_pipefail_inline(rng):
    return f"set -o pipefail && CHECK | tail -3"


@template("pipe_into_check")
def t_pipe_into_check(rng):
    return f"echo hi | CHECK"


@template("if_then_fi_oneline")
def t_if_oneline(rng):
    return f"if CHECK; then echo y; fi"


@template("if_else_oneline")
def t_if_else(rng):
    return f"if false; then echo n; else CHECK; fi"


@template("if_true_check_inside")
def t_if_true_inside(rng):
    return f"if true; then CHECK; fi"


@template("if_multiline")
def t_if_multiline(rng):
    return "if CHECK\nthen\n  echo y\nfi"


@template("while_zero_iter")
def t_while_zero(rng):
    return "while false; do CHECK; done"


@template("until_zero_iter")
def t_until_zero(rng):
    return "until true; do CHECK; done"


@template("for_real_iter")
def t_for_real(rng):
    return "for i in 1 2; do CHECK; done"


@template("for_zero_iter")
def t_for_zero(rng):
    return "for i in; do CHECK; done"


@template("case_hit")
def t_case_hit(rng):
    return "case x in\n  x) CHECK ;;\n  *) true ;;\nesac"


@template("case_miss_branch")
def t_case_miss(rng):
    return "case y in\n  x) CHECK ;;\n  *) true ;;\nesac"


@template("func_not_called")
def t_func_not_called(rng):
    return "foo() { CHECK; }"


@template("func_called_same_line")
def t_func_called(rng):
    return "foo() { CHECK; }; foo"


@template("func_keyword_called")
def t_func_keyword(rng):
    return "function foo { CHECK; }; foo"


@template("bang_false")
def t_bang_false(rng):
    return "! false"


@template("bang_check")
def t_bang_check(rng):
    return "! CHECK"


@template("time_check")
def t_time_check(rng):
    return "time CHECK"


@template("bash_dash_c_dquote")
def t_bash_c_d(rng):
    return 'bash -c "CHECK"'


@template("bash_dash_c_squote")
def t_bash_c_s(rng):
    return "bash -c 'CHECK'"


@template("bash_dash_c_nested_and")
def t_bash_c_and(rng):
    return 'bash -c "CHECK && echo ok"'


@template("cmdsub_assign")
def t_cmdsub_assign(rng):
    return "x=$(CHECK)"


@template("cmdsub_echo")
def t_cmdsub_echo(rng):
    return "echo $(CHECK)"


@template("backtick_echo")
def t_backtick(rng):
    return "echo `CHECK`"


@template("cmdsub_masks_then_real_check")
def t_cmdsub_then_check(rng):
    return "echo $(false; true) && CHECK"


@template("arith_expansion_then_check")
def t_arith_then_check(rng):
    return "x=$((1+2)) && CHECK"


@template("cmdsub_bareparen_then_check")
def t_cmdsub_bareparen(rng):
    return ": $(false; (true)) && CHECK"


@template("cmdsub_bareparen_nonharmless_after")
def t_cmdsub_bareparen_nh(rng):
    # exercises the _masked stray-')' bug right before a non-harmless tail
    return ": $(true; (true)) && CHECK && cd ."


@template("subshell_wrap")
def t_subshell(rng):
    return "( CHECK )"


@template("subshell_wrap_and_tail")
def t_subshell_tail(rng):
    return "( CHECK ) && echo ok"


@template("group_wrap")
def t_group(rng):
    return "{ CHECK; }"


@template("group_wrap_and_tail")
def t_group_tail(rng):
    return "{ CHECK; } && echo ok"


@template("set_e_plain")
def t_set_e(rng):
    return "set -e\nCHECK"


@template("set_e_then_false_then_check")
def t_set_e_false(rng):
    return "set -e\nfalse\nCHECK"


@template("set_plus_e")
def t_set_plus_e(rng):
    return "set +e\nCHECK"


@template("set_euo_pipefail")
def t_set_euo(rng):
    return "set -euo pipefail\nCHECK"


@template("set_plus_o_errexit")
def t_set_plus_o_errexit(rng):
    return "set -e\nset +o errexit\nfalse\nCHECK"


@template("set_e_and_chain_tail_nonharmless")
def t_set_e_and_chain(rng):
    return "set -e\nCHECK && false"


@template("set_e_last_and_segment")
def t_set_e_last_and(rng):
    return "set -e\ntrue && CHECK"


@template("cd_then_check")
def t_cd_check(rng):
    return "cd . && CHECK"


@template("check_then_exit_status")
def t_check_exit_status(rng):
    return "CHECK; exit $?"


@template("comment_before")
def t_comment_before(rng):
    return "# do the thing\nCHECK"


@template("comment_after_check")
def t_comment_after(rng):
    return "CHECK # trailing comment"


@template("heredoc_before_check")
def t_heredoc_before(rng):
    return "cat <<EOF\nhello\nEOF\nCHECK"


@template("heredoc_on_check_line")
def t_heredoc_on_check(rng):
    return "cat <<EOF && CHECK\nhello\nEOF"


@template("env_prefix_check")
def t_env_prefix(rng):
    return "FOO=bar CHECK"


@template("command_builtin_check")
def t_command_builtin(rng):
    return "command CHECK"


@template("exec_check")
def t_exec_check(rng):
    return "exec CHECK"


@template("nice_check")
def t_nice_check(rng):
    return "nice CHECK"


@template("nohup_check")
def t_nohup_check(rng):
    return "nohup CHECK"


@template("timeout_check")
def t_timeout_check(rng):
    return "timeout 5 CHECK"


@template("double_check_seq")
def t_double_check_seq(rng):
    return "CHECK; CHECK"


@template("double_check_and")
def t_double_check_and(rng):
    return "CHECK && CHECK"


@template("multi_and_chain")
def t_multi_and_chain(rng):
    return "true && true && CHECK"


@template("multi_and_chain_fail_middle")
def t_multi_and_chain_fail(rng):
    return "true && false && CHECK"


@template("check_background")
def t_check_bg(rng):
    return "CHECK &\nwait"


@template("subshell_inside_pipeline_last")
def t_subshell_pipeline_last(rng):
    return "echo hi | ( CHECK )"


@template("subshell_inside_pipeline_first_nopipefail")
def t_subshell_pipeline_first(rng):
    return "( CHECK ) | cat"


@template("subshell_inside_pipeline_first_pipefail")
def t_subshell_pipeline_first_pf(rng):
    return "set -o pipefail\n( CHECK ) | cat"


@template("nested_subshell")
def t_nested_subshell(rng):
    return "( ( CHECK ) )"


@template("bash_c_inside_and")
def t_bash_c_inside_and(rng):
    return 'true && bash -c "CHECK"'


@template("dashc_pipe_last")
def t_dashc_pipe_last(rng):
    return 'echo x | bash -c "CHECK"'


@template("multiline_and_split")
def t_multiline_and_split(rng):
    return "CHECK \\\n  && echo ok"


@template("if_elif_else")
def t_if_elif_else(rng):
    return "if false; then echo a; elif true; then CHECK; else echo b; fi"


@template("select_like_case_default")
def t_case_default_only(rng):
    return "case z in\n  *) CHECK ;;\nesac"


@template("for_c_style")
def t_for_c_style(rng):
    return "for ((i=0;i<0;i++)); do CHECK; done"


def wrap_noise(rng, s, name):
    """Randomly add harmless surrounding noise; returns (string, shape suffix)."""
    choices = []
    r = rng.random()
    if r < 0.15:
        return f"{neutral(rng)}\n{s}", name + "+prefix_noise"
    if r < 0.30:
        return f"{s}\n{neutral(rng)}" if not s.rstrip().endswith("CHECK") else s, name
    if r < 0.40:
        return f"echo start >/dev/null; {s}", name + "+prefix_semi_noise"
    return s, name


def generate(rng):
    name = rng.choice(list(TEMPLATES.keys()))
    s = TEMPLATES[name](rng)
    s, shape = wrap_noise(rng, s, name)
    return s, shape


# --- Execution against real bash ---------------------------------------

def run_bash(command_body: str, preamble: str, workdir: str) -> tuple[int, bool, bool]:
    script = preamble + command_body
    try:
        proc = subprocess.run(
            [BASH, "-c", script],
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
        )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = -1  # sentinel: hang / timeout
    ran_ok = os.path.exists(os.path.join(workdir, "ran_ok"))
    ran_fail = os.path.exists(os.path.join(workdir, "ran_fail"))
    return code, ran_ok, ran_fail


def eval_one(item):
    idx, command, shape = item
    result = {"idx": idx, "command": command, "shape": shape}
    try:
        runs_result = verify._runs(command, REGEX)
        result["runs"] = runs_result
        result["crash"] = None
    except Exception:  # noqa: BLE001
        result["runs"] = None
        result["crash"] = traceback.format_exc()
        return result

    with tempfile.TemporaryDirectory(prefix="fz_") as d1:
        pass_code, pass_ok, pass_fail_marker = run_bash(command, PASS_DEF, d1)
    with tempfile.TemporaryDirectory(prefix="fz_") as d2:
        fail_code, fail_ok, fail_fail_marker = run_bash(command, FAIL_DEF, d2)

    result.update(
        pass_code=pass_code, pass_ran=pass_ok,
        fail_code=fail_code, fail_ran=fail_fail_marker,
    )

    # Whether the check's status truly, exclusively governs the command's
    # exit status (used only for the false-refusal info metric).
    truly_gated = (pass_code == 0 and pass_ok and fail_code != 0 and fail_fail_marker)
    result["truly_gated"] = truly_gated

    false_clear = False
    if runs_result:
        if fail_code == 0:
            false_clear = True
        elif pass_code == 0 and not pass_ok:
            false_clear = True
    result["false_clear"] = false_clear
    return result


def run(n: int, seed: int = 20260927, out_path: str | None = None) -> dict:
    rng = random.Random(seed)
    items = []
    while len(items) < n:
        command, shape = generate(rng)
        items.append((len(items), command, shape))
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(eval_one, items))
    if out_path:
        with open(out_path, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
    truly_gated = [r for r in results if r.get("truly_gated")]
    return {"generated": len(results),
            "runs_true": sum(1 for r in results if r["runs"] is True),
            "crashes": [r for r in results if r["crash"]],
            "false_clears": [r for r in results if r.get("false_clear")],
            "truly_gated": len(truly_gated),
            "false_refusals": sum(1 for r in truly_gated if r["runs"] is False)}


def main():
    args = sys.argv[1:]
    out_path = args[args.index("--out") + 1] if "--out" in args else None
    numbers = [a for a in args if a.isdigit()]
    summary = run(int(numbers[0]) if numbers else 20000, out_path=out_path)
    print(f"generated={summary['generated']}")
    print(f"runs_true={summary['runs_true']}")
    print(f"crashes={len(summary['crashes'])}")
    print(f"false_clears={len(summary['false_clears'])}")
    print(f"truly_gated_cases={summary['truly_gated']}")
    print(f"false_refusals_among_truly_gated={summary['false_refusals']}")
    shapes = {}
    for r in summary["false_clears"]:
        shapes.setdefault(r["shape"], []).append(r)
    print(f"distinct_false_clear_shapes={len(shapes)}")
    for shape, rs in shapes.items():
        print(f"  shape={shape} count={len(rs)} example={rs[0]['command']!r}")
    for r in summary["crashes"][:5]:
        print(r["command"])
        print(r["crash"])
    return 1 if summary["false_clears"] or summary["crashes"] else 0


if __name__ == "__main__":
    sys.exit(main())
