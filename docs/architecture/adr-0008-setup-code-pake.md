# ADR-0008：Setup 码用来建立信任，不再在信任之后被发送

- 状态：Accepted
- 日期：2026-11-05
- 关闭：[ADR-0006](adr-0006-lan-commissioning-on-every-host.md) 的「未决」项
- 重算：[ADR-0007](adr-0007-factory-setup-code-and-one-window-logic.md) 第 3 条留下的限速问题

## Context

认领一台 Host 时，手机先读 endpoint 文档，钉住文档里写的 TLS 公钥，然后在这条 TLS 里
把 8 位码**明文**交给 Host，由 Host 求哈希、比对。endpoint 文档由它自己携带的那把公钥签名，
所以首次接触是不认证的：任何能在 BLE 或局域网上冒充 Host 的一方，都能收到这个码，再把它
转交给真正的 Host 完成认领——它就坐在中间。ADR-0006 把这件事列为「未决，应当单独立项」，
并且指出蓝牙那条路有完全相同的洞。

另外两件事让它更紧迫：

- **局域网上没有限速。** ADR-0007 去掉了「5 次错码吊销」，理由是 BLE 约 10 m 的物理半径
  比八位码的熵更能约束暴力猜测，同时写明「若将来窗口延伸到更大半径（局域网……），这一条要
  重新算」。ADR-0006 早已把窗口延伸到局域网，这笔账一直没算。现状是 BLE 一条链路每秒约
  10 次，局域网 Host 能答多快就多快，对着一个永不过期的出厂窗口。
- **手机对陌生 Host 的首次接触也不认证。** 已经有管理密钥的手机，对一台没确认过的 Host
  做 `controller.authenticate`，对方回「成功」就会被保存，之后还会把家里 Wi-Fi 的密码发给它。
  冒充者只需要无条件回「成功」。

## Decision

### 1. SRP-6a，RFC 5054 3072 位群，SHA-512，g = 5

- `bootstrap/pake.py`，纯函数，没有 I/O、时钟或自己的随机数。
- 手机端 Dart 用 `BigInt.modPow` 写同一份（放进 isolate）。
- 两边都由 `contracts/bootstrap/v1/golden/host-claim-srp6a-v1.json` 约束。这份向量包含每个
  中间值，以及双方都必须拒绝的输入。生成器在写出任何东西之前，先用同一份代码复算 RFC 5054
  附录 B 的已发表向量。

**为什么不用 SPAKE2+（Matter 的选择）：** Python 和 Dart 两边都没有与彼此一致、能把我们的
上下文绑进确认步骤的成熟实现，自己写椭圆曲线上的 M/N 点和 w0/w1 派生，出错面比 SRP 大得多。
SRP-6a 只需要模幂和 SHA-512，有 RFC 自带的测试向量可对，而且和设备侧 ESP32 Security 2
是同一个群、同一个公式。

**为什么不用 pysrp 或 pointycastle：** 两者的 M1 公式彼此不同，也都不能绑定额外上下文。
确认步骤是这里的安全要点，不能交给某个库的偶然选择。

### 2. 口令就是已经存着的 `secret_hash`

- SRP 的 `I` 是 `commissioning_id`，`P` 是 `sha256hex(code)`，也就是 `commissioning_sessions`
  里已有的那一列。
- Host 每次握手都用新的 16 字节 salt 现算 verifier。
- 不改表结构、不迁移，已经开着的窗口（包括出厂窗口）立即可用，回滚旧版本不碰数据库。

### 3. 两个证明都绑定 Host 的身份

上下文是规范 JSON（键排序、无空白、ASCII），字段如下：

| 字段 | 含义 |
|---|---|
| `suite` | 握手套件名 |
| `transport` | `ble` 或 `lan` |
| `host_public_key` | Host 长期公钥 |
| `tls_spki_fingerprint` | Host TLS 公钥指纹 |
| `commissioning_id` | 窗口 |
| `reset_epoch` | 重置纪元 |
| `controller_public_key_sha256` | 手机管理密钥的 SHA-256 |
| `handshake_id` | 本次握手 |

计算：

```
TH = SHA-512("eidolon-host-claim-pake-v1\0" ‖ u32(len ctx) ‖ ctx ‖ u32(len s) ‖ s ‖ PAD(A) ‖ PAD(B))
Kc, Ks = HKDF-SHA512(salt = TH, ikm = K, info = "… client confirm" / "… server confirm")
M1 = HMAC-SHA512(Kc, TH)
M2 = HMAC-SHA512(Ks, TH ‖ M1)
```

- 手机从它**已经钉住的** endpoint 文档自己构造上下文；Host 回显的上下文只用来在出证明之前
  对一遍，不一致就中止。
- 一个转发者用的是它自己的 TLS 公钥，手机的证明里写的就是那把钥匙，真正的 Host 会拒绝。
- 手机验证 M2 之后，才算认证了这台 Host。

### 4. 消息

**BLE（钉住的 TLS 里）**

