# ADR-0005：已联网开发 Host 复用签名端点与 Pinned HTTPS 完成认领

- 状态：Accepted
- 日期：2026-08-10

## Context

Pi 的首次开箱通过 BlueZ GATT 承载 Host 签名 endpoint、commissioning TLS 和 Setup
协议。macOS 开发 Host 没有 BlueZ，且已经联网；复制 Pi Host 私钥、向 Mobile 注入
fingerprint、关闭证书校验或直接写 Controller grant 都会破坏 Host identity 与 Bootstrap
authority。

现有开发模式已经把首次身份语义定义为受控 TOFU：Mobile 从公开 transport 读取 Host
public key 与 endpoint，验证 Ed25519 签名，再 pin endpoint 中的 TLS SPKI；6 位短期码只在
pinned TLS 内授权 mutation。Local API 与 BLE commissioning listener 使用同一份
`commissioning_tls.pem`，因此 transport 可以变化而不需要第二套身份或授权模型。

## Decision

只在 `mode=development + commissioning_adapter=disabled + network_adapter=memory` 时开放：

- `GET /api/local/v1/development/commissioning/endpoint` 返回原有
  `commissioning_endpoint()`，不创建权限；
- `PUT /api/local/v1/development/commissioning/claim` 消费原有 commissioning ID、6 位码
  和 Mobile Keystore Controller public key；
- Bootstrap 仍调用 `CommissioningService.authorize()` 与 `claim_controller()`，因此错误
  次数、过期、单次消费、reset epoch、Controller ID 派生和原子 grant/claim 语义不变；
- Local API 可达本身证明这个开发 Host 已联网，memory adapter 在认领前只把 Bootstrap
  network projection 收敛为 `connected`，不写系统 Wi-Fi profile；
- Mobile Debug 允许一次 bounded、无 CA 校验的 endpoint GET。它必须先验证 Host 签名，
  此后的 claim、Controller session、Workspace 和日常 API 全部使用签名 endpoint 中的
  SPKI pin；mDNS 仍只提供候选地址。

生产模式、BlueZ/NetworkManager 组合和 release APK 均不开放该入口。固定开发码可以由
既有 `EIDOLON_BOOTSTRAP_DEV_SETUP_CODE` 显式启用；默认通过 operator CLI 签发随机短期码，
不把万能码写入仓库、Mobile 或诊断输出。

## Consequences

Mac 与 Pi 在 Host identity、Controller Grant、Workspace 和对话层保持对等；差异仅是首次
commissioning transport。此路径不证明产品制造身份，安全等级与既有 BLE development
TOFU 相同，不能作为 production onboarding 的替代方案。
