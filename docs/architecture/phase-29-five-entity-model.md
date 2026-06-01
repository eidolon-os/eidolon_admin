# Phase 29 — 五实体资源模型 + 显式 Provisioning 架构

> **状态**: 设计稿,等待 review
> **作者**: 来自 2026-06 跟操作员的架构对谈
> **取代**: 当前隐式 user_id ("alice" placeholder)、device-下嵌套 agent、agent silent fallback 等模糊设计

---

## 1. 动机

当前栈跑通了,但有几个**结构性裂缝**:

1. **身份模型错位**: agent 用 `tenant + user_id`,memory 用 `user_id`,两边对身份的世界观不一样
2. **陌生 user_id 静默降级**: web client 进来时 user 不存在 → agent 默默 "no memory" → 用户看到一个"健忘的助手"且没有任何告警
3. **创建路径只有一条**: agent 只能在 device 下创建 (`POST /api/devices/{id}/agents`),没有独立 user/agent 的概念
4. **Template 只读**: 内置模板写死在 agent 项目源码里,operator 没有编辑/扩展能力
5. **边界模糊**: admin 直接写 memory 的 `users.yaml`,直接动 agent 的 SQLite,实质把"子项目的事"做在 admin 里

本设计目标: **建立 5 个一级资源 (Tenant / Template / User / Agent / Device),每个独立创建/管理,通过显式 binding 关联;admin 只做编排,业务实现回归子项目**。

---

## 2. 实体模型

```
┌──────────────────────────────────────────────────────────────────┐
│  Tenant                                                            │
│  ──────                                                            │
│  tenant_id (PK), display_name, created_at                          │
│  Lifecycle: operator 创建,默认安装时种下 "default" tenant            │
│  存储: admin 独有 (无子项目对应概念)                                  │
└──────────────────────────────────────────┬───────────────────────┘
                                           │ scope (1:N)
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  PersonaTemplate                                                   │
│  ───────────────                                                   │
│  template_id (PK), tenant_id, source (builtin|custom), revision,  │
│  identity_core, knobs, style, memory_adapter, evolution, assets   │
│  Lifecycle: 内置随 agent 项目 ship; custom 由 operator 创建/fork    │
│  存储: 内置在 agent 项目 (源码内 yaml); custom 在 agent 项目 (数据库)  │
└──────────────────────────────────────────┬───────────────────────┘
                                           │ render-time
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  User                                                              │
│  ────                                                              │
│  user_id (PK within tenant), tenant_id, display_name,             │
│  memory_palace_path, consolidator_cfg, created_at                 │
│  Lifecycle: operator 创建 → memory 同步起 user-worker              │
│  存储: memory 项目 (palace + users.yaml); admin 不直接写            │
└──────────────────────────────────────────┬───────────────────────┘
                                           │ owns (1:N)
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Agent                                                             │
│  ─────                                                             │
│  agent_id (PK), user_id, template_id + template_revision,         │
│  soul_md (rendered), knob_overlays, evolution_state,              │
│  created_at, is_active_for_user                                   │
│  Lifecycle: operator pick user + pick template → agent 项目渲染     │
│  存储: agent 项目 (persona instance + soul); admin 持 binding 映射   │
└──────────────────────────────────────────┬───────────────────────┘
                                           │ bind (N:1, 可空)
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Device                                                            │
│  ──────                                                            │
│  device_id (PK), kind (web|esp32|...), approved,                  │
│  bound_agent_id (FK, nullable), last_seen                         │
│  Lifecycle: discovered (hub) → approved (hub) → bound (admin)     │
│  存储: hub (device 事实) + admin (binding 编辑)                      │
└──────────────────────────────────────────────────────────────────┘
```

### 关键约束

- **创建 User 必须先有 Tenant** (默认 tenant = `"default"`)
- **创建 Agent 必须先有 User + Template**
- **绑定 Device 必须先有 Agent**
- **删除 Tenant** → 级联其所有 User、Agent、Device bindings
- **删除 User** → 级联其所有 Agent、解绑相关 Device、销毁 memory palace
- **删除 Template** → 如有 Agent 引用则拒绝(先迁移)
- **任意入口**收到陌生 user_id / agent_id → **立刻 403**,无 silent fallback

---

## 3. 边界铁律 — 谁拥有什么

> **admin = 编排 + UI;子项目 = 业务实现。admin 永远调子项目接口,不绕过去直接动其底层存储。**

