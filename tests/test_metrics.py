from beamlab.metrics import aggregate


def test_metrics_keep_failure_and_maintenance_denominators_separate():
    rows=[dict(policy='mimo',split='holdout',fault_class='geometric',wall_s=12.,model_calls=2,
        tokens={'input':40,'output':10,'cached_input':0},failure=None,task_completed=True,
        report={'diagnosis':'geometric'},judgment={'recoverable':True,'recovered':True,
        'maintenance_correct':False,'target_measurements':24,'stability_cv':.01}),
        dict(policy='mimo',split='holdout',fault_class='geometric',wall_s=20.,model_calls=3,
        tokens={'input':70,'output':20,'cached_input':30},failure='deadline',task_completed=False,
        report={'diagnosis':'uncertain'},judgment={'recoverable':True,'recovered':False,
        'maintenance_correct':False,'target_measurements':30,'stability_cv':None}),
        dict(policy='mimo',split='maintenance',fault_class='mechanical_stuck',wall_s=5.,model_calls=1,
        tokens={'input':10,'output':5,'cached_input':0},failure=None,task_completed=True,
        report={'diagnosis':'mechanical_stuck'},judgment={'recoverable':False,'recovered':False,
        'maintenance_correct':True,'target_measurements':4,'stability_cv':None})]
    r=aggregate(rows)
    assert r['recovery_fraction']==.5
    assert r['recoverable_cases']==2 and r['maintenance_cases']==1
    assert r['failures']==1 and r['model_calls']==6
    assert r['monetary_cost'] is None
    assert r['confusion_matrix']['geometric']['uncertain']==1
