# Admin Data V2 / Kernel 边界主线结项报告

日期：2026-08-07（Asia/Shanghai）

## 结论

本次主线的代码适配、隔离接线和验证已经完成：Admin 只经 eidolond 发布的
公开 HTTP contract 访问 Data、Data Workspace、Hub 和 Kernel；Local API 的首次
Workspace 初始化经精确的 Admin 内部路由转发给 Data Workspace Authority。Admin
不打开或复制 Data、Kernel、Hub SQLite，不恢复旧 schema/API，不向 Data outbox
双写，也不宣称跨服务原子事务。

`os-control-plane` 隔离 profile 已真实启动 Admin、eidolond、Data Companion、Data
Workspace、Hub、Kernel，并完成 Workspace content-bound 幂等、冲突和 Admin 重启
恢复冒烟。该 profile 已完整停止；正式 Data 库未修改，正式 Admin/Agent 未由本次
工作启动。

隔离开发 profile 可以安全重新启动。正式 Admin 仍不批准启动：产品 credential
注入、operator ingress/auth、其他子项目适配和统一 Raspberry Pi release 验收属于
独立部署主线，尚未完成。

## 代码与生产者证据

- 适配前 Admin 证据点：`c553872`。
- Data V2 基线：`2a338940681fb281e1d962f891f2d675a0bb97b5`。
- Data Workspace contract：`9fc4f4e6bcad1e4e44f0a63b7619ce0702031e4e`。
- Kernel Data V2 consumer 基线：`66e61c9`。
- Kernel Workspace manifest contract：`c711238ef0be8f87bcf79fec8920b4c3b5cc849c`。
- Hub Device Management contract：`a91ea8356f79da75b359faf9d90cace6d4a07ffb`。
- Admin 两条既有适配历史由普通 merge `b4e4667` 汇合，没有 rebase、squash
  或丢弃提交。

契约测试从上述 Data/Kernel Git object 读取实际 schema、router 和 manifest，而非
根据文档名称构造接口。最终真实 runtime 还使用了当时 sibling 工作树的当前代码：
Data HEAD 为 `9fc4f4e`，Kernel HEAD 为 `f795a69`，Hub HEAD 为 `a91ea83`。核验时
Data 和 Kernel 存在其他会话的未提交改动；本任务没有修改、暂存或提交这些改动，
也没有把它们算入 Admin commit。

## 适配前真实问题

以下问题均可由 `git show c553872:<path>` 复核，完整逐项证据见
`2026-08-06-data-v2-kernel-boundary-adaptation.md`：

1. `app/main.py` 启动时创建 `DataStore` 并执行 schema 初始化/修复，使 Admin 成为
   Data 的第二 schema owner 和 writer。
2. `app/data/router.py` 依赖 Data V2 已删除的 Device/Event/runtime 表并实现旧 CRUD；
   `app/data/hub_client.py` 调用 Hub 已不存在的 `/api/admin/devices*`。
3. `app/memory/runners.py` 直接打开 Data SQLite，并扫描 Memory SQLite/Chroma；
   Mission Control 又把 Data Event 表误当作全局审计账本。
4. onboarding、Guard、resolve 和 owner 删除/备份路径直接 import Data ORM/service，
   同步跨 Data、Memory、Hub/runtime 编排或清理。
5. Web/CLI 暴露依赖上述失效接口的 Owner、Companion、Device、Guard、Memory 和
   Mission Control 页面。
6. Admin 的依赖、migration、测试与旧 Data schema 耦合；基线甚至因已删除 SDK
   symbol 在 collection 阶段失败，不能伪造 baseline passed 数。

## 最终依赖方向和逻辑归属

```text
Web / CLI
  -> Admin HTTP transport
  -> Admin application orchestration + Admin-owned workflow/read DTO
  -> typed Data / Hub / Kernel / System Directory ports
  -> strict HTTP adapters
  -> eidolond endpoint directory
  -> bounded-context public contracts

Mobile Controller
  -> authenticated Local API
  -> exact loopback Admin Workspace route
  -> Data Workspace Authority
```

- Hub：Device admission 和 Owner scope。
- Kernel：Device Mount、可选 Companion Attachment、CAS、幂等和本地低频审计。
- Data：Owner/Companion lifecycle、Persona、Memory Realm catalog、Face、Guard 等
  低频权威事实；首次 Owner workspace 由窄化的 Workspace Authority 原子提交。
- Admin：operator 控制面和显式前向恢复编排；不拥有上述权威数据。
- Bootstrap：只拥有 Host commissioning/claim SQLite。
- Audit projection：独立、可重建的本地索引，不是 Data outbox，也不是 telemetry
  数据库。

## 删除、重构和新增

早期适配提交 `06e7a2e` 删除了旧 `data/`、`devices/`、`guard/`、`memory/`、
`mission_control/`、`nats_kv/`、`onboarding/`、`resolve/` 后端聚合层及对应 Web、
测试和旧依赖；新增 `app/control_plane/{contracts,errors,directory,clients,service,router}`
及控制面 Web/五层测试。详表见前述 2026-08-06 报告。

