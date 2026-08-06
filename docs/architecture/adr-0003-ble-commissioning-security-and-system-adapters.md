# ADR-0003：BLE Commissioning 使用 Host 签名端点、Pinned TLS 和窄系统 Adapter

- 状态：Accepted，代码完成；Pi/Android 真机验收待执行
- 日期：2026-08-05

## Context

首次开箱时树莓派没有 LAN，Mobile 仍需要认证正确的 Host、保密 Wi-Fi 凭据并建立
长期 Controller。BLE 配对状态不等价于 Eidolon Host 身份，也不能假设所有手机与
镜像都使用相同的 BlueZ pairing policy。

原计划要求“不自行发明密码协议”。调研过 Noise NKpsk0：它适合高熵一次性 secret，
但候选 Dart 实现缺少足够维护、审计与跨 Python 互操作证据。因此当前不把它放入
产品信任根。

## Decision

### 1. 传输和安全分层

BlueZ adapter 只提供可靠、有序的 GATT 字节链路：

- Service UUID 由 Bootstrap descriptor 指定；
- Info characteristic：`30af68fb-163b-581f-a94c-1488e8b3b4fd`，只读；
- RX characteristic：`518d55c5-5433-5312-9099-a0a03c90f003`，Write with response；
- TX characteristic：`c8a3ab33-7e3a-5827-adf0-f4358a0cfe38`，Indicate。

不用 Write Without Response/Notify 承载 TLS stream，因为 Setup 需要可靠、有序传输。
当前一次只接受一个 central；设备选择由 signed endpoint 而不是 MAC、RSSI 或名称完成。

GATT 之上使用 TLS 1.2+：Host 是 Python/OpenSSL `SSLObject + MemoryBIO`，Android
是平台 `SSLEngine`。双方按协商 MTU 分片 TLS record，应用层看不到 GATT 分片。

### 2. Host 身份绑定

Bootstrap 为 commissioning TLS 生成独立 P-256 key/certificate，私钥保存为 mode
`0600` 文件。Host Ed25519 identity 对 Host ID、reset epoch、BLE Service UUID、TLS
SPKI SHA-256 fingerprint、固定 purpose 和 contract version 做 canonical JSON 签名。

产品 Mobile 用二维码中的 Host Ed25519 public key 验 endpoint 签名。开发模式允许从
endpoint 读取 public key，派生并核对 Host ID/指纹、验证自签名 endpoint 后输入短期
Setup 码；这是受控 TOFU，不替代产品制造信任。随后 Android TrustManager pin SPKI。
自签名证书不进入系统 CA，也不要求用户装 CA。

Info endpoint 是公开数据，允许被读取和重放；签名、reset epoch、Host ID 匹配和后续
TLS SPKI pin 决定是否接受。广播名称、MAC 和 RSSI 永远不作为身份认证。

### 3. 应用层授权

TLS 成功只证明 Host 并提供加密，不自动授权 mutation：

- 开发开箱：App 在 TLS 内提交短期 commissioning ID 和 6 位 Setup 码；Bootstrap
  只保存 hash，并在连续 5 次失败后撤销；
- 认领：Controller 使用独立 Android Keystore P-256 key；Grant 创建、session 消费和
  `claim_state=claimed` 在一个 store 事务完成；
- 已认领换网：Host 返回随机 challenge，Controller 对带 purpose、ID 和 reset epoch
  的 canonical JSON 做 ECDSA-SHA256 签名；一次性开箱 secret 不复用；
- operation/request ID 保证重试不会创建重复 Grant 或配网 operation。

Controller key 与现有 Mobile Body/Hub device key 使用不同 alias 和 ID 前缀。

### 4. NetworkManager 与持久化边界

`NetworkManagerProvisioning` 通过系统 D-Bus 实现 scan、AddAndActivateConnection2、
CheckpointCreate/Rollback/Destroy。Wi-Fi profile/密码的唯一权威仍是 NetworkManager；
Bootstrap store 只记录 operation、目标 SSID、状态、error code 和 reset epoch。

`controller_grants`、`bootstrap_operations`、claim/session 消费是跨重启权威状态，继续
使用 `BootstrapStateStore` 的 SQLite adapter；一般日志仍由 journald 持有。
bootstrapd 启动会把未完成 operation 标记为 `daemon_restarted`，并要求具体 network
adapter 先 rollback 当前 Wi-Fi device 的遗留 NetworkManager checkpoint；恢复失败时
新的 stage fail closed，不能让旧 checkpoint 稍后回滚一次新的成功配置。

## Threat model result

- 被动监听：TLS 保密 Wi-Fi 密码和 commissioning secret。
- 主动 MITM：无法同时伪造 Host endpoint 签名和 pinned TLS SPKI。
- Descriptor 重放：expiry、单次消费和 reset epoch 限制重放。
- 旧二维码夺取已认领设备：一次性 session 已消费；换网要求 Controller challenge。
- 错 Host/MAC/RSSI 欺骗：Mobile 验 signed endpoint 后才建 TLS。
- Mobile 泄露：secret/密码只在 Setup 内存和 TLS payload；私钥留在 Keystore。

## Consequences and remaining evidence

无硬件测试已覆盖 endpoint 签名、TLS stream、错误 secret、原子 claim、Controller
challenge、配网 stage/confirm/rollback 和密码不落 DB；Android Debug APK 可以编译。

下列结论必须等待 Raspberry Pi 5 + Android 真机，当前不得宣称通过：

- BlueZ external GATT registration、long read、indication confirmation 和 MTU；
- NetworkManager D-Bus Variant、polkit action 和 checkpoint 在目标镜像上的行为；
- Android `SSLEngine` 与 Python `SSLObject` 经过真实 GATT 的互操作；
- BLE 断连、BlueZ/NM restart、错误密码、DHCP 失败和 App kill；
- 单 radio 环境的 BLE/Wi-Fi 共存。

物理 recovery GPIO/按键、产品二维码制造流程、Factory Reset manifests 和 iOS 不在本
ADR 中伪装成已完成能力。

## References

- BlueZ GATT API: <https://bluez.readthedocs.io/en/latest/gatt-api/>
- NetworkManager D-Bus API: <https://networkmanager.dev/docs/api/latest/spec.html>
- Android BLE permissions: <https://developer.android.com/develop/connectivity/bluetooth/bt-permissions>
- Noise Protocol Framework: <https://noiseprotocol.org/noise.html>
