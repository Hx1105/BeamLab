import importlib.util


def test_frozen_baselines_do_not_import_evaluator_or_world():
    assert importlib.util.find_spec('beamlab.baselines'), 'Protocol-only baselines absent'
    import beamlab.baselines as module
    from pathlib import Path
    text=Path(module.__file__).read_text()
    assert 'from .evaluation' not in text and 'from .world' not in text
    assert set(module.POLICIES)=={'fixed_coarse_fine','coordinate','powell','diagnostic_fit','ablation_no_normalization','ablation_no_diagnosis'}
