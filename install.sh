#!/usr/bin/env bash
#
# Install (or remove) Vericoding as a skill package by symlinking this tree
# into the agent skill directories.
#
# Two things this script used to get wrong, both measured in a sandbox HOME:
#
#   1. It ran `rm -rf` on the target paths without looking at them. A user who
#      had a real directory at ~/.claude/skills/vericoding -- their own notes,
#      a fork, anything -- lost it silently and got a symlink in its place.
#      Nothing warned, and `set -e` did not help because rm -rf succeeded.
#
#   2. `set -e` plus the diagnostic steps meant the install aborted partway on
#      any host that could not verify: symlinks created, test suite never run,
#      no success or failure summary printed. A diagnostic that halts the
#      thing it is diagnosing is worse than no diagnostic.
#
# So: never delete anything that is not a symlink, and let the checks report
# without killing the install.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGETS=(
    "${HOME}/.gemini/config/skills/vericoding"
    "${HOME}/.claude/skills/vericoding"
)

usage() {
    cat <<EOF
Usage: install.sh [--uninstall]

  (no args)     symlink this tree into the agent skill directories
  --uninstall   remove symlinks that point at this tree, and nothing else
EOF
}

# Remove a link only if it is a symlink we recognise. Anything else is the
# user's, and destroying it is not this script's call to make.
unlink_target() {
    local target="$1"
    if [ -L "$target" ]; then
        local points_to
        points_to="$(readlink -f "$target" 2>/dev/null || true)"
        if [ "$points_to" = "$REPO_DIR" ]; then
            rm -f "$target"
            echo "  removed symlink: $target"
        else
            echo "  left alone (symlink to something else): $target -> $points_to"
        fi
        return 0
    fi
    if [ -e "$target" ]; then
        echo "  left alone (not a symlink): $target"
        return 0
    fi
    return 0
}

link_target() {
    local target="$1"
    if [ -L "$target" ]; then
        rm -f "$target"                     # replacing a symlink is safe
    elif [ -e "$target" ]; then
        echo "✗ REFUSING to touch $target" >&2
        echo "    It exists and is not a symlink. This script used to rm -rf" >&2
        echo "    this path, which silently destroyed whatever was there." >&2
        echo "    Move or remove it yourself, then re-run." >&2
        return 1
    fi
    mkdir -p "$(dirname "$target")"
    ln -s "$REPO_DIR" "$target"
    echo "✓ linked: $target"
    return 0
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
fi

if [ "${1:-}" = "--uninstall" ]; then
    echo "Removing Vericoding skill links..."
    for target in "${TARGETS[@]}"; do
        unlink_target "$target"
    done
    echo "Done. The repository itself was not touched."
    exit 0
fi

echo "=================================================="
echo "    Installing Vericoding Skill Package           "
echo "=================================================="

chmod +x "${REPO_DIR}/bin/vericoding"

link_failures=0
for target in "${TARGETS[@]}"; do
    # Only install into a home that already uses that agent, except for the
    # first one, which is the primary.
    if [ "$target" = "${HOME}/.claude/skills/vericoding" ] && [ ! -d "${HOME}/.claude" ]; then
        continue
    fi
    link_target "$target" || link_failures=$((link_failures + 1))
done

# --- Diagnostics. These report; they do not decide whether the install ran. ---

echo
echo "Environment check:"
env_ok=0
"${REPO_DIR}/bin/vericoding" env --no-targets || env_ok=$?

echo
echo "Test suite:"
tests_ok=0
if [ -n "${VERICODING_INSTALL_SELFTEST:-}" ]; then
    # tests/test_install.py runs this script in a sandbox HOME. Without this
    # guard that is unbounded recursion -- install.sh runs pytest, pytest runs
    # test_install.py, test_install.py runs install.sh -- which is exactly how
    # it was discovered.
    echo "  skipped (already running inside the test suite)"
elif PYTHONPATH="${REPO_DIR}" python3 -c "import pytest" 2>/dev/null; then
    PYTHONPATH="${REPO_DIR}" python3 -m pytest "${REPO_DIR}/tests" -q || tests_ok=$?
else
    echo "  pytest not installed; skipping (it is a development dependency)"
fi

echo
echo "=================================================="
if [ "$link_failures" -gt 0 ]; then
    echo "✗ Install incomplete: $link_failures link(s) refused (see above)."
    exit 1
fi
echo "✓ Vericoding skill linked."
if [ "$env_ok" -ne 0 ]; then
    echo "⚠ This host cannot verify yet. The skill is installed, but"
    echo "  \`vericoding verify\` will not work until the environment check"
    echo "  above passes. Run \`vericoding env\` for the full report."
fi
if [ "$tests_ok" -ne 0 ]; then
    echo "⚠ Test suite did not pass on this host (exit $tests_ok)."
fi
echo "  Usage: vericoding [env|score|verify|compile|receipt|audit|pipeline]"
echo "  Remove with: ./install.sh --uninstall"
echo "=================================================="

# The install itself succeeded; the warnings above are about the environment,
# which is a different question and should not be reported as an install
# failure.
exit 0
