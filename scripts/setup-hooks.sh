#!/usr/bin/env bash
# Enables Jarvis's versioned git hooks (anti-secret guard).
# Run this ONCE after cloning:  bash scripts/setup-hooks.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "✓ Hooks enabled (core.hooksPath = .githooks)."
echo "  pre-commit: blocks secrets in what is staged (paths + patterns + real values)"
echo "              + ownership lock (do not commit another agent's files, via LIVE.md)."
echo "  pre-push:   re-scans ALL outgoing commits before publishing."
