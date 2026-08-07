# Eidolon Admin — Data V2 / Kernel 新边界适配报告

日期：2026-08-06（Asia/Shanghai）

## 结论

Admin 代码已从 Data/Kernel/Hub 的数据库聚合层改为无业务数据库的控制面：业务路由只经 eidolond 解析出的公开 HTTP contract 调用 Data、Hub、Kernel；旧 Data ORM、schema repair、Device/Event/runtime 表、旧 Hub `/api/admin` 设备接口、原生 Memory 聚合和 Mission Control 共享审计假设均已删除。

代码及隔离测试已完成，但当前正式开发运行配置**尚不具备安全恢复完整 Admin 业务功能的条件**：`eidolon_kernel/config/system-services.yaml` 只发布 Hub，没有发布 Data `companion-authority.http` 和 Kernel `device-mount.http`；同时未发现已配置的 `EIDOLON_ADMIN_DATA_AUTHORITY_TOKEN`。因此本次没有启动 Admin/Agent，最终进程检查也没有发现 Admin、Agent、eidolond、Data、Hub 或 Kernel 服务进程。

## 核验证据范围

- Admin 适配起点：`c553872`（tag `v0.1.0`）。
- Data 工作树：clean，HEAD `2a338940681fb281e1d962f891f2d675a0bb97b5`，即指定 V2 基线。
- Kernel 工作树：clean，HEAD `606d0441439d3cec32bec18a0f54dc9c50b55359`；`66e61c9` 是其祖先，当前 HEAD 比适配基线多 4 个提交。契约测试从 `66e61c9` 的 Git object 读取 schema/router，不把后续 M2-C 代码误当作基线；真实进程 E2E 使用当前实际 Kernel。
- Hub 工作树：clean，HEAD `a91ea8356f79da75b359faf9d90cace6d4a07ffb`。
- Data/Kernel/Hub sibling 仓库均未修改。
- 正式 Data DB 仅以 `mode=ro` 检查：`/Users/manson/eidolon/data/eidolon-system.sqlite3` mtime 仍为 `2026-08-06T15:30:32+0800`，`integrity_check=ok`、Alembic `0001_system_data_v2`，检查前后均无 WAL/SHM/临时切库文件。

## 适配前真实问题

以下均来自 `git show/grep c553872`，不是依据旧描述推断：

1. `server/eidolon_admin_server/app/main.py:23,93-95` import `DataStore`，启动时 `open + init_schema + ensure_eidolon_data_schema`，使 Admin 成为 Data 的第二 schema owner/writer。
2. `app/data/schema_guard.py:3,106-145` 明确把 shared Data SQLite 当作 Admin 可修复 schema；`app/ports.py:20,240` import Data settings 并导出 `EIDOLON_DATA_SQLITE_PATH`。
3. `app/data/router.py:380-1405` 使用已被 V2 删除的 `store.devices`、`store.events`，并实现旧 Device approve/revoke/release/bind CRUD 和同步事件写入。
4. `app/data/hub_client.py:47-87` 调用当前 Hub 已不存在的 `/api/admin/devices*`、commands、wiggle API。
5. `app/memory/runners.py:17,176-205` 直接 `sqlite3.connect(...mode=ro)` 读取 Data `memory_realms`，并扫描 Memory SQLite/Chroma artifacts；`app/memory/nats_publisher.py` 又把 Admin 放入高频 Memory/NATS 写链路。
6. `app/mission_control/ARCHITECTURE.md:9` 宣称 `eidolon_data.events` 是共享 evidence ledger；`service.py:96,113,313` 直接聚合 Data Device/Event 与 NATS KV。Data V2 没有这些表，Data outbox 也不是全局审计。
7. `app/onboarding`, `guard`, `resolve`, owner backup/delete finalizer 均 import Data ORM/service，并跨 Data、Memory、Hub/runtime 做同步编排或清理。
8. Web router/navigation 暴露 Owners、Companions、Devices、Guard、Memory、Mission Control 等页面，这些页面依赖上述失效 API。
9. `pyproject.toml` 直接依赖 `eidolon-data`、`eidolon-sdk`、MCP、NATS、SQL/ML/LiveKit 依赖链。当前 sibling SDK 已移除 `memory_space_storage_name`，基线测试在 collection 阶段即发生 ImportError，未产生可声明的 baseline passed 数。

