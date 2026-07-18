# M1.2 配置系统完善设计

**目标**

完成 `docs/v0.1-todolist.md` 中 `1.2 配置系统完善`：配置文件可自动初始化，`config-show` 在缺失配置时继续展示默认值并附带引导提示，真正依赖敏感配置的命令具备明确校验入口。

**核心决策**

- `load_config()` 继续保持宽松加载，不因缺少 `config.toml` 或 API Key 直接失败。
- 增加配置诊断层，统一产出“是否自动生成模板”“缺失哪些敏感字段”“如何修复”的信息。
- 若项目根目录缺少 `config.toml`，自动从 `config.example.toml` 复制生成。
- `config-show` 使用宽松加载 + 诊断输出；运行态命令使用严格校验入口，在默认 provider 缺少 API Key 或认证方式非法时报明确错误。

**范围**

- 修改 `src/openbiliclaw/config.py` 支持模板初始化、诊断、严格校验
- 修改 `src/openbiliclaw/cli.py` 让 `config-show` 展示提示信息，并让运行态命令在敏感字段缺失时给出清晰错误
- 补充 `tests/test_config.py` 和必要的 CLI 测试
- 完善 `config.example.toml` 注释

**不在范围内**

- 不实现完整的交互式初始化向导
- 不引入新的配置格式或外部依赖
- 不推进 provider fallback、health check 等 `2.x` 范围功能

**风险与边界**

- 本轮只校验 `1.2` 明确要求的敏感字段：默认 provider 对应的 `api_key`，以及 `bilibili.auth_method` 的合法性
- `ollama` 作为本地 provider，不强制要求 `api_key`
- 自动生成模板仅针对默认项目根目录下的 `config.toml`

**验收标准**

- 缺少 `config.toml` 时自动生成模板文件
- `openbiliclaw config-show` 继续显示配置，并提示用户补全 API Key
- 严格校验入口对缺失敏感字段给出明确错误
- 复制 `config.example.toml` 并填入 API Key 后，配置能正确加载
