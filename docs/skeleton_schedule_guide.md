# 按团队 Skeleton 开发日程模块

已同步 GitHub `main` 的 skeleton 及匹配实现（提交 `03ef6c4`），保留本地 `shuluun` 分支。团队入口是 `app.main:app`，后续日程工作从 `/schedule` 继续。已有 `schedule_service` 作为计算引擎、独立 API 和 Google 适配实现保留。

Skeleton 提供统一导航、样式、Profile → Schedule → Matching → Session → Invite 页面骨架和共用数据格式。你负责补日程逻辑和日程页面，队友继续使用相同函数与字段。

| 文件或入口 | 作用及本次状态 |
| --- | --- |
| `app/schedule_core.py` | 你的核心逻辑，已接入每周空闲、临时增减、考试与日历忙碌扣除、本人冲突及有效替代时段 |
| `app/routers/schedule.py` | 你的表单与 API，已接入保存、删除、错误反馈及可用时间查询 |
| `app/templates/schedule.html` | 你的页面，复用团队组件，增加实际可用时间和模拟状态展示 |
| `app/contracts.py` | 共用字段，本次未修改；继续使用 BusyInterval、MatchBusyInterval、Exam |
| `app/store.py` | 共用多学生内存模拟数据，保留队友实现；重启重置 |
| `app/static/styles.css`、`/style` | 团队视觉规范，本次复用已有组件 |
| `app/routers/session.py`、`partials/conflict.html` | 必要兼容：无替代时段时显示说明，不可覆盖的冲突隐藏 Study anyway |
| `schedule_service/engine.py` | 已有确定性时间引擎，skeleton 直接调用，不经过模型 |

## 现在查看

从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8014 --no-access-log
```

打开 <http://127.0.0.1:8014/schedule>，这是团队日程页。原 <http://127.0.0.1:8013/demo> 是 Alex/Bella 模块演示，数据集不同，不会互相同步。

默认查看 2026-10-29 16:00–21:00。模拟日历占用 18:00–19:00，所以返回 16:00–18:00 和 19:00–21:00。把周四结束时间改为 20:30 并保存，第二段会缩短。该行为已在浏览器实际验证并恢复默认值。

## 对接接口

`schedule_core.availability(starts_at, ends_at)` 返回连续可用区间。本地表单按 America/Los_Angeles 解释，输出带 UTC 偏移；窗口最多 14 天，夏令时缺失或重复时间须显式提供偏移。

```text
GET /api/schedule/availability?starts_at=2026-10-29T16:00&ends_at=2026-10-29T21:00
```

响应为 `synthetic`、`timezone`、`available: [{starts_at, ends_at}]`，没有事件标题。`list_busy_for_match(student_id)` 删除标题，并按 kind 区分 busy 阻断与 free 临时空闲；`weekly_hours(student_id)` 和 `list_exams(student_id)` 也已适配最新版匹配。匹配与日程页使用同一计算引擎，内部统一 UTC，本地表单按洛杉矶时间解释。

`own_conflict(starts_at, ends_at, study_anyway)` 继续供 Session 使用。替代时段保留原时长，并由引擎复查；14 天内没有完整时段就返回空。Study anyway 仅覆盖本人导入日历，且只对本次调用有效；考试、手动忙碌、周空闲边界和其他人的日历仍是硬约束。

## 下一步工作顺序

1. 已接入 David 最新评分模块并通过原有匹配用例。后续真实多用户数据仍需联调；地点依然使用团队种子数据。
2. 与 Stephen 对接真实登录，替换 `store.OWNER_ID`。Skeleton 没有真实鉴权，owner 标题接口仅适合本地模拟。
3. 将内存 Store 接到团队 Postgres，确定迁移和每用户归属。已有 SQLAlchemy 服务可参考，但实际 Postgres 尚未验证。
4. 登录和持久化接好后，将已有 Google OAuth、两周忙碌刷新、加密凭据接到 `/schedule`。现在的按钮不会假装连接成功。
5. 与 Marc 对接权威邀请状态，再复用组织者确认和日历执行器。Skeleton 的 accepted/calendar_written 是模拟标志，不能作为真实同意或 Google 写入证明。

Omnara 的只读模拟接入在 `/demo`，见 [接入状态](omnara_integration.md)，不改变排期规则。

验证：181 项测试、12 项子测试通过，含 21 项队友匹配用例、14 项 skeleton 检查、16 项跨模块联调用例。真实 Google、Omnara 模型运行、Postgres、邮件和完整产品评测未验证。

改动范围和操作步骤见 [本次检查说明](schedule_review.md)。
