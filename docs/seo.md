# OpenBiliClaw 主页 SEO

主页是 `docs/index.html`，通过 GitHub Pages 部署到
<https://whiteguo233.github.io/OpenBiliClaw/>。

本文只覆盖**搜索引擎提交**与**长期维护**。技术上的 meta / OG /
Twitter Card / JSON-LD / `sitemap.xml` / `robots.txt` 已经在
`docs/` 里全部就绪，部署一次即可生效。

> 站点托管在 GitHub Pages 的**子路径**
> `whiteguo233.github.io/OpenBiliClaw/` 下，根目录
> `whiteguo233.github.io/robots.txt` 不归本仓库管。所以本仓库的
> `docs/robots.txt` 只是冗余备份；真正起作用的是把 sitemap
> **手动提交到 Search Console / Bing Webmaster**。

---

## 提交到 Google Search Console（必做，10 分钟）

1. 打开 <https://search.google.com/search-console>，登录用 Pages 那个 GitHub 账号。
2. 左上「Add property」→ 选 **URL prefix**，填：
   ```
   https://whiteguo233.github.io/OpenBiliClaw/
   ```
   （末尾斜杠保留。`Domain` 方式需要 DNS，GitHub Pages 子路径不能用。）
3. 验证方式选 **HTML tag**，复制它给的
   `<meta name="google-site-verification" content="...">` 的 `content` 值。
4. 打开 `docs/index.html`，找到这段：
   ```html
   <!-- <meta name="google-site-verification" content="PASTE_VALUE_HERE" /> -->
   ```
   去掉两侧 `<!--` `-->`，把 `PASTE_VALUE_HERE` 替换成第 3 步那个值。
5. 提交、push、等 Pages 重新部署（一般 < 1 分钟），回 GSC 点 **Verify**。
6. 验证通过后，左侧 **Sitemaps** → 填：
   ```
   sitemap.xml
   ```
   （会被拼成 `https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml`），提交。
7. 想立即让 Google 抓首页：顶部搜索框输入
   `https://whiteguo233.github.io/OpenBiliClaw/` →
   **URL Inspection** → **Request Indexing**。

> 验证 meta 一旦提交不能删；删了 GSC 会自动取消验证，sitemap 数据会
> 跟着一起断掉。日后想换验证方式（例如改成 DNS）请先加新方式再去旧的。

## 提交到 Bing Webmaster Tools（推荐，2 分钟）

最快路径：从 GSC 一键导入。

1. 打开 <https://www.bing.com/webmasters>，用 Microsoft 账号登录。
2. **Import from Google Search Console** → 授权 → 选刚才那个 property → 导入。
3. Bing 会沿用 GSC 的验证记录、抓取设置和 sitemap，不需要重复贴 meta。

如果不想用 Google 一键导入，就独立验证：

1. **Add a site** → 填 `https://whiteguo233.github.io/OpenBiliClaw/`。
2. 选 **HTML Meta Tag**，把 `msvalidate.01` 那条 meta 同样在
   `docs/index.html` 取消注释、粘贴值、push。
3. Bing 验证通过后，左侧 **Sitemaps** → 提交
   `https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml`。

## 国内搜索（可选）

百度 / 必应（国内版）/ Yandex 都支持类似流程，并且
`docs/index.html` 已经预留了对应的 meta 占位：

- 百度站长平台：<https://ziyuan.baidu.com/> → 取
  `baidu-site-verification` 的 content 填进对应注释行
- Yandex Webmaster：<https://webmaster.yandex.com/> → 取
  `yandex-verification` 的 content 填进对应注释行

> 注意：百度对 `github.io` 子路径的抓取率较低，是否要做看自己取舍。

---

## 部署后的快速自检清单

部署到 Pages 之后跑一遍这几个 URL，确认收录前提没问题：

- 主页：<https://whiteguo233.github.io/OpenBiliClaw/>
- Sitemap：<https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml>
- 富片段调试：<https://search.google.com/test/rich-results?url=https%3A%2F%2Fwhiteguo233.github.io%2FOpenBiliClaw%2F>
- Twitter / X 卡片预览：<https://cards-dev.twitter.com/validator>（粘 URL）
- Facebook 分享 debugger：<https://developers.facebook.com/tools/debug/?q=https%3A%2F%2Fwhiteguo233.github.io%2FOpenBiliClaw%2F>
- Lighthouse SEO：本地 `chrome://lighthouse` 或 PageSpeed Insights，期望 SEO = 100

## 长期维护

每次主页有较大改动（slogan、核心功能、截图、安装方式变了）就刷新：

- `docs/sitemap.xml` 里 `<lastmod>` 改成发布当日的日期
- `og:image` 如果换图，新图建议 1200×630 PNG（社交分享卡片标准比例），更新
  `og:image:width` / `og:image:height` 与 sitemap 内的 `<image:loc>`
- 标题或描述变了，记得 `translations.zh.pageTitle` / `metaDescription` /
  `ogTitle` / `ogDescription` 与 `translations.en` 同时改；i18n 切语言会
  覆盖默认 meta
- 发布新版本时更新 `<head>` JSON-LD 里 `SoftwareApplication.softwareVersion`