| 实体 | 业务实现属于 | admin 的职责 |
|---|---|---|
| **Tenant** | admin (子项目无此概念) | 完整 CRUD |
| **PersonaTemplate** | **agent 项目** | 调 agent 的 template REST 接口;UI 展示 |
| **User** | **memory 项目** | 调 memory 的 user 管理接口;UI 展示;维护 tenant↔user 映射 |
| **Agent** | **agent 项目** (persona instance) + **memory 项目** (palace) | 调 agent 创建 persona;调 memory 确认 palace 已就绪;UI 展示 |
| **Device** | **hub 项目** (发现/批准) | 调 hub 接口;admin 内部存 device↔agent binding;UI 展示 |

### 即将要做的子项目接口扩充

为了让 admin 不再"越界",每个子项目要补齐自己资源的管理接口。**本轮 Phase 29 必须先做这一步**,后面 admin 端的工作才有 API 可调。

#### eidolon_agent 要新增

```
GET    /api/admin/templates                 list (builtin + custom)
GET    /api/admin/templates/{id}            detail
POST   /api/admin/templates                 create custom
PUT    /api/admin/templates/{id}            update custom
DELETE /api/admin/templates/{id}            delete custom (refcount 检查)
POST   /api/admin/templates/{id}/fork       clone builtin → custom
POST   /api/admin/templates/{id}/render     render → markdown (✅ 已有 Phase 25.2)

GET    /api/admin/personas                  list persona instances
GET    /api/admin/personas/{id}             detail
POST   /api/admin/personas                  create instance (pick template + user_id)
PUT    /api/admin/personas/{id}/knobs       override knobs
PUT    /api/admin/personas/{id}/soul        override soul markdown
DELETE /api/admin/personas/{id}
GET    /api/admin/personas/{id}/evolution   history
```

#### eidolon_memory 要新增

```
GET    /api/admin/users                     list (✅ discovery 已有,要整理)
GET    /api/admin/users/{id}                detail (palace stats, consolidator state)
POST   /api/admin/users                     create user (writes users.yaml + SIGHUP + waits for worker)
PUT    /api/admin/users/{id}                update meta (display_name, consolidator cfg)
DELETE /api/admin/users/{id}                terminate worker + delete palace
```

memory 内部依然用 `users.yaml` 作为运行时配置,但**对外 (admin) 只暴露 REST API**。admin 不再直接读写那个 yaml。

#### eidolon_hub 要新增

```
GET    /api/admin/devices                   list (✅ 已有)
POST   /api/admin/devices/{id}/approve      (✅ 已有 Phase 25.1)
POST   /api/admin/devices/{id}/pairing-code generate one-time code
DELETE /api/admin/devices/{id}              unregister
```

device ↔ agent **binding 不放在 hub**,放在 admin (因为 agent 概念在 hub 外)。

#### admin 自己负责

```
Tenant CRUD                                 admin-only
Device↔Agent binding storage                admin's NATS KV
跨实体一致性 (创建 agent 时确认 user 存在)    admin 编排
UI 全部                                       admin
```

---

## 4. 存储布局

### admin 自管 (NATS KV)

| Bucket | Key | Value | 用途 |
|---|---|---|---|
| `tenants` | `tenant.<id>` | `{display_name, created_at}` | admin 唯一来源 |
| `device_bindings` | `device.<device_id>` | `{agent_id, bound_at}` | binding 关系 |

### agent 项目内 (SQLite 或现有数据库)

- 内置 templates: yaml 文件,跟代码 ship
- custom templates: agent 自己存(SQLite 或 NATS KV,**由 agent 决定**)
- persona instances: agent 现有的 `persona_instances` 表

### memory 项目内

- `users.yaml`: memory 内部配置(admin 通过 REST 间接编辑)
- per-user palace 目录: `~/eidolon/memory/mempalaces/<user_id>/`

### hub 项目内

- `devices.json` (现有): device 物理事实

---

## 5. 数据流

### 5.1 安装后 seed (admin 启动钩子)

```
admin 启动 lifespan
  ↓ 检查 admin KV 是否有任何 tenant
  ↓ 若无:
      ① 创建 tenant "default"  (admin 内部)
      ② POST memory:/admin/users {user_id: "default"}
         → memory 写 users.yaml + SIGHUP + 启 user-worker:8030
      ③ POST agent:/admin/personas {template_id: "caretaker_jiezhi", user_id: "default"}
         → agent 渲染 soul + 创建 persona_instance
      ④ admin 记录 mapping: tenant=default, user=default, active_agent_id=<新建>
```

