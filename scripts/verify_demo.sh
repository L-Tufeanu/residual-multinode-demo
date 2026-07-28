#!/usr/bin/env bash
set -euo pipefail

NS="residual-demo"
EXPECTED_SCENARIO="${1:-}"
EXPECTED_RUN_ID="${2:-}"
TIMEOUT_SECONDS="${VERIFY_TIMEOUT_SECONDS:-90}"

workers=$(kubectl get nodes -l residual-demo/worker=true --no-headers | wc -l | tr -d ' ')
master_node=$(kubectl -n "$NS" get pod -l app=gc-master -o jsonpath='{.items[0].spec.nodeName}')
master_is_control_plane=$(kubectl get node "$master_node" -o json | python3 -c '
import json, sys
labels = json.load(sys.stdin).get("metadata", {}).get("labels", {})
print("true" if "node-role.kubernetes.io/control-plane" in labels else "false")
')

[[ "$workers" -eq 3 ]] || { echo "[fail] expected 3 workers" >&2; exit 1; }
[[ "$master_is_control_plane" == "true" ]] || { echo "[fail] gc-master is not on the control-plane" >&2; exit 1; }

# A completed DaemonSet rollout may briefly leave terminating pods in phase
# Running. Count only current, non-terminating, Ready pods and wait until the
# previous revision has disappeared.
start=$(date +%s)
while true; do
  read -r active_ready terminating total < <(
    kubectl -n "$NS" get pods -l app=gc-agent-upf -o json | python3 -c '
import json, sys
items = json.load(sys.stdin).get("items", [])
active_ready = 0
terminating = 0
for pod in items:
    meta = pod.get("metadata", {})
    if meta.get("deletionTimestamp"):
        terminating += 1
        continue
    conditions = pod.get("status", {}).get("conditions", [])
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
    if pod.get("status", {}).get("phase") == "Running" and ready:
        active_ready += 1
print(active_ready, terminating, len(items))
'
  )
  if [[ "$active_ready" -eq 3 && "$terminating" -eq 0 && "$total" -eq 3 ]]; then
    break
  fi
  now=$(date +%s)
  if (( now - start >= TIMEOUT_SECONDS )); then
    printf 'workers=%s active_ready_agents=%s terminating_agents=%s total_agent_pods=%s master_node=%s\n' \
      "$workers" "$active_ready" "$terminating" "$total" "$master_node" >&2
    kubectl -n "$NS" get pods -l app=gc-agent-upf -o wide >&2 || true
    echo "[fail] expected exactly one current Ready gc-agent-upf pod per worker" >&2
    exit 1
  fi
  sleep 1
done

printf 'workers=%s active_ready_agents=%s terminating_agents=%s master_node=%s\n' \
  "$workers" "$active_ready" "$terminating" "$master_node"

unique_nodes=$(kubectl -n "$NS" get pods -l app=gc-agent-upf -o json | python3 -c '
import json, sys
items = json.load(sys.stdin).get("items", [])
nodes = {
    p.get("spec", {}).get("nodeName", "")
    for p in items
    if not p.get("metadata", {}).get("deletionTimestamp")
}
nodes.discard("")
print(len(nodes))
')
[[ "$unique_nodes" -eq 3 ]] || { echo "[fail] agents are not distributed across distinct workers" >&2; exit 1; }

if [[ -n "$EXPECTED_SCENARIO" ]]; then
  actual_scenarios=$(kubectl -n "$NS" get pods -l app=gc-agent-upf -o json | python3 -c '
import json, sys
items = json.load(sys.stdin).get("items", [])
values = set()
for pod in items:
    if pod.get("metadata", {}).get("deletionTimestamp"):
        continue
    for env in pod.get("spec", {}).get("containers", [{}])[0].get("env", []):
        if env.get("name") == "SCENARIO": values.add(env.get("value", ""))
print("\n".join(sorted(values)))
')
  [[ "$actual_scenarios" == "$EXPECTED_SCENARIO" ]] || { echo "[fail] agent scenario mismatch" >&2; exit 1; }
fi
if [[ -n "$EXPECTED_RUN_ID" ]]; then
  actual_run_ids=$(kubectl -n "$NS" get pods -l app=gc-agent-upf -o json | python3 -c '
import json, sys
items = json.load(sys.stdin).get("items", [])
values = set()
for pod in items:
    if pod.get("metadata", {}).get("deletionTimestamp"):
        continue
    for env in pod.get("spec", {}).get("containers", [{}])[0].get("env", []):
        if env.get("name") == "RUN_ID": values.add(env.get("value", ""))
print("\n".join(sorted(values)))
')
  [[ "$actual_run_ids" == "$EXPECTED_RUN_ID" ]] || { echo "[fail] agent run_id mismatch" >&2; exit 1; }
fi

echo "[ok] topology, placement, and run identity verified"
kubectl -n "$NS" get pods -o wide
