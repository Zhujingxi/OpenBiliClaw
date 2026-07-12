import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

test("settings page exposes advanced config fields from backend schema", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const expectedIds = [
    "cfgBackendScheme",
    "cfgBackendPort",
    "cfgExtDeviceKey",
    "cfgDataDir",
    "cfgLlmFallbackProvider",
    "cfgEmbeddingFallbackProvider",
    "cfgOpenaiAuthMode",
    "cfgDeepseekReasoning",
    "cfgOpenrouterReferer",
    "cfgOpenrouterTitle",
    "cfgModuleSoulProvider",
    "cfgModuleSoulModel",
    "cfgModuleDiscoveryProvider",
    "cfgModuleDiscoveryModel",
    "cfgModuleRecommendationProvider",
    "cfgModuleRecommendationModel",
    "cfgModuleEvaluationProvider",
    "cfgModuleEvaluationModel",
    "cfgBiliBrowserExecutable",
    "cfgBiliBrowserHeaded",
    "cfgSourcesBrowserCdp",
    "cfgSourcesBrowserHeaded",
    "cfgBilibiliEnabled",
    "cfgXhsEnabled",
    "cfgXhsDailySearchBudget",
    "cfgXhsDailyCreatorBudget",
    "cfgXhsTaskInterval",
    "cfgDouyinEnabled",
    "cfgDouyinCookie",
    "cfgDouyinCookieEnv",
    "cfgTwitterCookie",
    "cfgDouyinDailySearchBudget",
    "cfgDouyinDailyHotBudget",
    "cfgDouyinDailyFeedBudget",
    "cfgDouyinRequestInterval",
    "cfgYoutubeEnabled",
    "cfgYoutubeDailySearchBudget",
    "cfgYoutubeDailyTrendingBudget",
    "cfgYoutubeDailyChannelBudget",
    "cfgYoutubeRequestInterval",
    "cfgYoutubeMinInterval",
    "cfgRedditEnabled",
    "cfgRedditBackend",
    "cfgRedditCookie",
    "cfgRedditModeSearch",
    "cfgRedditModeHot",
    "cfgRedditModeSubreddit",
    "cfgRedditModeRelated",
    "cfgRedditDailySearchBudget",
    "cfgRedditDailyHotBudget",
    "cfgRedditDailySubredditBudget",
    "cfgRedditDailyRelatedBudget",
    "cfgRedditRequestInterval",
    "cfgRedditMinInterval",
    "cfgExtensionDisconnectGrace",
    "cfgRefreshCheckInterval",
    "cfgSignalEventThreshold",
    "cfgTrendingRefreshHours",
    "cfgExploreRefreshHours",
    "cfgDiscoveryLimit",
    "cfgMultimodalEvaluationEnabled",
    "cfgMultimodalBatchSize",
    "cfgMultimodalImageMaxPx",
    "cfgMultimodalImageQuality",
    "cfgMultimodalImageTimeout",
    "cfgProactivePushInterval",
    "cfgSpeculatorIdleInterval",
    "cfgAccountSyncInterval",
    "backendUpdateCurrent",
    "backendUpdateLatest",
    "backendUpdateState",
    "backendUpdateLastCheck",
    "backendUpdateCheck",
    "backendUpdateApply",
    "backendUpdateError",
    "extensionVersionValue",
    "cfgAutoUpdateInterval",
    "cfgPoolShareBilibili",
    "cfgPoolShareXhs",
    "cfgPoolShareDouyin",
    "cfgPoolShareYoutube",
    "cfgPoolShareReddit",
    "cfgSuggestPoolShares",
    "cfgSpeculationInterval",
    "cfgSpeculationTtl",
    "cfgSpeculationCooldown",
    "cfgSpeculationThreshold",
    "cfgSpeculationMaxActive",
    "cfgSpeculationMaxPrimary",
    "cfgSpeculationMaxSecondary",
    "cfgStorageDbPath",
    "cfgLogFileLevel",
    "cfgLogPath",
    "cfgLogMaxFileSize",
    "cfgLogBackupCount",
    "cfgLogAggregateBudget",
    "cfgLogUnmanagedTruncate",
    "cfgLogUnmanagedMaxAge",
  ];

  for (const id of expectedIds) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }
  assert.doesNotMatch(popupHtml, /cfgExtLoginPassword|扩展登录密码/);
  assert.doesNotMatch(popupJs, /obc_auth_password|obc_auth_token/);
  assert.doesNotMatch(popupHtml, /id="cfgDiscoveryCron"/);
  assert.doesNotMatch(popupJs, /discovery_cron:\s*getVal\("cfgDiscoveryCron"\)/);
  assert.match(
    popupJs,
    /setVal\("cfgRefreshCheckInterval", cfg\.scheduler\?\.refresh_check_interval_seconds\)/,
  );
  assert.match(
    popupJs,
    /refresh_check_interval_seconds: getInt\("cfgRefreshCheckInterval", 60\)/,
  );
  assert.match(popupJs, /function formatBackendUpdateError/);
  assert.match(popupJs, /github_rate_limited:\s*"GitHub API 限流，请稍后再试"/);
});

