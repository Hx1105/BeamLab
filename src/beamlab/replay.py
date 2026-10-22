"""Audience-only replay from immutable raw scalar/complex-field traces."""
import json
from pathlib import Path
import numpy as np
from PIL import Image
from matplotlib import colormaps


def curve_row(sample, target_dark_w, reference_dark_w):
    ratio=None
    if sample['shutter_open'] and not sample['saturated']:
        ratio=(sample['target_w']-target_dark_w)/max(1e-9,sample['reference_w']-reference_dark_w)
    return {**sample,'ratio':ratio,'target_uw':sample['target_w']*1e6,'reference_uw':sample['reference_w']*1e6}


def field_png(field, output, dx_m):
    # Fixed calibration across cases, no per-frame rescaling of the brightness.
    # W/m² -> log10 scale; 0.001 W / beam nominal area gives O(10³ W/m²).
    irradiance=np.abs(field)**2
    scaled=np.clip(np.log10(1+irradiance)/4.,0,1)
    rgba=(colormaps['magma'](scaled)*255).astype(np.uint8)
    Image.fromarray(rgba).resize((384,384)).save(output)
    total=float(irradiance.sum()*dx_m**2)
    n=field.shape[0];axis=(np.arange(n)-n//2)*dx_m
    weights=irradiance/max(1e-20,irradiance.sum())
    return {'integrated_w':total,'centroid_x_mm':float((weights.sum(axis=0)*axis).sum()*1000),
            'centroid_y_mm':float((weights.sum(axis=1)*axis).sum()*1000),
            'fixed_log_color_scale':'log10(1 + W/m²) / 4; 0..10⁴ W/m²'}


def build_bundle(run_dir, output):
    run_dir=Path(run_dir);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    result=json.loads((run_dir/'result.json').read_text())
    report=result['report'];physical=run_dir/'physical'
    raw=[json.loads(l) for l in (physical/'measurements.jsonl').read_text().splitlines()]
    curve=[curve_row(s,report.get('target_dark_w',5e-6),report.get('reference_dark_w',1e-6)) for s in raw]
    field_meta=None
    image_path=physical/'evaluator/latest-field.npz'
    if image_path.exists():
        with np.load(image_path) as a:field_meta=field_png(a['field'],output/'receiver.png',float(a['dx_m']))
    payload={'label':'RECORDED / 实际会话回放','simulated':True,'run_id':result['run_id'],
             'curve':curve,'result':result,'field':field_meta,'field_frame':'final actual acquired field',
             'agent_input':'No receiver image or private evaluator data was sent to the agent'}
    (output/'replay.json').write_text(json.dumps(payload,ensure_ascii=False))
    return payload
