# Phase 33 — Reflection Cleanup

> 上一轮(Phase 29 后半 + 30 + 31 + 32)做了 16 个 commit、横跨 5 个仓库。
> 主体目标达成(memory 真按用户隔离,有 e2e 回归),但反思后留了一批
> "知道不够好但当时没做"的事 + 几个真逻辑漏洞。本 plan 把它们集中收
> 拾掉,让架构干净落地、CI 钉死契约。

---

## 两大主题

### A. 架构/代码不够优雅 — 需要重构或清理
### B. 逻辑漏洞 — 必须填补

> **关于跨项目共享代码(token signer 等)**:操作员决定与未来一次性
> 抽取多个共享 SDK 一起统一做,本轮不动 SDK 包。A1 改成"契约测试
> 防 schema drift",作为 SDK 落地前的兜底。

---

## A. 架构/代码 cleanup

### A1. `sign_device_token` schema 跨项目复制(契约测试兜底)

**问题**: agent (`eidolon_agent/app/transport/pairing/token.py`) 和 channel
(`eidolon_channel/eidolon/livekit/agent/runtime/token_signer.py`) 各持
一份 80 行的 JWT 载荷+签名代码。当前靠 docstring 互引同步。

**风险**: 一边改 schema,另一边单测不会失败,直到运行时
`PairingTokenVerifier.verify` reject → 所有 web/esp32 对话挂掉。

**最终修复**(未来 SDK 抽取时做): 抽 `eidolon-runtime-tokens` micro-pkg,
两边 import。**本轮不做** —— 跟其他共享 SDK 一起统一抽。

**本轮兜底修复**: 加**跨项目契约测试**。住在 `eidolon_admin` 的测试
套件里(它本来就是 orchestrator,跨项目验证是它的本职),内容:

```python
# server/tests/test_runtime_token_contract.py
def test_channel_signed_token_verifies_with_agent_schema():
    """A token signed by channel.token_signer.sign_device_token must
    decode into the exact same fields agent.token.PairingTokenVerifier
    extracts. This is the seam Phase 32 depends on; if a future change
    drifts the schema in one project, this test fails before the
    runtime does."""
    from eidolon_channel.eidolon.livekit.agent.runtime.token_signer import sign_device_token as channel_sign
    from eidolon_agent.eidolon_agent.app.transport.pairing.token import PairingTokenVerifier
    # ... sign via channel, verify via agent's verifier, assert fields
```

测试 imports 跨项目模块。dev stack 两个项目都 editable 安装,test
跑得动。CI 上同样。

**验收**:
- [ ] `test_runtime_token_contract.py` 落在 `eidolon_admin/server/tests/`
- [ ] 测试覆盖:
      * channel sign → agent verify 全字段对(device_id / tenant_id /
        user_id / template_id / scopes / exp / iat / jti)
      * 故意改一边的 schema(比如改字段名)测试要 fail —— 用 monkeypatch
        模拟"未来谁改坏了"
- [ ] CI 必须包含这条测试
- [ ] 在两边的 token 模块顶部 docstring 加链接指向这个 contract test

---

### A2. Hub 两个 `create_app` factory(死代码)

**问题**: `hub/main.py:create_app` 和 `hub/api/app.py:create_app` 都存
在,生产跑的是 `main.py`。`api/app.py` 是早期残留,我在 32.A 编辑了
错的那个,然后才发现。

**修复**: 删 `hub/api/app.py`(或者反过来,把 `main.py` 改成 thin
entry,实现住在 `api/app.py`)。**只留一个 factory**。

**验收**:
- [ ] `grep -rn "create_app" hub/` 只在一处定义
- [ ] hub 全套测试通过
- [ ] live restart 验证

---

### A3. 5 个 Overview.vue catalog 页面复制粘贴

**问题**: `web/src/modules/{tenants,templates,users,agents,devices}/
Overview.vue` 每个 ~150 行,page-head / hint / head-actions / error
toast 几乎一致。改一个错误消息样式要改 5 个文件。

