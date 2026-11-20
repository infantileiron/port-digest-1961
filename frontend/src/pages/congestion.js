import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {local} from '../utils/dates.js';import {badge} from '../components/common/status.js';import {table} from '../components/common/table.js';
const CATEGORY={'hot':'超阈','ok':'正常','gray':'样本不足','empty':'空窗'};
const KIND={'trigger':'触发','clear':'解除','correction':'更正'};
const EVENT_STATE={'pending':'待发送','sent':'已发送待确认','acknowledged':'接收已确认'};
const ALERT_STATE={'open':'告警中','cleared':'已解除','corrected':'已更正'};
const t=v=>v?local(v):'—';
const rate=v=>v===null||v===undefined?'—':(v*100).toFixed(0)+'%';
function windowsTable(ws){return table(['窗口截止 / 上海','班次','延误','延误率','判定'],(ws||[]).map(w=>[t(w.window_end),w.total,w.delayed,rate(w.delay_rate),CATEGORY[w.category]||w.category]))}
function eventBlock(e){return `<div class="rec"><strong>#${e.seq} ${KIND[e.kind]||e.kind}</strong> ${badge(EVENT_STATE[e.state]||e.state)}<p class="eyebrow">创建 ${t(e.created_at)} · 发送 ${t(e.sent_at)} · 确认 ${t(e.acknowledged_at)} · 投递 ${e.attempts} 次</p>${e.inbox?`<p class="eyebrow">接收端：收件 ${t(e.inbox.received_at)} · 处理 ${t(e.inbox.processed_at)} · ${esc(e.inbox.process_note||'')}</p>`:'<p class="eyebrow">接收端：尚未入收件箱</p>'}${e.payload&&e.payload.reason?`<p class="notice">${esc(e.payload.reason)}（${esc(e.payload.expected)} ← ${esc(e.payload.actual)}）</p>`:''}</div>`}
export function render(){
 let overview=state.congestion?.overview||[],alerts=state.congestion?.alerts||[],d=state.selectedAlert;
 let cards=overview.map(st=>`<div><strong>${esc(st.station)}</strong>${st.status==='alerting'?'告警中':'正常'}<p class="eyebrow">连续超阈 ${st.streak_over} · 连续正常 ${st.streak_ok}<br>最近窗口 ${st.last_window_end?t(st.last_window_end):'—'}</p></div>`).join('');
 let detail=d?`<h2>告警 #${d.id} ${esc(d.station)} ${badge(ALERT_STATE[d.status]||d.status)}</h2><p class="eyebrow">触发 ${t(d.triggered_at)} · 解除 ${t(d.cleared_at)} · 更正 ${t(d.corrected_at)} · 来源 ${esc(d.source==='recompute'?'重算补发':'实时扫描')}</p>${d.correction_note?`<p class="notice">${esc(d.correction_note)}</p>`:''}<h3>事件与送达依据</h3>${(d.events||[]).map(eventBlock).join('')||'<p>暂无事件。</p>'}<h3>触发证据（判定窗口）</h3>${windowsTable(d.trigger_evidence?.windows)}${d.clear_evidence?`<h3>解除证据</h3>${windowsTable(d.clear_evidence.windows)}`:''}`:'<p class="notice">选择右侧告警查看判定与送达依据。告警由worker每5分钟格子自动核算，无需手动操作。</p>';
 let recent=(overview.find(x=>d&&x.station===d.station)||overview[0]||{}).recent_windows||[];
 return `<div class="metrics">${cards}</div><div class="split"><section class="panel">${detail}</section><aside class="side"><h2>告警列表</h2>${alerts.map(a=>`<button class="study" data-alert="${a.id}">#${a.id} ${esc(a.station)} · ${ALERT_STATE[a.status]||a.status}<br>${t(a.triggered_at)}</button>`).join('')||'<p>尚无告警。</p>'}<h2>最近窗口判定</h2>${windowsTable(recent.slice(0,12))}<p class="eyebrow">hot=≥10班且延误率过半；ok=有样本未过半；gray=样本不足；empty=空窗（不算正常）</p></aside></div>`}
