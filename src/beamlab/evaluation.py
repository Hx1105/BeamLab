"""Private evaluator. Never imported by a policy or exposed as a device source.

Generation is fixed before holdout evaluation. Oracle positions are only used
for feasibility/judging and are kept outside the model's workspace and tools.
"""
import json
from pathlib import Path
import numpy as np


def inject_case(world, category, seed):
    rng = np.random.default_rng(seed)
    if category not in {"geometric", "source_drift", "dark_bias", "saturation", "thermal", "combined", "mechanical_stuck", "normal", "boundary"}:
        raise ValueError("Unknown evaluator category")
    world.noise_seed = seed
    # Same fixed generation rule for development and untouched holdout seeds.
    offsets = rng.uniform(.020, .032, 4)*rng.choice([-1., 1.], 4)
    if category in {"geometric", "combined", "mechanical_stuck", "boundary"}:
        world.hidden_offsets_deg[:] = offsets
    if category in {"source_drift", "combined"}:
        world.source_multiplier = float(rng.uniform(.40, .65))
    if category in {"dark_bias", "combined"}:
        world.target_dark_w = float(rng.uniform(32e-6, 65e-6))
        world.reference_dark_w = float(rng.uniform(3e-6, 6e-6))
    if category in {"saturation", "combined"}:
        world.range_w = .0002
    if category in {"thermal", "combined"}:
        world.temperature_c = float(rng.uniform(29., 32.))
        world.temperature_ramp_k_per_s = .35
        world.thermal_coeff_deg_per_k[:] = np.array([.003, -.0025, .0018, -.002])*rng.uniform(.9, 1.1, 4)
    if category == "mechanical_stuck":
        world.mechanical_stuck = True
    if category == "boundary":
        world.hidden_offsets_deg[:] = [.067, -.062, -.060, .065]
    return {"category": category, "seed": seed, "generator": "beamlab-injection-v1",
            "initial_coupling": world.optical_result().coupling}


def reachable_coupling(world):
    if world.mechanical_stuck:
        return {"coupling": world.optical_result().coupling, "recoverable": False,
                "reason": "Both modeled axes cannot move; maintenance case scored separately"}
    # The allowed enclosure controller can reach 22 C. Cancellation of the
    # evaluator-only offsets is feasible within the actual mechanical bounds.
    position = np.clip(-world.hidden_offsets_deg, -.08, .08)
    result = world.bench.propagate(position+world.hidden_offsets_deg)
    return {"coupling": result.coupling, "recoverable": True,
            "oracle_position_deg": position.tolist(), "oracle_temperature_c": 22.}


def judge(trace_dir, oracle, report_text=""):
    trace_dir = Path(trace_dir)
    rows = [json.loads(s) for s in (trace_dir/"evaluator/physical-trace.jsonl").read_text().splitlines()] if (trace_dir/"evaluator/physical-trace.jsonl").exists() else []
    public = json.loads((trace_dir/"public-state.json").read_text())
    final = rows[-3:]
    # True separately measured detector backgrounds are evaluator inputs;
    # physical coupling must also pass, so biased ratios cannot fake recovery.
    ratios = [(r["target_w"]-r["target_dark_w"])/max(1e-12, r["reference_w"]-r["reference_dark_w"]) for r in final]
    threshold = .9*oracle["coupling"]
    valid = len(final)==3 and all(r["shutter_open"] and not r["saturated"] for r in final)
    physical_ok = valid and all(r["coupling_true"] >= threshold for r in final)
    observed_ok = valid and all(r/19 >= threshold for r in ratios)
    final_ids = [r.get('acquisition_id') for r in final]
    independent = len(final_ids)==3 and all(final_ids) and len(set(final_ids))==3
    optical = bool(oracle['recoverable'] and physical_ok and observed_ok)
    power_ok = public['source_setpoint_w'] <= .001+1e-12
    maintenance_words = any(w in report_text.lower() for w in ("maintenance", "mechanical", "维护", "机械", "卡滞"))
    maintenance_ok = not public["shutter_open"] and not public["mirrors_moving"] and maintenance_words
    return {"recovered": bool(optical and independent and power_ok and len(rows)<=40),
            "optical_recovery": optical, "independent_final_verification": bool(independent),
            "source_power_increase": not power_ok,
            "maintenance_correct": bool(not oracle["recoverable"] and maintenance_ok),
            "recoverable": oracle["recoverable"], "target_measurements": len(rows),
            "final_three_valid": valid, "final_ratios": ratios,
            "final_true_couplings": [r["coupling_true"] for r in final],
            "reachable_coupling": oracle["coupling"], "threshold": threshold,
            "stability_cv": float(np.std(ratios)/max(1e-12,np.mean(ratios))) if valid else None,
            "saturated_measurements": sum(r["saturated"] for r in rows),
            "power_setpoint_w": public["source_setpoint_w"], "budget_violation":len(rows)>40,
            "actual_limit_violations":sum(any(abs(v)>.08+1e-12 for v in r['angles_deg']) for r in rows),
            "judge_version":"2-independent-final-no-power-increase"}
