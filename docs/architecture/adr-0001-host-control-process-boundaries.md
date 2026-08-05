# ADR-0001：Eidolon Host Control 在 Admin 同仓、运行时隔离

- 状态：Accepted
- 日期：2026-08-05

## Context

Eidolon OS 运行在无屏树莓派上，需要在没有 Wi-Fi、完整应用栈未启动或
Admin/Data/Hub/Kernel 故障时，仍能通过 Mobile 完成首次接入和恢复。

`eidolon_admin` 已经拥有系统集成、Owner onboarding、服务健康和部署编排能力，
因此新建独立仓库会增加版本与发布协调成本。但现有 Admin FastAPI 同时暴露
Supervisor、配置、日志和数据管理能力，并依赖完整应用栈，不适合作为早启或普通
Mobile 的安全边界。

## Decision

Bootstrap 与 Local API 放在 `eidolon_admin` codebase，形成三个独立运行单元：

1. `eidolon-bootstrapd`：systemd 直接管理的早启主机控制面。
2. `eidolon-local-api`：普通 Mobile 唯一访问的本地产品入口。
3. `eidolon-admin-api`：现有运维与开发管理面，产品默认只允许 loopback/支持模式。

仓库共享不是运行时共享。三个进程拥有不同 entrypoint、监听面、Linux 权限、
配置和失败边界。

## Bootstrap 生命周期规则

- `bootstrapd` 不依赖 `network-online.target`，并排在 `eidolon-stack.service` 之前。
- systemd 是唯一 restart authority；进程不实现自拉起或双重 supervisor。
- unit 使用 `Restart=always`、无限重试窗口和固定退避。
- unit 使用 systemd watchdog；事件循环失去响应时也会被重启。
- 正常 `systemctl stop`/机器关机时处理 SIGTERM，关闭 Unix socket；启停记录进入 journald。
- 开发环境可由命令行启动，但产品环境不能把它放入 supervisord application stack。

## 逻辑与权限边界

- Bootstrap 只拥有 Host Identity、Controller Grant、commissioning/recovery
  operation、reset epoch。
- NetworkManager 是 Wi-Fi profile 和密码的唯一权威。
- Data 拥有 Owner/Companion，Hub 拥有外部 Device admission，Kernel 拥有 Mount。
- Bootstrap 不导入、打开或修改兄弟项目数据库。
- Bootstrap package 不得 import Admin app、Data、Memory、NATS、Supervisor、torch 或
  uvicorn；该规则由 AST architecture test 约束。
- Admin API 不获得 BlueZ、NetworkManager 或 root reset 权限。
- 共享应用用户不永久加入 Bootstrap socket group；Local API 的访问权只由其
  systemd unit 通过 `SupplementaryGroups=` 注入，Admin/supervisord 不继承。
- Bootstrap 与 Local API 通过文件权限保护的 Unix socket 交互；Local API 不读取
  Bootstrap SQLite 或 Host private key。
- Local API 只发布明确 allowlist 的产品路由，不提供通用 Admin proxy。

## Failure behavior

- Admin/Data/Hub/Kernel 故障：Bootstrap 保持运行；Local API 对依赖功能返回 degraded。
- Bootstrap 异常退出：systemd 退避后重启。
- Bootstrap 卡死：systemd watchdog 重启。
- Host Identity/durable state store 不合法：生产模式 fail closed 并进入可观测 restart 状态，不能
  临时生成新的产品身份绕过制造信任。
- Local API 故障：不影响 BLE/recovery authority；systemd 独立重启 Local API。

## Consequences

正面结果：

- 保留 Admin 同仓的集成效率，同时建立真实 OS 进程与权限边界。
- 无网络和完整栈故障不再切断恢复入口。
- Mobile 产品 API 不继承 Admin 运维面权限。

代价：

- 同一项目需要维护三套 entrypoint 和两套外部 API 语义。
- Pi 镜像必须创建专用用户、systemd unit 和细粒度 D-Bus/Polkit policy。
- Bootstrap package 需要持续执行依赖反向检查，避免未来便利性修改重新耦合 Admin。

## Rejected alternatives

- 把 BLE background task 放进现有 Admin FastAPI：启动依赖、权限和故障域错误。
- 让 supervisord 管理 bootstrapd：supervisord 属于等待网络的完整应用栈。
- 让 bootstrapd 自行 fork/拉起：会与 systemd 形成双 supervisor，状态不可观测。
- Mobile 直接访问 Admin/Hub/Kernel：扩大攻击面，并允许客户端伪造 Owner scope。
