from sqlalchemy import select
from src.models.entities import AlertEvent,ReceiverInbox
from src.utils.time import now_utc
def iso(dt):return dt.isoformat()
class SimulatedCrash(Exception):pass
def deliver(s,event,now):
 if event.state!='pending':return
 inbox=s.scalars(select(ReceiverInbox).where(ReceiverInbox.event_id==event.id)).first()
 if not inbox:
  inbox=ReceiverInbox(event_id=event.id,station=event.station,seq=event.seq,kind=event.kind,payload=event.payload,received_at=iso(now))
  s.add(inbox)
 event.state='sent';event.sent_at=iso(now);event.attempts+=1
 s.commit()
def confirm(s,event,now):
 if event.state!='sent':return
 inbox=s.scalars(select(ReceiverInbox).where(ReceiverInbox.event_id==event.id)).first()
 if inbox and inbox.processed_at is None:
  inbox.processed_at=iso(now);inbox.process_note='接收端按站序处理 '+event.station+' #'+str(event.seq)+' '+event.kind
 event.state='acknowledged';event.acknowledged_at=iso(now)
 s.commit()
def pending_events(s):
 return s.scalars(select(AlertEvent).where(AlertEvent.state.in_(['pending','sent'])).order_by(AlertEvent.station,AlertEvent.seq)).all()
def process_once(s,now=None,crash_after=None,crash_event=None):
 now=now or now_utc();done=0;blocked=set()
 for event in pending_events(s):
  if event.station in blocked:continue
  hit=crash_event is None or event.id==crash_event
  try:
   deliver(s,event,now)
   if hit and crash_after=='sent':raise SimulatedCrash('崩溃注入：事件 '+str(event.id)+' 已投递未确认')
   confirm(s,event,now)
   if hit and crash_after=='acknowledged':raise SimulatedCrash('崩溃注入：事件 '+str(event.id)+' 确认提交后')
   done+=1
  except SimulatedCrash:s.rollback();raise
  except Exception:s.rollback();blocked.add(event.station)
 return done

