#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON="${EOS_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  done
fi
# `:-` guards EOS_INSTALL_PREFIX but not $HOME inside the default, and `set -u`
# then aborts with "HOME: unbound variable" -- the shape of `docker run -u 1000`
# and of several CI runners.
PREFIX=${EOS_INSTALL_PREFIX:-${HOME:-/usr/local}/.local}
DATA_DIR=${EOS_DATA_DIR:-"$PREFIX/share/eos"}
BIN_DIR=${EOS_BIN_DIR:-"$PREFIX/bin"}
VERSION=$(tr -d '[:space:]' < "$SOURCE_ROOT/core/VERSION")
TARGET_DIR="$DATA_DIR/$VERSION"
TARGET_CORE="$TARGET_DIR/core"
LAUNCHER="$BIN_DIR/eos"

if [ ! -f "$SOURCE_ROOT/core/eos.py" ] || [ -z "$VERSION" ]; then
  printf '%s\n' "EOS source is incomplete: core/eos.py and core/VERSION are required" >&2
  exit 1
fi

if [ -z "$PYTHON" ] || ! command -v "$PYTHON" >/dev/null 2>&1; then
  printf '%s\n' "Python 3.11+ executable not found" >&2
  exit 1
fi
PYTHON=$(command -v "$PYTHON")

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  printf '%s\n' "EOS requires Python 3.11 or newer" >&2
  exit 1
fi

mkdir -p "$TARGET_CORE" "$BIN_DIR"
"$PYTHON" - "$SOURCE_ROOT/core" "$TARGET_CORE" <<'PY'
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
shutil.copytree(
    source,
    target,
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
)
PY

if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  if [ -L "$LAUNCHER" ]; then
    existing_target=$(readlink "$LAUNCHER")
    case "$existing_target" in
      "$DATA_DIR"/*) ;;
      *)
        printf '%s\n' "Refusing to replace existing external symlink: $LAUNCHER" >&2
        exit 1
        ;;
    esac
  elif ! grep -q '^# EOS managed launcher$' "$LAUNCHER" 2>/dev/null; then
    printf '%s\n' "Refusing to replace existing non-EOS executable: $LAUNCHER" >&2
    printf '%s\n' "Set EOS_BIN_DIR to an empty bin directory or remove it explicitly." >&2
    exit 1
  fi
fi
"$PYTHON" - "$TARGET_CORE/eos.py" "$LAUNCHER" "$PYTHON" <<'PY'
import shlex
import sys
from pathlib import Path

target = shlex.quote(sys.argv[1])
launcher = Path(sys.argv[2])
python = shlex.quote(sys.argv[3])
launcher.write_text(
    "#!/bin/sh\n"
    "# EOS managed launcher\n"
    "set -eu\n"
    f"PYTHON=${{EOS_PYTHON:-{python}}}\n"
    f'exec "$PYTHON" {target} "$@"\n',
    encoding="utf-8",
)
launcher.chmod(0o755)
PY

# Drop every other installed version. The launcher just written names exactly
# one of them, so the rest are unreachable -- 19 of them had accumulated here
# before this pruned, none reachable. The canonical source is tracked in git,
# so a downgrade is a checkout plus a re-run of this script, not a directory
# to keep. `if` rather than `[ ... ] && continue`: under `set -e` a failing
# test as the last command of a loop body aborts the script.
for installed in "$DATA_DIR"/*; do
  if [ -d "$installed" ] && [ "$installed" != "$TARGET_DIR" ]; then
    rm -rf "$installed"
  fi
done

printf '%s\n' "EOS $VERSION installed: $LAUNCHER"

# Membership ("is $BIN_DIR anywhere in PATH?") is the wrong question: with
# $BIN_DIR present but late, an unrelated executable named `eos` earlier in
# PATH still wins every time anyone types `eos`, and the old test stayed
# silent. Walk PATH in order instead, stopping at $BIN_DIR, and report the
# first foreign `eos` ahead of it. Launchers written above carry the marker
# line, so another managed EOS is not treated as shadowing.
bin_dir_in_path=""
shadowing_eos=""
saved_ifs=$IFS
IFS=:
for path_entry in $PATH; do
  [ -n "$path_entry" ] || path_entry=.
  if [ "$path_entry" = "$BIN_DIR" ]; then
    bin_dir_in_path=yes
    break
  fi
  # -f as well as -x: directories carry the execute bit, so a directory named
  # `eos` inside a PATH entry would otherwise be reported as a shadowing
  # binary, telling the user to remove something that shadows nothing.
  if [ -z "$shadowing_eos" ] && [ -f "$path_entry/eos" ] && [ -x "$path_entry/eos" ] \
    && ! grep -q '^# EOS managed launcher$' "$path_entry/eos" 2>/dev/null; then
    shadowing_eos="$path_entry/eos"
  fi
done
IFS=$saved_ifs

if [ -z "$bin_dir_in_path" ]; then
  printf '%s\n' "Add this directory to PATH if needed: $BIN_DIR"
fi
if [ -n "$shadowing_eos" ]; then
  printf '%s\n' "WARNING: $shadowing_eos comes earlier in PATH and is not an EOS managed launcher." >&2
  printf '%s\n' "Typing 'eos' will run it instead of $LAUNCHER." >&2
  printf '%s\n' "Remove or rename it, or put this directory first: export PATH=\"$BIN_DIR:\$PATH\"" >&2
fi