Workspace 完整链路新增或重构：

- `contracts/local-api/v1/workspace-*.schema.json`；
- `local_api/workspace.py`、Controller/Host Owner binding 与 SQLite state port；
- Data Workspace typed client、Admin GET/PUT 内部路由和三组独立 credential 配置；
- Data Workspace supervisor、port registry 和 eidolond manifest 接线；
- `workspace_policy.py`，独立复现 Data canonical request fingerprint，拒绝恢复不同
  输入或接受错误 producer response；
- contract/component/integration/真实进程 E2E 覆盖 Data Workspace producer；
- runtime manifest 对所有 `required && enabled_by_default` service fail-closed，避免
  新必需服务未接线却通过 validate；
- runtime secret 升级兼容：旧隔离 env 缺少新 secret key 时安全生成；显式空值仍
  fail-closed。

## 失败语义和恢复保证

- 401/403、404、400/422、409、timeout/connect、5xx、schema/identity drift 分别保留
  为 `unauthorized/forbidden/not_found/invalid_request/conflict/unavailable/
  upstream_failure/contract_violation`。
- authority unavailable 不映射为 inactive/not-found。
- Device workflow 使用稳定父 request ID、确定性子 ID、Kernel CAS；部分成功返回
  最后提交阶段和 `retry-forward-same-request-id`。
- workflow 不执行破坏性自动补偿，因此不会把不存在的 rollback/compensation
  原子性报告为成功；补偿失败语义等价为继续保留已提交事实并要求前向重试或
  operator action。
- Workspace operation 是 Host UUIDv5 稳定 ID + canonical payload fingerprint；相同
  输入可并发重放，不同输入为 409。Data 已提交而 Host binding 中断时先恢复 Data
  operation，再重试 binding。
- E2E 验证旧路由 404、Admin 重启恢复、Data Workspace 停机为 503、重复/并发请求
  不产生第二份权威事实、冲突请求不改变既有结果。
- Admin 没有业务数据库事务可测试 rollback；Data Workspace 的多表事务/rollback
  属于 Data producer 自己的测试责任。本 E2E 只从 consumer 侧验证冲突/重放后
  既有 operation 不变，不把该证据夸大为 Admin 分布式事务测试。

## 最终测试结果

所有命令在本 Admin worktree 执行；数据库、凭据、端口、Unix socket 和 NATS 均为
pytest 临时资源或 `var/os-control-plane/` 隔离资源。

| 层级 | 命令 | 实际结果 |
| --- | --- | ---: |
| Unit | `.venv/bin/pytest server/tests -m unit -q` | 20 passed，254 deselected |
| Component/functional | `.venv/bin/pytest server/tests -m component -q` | 47 passed，225 deselected |
| Contract | `.venv/bin/pytest server/tests -m contract -q` | 12 passed，260 deselected |
| Integration | `.venv/bin/pytest server/tests -m integration -q` | 5 passed，267 deselected |
| Real-process E2E | `.venv/bin/pytest server/tests -m e2e -q -s` | 1 passed，271 deselected |
| Backend full + branch coverage | `.venv/bin/coverage run -m pytest server/tests -q` | 274 passed，0 failed/skipped，24 warnings，42.90s |
| Coverage report | `.venv/bin/coverage report` | 70%，7651 statements / 1922 branches |
| Frontend | `npm test -- --run` | 6 files / 25 tests passed |
| Frontend type/build | `npm run build` | `vue-tsc --noEmit` + Vite build passed |

静态/构建：

```bash
.venv/bin/ruff check server deploy
.venv/bin/ruff format --check <13 changed Python files>
.venv/bin/python -m compileall -q server/eidolon_admin_server deploy/dev
bash -n deploy/dev/run_all.sh
uv lock --check --offline
npm test -- --run
npm run build
```

Ruff、变更文件格式、compileall、shell syntax、lock、前端测试/类型/构建均通过。
linked worktree 内直接执行 lock check 会因 `../eidolon_sdk` 相对路径不存在而失败；
当前 worktree 与正常 sibling checkout 的 `pyproject.toml`、`uv.lock` SHA-256 分别
完全相同，最终 lock check 在正常 sibling 布局离线通过（65 packages）。项目未配置
mypy/pyright，未执行且不声明通过。Admin 无业务 migration；隔离 prepare 实际执行
Data Alembic V2 upgrade，并验证精确十表、`0001_system_data_v2`、integrity 和 FK。

24 个后端 warning 是 1 个 Starlette/httpx 上游弃用和 23 个 `dbus-next` Python
弃用。前端保留两条第三方 VueUse PURE annotation warning，以及 1.242 MB main
chunk 超过 500 kB 的 warning。

非最终失败记录：

1. 一次全量回归为 271 passed / 1 failed：旧测试 fake 把 workspace fingerprint
   硬编码为全零，新契约校验正确拒绝；fake 改为真实 canonical 算法后最终通过。
