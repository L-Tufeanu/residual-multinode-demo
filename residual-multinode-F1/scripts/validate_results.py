#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED_UPFS = ["UPF-1", "UPF-2", "UPF-3"]
EXPECTED_PROFILES = ["bursty", "high-load", "steady"]
EXPECTED_CYCLES = 240
EXPECTED_SAMPLES = len(EXPECTED_UPFS) * EXPECTED_CYCLES


def load_json(path: str):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: validate_results.py STATUS_JSON HISTORY_JSON "
            "EXPECTED_SCENARIO EXPECTED_RUN_ID"
        )

    status_path, history_path, expected_scenario, expected_run_id = sys.argv[1:5]
    if expected_scenario not in {"UPF-noGC", "UPF-withGC"}:
        raise AssertionError(f"unsupported scenario: {expected_scenario}")
    if not expected_run_id.strip():
        raise AssertionError("expected run_id must not be empty")

    status = load_json(status_path)
    history = load_json(history_path)
    assert isinstance(history, list), "history.json must contain a JSON array"

    required_fields = {
        "timestamp",
        "run_id",
        "scenario",
        "node",
        "pod",
        "upf",
        "profile",
        "cycle",
        "residuals",
        "dr",
        "predicted_residuals",
        "rai",
        "ttc",
        "decision",
        "gc_percent",
        "completed",
    }
    for index, row in enumerate(history):
        missing = required_fields.difference(row)
        assert not missing, f"sample {index} is missing fields: {sorted(missing)}"

    upfs = sorted({row["upf"] for row in history})
    nodes = sorted({row["node"] for row in history})
    pods = sorted({row["pod"] for row in history})
    profiles = sorted({row["profile"] for row in history})
    scenarios = {row["scenario"] for row in history}
    run_ids = {row["run_id"] for row in history}
    gc_rows = [row for row in history if row["decision"] == "GC"]
    hold_rows = [row for row in history if row["decision"] == "HOLD"]

    assert len(history) == EXPECTED_SAMPLES, (
        f"history has {len(history)} samples; expected {EXPECTED_SAMPLES}"
    )
    assert upfs == EXPECTED_UPFS, f"unexpected UPFs: {upfs}"
    assert len(nodes) == 3, f"expected three worker nodes, got {nodes}"
    assert len(pods) == 3, f"expected three agent pods, got {pods}"
    assert profiles == EXPECTED_PROFILES, f"unexpected profiles: {profiles}"
    assert scenarios == {expected_scenario}, f"unexpected scenarios: {scenarios}"
    assert run_ids == {expected_run_id}, f"unexpected run_ids: {run_ids}"
    assert status.get("scenario") == expected_scenario, "status scenario mismatch"
    assert status.get("run_id") == expected_run_id, "status run_id mismatch"
    assert status.get("samples") == EXPECTED_SAMPLES, (
        f"status reports {status.get('samples')} samples; expected {EXPECTED_SAMPLES}"
    )
    assert status.get("completed_upfs") == 3, "not all UPF simulators completed"
    assert hold_rows, "no HOLD decision observed"

    cycles_by_upf: dict[str, list[int]] = defaultdict(list)
    node_by_upf: dict[str, set[str]] = defaultdict(set)
    pod_by_upf: dict[str, set[str]] = defaultdict(set)
    completed_by_upf: Counter[str] = Counter()

    for row in history:
        cycles_by_upf[row["upf"]].append(int(row["cycle"]))
        node_by_upf[row["upf"]].add(row["node"])
        pod_by_upf[row["upf"]].add(row["pod"])
        if row["completed"]:
            completed_by_upf[row["upf"]] += 1

        assert row["decision"] in {"HOLD", "GC"}, (
            f"invalid decision in {row['upf']} cycle {row['cycle']}"
        )
        assert row["residuals"] >= 0, "negative residual count"
        assert row["dr"] >= 0, "negative residual growth"
        assert row["rai"] >= 0, "negative RAI"
        assert row["ttc"] >= 0, "negative TTC"
        assert row["predicted_residuals"] >= row["residuals"], "invalid prediction"
        assert 0 <= row["gc_percent"] <= 100, "invalid GC percentage"

    expected_cycle_sequence = list(range(1, EXPECTED_CYCLES + 1))
    for upf in EXPECTED_UPFS:
        cycles = sorted(cycles_by_upf[upf])
        assert cycles == expected_cycle_sequence, (
            f"{upf} does not contain exactly cycles 1..{EXPECTED_CYCLES}"
        )
        assert len(node_by_upf[upf]) == 1, f"{upf} moved between worker nodes"
        assert len(pod_by_upf[upf]) == 1, f"{upf} used more than one agent pod"
        assert completed_by_upf[upf] == 1, (
            f"{upf} must have exactly one completion sample"
        )

    latest = status.get("upfs", {})
    assert sorted(latest) == EXPECTED_UPFS, f"unexpected status UPFs: {sorted(latest)}"
    for upf in EXPECTED_UPFS:
        assert latest[upf]["run_id"] == expected_run_id
        assert latest[upf]["scenario"] == expected_scenario
        assert latest[upf]["cycle"] == EXPECTED_CYCLES
        assert latest[upf]["completed"] is True

    if expected_scenario == "UPF-withGC":
        assert gc_rows, "no GC decision observed in UPF-withGC"
        gc_upfs = sorted({row["upf"] for row in gc_rows})
        assert gc_upfs == EXPECTED_UPFS, (
            f"expected at least one GC action for every UPF, got {gc_upfs}"
        )
        assert all(row["gc_percent"] == 75 for row in gc_rows), (
            "GC decisions do not use the configured 75% reclaim ratio"
        )
    else:
        assert not gc_rows, "GC decision observed in UPF-noGC"
        assert all(row["gc_percent"] == 0 for row in history)

    print(
        f"PASS scenario={expected_scenario} run_id={expected_run_id} "
        f"upfs={len(upfs)} samples={len(history)} hold={len(hold_rows)} gc={len(gc_rows)}"
    )


if __name__ == "__main__":
    main()