test("settings source tab separates every platform into its own block", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const sourcesPanel =
    popupHtml.match(/<div id="settingsPanelSources"[\s\S]*?<div id="settingsPanelGeneral"/)?.[0] ??
    "";

  for (const sourceKey of [
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
    "browser",
    "pool",
  ]) {
    assert.match(
      sourcesPanel,
      new RegExp(`data-source-card="${sourceKey}"`),
      `${sourceKey} source card should exist`,
    );
  }
  assert.match(sourcesPanel, /id="cfgBilibiliEnabled"/);
  assert.match(sourcesPanel, />启用 Bilibili discovery</);
  assert.match(sourcesPanel, />调试：B 站登录时显示浏览器窗口</);
  assert.match(popupJs, /bilibiliEnabled\.checked = cfg\.sources\?\.bilibili\?\.enabled !== false/);
  assert.match(popupJs, /xhsEnabled\.checked = cfg\.sources\?\.xiaohongshu\?\.enabled === true/);
  assert.match(popupJs, /bilibili:\s*\{\s*enabled: checked\("cfgBilibiliEnabled", true\)/);
  assert.match(popupJs, /xiaohongshu:\s*\{\s*enabled: checked\("cfgXhsEnabled"\)/);
});

test("settings logging tab edits a single full log path", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const loggingPanel =
    popupHtml.match(/<div id="settingsPanelLogging"[\s\S]*?<p class="settings-note">/)?.[0] ?? "";

  assert.match(loggingPanel, /<label for="cfgLogPath">完整日志路径<\/label>/);
  assert.match(loggingPanel, /id="cfgLogPath"[^>]*placeholder="logs\/openbiliclaw\.log"/);
  assert.doesNotMatch(loggingPanel, /for="cfgLogDirectory"/);
  assert.doesNotMatch(loggingPanel, /for="cfgLogFilename"/);
  assert.match(popupJs, /setVal\("cfgLogPath", resolveLogPathFromConfig\(cfg\.logging\)\)/);
  assert.match(
    popupJs,
    /const logPath = splitLogPath\(getVal\("cfgLogPath"\), state\.runtimeConfig\?\.logging\)/,
  );
  assert.match(popupJs, /directory: logPath\.directory/);
  assert.match(popupJs, /filename: logPath\.filename/);
});

test("settings page organizes backend config into tabs", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const tabsMarkup = popupHtml.match(/<div class="settings-tabs"[\s\S]*?<\/div>/)?.[0] ?? "";
  const panelNames = ["models", "sources", "scheduler", "general", "logging"];

  assert.match(tabsMarkup, /role="tablist"/);
  for (const [id, label] of [
    ["settingsTabModels", "模型"],
    ["settingsTabSources", "平台源"],
    ["settingsTabScheduler", "调度"],
    ["settingsTabGeneral", "通用"],
    ["settingsTabLogging", "日志"],
  ]) {
    assert.match(tabsMarkup, new RegExp(`id="${id}"`));
    assert.match(tabsMarkup, new RegExp(`>${label}<`));
    assert.match(popupJs, new RegExp(`"${id}"`));
  }
  for (const name of panelNames) {
    assert.match(popupHtml, new RegExp(`data-settings-panel="${name}"`));
    assert.match(popupJs, new RegExp(`"${name}"`));
  }
});

