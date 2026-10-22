"""Read actual native AGH events without sending any prompt or tool instruction."""
import json
from pathlib import Path
import sqlite3


def read_events(home, session):
    with sqlite3.connect('file:'+str(Path(home)/'data/sessions.db')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        result=[]
        for row in db.execute('select * from events where session_key=? order by seq',(session,)):
            item=dict(row)
            for key,value in item.items():
                if isinstance(value,bytes):item[key]=value.hex()
                elif key in ('data','actor','origin') and isinstance(value,str):
                    try:item[key]=json.loads(value)
                    except ValueError:pass
            result.append(item)
        return result


def watch_registered(events):
    ids={e['data']['toolUseId'] for e in events if e['type']=='tool/call' and e['data']['name']=='watch_device'}
    replies=[e['data'].get('structured') for e in events if e['type']=='tool/result'
             and e['data']['toolUseId'] in ids and not e['data'].get('isError')]
    return next((r for r in reversed(replies) if r and r.get('watch')),None)


def follow_complete(events):
    starts=[i for i,e in enumerate(events) if e['type']=='turn/start']
    wakes=[i for i,e in enumerate(events) if is_device_wakeup(e)]
    return bool(wakes and len(starts)>=2 and starts[-1]>wakes[0]
                and any(e['type']=='turn/end' for e in events[starts[-1]+1:]))


def is_device_wakeup(event):
    return event['type']=='user/message' and event['data'].get('kind')=='prompt' and any(
        b.get('type')=='text' and b.get('text','').startswith('[AgnesHub] ')
        for b in event['data'].get('content',[]))