**修复**: 抽 `<CatalogPage>` 共享组件,5 个页面只声明:
```vue
<CatalogPage
  title="租户管理"
  hint="..."
  :loading="..."
  :rows="..."
  @refresh="..."
  @create="..."
>
  <template #table>...</template>
  <template #detail>...</template>
</CatalogPage>
```

**验收**:
- [ ] 新 `web/src/modules/common/CatalogPage.vue` 组件
- [ ] 5 个 Overview.vue 各砍 ~50 行
- [ ] 视觉对照截图前后无差
- [ ] vitest 加 1 个 `CatalogPage.test.ts` 验 slots / events

---

### A4. `agentsRegistry.ts` vs `agent.ts` 命名

**问题**: `web/src/api/agentsRegistry.ts`(新)和 `web/src/api/agent.ts`
(老,是 agent 服务的 proxy)两个名字相近,只靠 docstring 区分。

**修复**: 把老的 `agent.ts` 改名 `agentLegacyProxy.ts`,新的 `agentsRegistry.ts`
改名 `agents.ts`。**让长期生存的那个拿干净名字**。

**验收**:
- [ ] `grep -rn "from '@/api/agent'" web/` 全部更新为 `agentLegacyProxy`
- [ ] `vue-tsc --noEmit` 通过
- [ ] build 通过

---

### A5. `_resolve_context` 在 `kind` 缺/未知时默认走 device

**问题**: `eidolon_channel/eidolon/livekit/agent/runtime/resolver.py`
对 `metadata.kind` 缺失/非法值默认走 device 分支(配 warning log)。
违反 29.H "no silent fallback"。

**当前**:
```python
if kind == "user": return await admin.resolve_user(identity)
if kind == "device": return await admin.resolve_device(identity)
# TODO(32.D): tighten — raise
_log.warning("kind missing → defaulting to /api/resolve/device ...")
return await admin.resolve_device(identity)
```

**修复**: 改成 raise `DeviceTokenResolverError`。Phase 25 esp32 老 firmware
不带 metadata 的兼容路径已经在 hub 端被 32.B 修了(hub 给 esp32 也塞
`kind=device`),resolver 端可以收紧。

**验收**:
- [ ] resolver default branch raise,删 warning fallback
- [ ] 新单测 `test_resolver_missing_kind_raises_not_silent`
- [ ] e2e 跑通确认没 regression

---

### A6. Hub 的 `runtime_admin.enabled=false` legacy rollback

**问题**: 32.D 删了 channel 的静态 token fallback,但 hub 的"跳过
admin 校验"路径还在(`config.py` 的 `RuntimeAdminConfig.enabled`)。
两边**逻辑不再 align** —— hub `enabled=false` 时会放陌生 user 进来
签 LK token,但 channel 收到这个 user 后查 admin /api/resolve 一定
404,session 直接挂。

**修复**: 删 hub 的 `enabled` 字段(强制 admin 校验),或者明示
"hub 关了 channel 也必须关"的 invariant。**推荐删** —— rollback 路径
是 P1 解决"admin 暂时挂了想 demo"的需求,但 channel 也得到 admin
所以 hub 单边 enabled=false 没意义。

**验收**:
- [ ] `RuntimeAdminConfig.enabled` 字段移除
- [ ] hub /api/config 必经 admin 校验,无 bypass
- [ ] 老的 `test_web_legacy_rollback_skips_admin_lookup` 删除或改成"admin 不可达 → 503"

---

### A7. AdminClient 在 hub 和 channel 各一份

**问题**: 两个 `AdminClient`(hub: `/api/users`,channel: `/api/resolve/*`),
各自的 `_unwrap_detail` / 错误分层重复。

**评估**: 它们 surface 不同 —— hub 只查存在性,channel 要 resolve
聚合上下文。**保留两个但抽出共享 base** 是个选项,跟未来共享 SDK
一起做更合适。

**决策**: **不立刻做**。两个文件顶部 docstring 加"这是有意复制,
未来跟 token signer 一起抽包"备注;等共享 SDK 大动作时一起搬。

---

### A8. Mementos `bash -c "cd && exec"` 包装

