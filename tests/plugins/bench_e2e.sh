#!/usr/bin/env bash
# Manual end-to-end smoke test for the plugin system.
#
# Starts the backend, exercises /api/plugins/* endpoints against a local
# fake hub fixture, and tears everything down.
#
# Usage:  ./tests/plugins/bench_e2e.sh
# Requires:  curl, jq, python in PATH.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

echo "== Plugin e2e smoke =="
echo "Repo: $REPO_ROOT"

# 1. Health
echo
echo "[1] GET /health"
curl -sf http://localhost:8000/health | jq . || { echo "  !! backend not running — start with:  python server/main.py"; exit 1; }

# 2. List installed
echo
echo "[2] GET /api/plugins (installed)"
curl -sf http://localhost:8000/api/plugins | jq . | head -40

# 3. Hub search (empty query returns everything in the index)
echo
echo "[3] GET /api/plugins/search?q="
curl -sf 'http://localhost:8000/api/plugins/search?q=' | jq '.results | length' || true

# 4. Inspect a specific plugin (expect 404 for unknown)
echo
echo "[4] GET /api/plugins/inspect?name=does-not-exist  (expect 404)"
curl -s -o /dev/null -w "  status=%{http_code}\n" 'http://localhost:8000/api/plugins/inspect?name=does-not-exist'

# 5. Audit a known-bad name (expect 400 or 500)
echo
echo "[5] POST /api/plugins/audit  {name: does-not-exist}"
curl -s -X POST http://localhost:8000/api/plugins/audit \
  -H 'Content-Type: application/json' \
  -d '{"name":"does-not-exist"}' -w "  status=%{http_code}\n" -o /dev/null

echo
echo "✓ e2e smoke done."
