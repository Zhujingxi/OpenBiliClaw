# M1.3 日志系统设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `1.3 日志系统`：提供统一日志初始化，支持 Rich 控制台输出与文件日志落地，并允许通过配置文件或命令行覆盖日志级别。

**核心决策**

- 新增独立日志模块，集中完成 logger、handler、formatter 初始化
- 在 `config.py` 中增加 `LoggingConfig`
- CLI 在进入命令前统一初始化日志系统
- 控制台使用 Rich 友好渲染，文件日志写入 `logs/openbiliclaw.log`
- 命令行的 `--log-level` 优先级高于配置文件

**范围**

- 新增 `src/openbiliclaw/logging_setup.py`
- 修改 `src/openbiliclaw/config.py` 支持 `[logging]`
- 修改 `src/openbiliclaw/cli.py` 增加全局日志级别选项并在命令入口初始化日志
- 更新 `config.example.toml`
- 增加日志初始化与 CLI 覆盖行为测试

**不在范围内**

- 不引入复杂的 `dictConfig`
- 不增加按日期轮转、JSON 日志、远程日志等高级能力
- 不重构已有模块 logger 命名方式

**设计要点**

- 根 logger 只在初始化时配置一次，重复调用时清理旧 handler，避免测试和 CLI 多次初始化时重复输出
- 控制台 handler 默认级别使用 `INFO`
- 文件 handler 默认级别使用 `DEBUG`
- 日志目录从配置解析为项目根目录下绝对路径
- CLI 全局 callback 负责读取配置并初始化日志，这样所有命令共享同一入口

**验收标准**

- 运行任意 CLI 命令时，终端有 Rich 风格日志输出
- 自动创建 `logs/` 目录和日志文件
- 配置文件中的 `[logging]` 可控制目录和级别
- `--log-level DEBUG` 能覆盖配置文件中的控制台日志级别
