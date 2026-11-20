# 临港 · 港站晨报定时分发

把昨日计划到发、延误分位数与交接班晨报串起来，由真实worker到点生成站内收件箱回执，保留冻结来源。值班室另配持续拥堵告警：按各站实际到发每5分钟核算近30分钟，连续超阈自动触发/解除/更正，由独立本地接收端按序确认，晨报前即可发现持续拥堵。

## 场景与工作流

临港联运运营北站、南站和东港三个港站，每天约150条班次记录。现行工作流是值班员导出 `临港到发_20261114.xlsx`，分析员按日期筛选后手算延误，负责人再把表格转发给下一班。痛点是跨午夜班次归错日、未到发被当准点、重复转发后分不清同一口径。上游为港站计划/实际到发记录；本项目替代日界归档、晨报试算和定时分发，并对持续拥堵做自动判定与本地送达；下游为站内值班室交接班。

三个核心实体：Arrival到发记录、Subscription晨报订阅、Receipt分发回执，另有用户和审计。拥堵告警新增五个实体：CongestionWindow窗口核算、CongestionState每站状态机、CongestionAlert告警、AlertEvent告警事件、ReceiverInbox接收端收件箱。五页：到发台账、晨报预览、订阅计划、分发回执、拥堵告警。回执支持值班复核与下载，原始记录修订不改变已分发晨报；告警页可查看每次判定与送达依据。

|角色|权限|典型动作|
|---|---|---|
|admin|全部；独占订阅配置/值班复核/审计|设定08:30晨报并核对回执|
|analyst|到发录入/修订、晨报试算、读取计划回执、导出|补录跨午夜班次|
|viewer|业务只读与回执导出|下载冻结晨报；后端拒绝写入/计算|

