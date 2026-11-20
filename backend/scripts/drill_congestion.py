"""持续拥堵告警演练：跨午夜归窗、迟到拆分拥堵段、收件后崩溃恢复、原晨报照常。

用法：
  python -m scripts.drill_congestion          依次演练四个场景并保留告警证据供页面查看
  python -m scripts.drill_congestion --reset  清除全部拥堵演练产物（窗口/状态/告警/事件/收件箱）与DRILL班次

在compose内运行：docker compose exec -T backend python -m scripts.drill_congestion
演练通过tick(s, now=合成时刻)显式推进窗口，不等待真实5分钟；若Redis可达则持有调度/接收锁，
后台worker与receiver自动跳过同刻扫描，互不干扰；演练结束释放锁，实时检测照常。
"""
import sys,os
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from src.config.database import Base,engine,Session
from src.models.entities import Arrival,CongestionWindow,CongestionState,CongestionAlert,AlertEvent,ReceiverInbox,Subscription,Receipt
from src.services.congestion import tick,floor_step,iso,mark_recompute
from src.services.receiving import process_once,SimulatedCrash
from src.services.scheduler import run_due
from src.services.repository import save_arrival
from src.schemas.business import ArrivalInput
from src.utils.time import now_utc,SH
STATION='北站'
def next_midnight(now):
 local=now.astimezone(SH);mid=datetime.combine(local.date(),datetime.min.time(),tzinfo=SH)
 if mid<=local:mid+=timedelta(days=1)
 return mid.astimezone(timezone.utc)
def locks():
 try:
  import redis
  r=redis.Redis.from_url(os.environ.get('REDIS_URL',''),socket_connect_timeout=2);r.ping()
  return r.lock('port-digest:scheduler',timeout=600,blocking_timeout=30),r.lock('port-digest:receiver',timeout=600,blocking_timeout=30)
 except Exception:
  print('提示：Redis不可达，按无锁模式演练（本地单进程安全）');return nullcontext(),nullcontext()
def reset(s):
 for model in (ReceiverInbox,AlertEvent,CongestionAlert,CongestionWindow,CongestionState):s.query(model).delete()
 s.query(Arrival).filter(Arrival.code.like('DRILL-%')).delete()
 s.query(Receipt).filter(Receipt.recipient=='演练值班室').delete()
 s.query(Subscription).filter(Subscription.name.like('演练%')).delete()
 s.commit();print('已清除演练产物与DRILL班次')
def put(s,code,planned,actual):
 s.add(Arrival(code=code,station=STATION,planned_at=planned.isoformat(),actual_at=actual.isoformat(),note='拥堵演练构造'))
def build(s,M):
 for i in range(12):
  actual=M-timedelta(minutes=25)+timedelta(seconds=100*i);put(s,'DRILL-A%02d'%(i+1),actual-timedelta(minutes=15),actual)
 for i in range(12):
  actual=M-timedelta(minutes=5)+timedelta(seconds=100*i);put(s,'DRILL-B%02d'%(i+1),actual-timedelta(minutes=20),actual)
 for i in range(12):
  actual=M+timedelta(minutes=15)+timedelta(seconds=100*i);put(s,'DRILL-C%02d'%(i+1),actual-timedelta(minutes=25),actual)
 for i in range(10):
  actual=M+timedelta(minutes=95)+timedelta(seconds=100*i);put(s,'DRILL-D%02d'%(i+1),actual,actual)
 for i in range(12):
  actual=M+timedelta(minutes=120)+timedelta(seconds=100*i);put(s,'DRILL-E%02d'%(i+1),actual-timedelta(minutes=30),actual)
 s.commit()
def walk(s,state,target):
 while True:
  s.refresh(state)
  current=datetime.fromisoformat(state.last_window_end) if state.last_window_end else None
  if current and current>=floor_step(target):break
  tick(s,now=min(current+timedelta(hours=3),target) if current else target)
def window(s,end):return s.query(CongestionWindow).filter_by(station=STATION,window_end=iso(end)).first()
def expect(ok,message):
 if not ok:raise SystemExit('演练失败：'+message)
 print('  ✓ '+message)