## 生产者公开契约核对

| Producer | Admin 实际调用 | 证据 |
| --- | --- | --- |
| eidolond | `GET /api/system/v1/services/{service_id}/endpoints/{endpoint_id}` | 当前 Kernel system directory router、manifest contract |
| Data `2a33894` | `GET /api/companion-authority/v1/companions/{companion_id}` | `eidolon_data/api/companion_authority.py` + producer JSON Schema |
| Hub `a91ea83` | owner Device list、Device approval | `hub/interfaces/http/routers/device_management.py` + current schemas |
| Kernel `66e61c9` | Mount、Mount list、Attachment | `eidolon_kernel/interfaces/http/router.py` + pinned schemas |

Admin strict consumed models禁止 extra fields，并额外校验 response 的 companion/device/owner scope、active Mount 和 attached Companion 身份，防止结构正确但跨租户/错对象的响应被接受。

Data V2 当前没有公开 Owner/Companion mutation、Persona、Memory Realm catalog、face、Guard、Data audit query/event API。因此这些能力被删除并由 `/api/control-plane/v1/capabilities` 明确列为 producer contract 缺失，不通过 ORM/SQLite fallback 伪造。

## 最终依赖方向与逻辑归属

```text
Vue / CLI
  -> Admin HTTP transport
  -> Admin application orchestration + Admin-owned workflow/read DTO
  -> Data / Hub / Kernel / System Directory ports
  -> strict httpx adapters
  -> eidolond endpoint directory
  -> bounded-context public contracts

Hub    = Device admission / owner scope
Kernel = Device Mount / optional Companion Attachment / CAS / idempotency
Data   = Owner/Companion/Persona/Realm/Face/Guard authoritative facts
Admin  = operator control-plane entry + cross-service state-machine orchestration
```

Admin 不包含外部 ORM model/repository/migration，不打开任何 sibling DB，不做跨库事务或三方同步双写。Agent、Memory、Hub 各自管理 API 只由通用 gateway proxy 转发；Hub 明确声明 `passthrough`，操作端 JWT 不会被转发到其他服务。

## Workflow 状态与失败语义

状态：`received -> hub_approved -> kernel_mounted -> companion_attached(optional)`。

- caller workflow ID 最大 64 字符；子调用 ID 固定为 `admin:{id}:hub-approve|kernel-mount|kernel-attach`，在 producer 96 字符限制内。
- Kernel mutation 显式传 CAS revision；Hub/Kernel producer 自己做 request-ID fingerprint/idempotency。
- 可重试部分失败：HTTP 202，`outcome=retry_required`，`recovery=retry-forward-same-request-id`，保留安全中间态，不自动 revoke/unmount。
- 非重试 CAS/request-ID/validation 冲突：HTTP 4xx/5xx，`outcome=blocked`，`recovery=operator-action-required`；不会错误建议无限重试。
- Admin 重启或 response lost：相同 workflow ID 重放 producer 幂等记录。
- 401/403、404、timeout/connect、5xx、schema/identity drift 分别保留为 `unauthorized/forbidden/not_found/unavailable/upstream_failure/contract_violation`。authority unavailable 不会映射为 inactive/not found。
- 没有破坏性自动补偿，故不存在伪造的 rollback。前向恢复再次失败时仍返回最后已提交阶段和新 failure。真实 E2E 还验证了同 request ID 改 Companion 会 409 blocked，Kernel 已提交 Attachment 保持 revision 2，证明 producer transaction 没有被冲突请求破坏。

