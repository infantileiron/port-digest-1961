from sqlalchemy import Column,Integer,String,Float,JSON,Text,Boolean,ForeignKey,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Arrival(Base):
 __tablename__='arrivals'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);station=Column(String(30));planned_at=Column(String(40));actual_at=Column(String(40),nullable=True);note=Column(Text,default='')
class Subscription(Base):
 __tablename__='subscriptions'
 id=Column(Integer,primary_key=True);name=Column(String(100),unique=True,nullable=False);station=Column(String(30));daily_at=Column(String(5));recipient=Column(String(80));enabled=Column(Boolean,default=False);next_run_at=Column(String(40));last_run_at=Column(String(40),nullable=True)
class Receipt(Base):
 __tablename__='receipts'
 __table_args__=(UniqueConstraint('subscription_id','report_day',name='uq_receipt_subscription_day'),)
 id=Column(Integer,primary_key=True);subscription_id=Column(Integer,ForeignKey('subscriptions.id'));report_day=Column(String(10));recipient=Column(String(80));created_at=Column(String(40));state=Column(String(20),default='delivered');payload=Column(JSON);review_note=Column(Text,default='')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class CongestionWindow(Base):
 __tablename__='congestion_windows'
 __table_args__=(UniqueConstraint('station','window_end',name='uq_window_station_end'),)
 id=Column(Integer,primary_key=True);station=Column(String(30));window_end=Column(String(40));total=Column(Integer,default=0);delayed=Column(Integer,default=0);delay_rate=Column(Float,nullable=True);category=Column(String(10));checksum=Column(String(64));state_after=Column(JSON);computed_at=Column(String(40))
class CongestionState(Base):
 __tablename__='congestion_states'
 id=Column(Integer,primary_key=True);station=Column(String(30),unique=True,nullable=False);status=Column(String(20),default='clear');streak_over=Column(Integer,default=0);streak_ok=Column(Integer,default=0);last_window_end=Column(String(40),nullable=True);recompute_from=Column(String(40),nullable=True);changed_from=Column(String(40),nullable=True);event_seq=Column(Integer,default=0);open_alert_id=Column(Integer,nullable=True);updated_at=Column(String(40))
class CongestionAlert(Base):
 __tablename__='congestion_alerts'
 id=Column(Integer,primary_key=True);station=Column(String(30));status=Column(String(20),default='open');source=Column(String(20),default='scan');triggered_at=Column(String(40));trigger_evidence=Column(JSON);cleared_at=Column(String(40),nullable=True);clear_evidence=Column(JSON,nullable=True);corrected_at=Column(String(40),nullable=True);correction_note=Column(Text,default='');created_at=Column(String(40));updated_at=Column(String(40))
class AlertEvent(Base):
 __tablename__='alert_events'
 __table_args__=(UniqueConstraint('station','seq',name='uq_event_station_seq'),)
 id=Column(Integer,primary_key=True);alert_id=Column(Integer,ForeignKey('congestion_alerts.id'));station=Column(String(30));seq=Column(Integer);kind=Column(String(20));state=Column(String(20),default='pending');payload=Column(JSON);attempts=Column(Integer,default=0);created_at=Column(String(40));sent_at=Column(String(40),nullable=True);acknowledged_at=Column(String(40),nullable=True)
class ReceiverInbox(Base):
 __tablename__='receiver_inbox'
 id=Column(Integer,primary_key=True);event_id=Column(Integer,ForeignKey('alert_events.id'),unique=True,nullable=False);station=Column(String(30));seq=Column(Integer);kind=Column(String(20));payload=Column(JSON);received_at=Column(String(40));processed_at=Column(String(40),nullable=True);process_note=Column(Text,default='')
