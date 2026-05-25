# eidolon_admin 文档

跨子项目的约定与参考实现，由 admin 统一维护。

| 文档 | 说明 |
|------|------|
| [config-convention.md](config-convention.md) | agent / hub / memory / channel 配置规范（`settings.yaml` + `.env`） |
| [reference/settings_loader.py](reference/settings_loader.py) | pydantic-settings 加载器参考模板（复制到各子项目，无共享包依赖） |

运行时配置与编排见仓库根目录：

- `config/services.yaml` — 服务注册
- `config/ports.yaml` — dev 栈端口
- `deploy/supervisor/` — supervisord 程序定义
