# ADR-0002：Bootstrap 使用窄 Port，并分离权威状态与运行日志

- 状态：Accepted；具体 BlueZ/NetworkManager 实现由 ADR-0003 续订
- 日期：2026-08-05

## Context

Bootstrap 最终可能使用 BlueZ、NetworkManager 或其他 OS adapter，但当前暂不进行
树莓派实机测试。此时把 GATT characteristic、MTU、D-Bus object path、connection
profile 或 checkpoint 写入应用层，会把尚未验证的实现假设固化成产品架构。

同时，Bootstrap 的部分事实必须跨进程和主机重启恢复，但 daemon 启停和异常仅用于
诊断。把两类数据都写入 SQLite，会模糊权威边界；把所有数据都改成普通日志，则需要
自行实现 replay、断电截断、原子提交、schema migration 和幂等约束。

## Decision

Bootstrap application service 只依赖三个窄 Port：

1. `BootstrapStateStore`：保存恢复所需的最小权威状态。
2. `CommissioningListener/CommissioningLink`：接受附近连接并传递可靠有序字节流。
3. `NetworkProvisioning`：提供产品语义上的 stage、confirm、rollback 和状态读取。

Port 不包含 BlueZ、GATT、NetworkManager、SoftAP 或具体存储 API。当前提供：

- `SQLiteBootstrapStateStore`：产品默认 durable adapter；
- `InMemoryBootstrapStateStore`：非持久化测试 adapter；
- `InMemoryCommissioningLink`：TLS/应用协议互操作测试 adapter；
- `InMemoryNetworkProvisioning`：stage/confirm/rollback 状态测试 adapter。

该决策建立 Port 时尚未实现 BlueZ、NetworkManager 或 SoftAP。后续用户要求继续完成
无网链路后，ADR-0003 在不改变这些 Port/权威边界的前提下增加了 BlueZ、pinned TLS
和 NetworkManager concrete adapter；SoftAP 仍未引入。真实硬件结论继续等待 Pi PoC。

SQLite 不是领域前提，也不是外部数据库服务。它只是当前默认的单文件 adapter，负责
事务、唯一约束和断电恢复。SQLite schema v2 删除 `daemon_runs`；daemon 生命周期、
异常和一般诊断由 systemd/journald 负责。Host private key 继续使用独立权限文件，
Wi-Fi profile 仍由网络实现层权威持有，Owner/Workspace 仍由 `eidolon_data` 持有。

## Consequences

- 应用逻辑和测试不需要安装或模拟 BlueZ、NetworkManager。
- 后续真实 adapter 可以替换，不改变 Bootstrap use case。
- durable store 可以替换为正确实现原子性的 snapshot/journal adapter。
- 普通文本日志不能被误用为 claim、Controller 或 operation 的恢复权威。
- 当前 fake adapter 只证明软件状态边界，不代表真实无线通道可靠性。

## Rejected alternatives

- 现在直接实现 BlueZ/NetworkManager：没有实机证据，容易固化错误假设。
- 提前建立通用 transport/plugin 框架：只有一个候选真实实现，抽象依据不足。
- 让 Service 直接调用 SQLite：使存储 schema 渗透到应用层。
- 只用普通日志恢复权威状态：需要重新实现数据库已经解决的事务与恢复问题。
- 完全不持久化：claim、reset epoch、Controller 和未完成 operation 会在重启后丢失。
