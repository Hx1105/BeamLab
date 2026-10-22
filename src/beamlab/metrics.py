"""Summarize real results, preserving failures and separate maintenance cases."""
from collections import Counter
import numpy as np

LABELS=('geometric','source_drift','dark_bias','saturation','thermal','combined','mechanical_stuck','uncertain')


def aggregate(rows):
    recoverable=[r for r in rows if r['judgment']['recoverable']]
    maintenance=[r for r in rows if not r['judgment']['recoverable']]
    confusion={actual:{predicted:0 for predicted in LABELS} for actual in LABELS}
    for r in rows:
        diagnosis=r.get('report',{}).get('diagnosis','uncertain')
        if diagnosis not in LABELS:diagnosis='uncertain'
        confusion[r['fault_class']][diagnosis]+=1
    walls=[r['wall_s'] for r in rows]
    counts=[r['judgment']['target_measurements'] for r in rows]
    cvs=[r['judgment'].get('stability_cv') for r in recoverable if r['judgment'].get('stability_cv') is not None]
    recovered=sum(r['judgment']['recovered'] for r in recoverable)
    tokens={key:sum(r.get('tokens',{}).get(key,0) for r in rows) for key in ('input','output','cached_input')}
    return {'cases':len(rows),'recoverable_cases':len(recoverable),'recovered':recovered,
        'recovery_fraction':recovered/len(recoverable) if recoverable else None,
        'maintenance_cases':len(maintenance),'maintenance_correct':sum(r['judgment']['maintenance_correct'] for r in maintenance),
        'task_completed':sum(r.get('task_completed',False) for r in rows),
        'diagnosis_correct':sum(r['report'].get('diagnosis')==r['fault_class'] for r in rows),
        'confusion_matrix':confusion,'measurements_median':float(np.median(counts)) if counts else None,
        'measurements_max':max(counts) if counts else None,
        'wall_median_s':float(np.median(walls)) if walls else None,'wall_p95_s':float(np.percentile(walls,95)) if walls else None,
        'stability_cv_median':float(np.median(cvs)) if cvs else None,
        'saturated_acquisitions':sum(r['judgment'].get('saturated_measurements',0) for r in rows),
        'actual_limit_violations':sum(r['judgment'].get('actual_limit_violations',0) for r in rows),
        'budget_violations':sum(r['judgment'].get('budget_violation',False) for r in rows),
        'source_power_increases':sum(r['judgment'].get('source_power_increase',False) for r in rows),
        'clamped_requests':sum(r.get('report',{}).get('clamped_requests',0) for r in rows),
        'tool_rejections':sum(r.get('report',{}).get('tool_rejections',0) for r in rows),
        'failures':sum(bool(r.get('failure') or r.get('report',{}).get('error')) for r in rows),
        'model_calls':sum(r.get('model_calls',0) for r in rows),'tokens':tokens,
        'usage_missing_calls':sum(r.get('usage_missing_calls',0) for r in rows),
        'incomplete_model_calls':sum(r.get('incomplete_model_calls',0) for r in rows),
        'monetary_cost':None,'cost_note':'Verified gateway billing unavailable; usage only, not free',
        'failure_reasons':dict(Counter(str(r.get('failure') or r.get('report',{}).get('error')) for r in rows
            if r.get('failure') or r.get('report',{}).get('error')))}
