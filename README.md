# eidolon_admin

Eidolon OS 的控制面与编排面。后端是 FastAPI，前端是 Vue 3。

Admin 不拥有 Owner、Companion、Device、Mount、Memory、运行态 telemetry 或全局审计数据，也不打开 Data、Kernel、Hub 的 SQLite。业务访问方向固定为：

```text
Web / CLI
  -> Admin HTTP
  -> Admin application orchestration
  -> strict Data / Hub / Kernel clients
  -> eidolond System Service Directory
  -> bounded-context public HTTP contracts
```

完整边界、迁移矩阵和缺失生产者契约见 [Data V2 / Kernel control-plane ADR](docs/architecture/eidolon-os-control-plane-v2.md)。

## 当前业务能力

- `GET /api/control-plane/v1/capabilities`：报告已支持及因生产者契约缺失而不可用的能力。
- `GET /api/control-plane/v1/companions/{companion_id}`：通过 Data V2 的只读 Companion Authority 查询。
- `GET /api/control-plane/v1/owners/{owner_id}/inventory`：并发聚合 Hub Device Directory 与 Kernel Mount 的瞬时读模型；每个来源保留独立状态和延迟。
- `POST /api/control-plane/v1/workflows/device-admission`：按 `Hub approval -> Kernel Mount -> optional Companion Attachment` 编排。

设备接纳 workflow 要求调用方提供稳定 `request_id`。Admin 派生确定性的子 request ID，并把 CAS revision 传给 Kernel。它不是分布式事务：可重试的部分成功返回 HTTP 202、最后已提交阶段和 `retry-forward-same-request-id`；非重试冲突返回 `blocked/operator-action-required`。Admin 重启后由 Hub/Kernel 自有幂等记录恢复，不在本地复制权威状态。

旧 `/api/owners`、`/api/data/*`、`/api/devices`、`/api/events`、`/api/memory/*`、`/api/mission-control/*`、`/api/onboarding/*` 和 `/api/resolve/*` 已移除，不提供旧 schema、migration、CRUD 或 SQLite fallback。

Agent、Memory 和 Hub 的独立管理界面通过 `/api/services/{service_id}/*` 透明代理。Hub 声明为 `passthrough`，由操作端提供管理 JWT；其他服务的 Authorization 不会被默认透传。

## Host Bootstrap 与 Local API

无头 Host 初始化和面向 Mobile 的 Local API 位于本仓库，但它们是独立于
Admin operator API 的进程和 bounded context。开发模式必须显式启用：

```bash
EIDOLON_BOOTSTRAP_MODE=development .venv/bin/eidolon-bootstrapd
EIDOLON_BOOTSTRAP_MODE=development .venv/bin/eidolon-bootstrapctl health
EIDOLON_BOOTSTRAP_MODE=development .venv/bin/eidolon-bootstrapctl dev code --ttl 600
EIDOLON_BOOTSTRAP_MODE=development .venv/bin/eidolon-local-api
```

Local API 的 `GET /api/local/v1/host` 只返回 Host identity 与 host-control
状态；它不初始化 Owner/Data、不注册 Hub Device，也不启动 Audio。Bootstrap
自有 SQLite 只保存 commissioning/claim 状态，不读取或复制 Data、Kernel、Hub
的权威数据。

树莓派只读预检命令为：

```bash
.venv/bin/eidolon-bootstrap-preflight --pretty
```

生产模式由独立 systemd units 管理，并要求制造阶段配置的 Host Identity。
架构与状态机见
[Host bootstrap plan](docs/architecture/eidolon-os-local-bootstrap-plan.md)、
[ADR-0001](docs/architecture/adr-0001-host-control-process-boundaries.md) 和
[ADR-0002](docs/architecture/adr-0002-bootstrap-ports-and-state-store.md)。

## 配置

主要环境变量：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `EIDOLON_ADMIN_SYSTEM_DIRECTORY_URL` | `http://127.0.0.1:8090` | eidolond HTTP endpoint directory |
| `EIDOLON_ADMIN_SYSTEM_DIRECTORY_UDS` | 空 | 可选 eidolond Unix socket；配置时优先使用 |
| `EIDOLON_ADMIN_DATA_AUTHORITY_TOKEN` | 空 | Admin 自有 Data service credential；不会复用 Kernel 变量 |
| `EIDOLON_ADMIN_DIRECTORY_TIMEOUT_SECONDS` | `2` | 服务目录调用超时 |
| `EIDOLON_ADMIN_AUTHORITY_TIMEOUT_SECONDS` | `3` | Data/Hub/Kernel 调用超时 |
| `EIDOLON_ADMIN_SERVICES_FILE` | `config/services.yaml` | 通用服务代理和运维目录 |

Data、Hub、Kernel 的 URL 不在 Admin 静态配置中复制；必须由 eidolond 发布下列精确 endpoint/contract：

| service / endpoint | contract |
| --- | --- |
| `data / companion-authority.http` | `https://eidolon.dev/data/contracts/v1/companion/identity.schema.json` |
| `hub / device-authority.http` | `eidolon.hub.device-directory.v1` |
| `kernel / device-mount.http` | `eidolon.kernel.device-mount.v1` |

若目录未发布、服务不 ready、契约名不匹配或响应 schema 漂移，Admin 会返回显式 `unavailable` 或 `contract_violation`，不会伪造成 inactive/not found。

## 本地开发

```bash
uv sync --extra dev
pnpm --dir web install --frozen-lockfile

# 仅启动 Admin；不会自动启动 Data/Kernel/Agent
.venv/bin/uvicorn eidolon_admin_server.app.main:app --host 127.0.0.1 --port 9000
pnpm --dir web dev
```

现有 `deploy/dev/run_all.sh` 和 Supervisor 页面仍用于本地开发进程运维。启动整套环境前，必须先确认 eidolond 当前 manifest 已发布 Data/Hub/Kernel 三个上述 endpoint，并配置独立的 Admin Data credential。不要把正式 `eidolon-system.sqlite3` 用作测试库。

## 验证

```bash
# 后端 lint、格式、compile 和全量分层测试
.venv/bin/ruff check server
.venv/bin/ruff format --check <本次变更的 Python 文件>
.venv/bin/python -m compileall -q server/eidolon_admin_server
.venv/bin/pytest server/tests -q
.venv/bin/pytest server/tests --cov=eidolon_admin_server --cov-branch --cov-report=term-missing

# 前端单测、类型检查和生产构建
pnpm --dir web test
pnpm --dir web build
```

`server/tests/test_control_plane_process_e2e.py` 只使用临时数据库和随机 loopback 端口，真实启动 Admin、Data V2、Hub 和 Kernel，验证成功、并发重复、Admin 重启恢复、Data outage 的部分成功语义以及旧路由不可用。它不会读取或修改正式数据库。

性能脚本及当前环境结果见 `docs/testing/reports/`。所有数据仅是诊断值，不是产品 SLA。