## 文件变化

新增：

- `server/eidolon_admin_server/app/control_plane/{contracts,errors,directory,clients,service,router}.py`
- `web/src/api/controlPlane.ts`
- `web/src/modules/control-plane/ControlPlane.vue`
- 五层 control-plane 测试、真实进程 E2E support
- `docs/architecture/eidolon-os-control-plane-v2.md`
- 本报告

重构：

- `app/main.py` 改为无 Data/NATS/MCP 业务状态的 composition root。
- `settings.py` 新增 directory/authority timeout、可选 UDS 和独立 Admin Data credential；修复 Git worktree 下 sibling root 解析。
- gateway 增加显式 Hub auth passthrough，默认仍剥离 cookie/Authorization；动态 proxy 不再生成冲突 OpenAPI operation ID。
- `config/services.yaml` 将 Hub 对齐当前 `/api/device-management/v1`，Memory/Agent 回到各自公开 Admin API console。
- Web router/navigation 只保留 control plane、bounded-context console 和运维工具。
- `pyproject.toml`/`uv.lock` 移除 Data/SDK/SQLAlchemy/Alembic/aiosqlite/NATS/MCP/LiveKit/ML 等旧直连依赖；锁文件由约百个重型包缩减为 43 个解析包。

删除：

- 后端 `data/`, `devices/`, `guard/`, `memory/`, `mission_control/`, `nats_kv/`, `onboarding/`, `resolve/` 旧业务聚合模块及失效测试。
- Web Owners/Companions/Devices/Guard/native Memory/Mission Control/legacy Hub 页面、API、store、protocol 和失效测试。

回归顺带修复两项由全量测试证明的既有缺陷：config backup 同秒 snapshot 会覆盖 restore source；Git worktree 下 `default_eidolon_root()` 会错误指向 `.codex/worktrees/<id>`。此外更新了已增加第五块 board 后失效的 ESP32 测试和移动工具测试的有界进程调度等待。

## 测试结果与可复现命令

所有测试使用当前代码；真实 E2E 只使用 pytest 临时目录和随机 loopback port。

| 层级 | 命令 | 结果 |
| --- | --- | --- |
| Unit | `.venv/bin/pytest server/tests -m unit -q` | 10 passed，160 deselected |
| Component/functional | `.venv/bin/pytest server/tests -m component -q` | 30 passed，140 deselected |
| Contract | `.venv/bin/pytest server/tests -m contract -q` | 8 passed，162 deselected；从 Data `2a33894`、Kernel `66e61c9` Git object 和 current Hub schema 验证 |
| Integration | `.venv/bin/pytest server/tests -m integration -q` | 3 passed，167 deselected |
| Real process E2E | `.venv/bin/pytest server/tests/test_control_plane_process_e2e.py -q -s` | 1 passed；实际 Admin + Data + Hub + Kernel，临时 DB/端口 |
| Backend full | `.venv/bin/pytest server/tests -q` | 170 passed，0 failed/skipped，1 warning |
| Frontend | `cd web && pnpm test` | 25 passed（6 files） |
| Frontend type/build | `cd web && pnpm build` | `vue-tsc --noEmit` + Vite build passed |

覆盖率命令：

```bash
.venv/bin/pytest server/tests -q \
  --cov=eidolon_admin_server --cov-branch --cov-report=term-missing
```

最终完整覆盖运行：retained Admin 全代码 72%；新增 `control_plane` statements 403/424、branches 46/58，combined 93.15%。整体值被保留的 Supervisor、firmware/mobile tool、config reload 等旧运维代码未覆盖分支拉低，未通过排除文件美化数字。

质量检查：

