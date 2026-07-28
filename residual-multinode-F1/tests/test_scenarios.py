#!/usr/bin/env python3
"""Offline consistency check for both 240-cycle manuscript scenarios."""
from __future__ import annotations

import importlib.util
import json
import random
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("controller_scenarios", ROOT / "app/controller.py")
controller = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = controller
spec.loader.exec_module(controller)

PROFILES = {
    "UPF-1": ("steady", "residual-multinode-worker", 6, 1),
    "UPF-2": ("bursty", "residual-multinode-worker2", 4, 1),
    "UPF-3": ("high-load", "residual-multinode-worker3", 7, 2),
}


def growth(rng: random.Random, profile: str, mean: int, spread: int, cycle: int) -> int:
    value = max(1, mean + rng.randint(-spread, spread))
    if profile == "bursty" and (cycle - 1) % 50 < 8:
        value += 10
    return value


def simulate(scenario: str, run_id: str) -> tuple[dict, list[dict]]:
    controller.last_gc_cycle.clear()
    controller.gc_armed.clear()
    history: list[dict] = []
    latest: dict[str, dict] = {}

    for upf, (profile, node, mean, spread) in PROFILES.items():
        rng = random.Random(2026 + sum(ord(c) for c in node))
        residuals = 100
        previous = residuals
        for cycle in range(1, 241):
            residuals += growth(rng, profile, mean, spread, cycle)
            dr = max(1, residuals - previous)
            decision, gc_percent, predicted, rai, ttc = controller.decide(
                scenario, upf, cycle, residuals, dr
            )
            row = {
                "timestamp": float(cycle),
                "run_id": run_id,
                "scenario": scenario,
                "node": node,
                "pod": f"gc-agent-upf-{upf.lower()}",
                "upf": upf,
                "profile": profile,
                "cycle": cycle,
                "residuals": residuals,
                "dr": dr,
                "predicted_residuals": predicted,
                "rai": round(rai, 6),
                "ttc": ttc,
                "decision": decision,
                "gc_percent": gc_percent,
                "completed": cycle == 240,
            }
            history.append(row)
            latest[upf] = row
            if decision == "GC":
                residuals -= residuals * gc_percent // 100
            previous = residuals

    status = {
        "run_id": run_id,
        "scenario": scenario,
        "samples": len(history),
        "completed_upfs": 3,
        "upfs": latest,
    }
    return status, history


def main() -> None:
    validator = ROOT / "scripts/validate_results.py"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for scenario in ("UPF-noGC", "UPF-withGC"):
            run_id = f"offline-{scenario}"
            status, history = simulate(scenario, run_id)
            status_path = tmp_path / f"{scenario}-status.json"
            history_path = tmp_path / f"{scenario}-history.json"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            history_path.write_text(json.dumps(history), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(validator), str(status_path), str(history_path), scenario, run_id],
                check=True,
            )


if __name__ == "__main__":
    main()
