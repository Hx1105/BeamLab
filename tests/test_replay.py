import importlib.util


def test_replay_is_built_from_actual_acquisitions_not_synthetic_curves():
    assert importlib.util.find_spec('beamlab.replay'), 'Real trace replay builder absent'
    from beamlab.replay import curve_row
    sample={'sample':3,'t':12.5,'target_w':.0005,'reference_w':.000051,'shutter_open':True,'saturated':False}
    row=curve_row(sample,5e-6,1e-6)
    assert row['ratio']==9.9
    assert row['sample']==3 and row['t']==12.5