```bash
.venv/bin/ruff check server
.venv/bin/ruff format --check <本次所有变更 Python 文件>
.venv/bin/python -m compileall -q server/eidolon_admin_server
UV_CACHE_DIR=/tmp/eidolon-admin-uv-cache uv lock --check --offline
bash -n deploy/dev/run_all.sh
cd web && pnpm test && pnpm build
```

- Ruff lint passed；变更 Python 文件 format check passed；compileall passed；lock check passed。
- Backend mypy/pyright：项目未配置，未执行，不能声称通过。
- Admin migration/schema upgrade：Admin 已无业务 DB/migration，故不适用；隔离 E2E 实际执行 Data Alembic upgrade 并严格验证十张 V2 表、integrity/FK。
- Pytest 警告：1 个 StarletteDeprecationWarning（FastAPI TestClient 通过 httpx 的上游弃用提示）。
- Frontend build 警告：第三方 VueUse `PURE` comment 被 Rollup 移除；主 chunk 1.24 MB（gzip 402.98 kB）超过 500 kB 建议值。均未导致失败。

## 性能诊断（不是 SLA）

环境：macOS / Python 3.13.13，随机 127.0.0.1 ports，实际 Admin/Data/Hub/Kernel processes，Data/Hub/Kernel 均为 pytest 临时 SQLite；单次诊断 run。

命令：

```bash
.venv/bin/pytest server/tests/test_control_plane_process_e2e.py -q -s
```

最后一次诊断：

| 项目 | 结果 |
| --- | ---: |
| 首次 Hub approve + Kernel mount + Data-backed attachment | 46.32 ms |
| 6 路并发重复 mutation | p50 36.46 ms / p95 39.99 ms |
| 20 路并发 Hub+Kernel inventory | wall 120.04 ms / p50 105.16 ms / p95 117.70 ms |

- Admin 自身 SQLite 写锁/busy timeout：不适用，Admin 已无业务 SQLite writer；producer SQLite 各自隔离，不能用 Admin 数字冒充其 SLA。
- 全局审计 publisher queue/backpressure：未配置、未测试，因为 inspected producers 没有独立全局 audit publisher/projection contract。凭空建立同步 Data outbox 写入会违反本次边界；这是明确的跨项目剩余项。

## 已知风险与剩余跨项目工作

1. 当前 dev eidolond manifest 只含 Hub；需由 Kernel/eidolond 所有者加入真实 Data/Kernel host target、endpoint、health/readiness，再做正式配置 dry-run。
2. 需安全下发 Admin Data credential。Data 当前只有单 opaque token 配置；若不允许 Admin/Kernel 共享 token，Data 应最小扩展为多 service credential/audience，而不是让 Admin读 DB。
3. Data Owner/Companion lifecycle mutation、Persona、Realm catalog、Face/Guard 管理契约仍缺失；对应 Admin UI/API 保持删除。
4. 独立、可重建的全局 audit projection/query/publisher 及 queue lag/backpressure 指标仍缺失；Mission Control 不恢复。
5. 高频 presence/telemetry projection 仍缺失；不得以 Data outbox 或 Kernel audit 替代。
6. 当前 Admin 面向 trusted-local loopback，没有独立 operator identity middleware；Hub mutations 由 Hub JWT 保护，Data read 使用 Admin service credential。若 Admin 将来对非本机开放，必须先增加统一 operator authentication/authorization。
7. Agent 自身对 Data V2/Kernel 的任何未完成适配不属于本 commit；本次只把 Admin 对 Agent 降为其公开 `/api/admin` proxy。

## 启动判定

代码级：可以在隔离环境安全启动，真实进程 E2E 已证明。

当前正式开发环境：**不可声明可安全恢复完整 Admin 功能**。阻塞条件是 eidolond dev manifest 缺 Data/Kernel endpoint 以及 Admin Data credential 未配置。完成这两项并在隔离配置上复跑 health/directory/contract smoke 后，才应获得明确授权启动 Admin；Agent 仍应独立评估。当前 Admin/Agent 保持停止。
