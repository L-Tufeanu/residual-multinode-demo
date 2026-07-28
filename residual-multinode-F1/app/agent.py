#!/usr/bin/env python3
"""Node-local UPF residual simulator and GC agent."""
from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request

MASTER_URL = os.getenv("MASTER_URL", "http://gc-master:8080/v1/telemetry")
NODE_NAME = os.getenv("NODE_NAME", "unknown-node")
POD_NAME = os.getenv("POD_NAME", socket.gethostname())
SCENARIO = os.getenv("SCENARIO", "")
RUN_ID = os.getenv("RUN_ID", "")
CYCLE_DURATION = float(os.getenv("CYCLE_DURATION", "0.05"))
MAX_CYCLES = int(os.getenv("MAX_CYCLES", "240"))
BURST_INTERVAL = int(os.getenv("BURST_INTERVAL", "50"))
BURST_DURATION = int(os.getenv("BURST_DURATION", "8"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "80"))
START_RESIDUALS = int(os.getenv("START_RESIDUALS", "100"))
BASE_SEED = int(os.getenv("SEED", "2026"))
POST_RETRIES = int(os.getenv("POST_RETRIES", "8"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "0.10"))


def identity(node: str) -> tuple[str, str, int, int]:
    if node.endswith("worker3"):
        return "UPF-3", "high-load", 7, 2
    if node.endswith("worker2"):
        return "UPF-2", "bursty", 4, 1
    return "UPF-1", "steady", 6, 1


UPF_ID, PROFILE, LEAK_MEAN, LEAK_SPREAD = identity(NODE_NAME)
rng = random.Random(
    BASE_SEED
    + sum(ord(character) for character in NODE_NAME)
)


def post(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        MASTER_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())


def post_with_retry(payload: dict) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, POST_RETRIES + 1):
        try:
            return post(payload)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            print(
                json.dumps(
                    {
                        "run_id": RUN_ID,
                        "scenario": SCENARIO,
                        "upf": UPF_ID,
                        "cycle": payload["cycle"],
                        "attempt": attempt,
                        "telemetry_retry": str(exc),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(RETRY_DELAY)
    raise RuntimeError(
        f"telemetry delivery failed after {POST_RETRIES} attempts: {last_error}"
    )


def growth_for_cycle(cycle: int) -> int:
    growth = max(1, LEAK_MEAN + rng.randint(-LEAK_SPREAD, LEAK_SPREAD))
    if PROFILE == "bursty":
        burst_position = (cycle - 1) % BURST_INTERVAL
        if burst_position < BURST_DURATION:
            growth += max(1, BATCH_SIZE // BURST_DURATION)
    return growth


def run() -> None:
    if SCENARIO not in {"UPF-noGC", "UPF-withGC"}:
        raise ValueError(f"unsupported scenario: {SCENARIO}")
    if not RUN_ID.strip():
        raise ValueError("RUN_ID must be set")
    if MAX_CYCLES <= 0 or CYCLE_DURATION < 0:
        raise ValueError("MAX_CYCLES must be positive and CYCLE_DURATION non-negative")

    residuals = START_RESIDUALS
    previous_reported = residuals

    for cycle in range(1, MAX_CYCLES + 1):
        residuals += growth_for_cycle(cycle)
        dr = max(1, residuals - previous_reported)
        payload = {
            "run_id": RUN_ID,
            "scenario": SCENARIO,
            "node": NODE_NAME,
            "pod": POD_NAME,
            "upf": UPF_ID,
            "profile": PROFILE,
            "cycle": cycle,
            "residuals": residuals,
            "dr": dr,
            "completed": cycle == MAX_CYCLES,
        }

        reply = post_with_retry(payload)
        if reply.get("decision") == "GC":
            deleted = residuals * int(reply.get("gc_percent", 0)) // 100
            residuals = max(0, residuals - deleted)

        print(
            json.dumps(
                {**payload, **reply, "residuals_after_action": residuals},
                sort_keys=True,
            ),
            flush=True,
        )
        previous_reported = residuals
        time.sleep(CYCLE_DURATION)

    print(
        json.dumps(
            {
                "run_id": RUN_ID,
                "scenario": SCENARIO,
                "upf": UPF_ID,
                "status": "completed",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run()
