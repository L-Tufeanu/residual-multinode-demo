#!/usr/bin/env python3
"""Central GC master for the multi-node residual lifecycle demo."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = int(os.getenv("PORT", "8080"))
B_MAX = int(os.getenv("B_MAX", "2000"))
RESIDUAL_THRESHOLD = int(os.getenv("RESIDUAL_THRESHOLD", "1400"))
LOW_RESIDUAL_THRESHOLD = int(os.getenv("LOW_RESIDUAL_THRESHOLD", "900"))
PREDICTION_HORIZON = int(os.getenv("PREDICTION_HORIZON", "5"))
GC_INTERVAL = int(os.getenv("GC_INTERVAL", "15"))
GC_RECLAIM_RATIO = int(os.getenv("GC_RECLAIM_RATIO", "75"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "5000"))
MAX_CYCLES = int(os.getenv("MAX_CYCLES", "240"))
EXPECTED_RUN_ID = os.getenv("RUN_ID", "")
EXPECTED_SCENARIO = os.getenv("SCENARIO", "")


@dataclass
class Sample:
    timestamp: float
    run_id: str
    scenario: str
    node: str
    pod: str
    upf: str
    profile: str
    cycle: int
    residuals: int
    dr: int
    predicted_residuals: int
    rai: float
    ttc: int
    decision: str
    gc_percent: int
    completed: bool


lock = threading.Lock()
latest: dict[str, Sample] = {}
history: deque[Sample] = deque(maxlen=HISTORY_LIMIT)
last_gc_cycle: dict[str, int] = defaultdict(lambda: -10**9)
gc_armed: dict[str, bool] = defaultdict(lambda: True)
decision_cache: dict[tuple[str, str, int], dict[str, Any]] = {}

ALLOWED_SCENARIOS = {"UPF-noGC", "UPF-withGC"}
ALLOWED_UPFS = {"UPF-1", "UPF-2", "UPF-3"}
EXPECTED_PROFILE = {"UPF-1": "steady", "UPF-2": "bursty", "UPF-3": "high-load"}


def decide(
    scenario: str,
    upf: str,
    cycle: int,
    residuals: int,
    dr: int,
) -> tuple[str, int, int, float, int]:
    predicted = residuals + PREDICTION_HORIZON * max(dr, 0)
    rai = residuals / max(B_MAX, 1)
    ttc = max(0, (B_MAX - residuals) // max(dr, 1))

    if scenario != "UPF-withGC":
        return "HOLD", 0, predicted, rai, ttc

    if residuals <= LOW_RESIDUAL_THRESHOLD:
        gc_armed[upf] = True

    interval_ok = cycle - last_gc_cycle[upf] >= GC_INTERVAL
    trigger = predicted >= RESIDUAL_THRESHOLD and gc_armed[upf] and interval_ok
    if trigger:
        last_gc_cycle[upf] = cycle
        gc_armed[upf] = False
        return "GC", GC_RECLAIM_RATIO, predicted, rai, ttc
    return "HOLD", 0, predicted, rai, ttc


class Handler(BaseHTTPRequestHandler):
    server_version = "ResidualGCMaster/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(
                200,
                {
                    "status": "ok",
                    "run_id": EXPECTED_RUN_ID,
                    "scenario": EXPECTED_SCENARIO,
                },
            )
            return
        if self.path == "/status":
            with lock:
                payload = {
                    "run_id": EXPECTED_RUN_ID,
                    "scenario": EXPECTED_SCENARIO,
                    "config": {
                        "b_max": B_MAX,
                        "residual_threshold": RESIDUAL_THRESHOLD,
                        "prediction_horizon": PREDICTION_HORIZON,
                        "gc_interval": GC_INTERVAL,
                        "gc_reclaim_ratio": GC_RECLAIM_RATIO,
                    },
                    "upfs": {k: asdict(v) for k, v in sorted(latest.items())},
                    "samples": len(history),
                    "completed_upfs": sum(x.completed for x in latest.values()),
                }
            self._json(200, payload)
            return
        if self.path == "/history":
            with lock:
                payload = [asdict(x) for x in history]
            self._json(200, payload)
            return
        if self.path == "/metrics":
            with lock:
                samples = list(latest.values())
                total_r = sum(x.residuals for x in samples)
                completed = sum(x.completed for x in samples)
            lines = [
                "# HELP residual_demo_upfs Number of reporting UPF simulators.",
                "# TYPE residual_demo_upfs gauge",
                f"residual_demo_upfs {len(samples)}",
                "# HELP residual_demo_residuals_total Aggregated residual count.",
                "# TYPE residual_demo_residuals_total gauge",
                f"residual_demo_residuals_total {total_r}",
                "# HELP residual_demo_completed_upfs Completed UPF simulators.",
                "# TYPE residual_demo_completed_upfs gauge",
                f"residual_demo_completed_upfs {completed}",
            ]
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/telemetry":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length))
            run_id = str(data["run_id"])
            scenario = str(data["scenario"])
            node = str(data["node"])
            pod = str(data["pod"])
            upf = str(data["upf"])
            profile = str(data["profile"])
            cycle = int(data["cycle"])
            residuals = max(0, int(data["residuals"]))
            dr = max(0, int(data["dr"]))
            completed = bool(data.get("completed", False))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"invalid telemetry: {exc}"})
            return

        if not EXPECTED_RUN_ID or not EXPECTED_SCENARIO:
            self._json(503, {"error": "master run identity is not configured"})
            return
        if run_id != EXPECTED_RUN_ID:
            self._json(409, {"error": "telemetry run_id does not match active run"})
            return
        if scenario != EXPECTED_SCENARIO:
            self._json(409, {"error": "telemetry scenario does not match active run"})
            return
        if scenario not in ALLOWED_SCENARIOS:
            self._json(400, {"error": f"unsupported scenario: {scenario}"})
            return
        if upf not in ALLOWED_UPFS:
            self._json(400, {"error": f"unsupported UPF identity: {upf}"})
            return
        if profile != EXPECTED_PROFILE[upf]:
            self._json(400, {"error": f"profile {profile} does not match {upf}"})
            return
        if not 1 <= cycle <= MAX_CYCLES:
            self._json(400, {"error": f"cycle must be within 1..{MAX_CYCLES}"})
            return

        cache_key = (run_id, upf, cycle)
        with lock:
            cached_reply = decision_cache.get(cache_key)
            if cached_reply is not None:
                reply = cached_reply
                sample = None
            else:
                decision, gc_percent, predicted, rai, ttc = decide(
                    scenario, upf, cycle, residuals, dr
                )
                sample = Sample(
                    timestamp=time.time(),
                    run_id=run_id,
                    scenario=scenario,
                    node=node,
                    pod=pod,
                    upf=upf,
                    profile=profile,
                    cycle=cycle,
                    residuals=residuals,
                    dr=dr,
                    predicted_residuals=predicted,
                    rai=round(rai, 6),
                    ttc=ttc,
                    decision=decision,
                    gc_percent=gc_percent,
                    completed=completed,
                )
                latest[upf] = sample
                history.append(sample)
                reply = {
                    "run_id": run_id,
                    "decision": decision,
                    "gc_percent": gc_percent,
                    "predicted_residuals": predicted,
                    "rai": round(rai, 6),
                    "ttc": ttc,
                }
                decision_cache[cache_key] = reply

        if sample is not None:
            print(json.dumps(asdict(sample), sort_keys=True), flush=True)
        self._json(200, reply)


if __name__ == "__main__":
    if EXPECTED_SCENARIO not in ALLOWED_SCENARIOS:
        raise SystemExit("SCENARIO must be UPF-noGC or UPF-withGC")
    if not EXPECTED_RUN_ID.strip():
        raise SystemExit("RUN_ID must be set")
    print(
        f"GC master listening on :{PORT} run_id={EXPECTED_RUN_ID} "
        f"scenario={EXPECTED_SCENARIO}",
        flush=True,
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
