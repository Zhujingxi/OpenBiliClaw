from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shell_installers_recommend_same_default_llm_provider() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    config_example = _read("config.example.toml")

    expected_default = "Choose your LLM provider (default: deepseek):"
    expected_supported = (
        "Supported: deepseek | openai | gemini | claude | openrouter | ollama | openai_compatible"
    )

    assert expected_default in install_sh
    assert expected_default in install_ps1
    assert expected_supported in install_sh
    assert expected_supported in install_ps1
    assert "DeepSeek:   https://platform.deepseek.com/api_keys" in install_sh
    assert "DeepSeek:   https://platform.deepseek.com/api_keys" in install_ps1
    config_lines = {
        line.strip()
        for line in config_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert 'default_provider = "deepseek"' in config_lines
    assert 'default_provider = "openai"' not in config_lines


def test_install_sh_uses_interactive_auto_init_contract() -> None:
    install_sh = _read("scripts/install.sh")

    assert "--interactive-confirm" in install_sh
    assert "--wait-for-extension-cookie" in install_sh
    assert "docker exec -it openbiliclaw-backend openbiliclaw init" not in install_sh


def test_install_ps1_uses_interactive_auto_init_contract() -> None:
    install_ps1 = _read("scripts/install.ps1")

    assert "--interactive-confirm" in install_ps1
    assert "--wait-for-extension-cookie" in install_ps1
    assert "docker exec -it openbiliclaw-backend openbiliclaw init" not in install_ps1


def test_one_line_installers_default_to_lan_accessible_backend() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    bootstrap = _read("scripts/agent_bootstrap.py")

    assert 'HOST="${HOST:-0.0.0.0}"' in install_sh
    assert "HOST             API host  (default: 0.0.0.0)" in install_sh
    assert "Backend bind address. Default: 0.0.0.0" in install_ps1
    assert "if (-not $ApiHost)    { $ApiHost    = '0.0.0.0' }" in install_ps1
    assert 'DEFAULT_HOST = "0.0.0.0"' in bootstrap
    assert "default: 0.0.0.0" in bootstrap


def test_docs_make_auto_init_primary_for_all_install_channels() -> None:
    readme = _read("README.md")
    docker_doc = _read("docs/docker-deployment.md")
    agent_doc = _read("docs/agent-install.md")

    assert "自动运行 init" in readme
    assert "agent_bootstrap.py --mode docker" in docker_doc
    assert "init_complete" in agent_doc
    assert "手动 fallback" in docker_doc


def test_readmes_explain_macos_first_launch_security_bypass() -> None:
    readme = _read("README.md")
    readme_en = _read("README_EN.md")

    assert "macOS 安全阻挡" in readme
    assert "Control-click" in readme
    assert "隐私与安全性" in readme
    assert "已损坏" in readme
    assert "xattr -dr com.apple.quarantine" in readme
    assert "codesign --force" not in readme

    assert "macOS security blocking" in readme_en
    assert "Control-click" in readme_en
    assert "Privacy & Security" in readme_en
    assert "is damaged and can't be opened" in readme_en
    assert "xattr -dr com.apple.quarantine" in readme_en
    assert "codesign --force" not in readme_en


def test_docker_docs_promote_human_one_line_installer_contract() -> None:
    install_sh = _read("scripts/install.sh")
    docker_doc = _read("docs/docker-deployment.md")

    assert "MODE=docker curl -fsSL .../install.sh | bash" in install_sh
    assert "MODE=docker curl -fsSL https://raw.githubusercontent.com" in docker_doc
    assert "human Docker one-line installer asks the same LLM provider first" in docker_doc
    assert "http://ollama:11434/v1" in docker_doc
    assert "127.0.0.1:8420/api/bilibili/cookie" in docker_doc
    assert "init` 是 v0.3.20+ 的交互式向导" not in docker_doc
    assert "在 Docker 里跑时也会弹一个交互式问题" not in docker_doc
    assert "写到 `[llm.openai]` 同段" not in docker_doc


def test_install_contract_blocks_init_when_ai_service_checks_fail() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    agent_doc = _read("docs/agent-install.md")
    docker_doc = _read("docs/docker-deployment.md")
    cli_doc = _read("docs/modules/cli.md")

    assert "service_check_failed" in install_sh
    assert "service_check_failed" in install_ps1
    assert "AI service check failed before init" in install_sh
    assert "AI service check failed before init" in install_ps1
    assert "status=service_check_failed" in agent_doc
    assert "default LLM provider or embedding service failed" in agent_doc
    assert "service_check_failed" in docker_doc
    assert "service_check_failed" in cli_doc


def test_human_installers_run_full_terminal_wizard_before_init() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    bootstrap = _read("scripts/agent_bootstrap.py")
    agent_doc = _read("docs/agent-install.md")

    assert "--interactive-confirm" in install_sh
    assert "--interactive-confirm" in install_ps1
    assert "human_install_choices_set" in bootstrap
    assert "human one-line installer asks LLM provider first" in agent_doc
    assert "openai_compatible" in bootstrap
    assert "GetPassWarning" in bootstrap


def test_agent_install_llm_menu_numbering_matches_current_options() -> None:
    doc = _read("docs/agent-install.md")

    assert "Present **seven top-level options**" in doc
    assert "Present **three top-level options**" not in doc
    assert 'is folded into "Advanced" further down' not in doc
    assert "**Hardware caveat for option 7 (Ollama)**" in doc
    assert "#### Options 3-6 (OpenAI 官方 / Gemini / Claude / OpenRouter)" in doc
    assert "#### Option 2 (OpenAI 官方 / Gemini / Claude / OpenRouter)" not in doc


def test_cli_module_docs_show_native_model_editor_while_bootstrap_keeps_its_menu() -> None:
    doc = _read("docs/modules/cli.md")
    bootstrap = _read("scripts/agent_bootstrap.py")

    assert "Available chat connection types:" in doc
    assert "openai_compatible: OpenAI-compatible (api_protocol)" in doc
    assert "anthropic_compatible: Anthropic-compatible (api_protocol)" in doc
    assert "codex_oauth: Codex OAuth (oauth)" in doc
    assert "共享 model/维度/阈值/多模态只配置一次" in doc
    assert "不再写 legacy `[llm]`" in doc
    assert "1   DeepSeek 官方 ★默认推荐" not in doc

    # Agent/bootstrap and installer cutover belongs to Task 14; Task 13 only
    # removes the parallel menu from the native terminal CLI setup path.
    assert "HUMAN_LLM_MENU" in bootstrap
    assert "DeepSeek 官方 ★默认推荐" in bootstrap
    assert "User picked OpenAI 官方 (option 2 in agent-install.md)" not in bootstrap


def test_current_setup_embedding_docs_are_configuration_only() -> None:
    readme = _read("README.md")
    readme_en = _read("README_EN.md")
    faq = _read("docs/faq.md")
    agent_doc = _read("docs/agent-install.md")

    readme_section = readme.split("<summary>高级：本地 embedding / Ollama</summary>", 1)[1].split(
        "</details>", 1
    )[0]
    readme_en_section = readme_en.split("<summary>Advanced: local embedding / Ollama</summary>", 1)[
        1
    ].split("</details>", 1)[0]
    faq_section = faq.split("### 不想为 embedding 单独配 API Key？", 1)[1].split(
        "### 手机打不开", 1
    )[0]
    agent_section = agent_doc.split("## Optional: local Ollama as the embedding fallback", 1)[
        1
    ].split("## Hard rules", 1)[0]

    assert "只负责配置" in readme_section
    assert "原生 `[models.embedding]`" in readme_section
    assert "不会安装、启动、下载或访问网络" in readme_section
    assert "向导会自动拉取" not in readme_section

    assert "configuration-only" in readme_en_section
    assert "native `[models.embedding]`" in readme_en_section
    assert "does not install, start, download, or access the network" in readme_en_section
    assert "The wizard pulls" not in readme_en_section

    assert "只负责配置" in faq_section
    assert "原生 `[models.embedding]`" in faq_section
    assert "不会安装、启动、下载或访问网络" in faq_section
    assert "自动拉取" not in faq_section

    assert "configuration-only" in agent_section
    assert "native `[models.embedding]`" in agent_section
    assert "does not install, start, download, probe, or access the network" in agent_section
    assert "writes `[llm.embedding]" not in agent_section


def test_cli_docs_distinguish_guided_embedding_disable_from_item_commands() -> None:
    cli_doc = _read("docs/modules/cli.md")
    section = cli_doc.split("### `openbiliclaw setup-embedding`", 1)[1].split(
        "### `openbiliclaw recommend`", 1
    )[0]
    introduction = section.split("```text", 1)[0]

    assert "add/edit/disable" in introduction
    assert "--kind" not in introduction
    assert "models edit/remove" not in introduction
    assert "add/edit/disable" in section
    assert "`disable` 会清空整个 Embedding route" in section
    assert "`openbiliclaw models remove <id>`" in section
    assert "`openbiliclaw models move <id> --position <1-10>`" in section


def test_backend_tag_workflow_only_updates_aggregate_release() -> None:
    workflow = _read(".github/workflows/release-backend.yml")
    docs_index = _read("docs/index.md")
    extension_doc = _read("docs/modules/extension.md")

    assert "backend-v*" in workflow
    assert "Validate Backend Source Tag" in workflow
    assert "Verify backend version matches source tag" in workflow
    assert "Update aggregate latest release" in workflow
    assert "CHANNEL: backend" in workflow
    assert ".github/scripts/sync-aggregate-release.sh" in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "upload-artifact" not in workflow
    assert "Build backend release archive" not in workflow
    assert "Publish backend release" not in workflow

    assert "`openbiliclaw-v*` 聚合页" in docs_index
    assert "维护者通道仍保留 `extension-v*` / `desktop-v*` / `backend-v*`" in docs_index
    assert "后端源码更新仍只通过 `backend-v*` tag 标记" in extension_doc
    assert "桌面安装包仍由 `desktop-v*` workflow 构建" in extension_doc


def test_ollama_bundled_dockerfile_retries_model_pull_before_verification() -> None:
    dockerfile = _read("docker/ollama-bundled.Dockerfile")
    run_block = dockerfile.split("RUN set -eux;", maxsplit=1)[1].split(
        "COPY docker/seed-bge-m3.sh", maxsplit=1
    )[0]

    assert "start_ollama()" in run_block
    assert 'ollama serve & pid="$!"' in run_block
    assert 'model_blob="/root/.ollama/models/blobs/sha256-${BGE_M3_MODEL_DIGEST}"' in run_block
    assert 'until start_ollama && ollama pull bge-m3 && [ -f "$model_blob" ]; do' in run_block
    assert "attempts=$((attempts + 1))" in run_block
    assert "find /root/.ollama/models" in run_block

    between_pull_and_verify = run_block[
        run_block.index("ollama pull bge-m3") : run_block.index(
            "cp -a /root/.ollama/models/blobs /opt/bge-m3-seed/blobs"
        )
    ]
    assert 'kill "$pid"' not in between_pull_and_verify


def test_installers_can_clone_code_into_existing_packaged_data_root() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    bootstrap = _read("scripts/agent_bootstrap.py")

    assert "is_user_data_only_dir" in install_sh
    assert "clone_into_user_data_root" in install_sh
    assert "Test-UserDataOnlyRoot" in install_ps1
    assert "Clone-IntoUserDataRoot" in install_ps1
    assert "_is_user_data_only_root" in bootstrap
