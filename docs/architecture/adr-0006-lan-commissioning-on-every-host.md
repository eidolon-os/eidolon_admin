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

**5. 码可以由调用方指名，而且这条路上没有 mode。** `commissioning-code` 接受
`--code`，ops profile 可以用 `app.setup_code` 钉住它（`pi5.toml` 现在是 `99999990`）。
这**不是**把第 1 条删掉的东西放回来：坏的是那份实现——读 endpoint 会顺手铸 session、
窗口永不关闭——不是「开发时用同一个码」这个愿望。指名的码走的是同一条 `issue_setup_code`：
一个真 session，会过期、只能用一次、错五次吊销、并把之前的窗口作废。**Host 自己完全不知道
「固定码」这回事**，它只是收到一个值；「固定」纯粹是操作员一侧的输入。

这里刻意不加 `mode is PRODUCTION → 拒绝`，理由有三条，第一条是决定性的：

- 出厂 Host 最终要把**印在盒子上的码**写进自己（身份私钥已经是制造时经写一次通道投放的，
  同一条通道），那正是「向一台生产 Host 指名一个码」。造一个注定要删的护栏不如不造；
- 走到这个操作已经意味着握着 root 本地套接字——同一个调用方本来就能抽一个随机码再读回来，
  指名不给他任何新能力；
- 真正想防的「整批机器共用一个码」，Host 根本看不见：它一次只见一个码，永远不知道有没有
  别的机器也用它。那是制造/ops 的纪律，不是 Host 能判的。留一个假装管用的代理护栏更糟。

剩下的是唯一一条与调用方无关的规则，且抽来的和给来的一视同仁：**这个码必须是这台 Host
自己也愿意抽出来的**（`is_usable_setup_code`）。ops 在解析 profile 时按同一条规则先挡一遍，
只为把错误移到写下这个值的地方；Host 复检并保持权威。

**6. `dev.show` 改名 `commissioning.status`，并且修好了。** `ac0d9f4` 把
`development_setup_status` 改名成 `setup_session_status` 时漏了 `control.py` 这个唯一调用点，
该操作从 2026-08-12 起对每个调用方抛 `AttributeError`，因为没有任何测试盖到它。它同时不再
按 mode 设闸：「有没有开着的窗口」是关于一台出厂 Host 最实际的问题，那一行不含码，只有
签发时间、失效时间、用没用过、错了几次——按构建类型拒答它保护不了任何东西。

## Consequences

**安全**：认领 Pi 的前提从「蓝牙距离内 + 8 位码」变成「同一局域网 + 8 位码」。这是一次
**有据可查的半径扩大，不是一个新洞**——首次接触本来在两条路上都不认证。增量披露只有
`tls_spki_fingerprint`、`reset_epoch`、`local_api_base_urls` 与「窗口开没开」；`host_id`、
公钥、指纹早已由 `/api/local/v1/host` 公开。没有窗口时这条路是惰性的：`claim` 直接 401。
删掉固定码之后，dev 与 prod 在这条路上完全相同。

**运维**：BLE 是这套栈里最不可靠、也是唯一无法从工作站脚本化的一环。HIL/CI 现在可以无人
值守地认领一台 Pi。配上钉住的码，开发循环里「查码」这一步整个消失：`commissioning-code`
变成一条不看输出的命令，手机上敲的永远是同一串数字。

**没有做的一件事**：install 收尾自动签一个码。放进 release transaction 意味着要么让一次
authority 变更**不出现在 plan 里**（而这套 ops 的整个契约就是「apply 之前逐条审阅计划里的
每一次 mutation」），要么就得扩这条最要命操作的 plan 词汇与 resume 语义——为了省一条不看
输出的命令，不成比例。留作单独一步。

**未决（应当单独立项）**：用那 8 位码去**建立**首次接触的信任，而不是在信任之后才用来授权——
即 SPAKE2 之类的 PAKE，Matter/HomeKit 正为此而做。做完之后不知道码的冒充者根本完不成握手，
半径不再是安全参数，transport 才真正退化成实现细节。**这件事与本 ADR 无关也该做**，因为
蓝牙那条路有完全相同的洞。