- `pake.start`：`{suite, commissioning_id, controller_public_key}` → `{handshake_id, salt, B, context}`
- `pake.confirm`：`{handshake_id, A, client_proof}` → `{server_proof, state}`
- 之后的 `claim.complete` 必须出示被绑定的那把管理密钥，换一把会被拒绝。
- `session.authenticate` 已删除，没有任何操作接受 Setup 码本身。还在发它的手机得到
  `unknown_operation`，而不是「码错了」：未知操作在查授权之前就被点名。

**局域网**

- `POST /api/local/v1/commissioning/pake/start`
- `POST /api/local/v1/commissioning/pake/claim`：一次请求里完成确认和认领，返回认领结果和 `server_proof`。
- `PUT /api/local/v1/commissioning/claim`（带明文码的那条）已删除，回 404。
- 这两条路由的错误体带 Host 自己的错误码，因为 409 既可能是「重新开始握手」，也可能是
  「已经被别人认领」，手机不能对两者做同样的反应。

**握手状态**（在 bootstrapd 内存里）

- 30 s 过期；只答一次，对错都一样；最多 16 个，满了丢最旧的。
- BLE 上绑定发起它的那条链路。
- 确认通过之后，还要再查一次窗口是否仍然开着：证明只对它所针对的那个窗口有效。

### 5. 限速：只拖慢，永不关窗

`bootstrap/guess_budget.py`，令牌桶。

**每一次猜测都扣一个令牌**，扣在 PAKE 的 start 上，两条通道都一样：A 一旦发出，猜的是
哪个码就定了。这是猜码的唯一入口。

| 通道 | 容量 | 补充速度 |
|---|---|---|
| BLE | 10 | 每 2 s 补 1 |
| 局域网，每个来源地址 | 5 | 每 20 s 补 1 |
| 局域网，全局 | 10 | 每 10 s 补 1 |
| 局域网，同一窗口错满 100 次之后 | 10 | 每 60 s 补 1 |

- **永不关窗**，所以不会把 ADR-0007 去掉的「开箱即砖」带回来；最坏情况是合法手机在
  **攻击者所用的那条通道**上排队。
- BLE 和局域网分开计，局域网上的洪水饿不死站在 Host 旁边的人。
- 被拒的一次不扣任何桶：繁忙的邻居不会把一个还没猜过的来源拖干。
- 对一个不存在的窗口的请求，在扣令牌之前就拒绝：窗口 id 本来就公开在 endpoint 文档里，
  而用它来重置预算的做法会被这条挡住。
- 状态在内存里，只覆盖当前开着的窗口。新签一个窗口，预算回满。
- `rate_limited` 带 `retry_after_ms`；局域网另带 `Retry-After` 头。

### 6. 不兼容旧版本（2026-11-05 决定）

产品还在开发阶段，所有 Host 和设备都可以重新配网，所以**不留任何旧路径**：

- Host 只接受 PAKE：`session.authenticate` 和 `PUT /commissioning/claim` 都删了。
- 手机只走 PAKE：旧 Host 在 BLE 上回 `unknown_operation`、在局域网上回 404，手机据此告诉人
  「主机软件太旧，先升级主机」，**不退回**明文发码。
- 因此不存在降级路径：冒充者回一个 404 也拿不到码。
- endpoint 文档仍然一个字段都不加：它已经贴近 512 B 的 GATT 上限，而 PAKE 不需要它多说什么。

### 7. 手机只信任确认过的 Host 公钥

- 手机保存一个「验证过 M2 的 Host 公钥」集合。
- 对一台不在集合里的 Host，`controller.authenticate` 即使成功，也不保存、不发 Wi-Fi，
  改为要求输入 Setup 码。
- 这一条在手机仓库实现。

## Consequences

**安全**

- 不知道码的一方完不成握手，也拿不到码。
- 中间转发会在 M1 处失败，离线猜测不可能，剩下的只有受限速的在线猜测。
- 认领的半径（BLE 还是局域网）不再是安全参数。ADR-0006 期待的「transport 退化成实现细节」
  从这里开始成立。

**残余，有记录**

- **出厂窗口的长期在线猜测。** 局域网全局速度在慢道上大约每年 52 万次，对 10^8 的码空间约
  为 0.5%。窗口永不过期是 ADR-0007 的选择，这个数字随它一起被接受；要再降，就给慢道降速，
  不必关窗。
- **数据库里的 `secret_hash`**：拿到它就能离线穷举 10^8 个码，和以前一样。出厂码按
  ADR-0007 本来就以明文存在设备上，PAKE 不改变这一点。
- **限速状态不落盘。** Host 重启后桶回满。

**运维**

- 不改表结构、不改 endpoint 文档；但 Host 和手机必须一起升级，旧的一方只会得到明确的
  「操作不存在」，不会被悄悄兼容。
- 握手在 Pi 级 CPU 上每次需要几次 3072 位模幂；对认领这种低频操作可以接受，台架上测 p95。