def run():
 now=now_utc();M=next_midnight(now)
 print('演练基准：下一个上海午夜 M=%s（UTC %s）'%(M.astimezone(SH).isoformat(),M.isoformat()))
 lock_scheduler,lock_receiver=locks()
 with lock_scheduler,lock_receiver:
  with Session() as s:
   if s.query(CongestionAlert).count():raise SystemExit('已存在告警证据，请先 --reset 再演练')
   state=s.query(CongestionState).filter_by(station=STATION).first()
   if not state:state=CongestionState(station=STATION,status='clear',streak_over=0,streak_ok=0,event_seq=0,updated_at=iso(now));s.add(state);s.commit()
   if not state.last_window_end:state.last_window_end=iso(M-timedelta(minutes=35));s.commit()
   print('场景零：构造58条DRILL班次（计划/实际围绕午夜前后）');build(s,M)
   mark_recompute(s,STATION,[r.actual_at for r in s.query(Arrival).filter(Arrival.code.like('DRILL-%')).all()]);s.commit()
   print('场景一：跨午夜归窗与延误口径');walk(s,state,M+timedelta(minutes=40))
   w=window(s,M+timedelta(minutes=10))
   expect(w and w.total==18 and w.delayed==18,'跨午夜窗口[23:40,00:10)共18班全延误（实际00:0x归午夜后窗口，计划23:4x延误照算）')
   expect(w.category=='hot','该窗分类hot（≥10班且延误率100%）')
   alert1=s.query(CongestionAlert).filter_by(station=STATION).order_by(CongestionAlert.id).first()
   expect(alert1 and alert1.triggered_at==iso(M+timedelta(minutes=5)),'连续3个hot窗于00:05触发告警#%d'%alert1.id)
   expect(len(alert1.trigger_evidence['windows'])==3,'触发证据含3个hot窗明细')
   process_once(s,now=now)
   ev1=s.query(AlertEvent).filter_by(alert_id=alert1.id,kind='trigger').one()
   expect(ev1.state=='acknowledged' and ev1.attempts==1,'触发通知已送达并被接收端确认（一次投递）')
   print('场景二：迟到修订拆分拥堵段→重算→更正，已送通知保留')
   for r in s.query(Arrival).filter(Arrival.code.like('DRILL-A%')).all():
    save_arrival(s,ArrivalInput(code=r.code,station=r.station,planned_at=r.actual_at,actual_at=r.actual_at,note='拥堵演练：迟到修订为准点'),r.id)
   tick(s,now=M+timedelta(minutes=40));s.refresh(state);s.refresh(alert1)
   expect(alert1.status=='corrected','原告警被更正（误报触发失效）')
   corr=s.query(AlertEvent).filter_by(alert_id=alert1.id,kind='correction').first()
   expect(corr is not None,'生成correction事件说明重算依据')
   s.refresh(ev1);expect(ev1.state=='acknowledged','原触发通知保留且仍为已确认')
   alert2=s.query(CongestionAlert).filter_by(station=STATION).order_by(CongestionAlert.id).all()[1]
   expect(alert2.status=='open' and alert2.source=='recompute' and alert2.triggered_at==iso(M+timedelta(minutes=25)),'拥堵段重定：00:25补发新告警#%d'%alert2.id)
   print('场景三：准点段解除→再拥堵→收件后崩溃恢复')
   walk(s,state,M+timedelta(minutes=150))
   s.refresh(alert2)
   expect(alert2.status=='cleared' and alert2.cleared_at==iso(M+timedelta(minutes=105)),'连续2个ok窗于01:45解除告警#%d'%alert2.id)
   alert3=s.query(CongestionAlert).filter_by(station=STATION).order_by(CongestionAlert.id).all()[2]
   expect(alert3.triggered_at==iso(M+timedelta(minutes=145)),'新拥堵段于02:25触发告警#%d'%alert3.id)
   ev3=s.query(AlertEvent).filter_by(alert_id=alert3.id,kind='trigger').one()
   crashed=False
   try:process_once(s,now=now,crash_after='sent',crash_event=ev3.id)
   except SimulatedCrash:crashed=True
   expect(crashed,'注入崩溃：通知已入接收端收件箱但未确认')
   s.expire_all();ev3=s.query(AlertEvent).filter_by(id=ev3.id).one()
   inbox=s.query(ReceiverInbox).filter_by(event_id=ev3.id).all()
   expect(ev3.state=='sent' and len(inbox)==1 and inbox[0].processed_at is None,'崩溃现场：事件sent未确认，收件箱1条未处理')
   process_once(s,now=now)
   s.expire_all();ev3=s.query(AlertEvent).filter_by(id=ev3.id).one()
   inbox=s.query(ReceiverInbox).filter_by(event_id=ev3.id).all()
   expect(ev3.state=='acknowledged' and len(inbox)==1 and inbox[0].processed_at is not None and ev3.attempts==1,'重启恢复：补确认完成，收件箱无重复，未重复投递')
   events=s.query(AlertEvent).filter_by(station=STATION).order_by(AlertEvent.seq).all()
   expect([e.seq for e in events]==list(range(1,len(events)+1)),'全站事件序号连续无交错：'+'→'.join(e.kind for e in events))
   print('场景四：原晨报照常')
   sub=Subscription(name='演练晨报-'+STATION,station=STATION,daily_at='08:30',recipient='演练值班室',enabled=True,next_run_at=(now-timedelta(minutes=1)).isoformat())
   s.add(sub);s.commit();run_due(s,now)
   receipt=s.query(Receipt).filter_by(subscription_id=sub.id).first()
   expect(receipt is not None and receipt.payload.get('checksum'),'到点订阅照常生成冻结晨报回执#%d'%receipt.id)
   s.delete(receipt);s.delete(sub);s.commit()
   s.query(Arrival).filter(Arrival.code.like('DRILL-%')).delete();s.commit()
   alerts=s.query(CongestionAlert).order_by(CongestionAlert.id).all()
   print('演练完成。保留证据：告警%s；DRILL班次与演练订阅已清理。'%(','.join('#%d(%s)'%(a.id,a.status) for a in alerts)))
   print('页面「拥堵告警」可查看判定与送达依据；--reset 可清除全部演练证据。')
if __name__=='__main__':
 Base.metadata.create_all(engine)
 if '--reset' in sys.argv:
  with Session() as s:reset(s)
 else:run()
