# ADR-0006：局域网认领对所有 Host 开放，并删除固定开发码

- 状态：Accepted
- 日期：2026-08-27
- 取代：[ADR-0005](adr-0005-development-lan-commissioning.md)

## Context

ADR-0005 只在 `mode=development + commissioning_adapter=disabled + network_adapter=memory`
时开放 LAN commissioning。那组条件描述的是**一台 macOS 工作站长什么样**，不是一条信任边界。

代价在真机上出现了。仓库自带的开发 drop-in
（`deploy/systemd/eidolon-bootstrapd-development.conf.example`）给 Pi 配的是
`development + bluez + networkmanager`——恰好是被拒的那一组。于是
`GET /api/local/v1/host` 报 `"mode": "development"`，看着这条路该开着，实际恒 404；
手机把这个 404 归成「静默」，在自己刚解析并对话过的两个地址下面印出
「局域网里没有任何设备应答」。一整个会话被这句与事实相反的话带走。

复核首次接触之后，闸门守的东西比想象的少：

- `parseAndVerifyDiscovered` 传的是 `expectedHostId = null`、`expectedHostPublicKey = null`。
  签名是**自指的**——文档带着签它的公钥，`host_id` 又从那把公钥派生。任何人用自己的
  Ed25519 密钥对都能造出一份合法自洽的首次接触文档；
- `ble_service_uuid` 默认是全产品常量（`_DEFAULT_BLE_SERVICE_UUID`），不是每台不同的，
  绑不住任何东西。

也就是说**两条 transport 的首次接触都没有认证**，8 位码是交给「第一个应答的东西」的
bearer secret。蓝牙唯一多出来的是半径：十米、要有人站在那儿，对比整个网段、可脚本化。
闸门在配给一个半径，不在守一个属性。

同时暴露的第二件事：`_active_setup_session()` 在 development + 配了
`EIDOLON_BOOTSTRAP_DEV_SETUP_CODE` + 未认领时，会在**读** endpoint 的时候顺手铸一个
session。没有任何 Host、ops profile 或 CI 设置过这个变量，而它的实际效果是让未认领 Host
的认领窗口永远开着、一次性码不再是一次性的，并且让「有没有窗口」变成 mode 相关。

## Decision

**1. 删除固定开发码。** `EIDOLON_BOOTSTRAP_DEV_SETUP_CODE` 与 `_active_setup_session()`
的自动铸造分支一并移除。`_active_setup_session()` 塌缩为「有活会话就返回，否则 None」。
**认领窗口存在 ⟺ 操作员签发过一个**——所有 Host、所有 mode 同一个答案。
弱码规则（Matter/HomeKit 那三种形状）没有消失，它移到了唯一的产码处：`generate_setup_code()`。

**2. 两条 transport 都常开。** 删除 `_require_development_lan_commissioning`。BLE 与 LAN
交付**同一份签名文档**，认领走**同一个 `CommissioningService`**，因此原子性、单次消费、
`reset_epoch` 校验、5 次失败吊销全部不变。路由随之改名，去掉不再成立的 `development/`：

- `GET /api/local/v1/commissioning/endpoint`
- `PUT /api/local/v1/commissioning/claim`
- 控制面操作 `dev.lan.*` → `commissioning.lan.*`，应答 `operation` 改为
  `local.lan-commissioning-claim`

**3. 认领不再替真适配器声明网络状态。** 原先 `claim_*_controller` 无条件写
`reconcile_network_state(CONNECTED)`，这是为 memory adapter 写的：它没有 OS 状态可发现，
「这个 pinned Local API 被够到了」是唯一证据。带真适配器的 Host 有真答案（daemon 启动时
与每次变化时已对齐），继续无条件写等于让一个**请求**覆写它没有权威的事实——一台靠
link-local 网线可达、而 NetworkManager 报 Wi-Fi 断开的 Pi 会因为有人来认领而被记成
connected。改为只在 memory adapter 下自行发布；真适配器说不通就让 store 如实拒绝。

**4. App 侧仍然只在 Debug 开放这个入口。** Host 具备能力，产品 UI 还没有为它设计
（Pi 没有屏幕，码从哪来是个产品问题）。这是一处**刻意保留的缺口**，不是遗漏。

## Consequences

**安全**：认领 Pi 的前提从「蓝牙距离内 + 8 位码」变成「同一局域网 + 8 位码」。这是一次
**有据可查的半径扩大，不是一个新洞**——首次接触本来在两条路上都不认证。增量披露只有
`tls_spki_fingerprint`、`reset_epoch`、`local_api_base_urls` 与「窗口开没开」；`host_id`、
公钥、指纹早已由 `/api/local/v1/host` 公开。没有窗口时这条路是惰性的：`claim` 直接 401。
删掉固定码之后，dev 与 prod 在这条路上完全相同。

**运维**：BLE 是这套栈里最不可靠、也是唯一无法从工作站脚本化的一环。HIL/CI 现在可以无人
值守地认领一台 Pi。

**未决（应当单独立项）**：用那 8 位码去**建立**首次接触的信任，而不是在信任之后才用来授权——
即 SPAKE2 之类的 PAKE，Matter/HomeKit 正为此而做。做完之后不知道码的冒充者根本完不成握手，
半径不再是安全参数，transport 才真正退化成实现细节。**这件事与本 ADR 无关也该做**，因为
蓝牙那条路有完全相同的洞。