test("settings page exposes backend-only update controls and plugin release fallback", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /版本与更新/);
  assert.match(popupHtml, /id="cfgAutoUpdate"/);
  assert.match(popupHtml, /自动更新后端/);
  assert.match(popupHtml, /此开关不会更新浏览器插件/);
  assert.match(popupHtml, /id="backendUpdateDownload"/);
  assert.match(popupHtml, /href="https:\/\/github\.com\/whiteguo233\/OpenBiliClaw\/releases"/);
  assert.match(popupJs, /fetchUpdateStatus/);
  assert.match(popupJs, /checkBackendUpdate/);
  assert.match(popupJs, /applyBackendUpdate/);
  assert.match(popupJs, /install_mode/);
  assert.match(popupJs, /backendUpdateDownload/);
  assert.match(popupJs, /releases\/tag/);
  assert.doesNotMatch(popupJs, /extension_auto_apply|extension_update_available/);
});

test("settings backend update apply failures show backend reason and refresh status", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /dirty_worktree:\s*"代码目录有未提交改动，更新被阻止"/);
  assert.match(popupJs, /untrusted_remote:\s*"git 远端不在允许列表，更新被阻止（可在后端日志查看实际远端地址）"/);
  assert.match(popupJs, /docker_install_mode:\s*"Docker 安装通过拉取新镜像升级，无法就地自更新"/);
  assert.match(popupJs, /branch_not_fast_forwardable:\s*"本地代码与发布版本分叉，无法快进更新"/);
  assert.match(popupJs, /missing_target_tag:\s*"远端未找到目标版本标签"/);

  assert.match(popupJs, /const details = error\?\.details/);
  assert.match(popupJs, /renderBackendUpdateStatus\(details\)/);
  assert.match(popupJs, /后端更新未能开始：/);
  assert.match(popupJs, /await loadBackendUpdateStatus\(\)/);
});

test("settings backend update actions require explicit install branch", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /const isGitInstall = installMode === "git"/);
  assert.match(popupJs, /const isFrozenInstall = installMode === "frozen"/);
  assert.match(popupJs, /const isDockerInstall = installMode === "docker"/);
  assert.match(popupJs, /docker compose pull && docker compose up -d/);
  assert.match(
    popupJs,
    /const isDesktopInstallerUpdate = String\(backend\.latest_tag \|\| ""\)\.startsWith\("desktop-v"\)/,
  );
  assert.match(popupJs, /isGitInstall &&\s*backend\.state === "update_available"/);
  assert.match(
    popupJs,
    /\(isFrozenInstall \|\| isDesktopInstallerUpdate\) && backend\.state === "update_available"/,
  );
  assert.doesNotMatch(popupJs, /!unsupportedInstall && backend\.state === "update_available"/);
});