### 5.2 ESP32 接入

```
ESP32 上电 → mDNS broadcast
  ↓
hub: device 表插入 (status=discovered)
  ↓
admin UI 操作员:
  POST admin → hub:/admin/devices/<id>/approve   (admin 仅代理)
  POST admin → admin's binding 存:                (admin 自管)
       device_bindings/<device_id> = {agent_id: <existing>}
  ↓
ESP32 后续会话:
  channel 拿到 device_id
    ↓ admin: GET /api/devices/<id>/binding  → 查 admin KV 得 agent_id
    ↓ admin: GET /api/agents/<agent_id>      → 查 agent 项目得 user_id, soul
    ↓ admin: GET /api/users/<user_id>        → 查 memory 项目得 mcp_url
  channel 启动 session,把 agent + memory 配齐
```

> **注**: channel 现在还没有这套"统一解析"的入口。需要在 admin 加一个 `GET /api/resolve/device/{device_id}` 之类的聚合端点,把上面三步合并成一次调用。

### 5.3 Web client 调试入口

```
Web client → POST /api/turn {user_id, text}
  ↓ admin: GET /api/users/{user_id}        → 校验存在,否则 403
  ↓ admin: GET /api/agents?user_id=...     → 拿 active agent
  ↓ admin: forward to agent 项目的对话入口  (复用现有 agent 接口)
agent:
  ↓ MCP recall_context(user_id)
  ↓ 编 prompt → LLM
  ↓ 流式返回
```

### 5.4 错误路径(全部 loud)

| 场景 | 响应 |
|---|---|
| `user_id` 不存在 | HTTP 404 `user not provisioned` |
| `agent_id` 不存在 | HTTP 404 |
| `agent.user_id` ≠ 请求里的 user_id | HTTP 403 |
| `device` 未绑 agent | HTTP 412 `device not configured` |
| Template 已删但 agent 还引用它 | 创建时 404; 已有 agent 继续工作直到迁移 |
| memory user-worker 暂时不可达 | HTTP 503 + agent prompt 注入 `⚠ memory backend degraded` |
| 渲染失败 | 创建 agent → 503 + admin 触发反向 cleanup |

---

## 6. Admin REST API 全集

> 命名约定: `/api/<resource>` 复数,POST 创建,PUT 更新,DELETE 删除。

```
┌──── Tenants (admin-only) ─────────────────────────────┐
│ GET    /api/tenants                                    │
│ GET    /api/tenants/{id}                               │
│ POST   /api/tenants                                    │
│ PUT    /api/tenants/{id}                               │
│ DELETE /api/tenants/{id}                  (cascade chk) │
└────────────────────────────────────────────────────────┘

┌──── Templates (admin → agent project) ────────────────┐
│ GET    /api/templates                                  │
│ GET    /api/templates/{id}                             │
│ POST   /api/templates                                  │
│ PUT    /api/templates/{id}                             │
│ DELETE /api/templates/{id}                             │
│ POST   /api/templates/{id}/fork                        │
└────────────────────────────────────────────────────────┘

┌──── Users (admin → memory project) ───────────────────┐
│ GET    /api/users                                      │
│ GET    /api/users/{id}                                 │
│ POST   /api/users                                      │
│ PUT    /api/users/{id}                                 │
│ DELETE /api/users/{id}                                 │
│ POST   /api/users/{id}/set-active-agent                │
└────────────────────────────────────────────────────────┘

┌──── Agents (admin → agent project + memory check) ────┐
│ GET    /api/agents?user_id=...                         │
│ GET    /api/agents/{id}                                │
│ POST   /api/agents                                     │
│ DELETE /api/agents/{id}                                │
│ GET    /api/agents/{id}/soul                           │
│ PUT    /api/agents/{id}/soul                           │
│ POST   /api/agents/{id}/soul/regenerate                │
│ PUT    /api/agents/{id}/knobs                          │
│ GET    /api/agents/{id}/evolution                      │
└────────────────────────────────────────────────────────┘

┌──── Devices (admin → hub + admin own binding) ────────┐
│ GET    /api/devices                                    │
│ POST   /api/devices/{id}/approve         (→ hub)        │
│ POST   /api/devices/{id}/bind            (admin own)    │
│ POST   /api/devices/{id}/unbind          (admin own)    │
│ POST   /api/devices/{id}/pairing-code    (→ hub)        │
│ DELETE /api/devices/{id}                 (→ hub)        │
└────────────────────────────────────────────────────────┘

┌──── Resolve (聚合查询,供 channel/livekit 调) ──────────┐
│ GET    /api/resolve/device/{device_id}                 │
│   → {device, binding, agent, user, memory_mcp_url, soul}│
│ GET    /api/resolve/user/{user_id}                     │
│   → {user, active_agent, memory_mcp_url, soul}         │
└────────────────────────────────────────────────────────┘

┌──── Ops / Observe (本来就有) ─────────────────────────┐
│ /api/system/health, /api/supervisor/..., /api/configs  │
└────────────────────────────────────────────────────────┘
```

