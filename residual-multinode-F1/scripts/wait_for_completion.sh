#!/usr/bin/env bash
set -euo pipefail
NS="residual-demo"
TIMEOUT_SECONDS="${1:-90}"
EXPECTED_SCENARIO="${2:-}"
EXPECTED_RUN_ID="${3:-}"
PORT="${STATUS_PORT:-18080}"

pf_log=$(mktemp)
kubectl -n "$NS" port-forward svc/gc-master "$PORT":8080 >"$pf_log" 2>&1 &
pf=$!
cleanup() {
  kill "$pf" 2>/dev/null || true
  wait "$pf" 2>/dev/null || true
  rm -f "$pf_log"
}
trap cleanup EXIT

ready=false
for ((attempt = 1; attempt <= 40; attempt++)); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then ready=true; break; fi
  sleep 0.25
done
[[ "$ready" == true ]] || { echo "[fail] gc-master endpoint did not become ready" >&2; cat "$pf_log" >&2; exit 1; }

start=$(date +%s)
while true; do
  status=$(curl -fsS "http://127.0.0.1:${PORT}/status")
  read -r completed scenario run_id < <(printf '%s' "$status" | python3 -c 'import json,sys; x=json.load(sys.stdin); print(x.get("completed_upfs",0), x.get("scenario",""), x.get("run_id",""))')
  [[ -z "$EXPECTED_SCENARIO" || "$scenario" == "$EXPECTED_SCENARIO" ]] || { echo "[fail] active scenario mismatch" >&2; exit 1; }
  [[ -z "$EXPECTED_RUN_ID" || "$run_id" == "$EXPECTED_RUN_ID" ]] || { echo "[fail] active run_id mismatch" >&2; exit 1; }
  if [[ "$completed" -eq 3 ]]; then
    echo "[ok] all three UPF simulators completed run_id=$run_id"
    break
  fi
  now=$(date +%s)
  if (( now - start >= TIMEOUT_SECONDS )); then
    echo "[fail] timeout waiting for scenario completion" >&2
    kubectl -n "$NS" get pods -o wide >&2 || true
    kubectl -n "$NS" logs deployment/gc-master --tail=50 >&2 || true
    exit 1
  fi
  sleep 1
done