2. 受限文件系统沙箱内创建 Unix socket 返回 `Operation not permitted`；在获准的
   隔离本机 socket 权限下原命令通过，不是业务失败。
3. 第一次 runtime prepare 因旧隔离 `data.env` 缺少新 Workspace secret key 失败；
   这是实际升级缺陷，已修复并以“缺 key 生成、显式空值拒绝”两项单测覆盖。
4. 第一次手工 loopback smoke 被执行进程继承的外部 HTTP proxy 返回 502；设置
   `trust_env=False` 后真实 loopback 结果为 401/200/200/409/200。
5. linked worktree 内的首次 `uv lock --check --offline` 因相对 SDK source 不存在
   退出 2；在文件哈希相同且 sibling SDK 存在的正常 checkout 中最终通过。

## 真实 runtime 接线

```bash
./deploy/dev/run_all.sh os-control-plane prepare
./deploy/dev/run_all.sh os-control-plane validate
./deploy/dev/run_all.sh os-control-plane start
./deploy/dev/run_all.sh os-control-plane sv restart admin:admin-api
./deploy/dev/run_all.sh os-control-plane stop
```

- prepare、manifest/credential/schema/Supervisor validate 通过；
- readiness：Admin、eidolond、Data Companion、Data Workspace、Hub、Kernel 全部 ready；
- Workspace：未认证 401、首次写 200、同 payload 重复 200、变更 payload 409、GET
  200/ready；Admin 重启后 GET 仍 200/ready；
- 启动时 Web readiness 曾返回 200；后续 Codex 命令边界后后台 Vite PID 消失且日志
  没有退出原因，未将此观察包装成持久部署通过。Vite 独立测试/构建通过，后台进程
  生命周期留给单独部署主线复核；
- stop 后 supervisor/profile 均不运行，8082–8085、8090、9000、9001 无监听，隔离
  Data 目录无 WAL/SHM；
- 正式 `/Users/manson/eidolon/data/eidolon-system.sqlite3` mtime 前后均为
  `1786001432`。

## 性能诊断（不是 SLA）

环境：macOS 26.5.2 arm64、Python 3.13.13、实际 loopback Admin/Data/Hub/Kernel
进程、临时 producer SQLite；单次 E2E run。

| 低频 mutation / 聚合读取 | 结果 |
| --- | ---: |
| Workspace 首次 mutation | 23.77 ms |
| Device admission + Mount + Attachment 首次 mutation | 32.16 ms |
| 6 路重复 Device mutation | p50 39.62 ms / p95 40.53 ms |
| 20 路 Hub + Kernel inventory | wall 103.76 ms / p50 93.95 ms / p95 99.52 ms |

合法本地 SQLite / 审计投影诊断：

```bash
.venv/bin/python -m deploy.dev.local_state_diagnostics \
  --events 2000 --fetch-batch 200
```

| 项目 | 结果 |
| --- | ---: |
| Bootstrap SQLite busy timeout | 5000 ms |
| 实测竞争写失败 | 5381.41 ms |
| audit backlog / batch | 2000 / 最大 200，10 batches |
| publish / drain | 11.14 ms / 299.69 ms（6673.60 events/s） |
| read 200 projection events | p50 2.32 ms / p95 2.61 ms |

Admin operator 业务路径没有 SQLite writer。上述 audit 是独立临时 JetStream +
rebuildable projection 的有限 backlog 诊断，只证明 bounded batch 与当前机器吞吐，
不代表全局审计 producer 已完成，也不是 Raspberry Pi SLA。

## 已知风险和剩余跨项目工作

1. Data 除首次 Workspace 外仍缺 general Owner/Companion、Persona、Realm、Face、Guard
   管理 contract；Admin 对应旧页面/API 必须保持删除。
2. 独立全局 audit publisher/query contract 和 backlog/lag 运维接口仍需跨项目实现；
   Admin 不得写 Data outbox 或扫描 Kernel/Hub DB。
3. 高频 presence/telemetry 投影仍缺失，不能用 audit 或 System Data 替代。
4. Data Companion read 当前产品配置仍是单 opaque token；正式环境需 producer-owned
   per-consumer credential/rotation。Workspace write 和 Local API→Admin 已独立。
5. Admin operator ingress/auth、Web 持久服务方式、secret 注入、其他子项目适配与
   Raspberry Pi 真机 activation/rollback 验收属于单独部署主线。
6. 本次 runtime 使用的 sibling 工作树存在其他会话改动；pinned contract 测试防止
   基线漂移，但统一 release 仍必须在各仓库主线完成后重新跑跨项目 E2E。

## 启动判定

- 隔离 `os-control-plane` backend：可以安全启动，已启动、重启、冒烟和停止。
- 正式 Admin：当前不批准启动；阻塞为 product credential/operator ingress、其他
  子项目主线和统一 release 验收，而不是本次 Admin Data V2/Kernel 代码适配。
- Agent：不属于该 profile 或本次启动判定；本任务没有启动或修改 Agent。
