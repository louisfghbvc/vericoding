#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_TARGET_DIR="${HOME}/.gemini/config/skills/vericoding"
CLAUDE_SKILL_DIR="${HOME}/.claude/skills/vericoding"

echo "=================================================="
echo "    Installing Vericoding Skill Package           "
echo "=================================================="

chmod +x "${REPO_DIR}/bin/vericoding" "${REPO_DIR}"/scripts/*.py

# 1. Install / link to Antigravity global skills directory
mkdir -p "$(dirname "${SKILL_TARGET_DIR}")"
rm -rf "${SKILL_TARGET_DIR}"
ln -s "${REPO_DIR}" "${SKILL_TARGET_DIR}"
echo "✓ Symlinked to Antigravity skills: ${SKILL_TARGET_DIR}"

# 2. Check and link to Claude Code skills if ~/.claude exists
if [ -d "${HOME}/.claude" ]; then
    mkdir -p "$(dirname "${CLAUDE_SKILL_DIR}")"
    rm -rf "${CLAUDE_SKILL_DIR}"
    ln -s "${REPO_DIR}" "${CLAUDE_SKILL_DIR}"
    echo "✓ Symlinked to Claude Code skills: ${CLAUDE_SKILL_DIR}"
fi

# 3. Check dependencies
echo -e "\nRunning environment check:"
"${REPO_DIR}/bin/vericoding" env

# 4. Verify test suite
echo -e "\nRunning test suite:"
PYTHONPATH="${REPO_DIR}" pytest "${REPO_DIR}/tests" -q

echo -e "\n=================================================="
echo "✓ Vericoding Skill Installed Successfully!"
echo "  Usage: vericoding [score|verify|compile|receipt|pipeline]"
echo "  Or invoke directly via agent skill: vericoding"
echo "=================================================="