---

## 7. Admin UI 结构

### 7.1 顶层导航

```
📚 Catalog
   Tenants  ·  Templates  ·  Users  ·  Agents  ·  Devices

🔍 Live
   Conversations  ·  Memory  ·  NATS Browser

⚙️ Ops
   Supervisor  ·  System Health  ·  Configs
```

### 7.2 每个 Catalog 页标配

- 列表(状态徽章 + 搜索 + 数量)
- 详情 drawer(Tab 组织:Overview / 子资源 / 关联)
- 创建向导(分步,有上下文提示)
- 删除(带级联检查)
- **空状态引导**(明显的"创建第一个"按钮 + 推荐路径)

### 7.3 创建向导

| 资源 | 步骤 |
|---|---|
| Tenant | 单页表单 (id + display_name) |
| Template | (内置 → fork) 或 (custom 新建): YAML 编辑器 + 服务端 schema 校验 |
| User | 2 步: 基本信息 → memory 配置(palace 路径/consolidator) |
| Agent | 3 步: 选 user → 选 template (卡片网格) → 备注名 + 确认 |
| Device-bind | 单页: 选已有 agent (radio list) 或 "新建 agent 跳到 agent 向导" |

### 7.4 First-run onboarding

```
┌──────────────────────────────────────────────────┐
│ 👋 欢迎使用 Eidolon Admin                          │
│                                                   │
│ 系统已自动 seed:                                   │
│   tenant=default, user=default,                  │
│   agent=default's caretaker_jiezhi               │
│                                                   │
│ 下一步:                                            │
│  [试一试 Live]  [创建自己的 user]  [跳过]          │
└──────────────────────────────────────────────────┘
```

---

## 8. Phase 实施计划

> **核心序列**: 先扩子项目接口 (29.B) → admin 再编排 (29.C-G)。**admin 永远不绕过子项目动其底层。**

| Phase | 项目 | 内容 | 验收 |
|---|---|---|---|
| **29.A** | admin (设计) | 本文档定稿 + schema 文件 + KV bucket 命名约定 | doc 通过 review |
| **29.B** | **agent / memory / hub** | 在三个子项目里补齐"业务实现"接口(见 §3) | 三方各自单测 + admin 能 curl 通 |
| **29.C** | admin | Tenants 模块 (CRUD,admin-only,最简单) | 能 CRUD,空状态引导 OK |
| **29.D** | admin | Templates 模块 (admin 调 agent 接口) | UI 看到 builtin,能 fork,能编辑 custom |
| **29.E** | admin | Users 模块 (admin 调 memory 接口) | 创建 → user-worker 起来;删除 → 干净清理 |
| **29.F** | admin | Agents 模块 (admin 调 agent 接口) | 选 user+template 创建 agent;能改 knobs/soul |
| **29.G** | admin | Devices 改造 (bind 选已有 agent;聚合 resolve) | ESP32 sim 走通 discover→approve→bind→speak |
| **29.H** | 全栈 | 严格 user_id 校验 + 移除所有 silent fallback | 陌生 user_id 一律 403;memory 挂了 LLM 老实承认 |
| **29.I** | admin (web) | UI 重做 (Catalog/Live/Ops 三层导航) | 新导航跑通,创建向导覆盖所有路径 |
| **29.J** | admin | Bootstrap seed + first-run onboarding | `rm -rf var/ && start` → 自动 seed → 直接能聊 |
| **29.K** | 全栈 | 清理 (tenant_id 字面值、alice placeholder) + e2e | grep 干净;e2e 覆盖完整链路 |