**问题**: `mementos.conf` 的 `command=bash -c "cd ... && exec
./node_modules/.bin/electron-vite dev"` 用 bash 中间层是为了:
1. 让 stopasgroup 抓到 Electron PGID
2. 解决 npm 不转发 SIGTERM 的问题

**评估**: 能用,但多一层 shell。可以改成 `command=./node_modules/.bin/
electron-vite dev` + `directory=...`,supervisord 直接 spawn 该可执行
文件。bash 是不必要的。

**修复**:
- [ ] 改 `command=` 直接指向 electron-vite binary
- [ ] 重新验证 stopasgroup 仍能正确杀 Python sidecar
- [ ] 如果直接 spawn 失败(electron-vite 需要 shell 环境),退回 bash 但加注释说明原因

---

### A9. `/companion` 暗色主题里 picker 视觉

**问题**: 32.C 给 companion 装了 `<UserPicker>`,但 picker 本身是
浅色主题,只用 glass container 包了一层。**没真在浏览器看过**。

**修复**:
- [ ] 浏览器打开 `/companion`,目测 picker 文字/边框对比度
- [ ] 如有问题,要么传 `theme="dark"` prop 给 UserPicker、要么写一个
      `<DarkUserPicker>` wrapper

**验收**: 操作员能在 companion 页清楚读到 user 列表

---

### A10. Mementos cold-start race 没真重测

**问题**: 修了 `mementos.conf` 让 `stopasgroup` 真生效后,只单独
`sv restart mementos` 测过,**没跑完整的 `run_all.sh restart` 一遍**。

**修复**:
- [ ] 完整 cold start 一次,确认 mementos 跟其他服务一起干净起来
- [ ] supervisord shutdown 一次,确认 mementos 子进程不残留

---

## B. 逻辑漏洞填补

### B1. Device token revocation 不通到 channel(真漏洞)

**问题**: agent 的 `PairingTokenVerifier` 查 NATS KV bucket
`DEVICE_REVOCATIONS`。channel 自签的 device JWT 没经过任何"撤销"
流程。**24h TTL 内泄漏的 token 一直能用**,管理员在 admin UI 撤销
一个 user 也不会让正在进行的 session 立刻断。

**修复方案**(分阶段):
1. **短期**: 加 admin endpoint `POST /api/users/{id}/revoke-sessions` —
   写入 NATS `DEVICE_REVOCATIONS` bucket(key = `revoked.web-*` 这种
   pattern,或者 key = `revoked.user.<user_id>` 让 verifier 按 user 拒)
2. **中期**: agent 的 verifier 已经查这个 bucket,把 user-level revoke
   也支持上(目前只按 device_id 撤);channel 不变
3. **长期**: 把"撤销"做成 user 状态机的一部分(disable user → 自动
   revoke all sessions of that user)

**验收**:
- [ ] admin POST /api/users/<id>/revoke 后,该 user 的活跃 session
      下一次 chat() 收到 401 / UNAUTHENTICATED
- [ ] 新单测 cover

---

### B2. JWT secret 文件 rotate 不热加载

**问题**: channel 启动时读 `~/eidolon/run/jwt-secret`,缓存到
resolver closure。secret 在运行时被 rotate,channel 不知,继续用旧
secret 签 → agent verify 失败 → 所有 session 挂。

**评估**: dev 阶段无所谓(rotate 频率极低),prod 应该有 reload 机制。

**修复**:
- [ ] 文档化:`~/eidolon/run/jwt-secret` rotate 必须重启 channel + agent
- [ ] 或者: channel 在 resolver 工厂里改成"每次新会话重读 secret 文件",
      代价是每会话一次 disk read(< 1ms)。**推荐**

**验收**: rotate secret 文件后,新会话用新 secret(老 session 仍用
老 secret 直到 token 过期)

---

### B3. Mid-session `participant.identity` 切换语义未定义

**问题**: LiveKit 允许动态改 participant.identity。如果 web client
JS 改了 identity,**channel 资源仍是初始 identity 的 token**,但
agent 那边 conversation_id 可能跟着改了(`livekit:<participant>:
<room>`)。行为不确定。

**评估**: 当前 web client 代码不会动态改 identity,这是 LiveKit SDK
理论支持但不被 UI 触发的能力。

