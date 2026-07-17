# OpenBiliClaw vNext 文档索引

## 权威文档

- [Architecture](architecture.md)
- [System spec](spec.md)
- [vNext API](modules/vnext-api.md)
- [vNext domain](modules/vnext-domain.md)
- [vNext persistence](modules/vnext-persistence.md)
- [vNext typed AI](modules/vnext-ai.md)
- [vNext sources](modules/vnext-sources.md)
- [Platform source integration](platform-source-integration.md)
- [vNext use cases and jobs](modules/vnext-use-cases-jobs.md)
- [CLI](modules/cli.md)
- [Configuration](modules/config.md)
- [API authentication](modules/api-auth.md)
- [Web client](modules/web.md)
- [Browser extension](modules/extension.md)

## 安装与验证

- [Agent installer contract](agent-install.md)
- [Agent deployment](agent-deployment.md)
- [Docker deployment](docker-deployment.md)
- [Manual E2E](manual-e2e.md)
- [FAQ](faq.md)
- [Changelog](changelog.md)

## 迁移状态

Backend、API、worker、persistence、sources、typed AI、static Web 与 extension 已切到
vNext；浏览器端使用 generated clients、fetch-SSE 和 generic source-task dispatcher。`docs/plans/`、
`docs/specs/`、旧 module 文档和早期 changelog 是历史设计记录，不应作为当前
命令、API 或配置说明；Task 23 会删除剩余不可达 legacy tree。