> **29.K 已落地决策记录**
>
> - **memory_mcp_url 不再 synth**: 之前 admin 内的 `_build_memory_mcp_url` 用 `port = 8030 + 偏移` 拼 URL,只对默认 user 正确。29.K 让 memory 项目在 user view envelope 上直接暴露 `mcp_http_url`(memory 是端口分配权威方),admin 透传。memory 没返回时 admin 留空字符串,channel 拒绝拨号 —— 拒绝 > silent-stale URL。
> - **UserView.agent_ids 不再永远空**: 早期 schema 里 `agent_ids: list[str] = []` 注释 "29.F 之后再填",但一直没填,UI 切换 active agent 的下拉永远空。29.K 用 setter 注入 (`UserOrchestrator.set_agent_ids_provider`) + main.py lifespan 在 AgentOrchestrator 起来后 wire 进去。partial-degradation: provider 抛异常 → agent_ids=[],list 仍 render。

### 依赖关系

```
29.A (设计)
  └─ 29.B (子项目接口) ← 必须先做,后面所有 admin 都依赖
      ├─ 29.C (Tenants, 独立, 没依赖)
      ├─ 29.D (Templates, 依赖 29.B 的 agent 接口)
      ├─ 29.E (Users, 依赖 29.B 的 memory 接口,要 29.C 的 tenant)
      ├─ 29.F (Agents, 依赖 29.D + 29.E)
      └─ 29.G (Devices, 依赖 29.F + 29.B 的 hub 接口)
        └─ 29.H (横切,所有 module 完成后)
            └─ 29.I (UI 重做)
              └─ 29.J (seed/onboarding)
                └─ 29.K (清理 + e2e)
```

---

## 9. 验收 (Definition of Done)

整个 Phase 29 完成 = 以下都成立:

1. `rm -rf ~/eidolon/data/nats-jetstream && ./deploy/dev/run_all.sh start`
   → 自动 seed → 浏览器开 admin → first-run 引导 → 一键试 chat → memory recall 工作
2. 任何 admin 端代码 grep 不到对 memory `users.yaml`、agent SQLite、hub `devices.json` 的**直接读写**(只能通过 REST 接口)
3. grep 全仓库找不到字面 `alice` placeholder
4. grep 全仓库找不到 hardcoded `tenant_id = "default"` (除了 admin 内部的 default 常量)
5. agent stderr 找不到 `memory recall failed; continuing without`,改成显式的 prompt 注入
6. 一个 e2e test 模拟 ESP32 全链路(discover → approve → bind → speak → memory recall),通过
7. 一个 e2e test 模拟 web client 全链路(选 user → speak → memory recall),通过
8. admin 每个 Catalog 页空状态都有引导,创建向导都能走完

---

## 10. 待操作员 confirm 的细节

在动手 29.A 之前需要拍板:

1. **Tenant 的可见度**: 在 UI 里全程展示,还是单租户用户默认隐藏("default" 时不显示选择器)?
   - 推荐: **默认隐藏**,只有 multi-tenant 模式才显示
2. **Template 编辑器**: 先 YAML 编辑器兜底,还是直接做结构化表单?
   - 推荐: **先 YAML**(像 configs 模块),后续再上结构化
3. **一个 User 是否可以有 N 个 Agent**?
   - 推荐: **N:1**(共享 memory,不同 persona)
4. **ESP32/web 未配置时**: HTTP 412 hard reject,还是 fallback 到 setup_assistant 临时 agent?
   - 推荐: **412 hard reject**,前端给到 admin 配置链接
5. **memory 子项目改造范围**: 只加 REST 接口,内部 `users.yaml` 机制保留?
   - 推荐: **是**,内部机制不动,只在 memory 加一个对外的 REST 层

---

## 附 A. 不在本轮的(明确划出)

- multi-tenant 在 UI 暴露 (default 单租户够用)
- Template 公网下载 / 市场
- Agent A/B 切流
- Memory palace 跨 user 迁移
- 移动端 admin

## 附 B. 跨项目同步要点

| 子项目 | 本轮要做 | 后续可能 |
|---|---|---|
| eidolon_agent | template CRUD endpoints, persona CRUD endpoints | 暴露 evolution timeline endpoint |
| eidolon_memory | user CRUD endpoints (yaml 自管 + SIGHUP 自管) | 暴露 palace migration / merge endpoint |
| eidolon_hub | pairing-code endpoint, unregister endpoint | device kind 类型注册 |
| eidolon_admin | 整个 5 实体模块 + UI | onboarding / docs in-app |