test("settings page round-trips YouTube source budgets", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(
    popupJs,
    /setVal\("cfgYoutubeDailySearchBudget", cfg\.sources\?\.youtube\?\.daily_search_budget\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeDailyTrendingBudget", cfg\.sources\?\.youtube\?\.daily_trending_budget\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeDailyChannelBudget", cfg\.sources\?\.youtube\?\.daily_channel_budget\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeRequestInterval", cfg\.sources\?\.youtube\?\.request_interval_seconds\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeMinInterval", cfg\.sources\?\.youtube\?\.min_interval_minutes\)/,
  );
  // Empty-field fallbacks must mirror the backend dataclass defaults
  // (budgets: 0 = uncapped) so popup and web settings write the same values.
  assert.match(popupJs, /daily_search_budget: getInt\("cfgYoutubeDailySearchBudget", 0\)/);
  assert.match(popupJs, /daily_trending_budget: getInt\("cfgYoutubeDailyTrendingBudget", 0\)/);
  assert.match(popupJs, /daily_channel_budget: getInt\("cfgYoutubeDailyChannelBudget", 0\)/);
  assert.match(popupJs, /request_interval_seconds: getInt\("cfgYoutubeRequestInterval", 2\)/);
  assert.match(popupJs, /min_interval_minutes: getInt\("cfgYoutubeMinInterval", 60\)/);

  for (const id of [
    "cfgYoutubeDailySearchBudget",
    "cfgYoutubeDailyTrendingBudget",
    "cfgYoutubeDailyChannelBudget",
    "cfgYoutubeRequestInterval",
    "cfgYoutubeMinInterval",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`));
  }
});

test("settings page round-trips Zhihu discovery source modes", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgZhihuModeSearch",
    "cfgZhihuModeHot",
    "cfgZhihuModeFeed",
    "cfgZhihuModeCreator",
    "cfgZhihuModeRelated",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /setZhihuSourceModes\(cfg\.sources\?\.zhihu\?\.source_modes\)/);
  assert.match(popupJs, /source_modes: collectZhihuSourceModes\(\)/);
});

test("settings page round-trips Reddit discovery config", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgRedditEnabled",
    "cfgRedditBackend",
    "cfgRedditCookie",
    "cfgRedditModeSearch",
    "cfgRedditModeHot",
    "cfgRedditModeSubreddit",
    "cfgRedditModeRelated",
    "cfgRedditDailySearchBudget",
    "cfgRedditDailyHotBudget",
    "cfgRedditDailySubredditBudget",
    "cfgRedditDailyRelatedBudget",
    "cfgRedditRequestInterval",
    "cfgRedditMinInterval",
    "cfgPoolShareReddit",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /setRedditSourceModes\(cfg\.sources\?\.reddit\?\.source_modes\)/);
  assert.match(popupJs, /source_modes: collectRedditSourceModes\(\)/);
  assert.match(popupJs, /backend: getVal\("cfgRedditBackend"\) \|\| "rdt"/);
  assert.match(popupJs, /daily_search_budget: getInt\("cfgRedditDailySearchBudget", 300\)/);
  assert.match(popupJs, /reddit: getInt\("cfgPoolShareReddit", 1\)/);
  assert.match(popupJs, /reddit: checked\("cfgRedditEnabled"\)/);
  assert.match(popupJs, /if \(shares\.reddit !== undefined\) setVal\("cfgPoolShareReddit", shares\.reddit\)/);
});

test("settings page round-trips multimodal discovery evaluation controls", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgCandidateEvalConcurrency",
    "cfgMultimodalEvaluationEnabled",
    "cfgMultimodalBatchSize",
    "cfgMultimodalImageMaxPx",
    "cfgMultimodalImageQuality",
    "cfgMultimodalImageTimeout",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(
    popupJs,
    /setVal\("cfgCandidateEvalConcurrency", cfg\.discovery\?\.candidate_eval_concurrency\)/,
  );
  assert.match(
    popupJs,
    /multimodalEvaluation\.checked = cfg\.discovery\?\.multimodal_evaluation_enabled === true/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalBatchSize", cfg\.discovery\?\.multimodal_batch_size\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalImageMaxPx", cfg\.discovery\?\.multimodal_image_max_px\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalImageQuality", cfg\.discovery\?\.multimodal_image_quality\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalImageTimeout", cfg\.discovery\?\.multimodal_image_timeout_seconds\)/,
  );
  assert.match(popupJs, /multimodal_evaluation_enabled: checked\("cfgMultimodalEvaluationEnabled"\)/);
  assert.match(
    popupJs,
    /candidate_eval_concurrency: getInt\("cfgCandidateEvalConcurrency", 3\)/,
  );
  assert.match(popupJs, /multimodal_batch_size: getInt\("cfgMultimodalBatchSize", 8\)/);
  assert.match(popupJs, /multimodal_image_max_px: getInt\("cfgMultimodalImageMaxPx", 384\)/);
  assert.match(popupJs, /multimodal_image_quality: getInt\("cfgMultimodalImageQuality", 72\)/);
  assert.match(
    popupJs,
    /multimodal_image_timeout_seconds: getInt\("cfgMultimodalImageTimeout", 6\)/,
  );
});

test("settings page round-trips douyin and x cookies like the bilibili card", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Plaintext cookie textareas, same shape as the bilibili card.
  assert.match(popupHtml, /<textarea id="cfgBiliCookie"/);
  assert.match(popupHtml, /<textarea id="cfgDouyinCookie"/);
  assert.match(popupHtml, /<textarea id="cfgTwitterCookie"/);
  assert.match(popupHtml, /<textarea id="cfgRedditCookie"/);

  assert.match(popupJs, /setVal\("cfgDouyinCookie", cfg\.sources\?\.douyin\?\.cookie\)/);
  assert.match(popupJs, /setVal\("cfgTwitterCookie", cfg\.sources\?\.twitter\?\.cookie\)/);

  // An empty textarea is omitted from the payload so saving the form can
  // never wipe a synced cookie (bilibili included).
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgBiliCookie"\) \? \{ cookie: getVal\("cfgBiliCookie"\) \} : \{\}\)/,
  );
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgDouyinCookie"\) \? \{ cookie: getVal\("cfgDouyinCookie"\) \} : \{\}\)/,
  );
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgTwitterCookie"\) \? \{ cookie: getVal\("cfgTwitterCookie"\) \} : \{\}\)/,
  );
  // Reddit has no config-side cookie echo (GET /api/config carries no
  // sources.reddit.cookie) — paste-only, routed to rdt-cli's store.
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgRedditCookie"\) \? \{ cookie: getVal\("cfgRedditCookie"\) \} : \{\}\)/,
  );
});

test("settings page round-trips OpenAI auth mode", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgOpenaiAuthMode"/);
  assert.match(popupHtml, /<option value="api_key">API Key<\/option>/);
  assert.match(popupHtml, /<option value="codex_oauth">Codex OAuth<\/option>/);
  assert.match(
    popupJs,
    /setVal\("cfgOpenaiAuthMode", cfg\.llm\?\.openai\?\.auth_mode \|\| "api_key"\)/,
  );
  assert.match(popupJs, /auth_mode: getVal\("cfgOpenaiAuthMode"\) \|\| "api_key"/);
});

test("settings page round-trips explicit LLM and embedding fallback providers", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgLlmFallbackProvider"/);
  assert.match(popupHtml, /id="cfgEmbeddingFallbackProvider"/);
  assert.doesNotMatch(popupHtml, /id="cfgLlmFallbackEnabled"/);
  assert.doesNotMatch(popupHtml, /id="cfgEmbeddingFallbackEnabled"/);
  assert.match(popupJs, /setVal\("cfgLlmFallbackProvider", cfg\.llm\?\.fallback_provider\)/);
  assert.match(
    popupJs,
    /setVal\("cfgEmbeddingFallbackProvider", cfg\.llm\?\.embedding\?\.fallback_provider\)/,
  );
  assert.match(popupJs, /const llmFallbackProvider = getVal\("cfgLlmFallbackProvider"\)/);
  assert.match(popupJs, /fallback_provider: llmFallbackProvider/);
  assert.match(
    popupJs,
    /const embeddingFallbackProvider = getVal\("cfgEmbeddingFallbackProvider"\)/,
  );
  assert.match(
    popupJs,
    /fallback_provider: embeddingFallbackProvider/,
  );
});

test("settings page exposes and wires LLM and embedding probe buttons", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgProbeLlm"/);
  assert.match(popupHtml, /id="cfgProbeEmbedding"/);
  assert.match(popupHtml, /id="cfgProbeLlmStatus"/);
  assert.match(popupHtml, /id="cfgProbeEmbeddingStatus"/);
  assert.match(popupHtml, /id="cfgProbeLlmFallback"/);
  assert.match(popupHtml, /id="cfgProbeLlmFallbackStatus"/);
  assert.match(popupJs, /probeConfigService\("llm", collectForm\(\)\)/);
  assert.match(popupJs, /probeConfigService\("embedding", collectForm\(\)\)/);
  assert.match(popupJs, /probeConfigService\("llm_fallback", collectForm\(\)\)/);
  assert.match(popupJs, /function renderProbeResult/);
});

test("settings general tab exposes and wires the network proxy field (aligned with desktop web)", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Field + probe control + copy stating CN requests stay direct.
  assert.match(popupHtml, /id="cfgNetworkProxy"/);
  assert.match(popupHtml, /id="cfgProbeNetworkProxy"/);
  assert.match(popupHtml, /id="cfgProbeNetworkProxyStatus"/);
  assert.match(popupHtml, /海外/);
  assert.match(popupHtml, /国内请求始终直连/);

  // Restore from config.network.proxy, collect into payload.network, probe wired.
  assert.match(popupJs, /setVal\("cfgNetworkProxy", cfg\.network\?\.proxy \|\| ""\)/);
  assert.match(popupJs, /network:\s*\{\s*proxy: getVal\("cfgNetworkProxy"\),/);
  assert.match(popupJs, /probeConfigService\("network_proxy", \{ network: \{ proxy \} \}\)/);
  assert.match(popupJs, /function runNetworkProxyConfigProbe/);
});

test("settings page guards against a same-name LLM fallback (aligned with desktop web)", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Inline warning for legacy same-name configs (data is never silently reset).
  assert.match(popupHtml, /id="cfgLlmFallbackSameWarning"/);
  assert.match(popupHtml, /备选与默认 Provider 相同时永远不会生效/);
  // The sync disables the same-name option and runs on hydration + both selects.
  assert.match(popupJs, /function syncLlmFallbackSameState/);
  assert.match(popupJs, /option\.value === mainValue/);
  const syncCalls = popupJs.match(/syncLlmFallbackSameState\(\)/g) ?? [];
  assert.ok(syncCalls.length >= 2, "sync must run from hydration and the provider change handler");
});

test("settings page placeholders match config example defaults", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const expectedDefaults = [
    ["cfgOpenaiModel", "gpt-5-nano"],
    ["cfgClaudeModel", "claude-sonnet-4-6"],
    ["cfgOllamaModel", "qwen2.5:7b"],
    ["cfgOllamaBaseUrl", "http://localhost:11434/v1"],
    ["cfgOpenrouterModel", "openai/gpt-5-nano"],
  ];

  for (const [id, placeholder] of expectedDefaults) {
    assert.match(
      popupHtml,
      new RegExp(`id="${id}"[^>]*placeholder="${placeholder.replaceAll("*", "\\*")}"`),
      `${id} placeholder should match config.example.toml default`,
    );
  }
});

test("source-share suggestion button uses settings-scope helpers and form switches", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const bindSettingsBlock =
    popupJs.match(/function bindSettings\(\) \{[\s\S]*?\nasync function initializePopup/)?.[0] ?? "";
  const populateFormIndex = bindSettingsBlock.indexOf("function populateForm");
  const collectFormIndex = bindSettingsBlock.indexOf("function collectForm");
  const populateFormBlock = bindSettingsBlock.slice(populateFormIndex, collectFormIndex);
  const beforePopulate = bindSettingsBlock.slice(0, populateFormIndex);
  const suggestionBlock =
    bindSettingsBlock.match(/suggestBtn\.addEventListener\("click"[\s\S]*?\n  \}\n\n  saveBtn/)?.[0] ?? "";

  assert.match(beforePopulate, /const setVal = \(id, val\) => \{/);
  assert.doesNotMatch(populateFormBlock, /const setVal = \(id, val\) => \{/);
  assert.match(suggestionBlock, /fetchSourceShareSuggestion\(\{/);
  assert.match(suggestionBlock, /enabled_sources:\s*\{/);
  assert.match(suggestionBlock, /bilibili:\s*checked\("cfgBilibiliEnabled", true\)/);
  assert.match(suggestionBlock, /xiaohongshu:\s*checked\("cfgXhsEnabled"\)/);
  assert.match(suggestionBlock, /youtube:\s*checked\("cfgYoutubeEnabled"\)/);
  assert.match(suggestionBlock, /configured_shares:\s*\{/);
});

test("settings save renders structured config validation errors inline", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const bindSettingsBlock =
    popupJs.match(/function bindSettings\(\) \{[\s\S]*?\nasync function initializePopup/)?.[0] ?? "";
  const saveBlock =
    popupJs.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0] ?? "";
  const structuredErrorBlock =
    bindSettingsBlock.match(/function renderStructuredConfigError[\s\S]*?\n  \}/)?.[0] ?? "";

  assert.match(structuredErrorBlock, /err\.details\?\.config\?\.issues/);
  assert.match(structuredErrorBlock, /applyRuntimeConfig\(err\.details\.config\)/);
  assert.match(structuredErrorBlock, /renderIssues\(err\.details\.config\.issues\)/);
  assert.match(structuredErrorBlock, /配置未保存，请先修正高亮问题。/);
  assert.match(structuredErrorBlock, /showToast\([^)]*,\s*"error"\)/);
  assert.match(saveBlock, /renderStructuredConfigError\(err\)/);
});

test("settings save renders timeout warning before structured or generic errors", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const saveBlock =
    popupJs.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0] ?? "";

  const abortIndex = saveBlock.indexOf('err?.name === "AbortError"');
  const structuredIndex = saveBlock.indexOf("renderStructuredConfigError(err)");
  const genericIndex = saveBlock.indexOf("保存失败");
  const successIndex = saveBlock.indexOf("applyRuntimeConfig(result.config)");

  assert.notEqual(abortIndex, -1, "save handler should special-case AbortError");
  assert.match(saveBlock, /后端处理超时[\s\S]*保存请求可能已写入[\s\S]*后台/);
  assert.match(saveBlock, /showToast\([\s\S]*"warning"[\s\S]*\)/);
  assert.ok(abortIndex < structuredIndex, "AbortError should be handled before structured errors");
  assert.ok(abortIndex < genericIndex, "AbortError should not fall through to generic error toast");
  assert.ok(abortIndex > successIndex, "AbortError branch should wrap the updateConfig call");
  assert.match(saveBlock, /return;/);
  assert.match(saveBlock, /finally[\s\S]*saveBtn\.disabled = false/);
  assert.match(saveBlock, /finally[\s\S]*setSaveButtonMode/);
});

test("settings page wires offline cache and degraded-mode banners", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of ["cfgBannerOffline", "cfgBannerDegraded", "cfgBannerNoCache"]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /readCachedConfigSnapshot/);
  assert.match(popupJs, /cached_at/);
  assert.match(popupJs, /后端不可达且没有缓存配置/);
  assert.match(popupJs, /renderDegradedBanner\(cfg\)/);
  assert.match(popupJs, /restart_required/);
  assert.match(popupJs, /保存并提示重启/);
});

test("settings page shows the budget-semantics hint for every per-source budget group", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

  // The hint must match the desktop web wording so users learn budget is a
  // per-day cap, not an on/off toggle.
  const baseNote =
    "预算 = 每日任务次数上限，不是开关；填 1 表示每天只允许 1 次。0 或留空 = 不限。";
  const redditNote =
    "预算 = 每日任务次数上限，不是开关；填 1 表示每天只允许 1 次。0 或留空 = 不限（Reddit 各分支默认 300）。";

  // Every source card that has a daily budget input must carry a note.
  const budgetCards = ["xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit"];
  for (const card of budgetCards) {
    const start = popupHtml.indexOf(`data-source-card="${card}"`);
    assert.ok(start >= 0, `source card ${card} should exist`);
    const rest = popupHtml.slice(start);
    const end = rest.indexOf("settings-source-card", 1);
    const cardHtml = end >= 0 ? rest.slice(0, end) : rest;
    assert.match(
      cardHtml,
      /class="settings-hint" data-budget-note>预算 = 每日任务次数上限/,
      `${card} card should carry the budget-semantics hint`,
    );
  }

  // Reddit keeps its 300-default clarification.
  assert.ok(popupHtml.includes(redditNote), "reddit note should mention the 300 default");
  // The other five use the base wording.
  const baseCount = popupHtml.split(baseNote).length - 1;
  assert.ok(baseCount >= 5, `expected >=5 base budget notes, got ${baseCount}`);
});

test("settings page wires the keyword generation mode selector (matches desktop web)", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Select + the three options — values/labels byte-identical to desktop web.
  assert.match(popupHtml, /id="cfgKeywordGenerationMode"/);
  assert.match(popupHtml, /<option value="legacy">经典<\/option>/);
  assert.match(popupHtml, /<option value="hybrid">混合<\/option>/);
  assert.match(popupHtml, /<option value="inspiration">灵感<\/option>/);
  // Cost hint conveys 混合最贵.
  assert.match(popupHtml, /混合最贵/);

  // Load fills the select from the derived discovery field.
  assert.match(
    popupJs,
    /setVal\("cfgKeywordGenerationMode", cfg\.discovery\?\.keyword_generation_mode \|\| "legacy"\)/,
  );

  // Save collects it into the discovery payload AFTER the snapshot spread, so a
  // loaded value never clobbers the user's live selection (R2 spread-order).
  const saveKey = 'keyword_generation_mode: getVal("cfgKeywordGenerationMode")';
  assert.ok(popupJs.includes(saveKey), "save key should be present");
  const spread = "...(state.runtimeConfig?.discovery || {})";
  assert.ok(
    popupJs.indexOf(spread) !== -1 && popupJs.indexOf(spread) < popupJs.indexOf(saveKey),
    "keyword_generation_mode must be written after the discovery spread",
  );
});

test("settings source status labels distinguish local readiness", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /ready: "凭据已就绪"/);
  assert.match(popupJs, /unverified: "状态待验证"/);
  assert.match(popupJs, /login_required: "需要登录"/);
  assert.match(popupJs, /error: "检查失败"/);
});