## 快速启动

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
docker compose exec -T backend python -m scripts.drill_congestion   # 可选：拥堵告警演练
```

前端 [localhost:18212](http://localhost:18212)，API [localhost:19212/docs](http://localhost:19212/docs)，PostgreSQL映射17712。账号 `admin@demo.local`、`analyst@demo.local`、`viewer@demo.local`，密码均 `Demo123!`。种子为3账号、150条昨日计划到发、3条暂停订阅；不会自动对外发送。自然编号幂等，重复运行不覆盖人工修订；种子日期为首次启动的昨日，后续日需业务录入新记录。

## 一日业务剧本

1. 08:00 viewer 查看LG-001至LG-150，核对未到发为空。
2. 08:05 analyst 录入 `LG-151`，北站，计划2026-11-14 23:55，实际2026-11-15 00:05，交接说明为跨午夜。
3. 08:10 在晨报预览选择2026-11-14和北站，检查该班次仍归14日、延误10分钟。
4. 08:15 admin 创建“北站08:30值班晨报”，站内收件箱“北站值班室”，每日08:30，启用。
5. 演示时可把“首次到点”设为未来10秒；不需要任何手动发送按钮。
6. 08:30 worker每秒检查到点订阅，冻结昨日来源并写入回执。
7. 08:31 刷新分发回执，核对P50/P95、完成/未到发人数和来源checksum。
8. 08:35 admin 写入“跨日班次已核对”复核意见；viewer下载 `port-digest-<业务日>-<回执id>.json`。
9. 任意时刻打开“拥堵告警”页：三站状态卡、告警列表、事件送达时间线与判定窗口依据；补录跨午夜班次后，受影响窗口自动重算，误报以更正事件闭环。

## 计算与调度口径

**归档**按计划到发时间转Asia/Shanghai后的自然日，而非实际到达日。后端时间输入必须有偏移（如+08:00），统一保存UTC；前端datetime-local明确按上海解释。实际可空，表示未到发。时间差超过3天拒绝，提示核对。

**延误**=max(0,实际−计划)分钟，提前到达记0。已到发才进入延误率与分位数分母；未到发单独计数。P50/P95使用排序后位置(n−1)p的线性插值；没有已到发样本时分位数、均值和延误率为null，页面显示“未定义”。输出带完整选中来源、口径版本与SHA256 checksum。

**到点调度**由worker每秒扫描UTC next_run_at，Redis锁串行化扫描，数据库用行锁和唯一约束 `(subscription_id, report_day)` 防止同订阅同报告日重复入箱。首次时间可设未来数秒进行真实调度验证；以后按Asia/Shanghai每日HH:MM安排。到点或服务中断恢复时只生成“执行时刻的最新昨日”一份，不补历史天；同日再次到点只推进next_run_at，不新增回执。暂停订阅不执行。修订订阅如果未填写首次时间，会重新安排下一个每日时刻；恢复暂停也按此规则。

**拥堵判定**按各站实际到发时刻，每5分钟对齐格子核算近30分钟（窗口截止T统计[T−30分,T)内已到发班次），延误口径与晨报一致。窗口四分类：hot=样本≥10班且延误率>50%；ok=有样本且延误率≤50%；gray=样本不足10班但延误率>50%（重置双计数）；empty=空窗（中性，不推进也不打断，空窗不算正常）。连续3个hot窗触发告警，连续2个ok窗解除。补录/修订到发记录会标记受影响窗口（实际时刻前后30分钟覆盖的格子），worker按站限量重算（每站每轮最多96窗，补录不拖住其他站），自最早变化窗重放状态机并与已发事件调和：误报触发→告警更正为corrected并发correction事件；拥堵段被迟到记录拆分重定→原告警更正、按新触发点补发；漏报→补建告警（source=recompute）；仅证据窗计数变化→刷新触发证据并登记correction。已发送的通知与关联依据全部保留，事件流append-only。

**告警送达**事件状态机：pending待发送→sent已发送→acknowledged接收确认。独立本地接收端（receiver进程）按（站，seq）严格升序处理，同站前序未完成不越过后续，并发扫描不交错事件；每站事件序号在同一事务内单调分配。接收端收件箱以event_id唯一约束幂等写入，投递与确认各自独立提交：接收后中断，重启从未确认事件恢复，重试不重复通知（不重复入箱、不重复计数）。某站处理异常仅阻塞该站当轮，不影响其他站。

**演练** `python -m scripts.drill_congestion` 依次验证：跨午夜归窗（计划23:5x实际00:0x仍算延误并入午夜后窗口）、连续超阈触发、迟到修订拆分拥堵段→重算→更正且已送通知保留、准点段解除、收件后崩溃注入与恢复（无重复通知）、事件序号连续、原晨报到点分发照常。演练以下一个上海午夜为基准构造DRILL班次，通过tick显式推进合成时刻，不等待真实5分钟；持Redis调度/接收锁与后台进程互斥。演练结束清理DRILL班次与演练订阅，保留告警证据供页面查看；`--reset` 清除全部拥堵演练产物。

晨报“送达”表示已提交站内数据库收件箱，不是SMTP邮件。回执保存冻结payload，DB提交后附加写共享产物卷；附加文件失败不影响DB回执和导出。值班复核仅管理员可做，一次复核不可覆盖，且不改变payload。支持单worker；唯一约束与锁防重复，但不声称对外消息的exactly-once。

## 工程、环境变量和开发

Python3.11/FastAPI/SQLAlchemy/PyJWT、原生ES modules/CSS、Nginx、PostgreSQL16、Redis7。恰好frontend/backend/worker/receiver/db/redis六服务，各有健康检查；backend等db/redis、worker与receiver还等backend、frontend等backend。Redis实际提供调度锁与接收锁；worker心跳 `/tmp/heartbeat`，receiver心跳 `/tmp/receiver-heartbeat`，DB/Redis/产物分别命名卷。Nginx `/api/` 去前缀代理后端根路由。

`.env.example`列出COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET。显式子网10.253.113.0/24规避当前机默认地址池耗尽，可按环境调整。演示凭据应在正式部署替换。清理本项目用 `docker compose down -v --remove-orphans`。

本地开发：启动db/redis，backend安装requirements，设置DATABASE_URL/REDIS_URL/JWT_SECRET，再运行 `uvicorn src.main:app --reload`、`python -m src.worker` 和 `python -m src.receiver`；前端仍建议用Nginx同源代理。后端目录src按routes/controllers/services/models/schemas/middlewares/config/utils/constants分工；前端src按api/stores/pages/components/hooks/utils/constants/types/router分工。ORM首次建表，migrations保留实际初始DDL（001）与拥堵告警DDL（002），无在线迁移执行器。

枚举：后端 `src/constants/enums.py`、`src/schemas/business.py` 与前端 `src/constants/enums.js` 定义ROLES、STATIONS、RECEIPT_STATES及WINDOW_CATEGORIES、ALERT_STATES、EVENT_KINDS、EVENT_STATES；TIMEZONE固定Asia/Shanghai。共用表格、表单、状态标签、晨报摘要组件；表单hooks、HTML转义和日期工具；请求ID/安全响应头两个中间件，业务审计与全局错误恢复。测试与截图在 `../../test-artifacts/gb-3113/`，不放源码内。

范围：逐条录入/完整修订，无CSV导入、删除或分页；只有三个固定港站与上海时区。站内收件箱按名称记录，当前所有登录角色可读业务回执，无多租户邮箱隔离；不连接邮件、短信或外部IM。Google Fonts离线时字体回退。许可证MIT，见LICENSE。
