#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="residual-multinode"
IMAGE="residual-multinode-demo:1.2"
NS="residual-demo"

for cmd in docker kind kubectl python3 curl; do
  command -v "$cmd" >/dev/null || { echo "[error] missing dependency: $cmd" >&2; exit 1; }
done

render_manifest() {
  local src="$1"
  local dst="$2"
  local scenario="$3"
  local run_id="$4"
  python3 - "$src" "$dst" "$scenario" "$run_id" <<'PY'
from pathlib import Path
import sys
src, dst, scenario, run_id = sys.argv[1:5]
text = Path(src).read_text(encoding="utf-8")
text = text.replace("__SCENARIO__", scenario).replace("__RUN_ID__", run_id)
if "__SCENARIO__" in text or "__RUN_ID__" in text:
    raise SystemExit("unresolved manifest placeholder")
Path(dst).write_text(text, encoding="utf-8")
PY
}

run_scenario() {
  local scenario="$1"
  local run_id="${scenario}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  local out="$ROOT/results/$scenario"
  local tmp
  tmp="$(mktemp -d)"
  echo "[scenario] $scenario run_id=$run_id"

  # Remove all producers before replacing the master. This prevents telemetry
  # from a preceding scenario from reaching the new controller instance.
  kubectl -n "$NS" delete daemonset gc-agent-upf --ignore-not-found --wait=true
  kubectl -n "$NS" delete deployment gc-master --ignore-not-found --wait=true

  render_manifest "$ROOT/k8s/master.yaml" "$tmp/master.yaml" "$scenario" "$run_id"
  render_manifest "$ROOT/k8s/agents.yaml" "$tmp/agents.yaml" "$scenario" "$run_id"

  kubectl apply -f "$tmp/master.yaml"
  kubectl -n "$NS" rollout status deployment/gc-master --timeout=120s
  kubectl apply -f "$tmp/agents.yaml"
  kubectl -n "$NS" rollout status daemonset/gc-agent-upf --timeout=120s

  "$ROOT/scripts/verify_demo.sh" "$scenario" "$run_id"
  "$ROOT/scripts/wait_for_completion.sh" 90 "$scenario" "$run_id"
  "$ROOT/scripts/collect_results.sh" "$out" "$scenario" "$run_id"

  rm -rf "$tmp"
}

echo "[1/6] creating the four-node KinD cluster"
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --config "$ROOT/kind-config.yaml"

echo "[2/6] building and loading the demo image"
docker build -t "$IMAGE" "$ROOT"
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo "[3/6] deploying shared namespace and parameters"
kubectl apply -f "$ROOT/k8s/namespace.yaml"
kubectl apply -f "$ROOT/k8s/parameters.yaml"

echo "[4/6] clearing previous result directories"
rm -rf "$ROOT/results/UPF-noGC" "$ROOT/results/UPF-withGC"

echo "[5/6] running isolated manuscript scenarios"
run_scenario "UPF-noGC"
run_scenario "UPF-withGC"

echo "[6/6] completed"
echo "Results: $ROOT/results/UPF-noGC and $ROOT/results/UPF-withGC"
