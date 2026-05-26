# Eidolon 子项目配置约定

本文档是 agent / hub / memory / channel 等 Python 服务的统一配置规范，由 **eidolon_admin** 维护。各子仓库**无 Python 包依赖**，通过复制 [`docs/reference/settings_loader.py`](reference/settings_loader.py) 对齐行为。

相关 admin 侧约定：

- 服务注册与 UI 集成 — [`config/services.yaml`](../config/services.yaml)
- dev 栈端口 — [`config/ports.yaml`](../config/ports.yaml)
- supervisord 编排 — [`deploy/supervisor/`](../deploy/supervisor/)

## 运行时依赖

进程只认两类来源：

1. **`settings.yaml`** — 结构化配置（端口、URL、provider 选择等），**禁止**明文密钥
2. **环境变量** — 密钥与可选覆盖项

`config/.env` 是环境变量的**本地载体**，不是第三套配置语义。生产可由 K8s/systemd 注入 env，无需磁盘上的 `.env`（supervisord 可不使用 `with-env.sh`）。

## 目录布局

### 标准（agent、hub、channel）

```
<repo>/
  config/
    settings.yaml           # gitignore
    settings.example.yaml   # 模板
    .env                    # gitignore
    .env.example            # 模板
  deploy/dev/init.sh
```

### memory（与其它服务相同：仓库根 `config/`）

```
eidolon_memory/
  config/
    settings.yaml
    settings.example.yaml
    .env
    .env.example
    users.yaml              # 运维数据，独立热加载
```

`eidolon/memory/config/` 仅保留 Python 加载器与 `users.yaml.tpl` 等包内资源；运行时 YAML/env 在仓库根 `config/`。

## 环境变量

| 变量 | 含义 |
|------|------|
| `EIDOLON_ROOT` | monorepo 根（默认 `~/ai/eidolon`） |
| `EIDOLON_<SERVICE>_SETTINGS_YAML` | 覆盖主 YAML 路径 |
| `EIDOLON_<SERVICE>_ENV_FILE` | 覆盖 dotenv 路径 |
| `EIDOLON_<SERVICE>_LOG_DIR` / `_RUN_DIR` | 运行时目录（env 优先于 yaml） |

`<SERVICE>`：`AGENT`、`HUB`、`MEMORY`、`CHANNEL`。

## pydantic-settings 源优先级（高 → 低）

1. `init` kwargs（测试）
2. shell / 容器环境变量
3. `config/.env`（`DotEnvSettingsSource`）
4. `settings.yaml`（自定义 `YamlSettingsSource`）
5. 字段默认值

缺失 `settings.yaml` → **启动失败**（须先 `./deploy/dev/init.sh`）。核心服务缺失必填 env / `.env` → startup fail-fast。

## supervisord（dev）

所有 Python 程序由 **eidolon_admin** 的 supervisord 启动：

```ini
command=.../eidolon_admin/deploy/supervisor/wrappers/with-env.sh <repo_root> config/.env -- <entrypoint> ...
```

- 所有 Python 服务：`config/.env`
- `with-env.sh`：env 文件不存在 → **exit 1**
- **子项目 `config/settings.yaml` 是绑定端口的唯一真实源**（含栈内互连 URL、NATS 等）
- [`config/ports.yaml`](../config/ports.yaml) 仅供 **admin** 聚合展示与健康检查：`run_all.sh start` 执行 `ports collect`（从子项目**读取**并更新 ports.yaml），**不会**改写任何子项目配置
- `ports export` 将 ports.yaml 导出为 `EIDOLON_*` 环境变量（supervisord、`services.yaml` 展开）

## 密钥与 YAML 占位符

- `settings.yaml` 中声明密钥字段时，**占位符值 = 环境变量名**（如 `livekit.api_key: LIVEKIT_API_KEY`），与 `config/.env` / `.env.example` 中的键一致
- 实际密钥只写在 `config/.env`（或容器环境变量）；`.env.example` 中建议 `KEY=KEY` 自引用占位，便于复制后替换
- 禁止在 yaml 中写入真实密钥（非空且不是合法 `UPPER_SNAKE_CASE` 占位符名）
- pydantic 服务：YAML 校验拒绝非空 `api_key` / `secret` / `token`；可用 `SecretStr` + `Field(validation_alias="ENV_NAME")`
- 日志 `model_dump()` 须脱敏

## 前端例外

- **client-web**：`.env.local` + `NEXT_PUBLIC_*`（Next.js）
- 与 hub `settings.yaml` 中 public URL 的对照见 `eidolon_client_web/README` 或 admin UI Configs 页

## Admin 注册

[`config/services.yaml`](../config/services.yaml) 每个 Python 服务声明：

- `settings` → `format: yaml`
- `env` → `format: dotenv`

路径使用 `$EIDOLON_ROOT/...`。
