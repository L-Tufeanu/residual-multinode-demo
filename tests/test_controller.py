#!/usr/bin/env python3
import importlib.util
import sys
from pathlib import Path

path = Path(__file__).parents[1] / "app" / "controller.py"
spec = importlib.util.spec_from_file_location("controller", path)
controller = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = controller
spec.loader.exec_module(controller)


def reset():
    controller.last_gc_cycle.clear()
    controller.gc_armed.clear()
    controller.decision_cache.clear()


def test_no_gc_scenario_holds():
    reset()
    assert controller.decide("UPF-noGC", "UPF-1", 100, 1500, 5)[0] == "HOLD"


def test_with_gc_triggers_above_prediction_threshold():
    reset()
    assert controller.decide("UPF-withGC", "UPF-1", 100, 1380, 5)[0] == "GC"


def test_hysteresis_blocks_immediate_retrigger():
    reset()
    assert controller.decide("UPF-withGC", "UPF-1", 100, 1380, 5)[0] == "GC"
    assert controller.decide("UPF-withGC", "UPF-1", 120, 1500, 5)[0] == "HOLD"


def test_low_state_rearms_gc():
    reset()
    assert controller.decide("UPF-withGC", "UPF-1", 100, 1380, 5)[0] == "GC"
    assert controller.decide("UPF-withGC", "UPF-1", 110, 500, 5)[0] == "HOLD"
    assert controller.decide("UPF-withGC", "UPF-1", 120, 1380, 5)[0] == "GC"


def test_each_upf_can_trigger_gc_with_section_parameters():
    reset()
    for upf, residuals, dr in (
        ("UPF-1", 1375, 6),
        ("UPF-2", 1350, 10),
        ("UPF-3", 1365, 7),
    ):
        assert controller.decide("UPF-withGC", upf, 200, residuals, dr)[0] == "GC"


if __name__ == "__main__":
    for name, fn in sorted(globals().copy().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
