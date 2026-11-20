-- 持续拥堵告警：窗口核算、每站状态机、告警、事件流与本地接收端收件箱。
-- ORM首次启动自动建表；本文件仅留存实际DDL，无在线迁移执行器。
CREATE TABLE public.congestion_windows (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station character varying(30),
    window_end character varying(40),
    total integer,
    delayed integer,
    delay_rate double precision,
    category character varying(10),
    checksum character varying(64),
    state_after json,
    computed_at character varying(40),
    CONSTRAINT uq_window_station_end UNIQUE (station, window_end)
);
CREATE TABLE public.congestion_states (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station character varying(30) NOT NULL UNIQUE,
    status character varying(20),
    streak_over integer,
    streak_ok integer,
    last_window_end character varying(40),
    recompute_from character varying(40),
    changed_from character varying(40),
    event_seq integer,
    open_alert_id integer,
    updated_at character varying(40)
);
CREATE TABLE public.congestion_alerts (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station character varying(30),
    status character varying(20),
    source character varying(20),
    triggered_at character varying(40),
    trigger_evidence json,
    cleared_at character varying(40),
    clear_evidence json,
    corrected_at character varying(40),
    correction_note text,
    created_at character varying(40),
    updated_at character varying(40)
);
CREATE TABLE public.alert_events (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_id integer REFERENCES public.congestion_alerts(id),
    station character varying(30),
    seq integer,
    kind character varying(20),
    state character varying(20),
    payload json,
    attempts integer,
    created_at character varying(40),
    sent_at character varying(40),
    acknowledged_at character varying(40),
    CONSTRAINT uq_event_station_seq UNIQUE (station, seq)
);
CREATE TABLE public.receiver_inbox (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id integer NOT NULL UNIQUE REFERENCES public.alert_events(id),
    station character varying(30),
    seq integer,
    kind character varying(20),
    payload json,
    received_at character varying(40),
    processed_at character varying(40),
    process_note text
);
