from beamlab.sessionlog import watch_registered, follow_complete


def test_watch_requires_successful_actual_tool_receipt():
    events=[dict(seq=1,type='tool/call',data=dict(name='watch_device',toolUseId='a',args={}))]
    assert watch_registered(events) is None
    events.append(dict(seq=2,type='tool/result',data=dict(toolUseId='a',isError=False,
        structured=dict(watch='w1',device='target-01',until={'state':'remaining','lt':33}))))
    assert watch_registered(events)['watch']=='w1'
    assert not follow_complete(events)


def test_follow_waits_for_second_turn_terminal_after_actual_device_wakeup():
    events=[dict(type='turn/start',data={'trigger':'prompt'}),dict(type='turn/end',data={'reason':'completed'})]
    assert not follow_complete(events)
    events.extend([dict(type='user/message',data={'kind':'prompt','content':[{'type':'text','text':'[AgnesHub] target-01: remaining < 33.'}]}),
                   dict(type='turn/start',data={'trigger':'prompt'}),dict(type='turn/end',data={'reason':'completed'})])
    assert follow_complete(events)


def test_verifier_continuation_is_not_a_real_device_wakeup():
    events=[dict(type='turn/start',data={'trigger':'prompt'}),dict(type='turn/end',data={'reason':'completed'}),
            dict(type='turn/start',data={'trigger':'verifier'}),dict(type='turn/end',data={'reason':'completed'})]
    assert not follow_complete(events)
