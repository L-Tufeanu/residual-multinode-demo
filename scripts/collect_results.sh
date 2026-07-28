#!/usr/bin/env bash
set -euo pipefail
OUT="${1:-results}"
SCENARIO="${2:-}"
RUN_ID="${3:-}"
NS="residual-demo"
PORT="${STATUS_PORT:-18080}"
mkdir -p "$OUT"

# Permit manual invocation without positional arguments by reading the active
# run identity from the gc-master Deployment.
if [[ -z "$SCENARIO" || -z "$RUN_ID" ]]; then
  read -r active_scenario active_run_id < <(
    kubectl -n "$NS" get deployment gc-master -o json | python3 -c '
import json, sys
obj = json.load(sys.stdin)
env = obj["spec"]["template"]["spec"]["containers"][0].get("env", [])
values = {x.get("name"): x.get("value", "") for x in env}
print(values.get("SCENARIO", ""), values.get("RUN_ID", ""))
'
  )
  SCENARIO="${SCENARIO:-$active_scenario}"
  RUN_ID="${RUN_ID:-$active_run_id}"
fi
[[ "$SCENARIO" == "UPF-noGC" || "$SCENARIO" == "UPF-withGC" ]] || {
  echo "[fail] could not determine a supported active scenario" >&2; exit 1;
}
[[ -n "$RUN_ID" ]] || { echo "[fail] could not determine the active run_id" >&2; exit 1; }

kubectl get nodes -o wide > "$OUT/nodes.txt"
kubectl -n "$NS" get pods -o wide > "$OUT/pods.txt"
kubectl -n "$NS" logs deployment/gc-master > "$OUT/gc-master.log"
for pod in $(kubectl -n "$NS" get pods -l app=gc-agent-upf -o json | python3 -c '
import json,sys
for p in json.load(sys.stdin).get("items", []):
    if not p.get("metadata", {}).get("deletionTimestamp"):
        print("pod/" + p["metadata"]["name"])
'); do
  short=${pod#pod/}
  kubectl -n "$NS" logs "$pod" > "$OUT/${short}.log"
done

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

curl -fsS "http://127.0.0.1:${PORT}/status" > "$OUT/status.json"
curl -fsS "http://127.0.0.1:${PORT}/history" > "$OUT/history.json"
curl -fsS "http://127.0.0.1:${PORT}/metrics" > "$OUT/metrics.prom"
printf '%s\n' "$RUN_ID" > "$OUT/run_id.txt"
python3 "$(dirname "$0")/history_to_csv.py" "$OUT/history.json" "$OUT/history.csv"
python3 "$(dirname "$0")/validate_results.py" "$OUT/status.json" "$OUT/history.json" "$SCENARIO" "$RUN_ID" | tee "$OUT/validation.txt"
echo "[ok] evidence exported to $OUT"
