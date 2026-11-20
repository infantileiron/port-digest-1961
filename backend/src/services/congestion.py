import hashlib,json,logging
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from src.models.entities import CongestionWindow,CongestionState,CongestionAlert,AlertEvent
from src.constants.enums import STATIONS
from src.utils.time import aware,now_utc
STEP=300;SPAN=1800;MIN_SAMPLES=10;RATE_THRESHOLD=0.5;TRIGGER_STREAK=3;CLEAR_STREAK=2
SCAN_LIMIT=36;RECOMPUTE_LIMIT=96
METHOD='每5分钟对齐格子核算近30分钟实际到发；延误=max(0,实际−计划)分钟，与晨报同口径。窗口分类：hot=样本≥10且延误率>50%；ok=有样本且延误率≤50%；gray=样本不足10但延误率>50%（重置双计数）；empty=空窗（中性，不推进也不打断）。连续3个hot窗触发告警，连续2个ok窗解除；空窗不算正常。补录/修订重算受影响窗口并重放状态机，误报以correction事件更正，已送通知与依据全部保留。'
def floor_step(dt):return datetime.fromtimestamp(int(dt.timestamp())//STEP*STEP,tz=timezone.utc)
def grid(start,end):
 t=start
 while t<=end:yield t;t+=timedelta(seconds=STEP)
def iso(dt):return dt.isoformat()
def parse(value):return aware(value)
def classify(total,delayed):
 if total==0:return 'empty',None
 rate=delayed/total
 if total>=MIN_SAMPLES and rate>RATE_THRESHOLD:return 'hot',rate
 if rate<=RATE_THRESHOLD:return 'ok',rate
 return 'gray',rate
def window_checksum(parts):return hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()
def compute_window(rows,station,end):
 start=end-timedelta(seconds=SPAN)
 sel=[r for r in rows if r['station']==station and r['actual_at'] and start<=parse(r['actual_at'])<end]
 parts=sorted((r['id'],r['planned_at'],r['actual_at']) for r in sel)
 delayed=sum(1 for r in sel if (parse(r['actual_at'])-parse(r['planned_at'])).total_seconds()>0)
 category,rate=classify(len(sel),delayed)
 return dict(total=len(sel),delayed=delayed,delay_rate=rate,category=category,checksum=window_checksum(parts))
def get_state(s,station):
 state=s.scalars(select(CongestionState).where(CongestionState.station==station)).first()
 if not state:
  state=CongestionState(station=station,status='clear',streak_over=0,streak_ok=0,event_seq=0,updated_at=iso(now_utc()))
  s.add(state);s.flush()
 return state
def get_window(s,station,end):
 return s.scalars(select(CongestionWindow).where(CongestionWindow.station==station,CongestionWindow.window_end==iso(end))).first()
def machine_step(cur,category):
 cur=dict(cur)
 if category=='hot':cur['over']=cur['over']+1;cur['ok']=0
 elif category=='ok':cur['ok']=cur['ok']+1;cur['over']=0
 elif category=='gray':cur['over']=0;cur['ok']=0
 transition=None
 if cur['status']=='clear' and cur['over']>=TRIGGER_STREAK:cur['status']='alerting';transition='trigger'
 elif cur['status']=='alerting' and cur['ok']>=CLEAR_STREAK:cur['status']='clear';transition='clear'
 return cur,transition
def recent_windows(s,station,category,end,limit):
 rows=s.scalars(select(CongestionWindow).where(CongestionWindow.station==station,CongestionWindow.category==category,CongestionWindow.window_end<=iso(end)).order_by(CongestionWindow.window_end.desc()).limit(limit)).all()
 return [dict(window_end=w.window_end,total=w.total,delayed=w.delayed,delay_rate=w.delay_rate,category=w.category) for w in reversed(rows)]
def emit(s,state,alert,kind,payload,now):
 state.event_seq+=1
 event=AlertEvent(alert_id=alert.id,station=state.station,seq=state.event_seq,kind=kind,state='pending',payload=payload,attempts=0,created_at=iso(now))
 s.add(event);s.flush();return event
def open_alert(s,state,end,source,now):
 evidence=dict(windows=recent_windows(s,state.station,'hot',end,TRIGGER_STREAK),method=METHOD)
 alert=CongestionAlert(station=state.station,status='open',source=source,triggered_at=iso(end),trigger_evidence=evidence,created_at=iso(now),updated_at=iso(now))
 s.add(alert);s.flush()
 state.status='alerting';state.open_alert_id=alert.id
 emit(s,state,alert,'trigger',dict(alert_id=alert.id,station=state.station,triggered_at=iso(end),evidence=evidence,source=source),now)
 return alert
def close_alert(s,state,alert,end,now):
 evidence=dict(windows=recent_windows(s,state.station,'ok',end,CLEAR_STREAK),method=METHOD)
 alert.status='cleared';alert.cleared_at=iso(end);alert.clear_evidence=evidence;alert.updated_at=iso(now)
 state.status='clear';state.open_alert_id=None
 emit(s,state,alert,'clear',dict(alert_id=alert.id,station=state.station,cleared_at=iso(end),evidence=evidence),now)
def apply_window(s,state,window,end,now):
 cur,transition=machine_step({'status':state.status,'over':state.streak_over,'ok':state.streak_ok},window.category)
 state.status=cur['status'];state.streak_over=cur['over'];state.streak_ok=cur['ok']
 if transition=='trigger':open_alert(s,state,end,'scan',now)
 elif transition=='clear':
  alert=s.get(CongestionAlert,state.open_alert_id)
  if alert:close_alert(s,state,alert,end,now)
 return transition
def scan_station(s,station,rows,now,limit=SCAN_LIMIT):
 state=get_state(s,station)
 latest=floor_step(now)
 if state.last_window_end:start=parse(state.last_window_end)+timedelta(seconds=STEP)
 else:start=latest
 made=0
 for end in grid(start,latest):
  if made>=limit:break
  result=compute_window(rows,station,end)
  window=CongestionWindow(station=station,window_end=iso(end),computed_at=iso(now),**result)
  s.add(window);s.flush()
  apply_window(s,state,window,end,now)
  window.state_after={'status':state.status,'over':state.streak_over,'ok':state.streak_ok}
  state.last_window_end=iso(end);made+=1
 state.updated_at=iso(now)
 return made
def mark_recompute(s,station,actual_values):
 ends=[]
 for value in actual_values:
  if not value:continue
  at=parse(value);first=floor_step(at)+timedelta(seconds=STEP);last=floor_step(at+timedelta(seconds=SPAN))
  ends.extend(grid(first,last))
 if not ends:return
 state=get_state(s,station)
 earliest=iso(min(ends))
 if state.recompute_from is None or earliest<state.recompute_from:state.recompute_from=earliest
 state.updated_at=iso(now_utc())
def replay_base(s,station,changed_from):
 prev=parse(changed_from)-timedelta(seconds=STEP)
 window=get_window(s,station,prev)
 if window and window.state_after:return dict(window.state_after)
 return {'status':'clear','over':0,'ok':0}
def recompute_station(s,station,rows,now,limit=RECOMPUTE_LIMIT):
 state=get_state(s,station)
 if not state.recompute_from or not state.last_window_end:state.recompute_from=None;return 0
 start=parse(state.recompute_from);last=parse(state.last_window_end)
 if start>last:state.recompute_from=None;state.changed_from=None;return 0
 processed=0
 for end in grid(start,last):
  if processed>=limit:break
  result=compute_window(rows,station,end)
  window=get_window(s,station,end)
  if window is None:
   window=CongestionWindow(station=station,window_end=iso(end),computed_at=iso(now),state_after={'status':'clear','over':0,'ok':0},**result)
   s.add(window);s.flush()
  if window.checksum!=result['checksum']:
   for k,v in result.items():setattr(window,k,v)
   window.computed_at=iso(now)
   if state.changed_from is None or iso(end)<state.changed_from:state.changed_from=iso(end)
  processed+=1
 if processed>=limit and start+timedelta(seconds=STEP*processed)<=last:
  state.recompute_from=iso(start+timedelta(seconds=STEP*processed));state.updated_at=iso(now);return processed
 state.recompute_from=None
 if state.changed_from is None:state.updated_at=iso(now);return processed
 changed_from=parse(state.changed_from);state.changed_from=None
 cur=replay_base(s,station,iso(changed_from));transitions=[]
 for end in grid(changed_from,last):
  window=get_window(s,station,end)
  cur,transition=machine_step(cur,window.category)
  window.state_after=cur
  if transition:transitions.append((end,transition))
 reconcile(s,state,station,cur,transitions,changed_from,now)
 state.streak_over=cur['over'];state.streak_ok=cur['ok'];state.updated_at=iso(now)
 return processed
def reconcile(s,state,station,cur,transitions,changed_from,now):
 expected=cur['status'];actual=state.status
 note='补录/修订后自 '+iso(changed_from)+' 起重算受影响窗口并重放状态机'
 trail=[(iso(t),k) for t,k in transitions]
 if expected==actual=='clear':return
 if expected=='clear' and actual=='alerting':
  alert=s.get(CongestionAlert,state.open_alert_id)
  if alert:
   alert.status='corrected';alert.corrected_at=iso(now);alert.correction_note=note+'：重放结论为不应维持告警，原触发判定失效';alert.updated_at=iso(now)
   emit(s,state,alert,'correction',dict(alert_id=alert.id,station=station,reason=note,expected='clear',actual='alerting',transitions=trail,method=METHOD),now)
  state.status='clear';state.open_alert_id=None
 elif expected=='alerting' and actual=='clear':
  trigger_points=[t for t,k in transitions if k=='trigger']
  end=trigger_points[-1] if trigger_points else changed_from
  alert=open_alert(s,state,end,'recompute',now)
  alert.correction_note=note+'：重放发现漏报，补发触发'
 elif expected==actual=='alerting':
  alert=s.get(CongestionAlert,state.open_alert_id)
  new_triggers=[t for t,k in transitions if k=='trigger']
  retriggered=new_triggers and alert and iso(new_triggers[-1])!=alert.triggered_at
  if alert and retriggered:
   alert.status='corrected';alert.corrected_at=iso(now);alert.correction_note=note+'：原拥堵段被迟到记录拆分，原触发判定失效';alert.updated_at=iso(now)
   emit(s,state,alert,'correction',dict(alert_id=alert.id,station=station,reason=note,expected='retrigger',actual='alerting',transitions=trail,method=METHOD),now)
   state.status='clear';state.open_alert_id=None
   alert2=open_alert(s,state,new_triggers[-1],'recompute',now)
   alert2.correction_note=note+'：拥堵段重定，补发触发'
  elif alert and alert.triggered_at and iso(changed_from)<=alert.triggered_at:
   fresh=recent_windows(s,station,'hot',parse(alert.triggered_at),TRIGGER_STREAK)
   old=(alert.trigger_evidence or {}).get('windows') or []
   if [(w['window_end'],w['total'],w['delayed']) for w in fresh]!=[(w['window_end'],w['total'],w['delayed']) for w in old]:
    alert.trigger_evidence=dict(windows=fresh,method=METHOD)
    alert.correction_note=note+'：触发证据窗口被重算，已按最新判定刷新';alert.updated_at=iso(now)
    emit(s,state,alert,'correction',dict(alert_id=alert.id,station=station,reason=note,expected='alerting',actual='alerting',transitions=trail,method=METHOD),now)
def tick(s,now=None):
 now=now or now_utc()
 from src.services.repository import arrival_rows
 rows=arrival_rows(s)
 for station in STATIONS:
  try:
   scan_station(s,station,rows,now)
   recompute_station(s,station,rows,now)
   s.commit()
  except Exception:
   s.rollback();logging.exception('congestion tick failed for '+station)