**修复**:
- [ ] 在 resolver docstring 标注"identity 假设不变;若变更行为未定义"
- [ ] 可选:resolver 第二次被调时检查 participant 是否变了,变了 raise

---

### B4. Channel resolver 缓存 vs admin 状态变化

**问题**: 操作员在 admin UI 改了 `user.active_agent_id`,**这个 LK
session 内不生效**(token 已签好缓存了)。下次新连接才换。

**评估**: 这是 feature 不是 bug —— 会话中途切人格会非常诡异。但需
要文档化。

**修复**:
- [ ] resolver docstring 明示这个 cache-for-session 决策
- [ ] admin UI 在切 active_agent 时显示"对正在进行的会话不生效"提示

---

### B5. Companion page localStorage race

**问题**: 极小 edge case — 开发者在 devtools 清 localStorage 同时
刷新 companion,可能短暂渲染空 picker。无害。

**决策**: **不修**,记一笔。如果 UX 团队有人提到再优化。

---

## 共享代码抽取(本轮不做,记录待办)

被识别但本轮不解决,等未来一次性多 SDK 抽取一起做:

- agent `pairing/token.py` ↔ channel `runtime/token_signer.py` —— 80
  行 JWT sign 逻辑双份(A1 兜底用契约测试防漂)
- hub `api/clients/admin.py` ↔ channel `runtime/admin_client.py` ——
  AdminClient 双份不同 surface(A7 决策不立刻做)
- 跨项目共用的错误归类 helper / `_unwrap_detail` 等小工具

未来抽取候选包名建议:
- `eidolon-runtime-tokens` (JWT contract)
- `eidolon-admin-rest-client` (admin /api/* 调用)
- `eidolon-error-shape` (errors envelope unwrap)

时机: 当 3 个以上跨项目重复点累积、或决定开始公开发布 SDK 时,
集中起一次 `eidolon_libs/` monorepo subdir。届时把这里列的 + 那时
冒出来的新候选一起搬。

---

## 优先级 + 依赖图

```
P0 (做完这些,32 系列真完工):
  A1 — 加跨项目契约测试 (防 token schema drift)
  A5 — resolver kind missing 收紧 raise
  B1 — token revocation 通到 channel

P1 (架构干净度):
  A2 — hub 删 api/app.py
  A6 — hub 删 enabled=false rollback
  B2 — secret 热加载

P2 (代码优雅):
  A3 — <CatalogPage> 共享组件
  A4 — agentsRegistry / agent 重命名

P3 (polish):
  A8 — mementos bash 包装清理
  A9 — companion picker 视觉
  A10 — cold-start race re-verify
  B3 — identity 切换 docstring
  B4 — admin UI 切人格提示

不做(决策记录):
  A7 — 两个 AdminClient 保留(用途不同,跟未来 SDK 一起抽)
  B5 — companion devtools race
  SDK 抽取 — 等多个共享点累积后统一做
```

---

## 不在本轮范围

- Phase 32.B P1 之外的 admin UI 改动
- Multi-tenant 真正的隔离(目前所有 user 都在 tenant=default)
- Memory consolidator + recall 的真测(需要 etl 跑过 6h)
- 移动端 client
- ESP32 真硬件 e2e(目前只 mock 测过 device 路径)

---

## 估时

- P0(A1 契约测试 + A5 + B1): ~ 半天
- P1: ~ 半天
- P2: ~ 一天(Vue 组件抽提 + tsc 验证)
- P3: ~ 半天
- 合计 2.5 天

## 验证整轮完成的标志

1. `test_runtime_token_contract` 在 admin 测试套件里,跨项目 sign↔verify
   roundtrip pass
2. `test_phase32_e2e_isolation` 仍 pass
3. 5 个 catalog 页面共享 `<CatalogPage>`,各 < 80 行
4. hub 单一 `create_app`,api/app.py 已删
5. resolver 对 unknown kind raise(测试覆盖)
6. revoke endpoint 撤销 user → 下次 chat 401
7. hub 没有 `runtime_admin.enabled=false` rollback 路径
