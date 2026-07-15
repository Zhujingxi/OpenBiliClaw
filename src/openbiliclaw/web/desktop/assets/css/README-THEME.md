# 主题色彩系统交接文档

## 一、架构概览

```
obc.accentStyle
  ├── classic → <html data-accent="classic"> → classic.css 固定色板（新用户默认）
  └── modern  → 不设置 data-accent          → app.css 动态色相

modern 模式：--hue-primary (单一控制点)
  ├── --accent-subtle      极淡（底色/悬停基底）
  ├── --accent-light       亮色（按钮/标签悬停）
  ├── --accent             基准强调（主按钮、hover 态）
  ├── --accent-strong      强强调（active 态）
  ├── --accent-hover       更深强调（hover 提升）
  ├── --accent-deep        最深强调（active 加深）
  ├── --contrast-strong    hue +180° 互补（最强 CTA、焦点环）
  ├── --focus-ring         3px 互补色焦点环
  ├── --probe-interest     hue +30°
  ├── --probe-challenge    hue +120°
  └── --probe-avoidance    hue +210°
```

动态主题色以 `oklch()` 定义，仅依赖 `--hue-primary`、`--light-base`、`--chroma-base` 三个参数。经典配色由 `classic.css` 覆盖为固定的陶土色板；浅色模式的陶土主色略微加深，确保按钮文字至少 4.5:1 对比度，并保留蓝色可见焦点环。

---

## 二、文件分布

| 文件 | 作用 |
|------|------|
| `assets/css/app.css` | 全部主题变量、派生色、12 色相预设、交互态样式 |
| `assets/css/classic.css` | `data-accent="classic"` 下的固定经典色板与元素级覆盖 |
| `assets/js/app.js` | accent / hue 状态、迁移、键盘单选导航和首次经典模式引导 |
| `index.html` | 色彩引擎、12 色块、slider / number 输入，以及 CSS 前的 localStorage 恢复脚本 |
| `api/app.py` | 为 `app.css`、`classic.css` 与 `app.js` 注入同一静态资源版本指纹 |

---

## 三、核心变量定义（:root Light 模式）

```css
--hue-primary: 20;         /* 默认珊瑚橙，0–360 步进 */
--light-base: 58%;         /* 基准明度 */
--light-strong: 45%;       /* 强调色明度 */
--light-ultra: 38%;        /* 极深明度 */
--chroma-base: 0.14;       /* 基准色度 */
--chroma-strong: 0.19;     /* 强调色度 */

/* 强调色层级 */
--accent-subtle:  oklch(90%  0.05 var(--hue-primary));
--accent-light:   oklch(75%  0.10 var(--hue-primary));
--accent:         oklch(58%  0.14 var(--hue-primary));
--accent-strong:  oklch(45%  0.19 var(--hue-primary));
--accent-hover:   oklch(39%  0.19 var(--hue-primary));
--accent-deep:    oklch(32%  0.10 var(--hue-primary));

/* 互补 */
--contrast-strong: oklch(var(--light-base) var(--chroma-strong) calc(var(--hue-primary) + 180));
--focus-ring: 0 0 0 3px color-mix(in oklch, var(--contrast-strong), transparent 50%);

/* 探测色 */
--probe-challenge:  oklch(58% 0.14 calc(var(--hue-primary) + 120));
--probe-avoidance:  oklch(58% 0.14 calc(var(--hue-primary) + 210));
--probe-interest:   oklch(58% 0.14 calc(var(--hue-primary) + 30));

/* 语义色（固定 hue） */
--success: oklch(58% 0.13 140);  /* 绿 */
--warn:    oklch(58% 0.15 85);   /* 黄 */
--danger:  oklch(58% 0.15 20);   /* 红（与默认同色系） */
```

### Dark 模式覆盖

```css
--light-base: 72%;
--light-strong: 62%;
--chroma-base: 0.13;
--chroma-strong: 0.17;
--accent-subtle: oklch(25% 0.06 var(--hue-primary));
--accent-light:  oklch(40% 0.12 var(--hue-primary));
--accent-deep:   oklch(55% 0.10 var(--hue-primary));
/* accent / accent-strong / accent-hover 由 --light-base 自动变化 */
```

---

## 四、12 色相预设

定义于 `app.css:264-275`，属性选择器 `[data-theme-hue="N"]`：

```
0°   烈焰红    60°  柠檬黄    120° 薄荷绿    180° 青瓷色    240° 星空蓝    300° 梦幻紫
30°  珊瑚橙    90°  嫩草绿    150° 自然绿    210° 极客蓝    270° 暗夜紫    330° 元气粉
```

仅设置 `--hue-primary`，所有派生色自动跟随。

---

## 五、JS 接口

| 函数 | 位置 | 功能 |
|------|------|------|
| `applyAccentStyle(style)` | `app.js` | 校验 `modern / classic` 并同步根节点 `data-accent` |
| `setAccentStyle(style)` | `app.js` | 切换色彩引擎并持久化 `obc.accentStyle` |
| `renderThemeAccentControls()` | `app.js` | 同步色彩引擎单选状态，并在经典模式禁用 hue 控件 |
| `applyThemeHue(hue)` | `app.js` | 设置 `document.documentElement.style.--hue-primary`、更新所有控件状态、持久化到 `obc.themeHue` |
| `setThemeHue(hue)` | `app.js` | 设置 hue + 同步 slider + number input + swatch active |
| `renderThemeHueControls()` | `app.js` | 渲染 hue 选择器 DOM（swatches + slider + number） |

数据流：
```
页面解析（CSS 之前）
  → 读取 obc.accentStyle
  → 未设置且已有 obc.themeHue：迁移为 modern
  → 未设置且没有 obc.themeHue：写入并启用 classic
  → 加载 app.css + classic.css（无首屏闪色）

slider/number/swatch click
  → setThemeHue(hue)
    → applyThemeHue(hue)
      → document.style.setProperty('--hue-primary', hue)
      → localStorage.setItem('obc.themeHue', hue)
      → update active class on swatches
      → sync slider.value + number.value
```

---

## 六、中性色（Light / Dark）

| 变量 | Light | Dark |
|------|-------|------|
| `--bg` | `#f5f4ed` | `#2d2d2b` |
| `--surface` | `#faf9f5` | `#211f1a` |
| `--surface-warm` | `#e8e6dc` | `#302b23` |
| `--fg` | `#141413` | `#f4eee3` |
| `--fg-2` | `#3d3d3a` | `#ded4c5` |
| `--muted` | `#5e5d59` | `#b8afa1` |
| `--border` | `#f0eee6` | `#332e26` |
| `--border-soft` | `#e8e6dc` | `#4a4337` |
| `--accent-on` | `#faf9f5` | `#2d2d2b` |

---

## 七、交互态规范

所有可交互元素统一 pattern：

| 状态 | background | border-color | box-shadow | transform |
|------|-----------|-------------|------------|-----------|
| 常态 | `var(--surface)` 或层级对应 | `var(--border-soft)` | 无 | 1 |
| hover | `var(--accent)` | `var(--accent)` | `0 0 5px 1px color-mix(in oklab, var(--accent), transparent 40%)` | 1 |
| active | `var(--accent-strong)` | `var(--accent-strong)` | 同上 glow | `scale(0.97)` |

【注意】`box-shadow` 中**不可**混入 `var(--elev-ring)`，否则 1px 硬色环会切断 glow。

主题模式、色彩引擎和色相预设均使用 `radiogroup / radio` 语义，支持方向键与 Home / End。设置下拉框保留原生 `<select>`，不要用隐藏原控件的自制菜单替换。经典配色可以移除强调色辉光，但不能移除 `:focus-visible` 的 `--focus-ring`；粗指针下常用保存操作保持至少 44×44px。

---

## 八、Dark 模式特殊处理

Dark mode 使用两层定义：

1. `:root[data-theme="dark"]` — 变量覆盖（line 155）
2. `@media (prefers-color-scheme: dark)` 内 `:root:not([data-theme="light"])` — 兜底（line 208+）

动态主题在 dark 下对以下非主操作使用强调色阶：
- `.search button`
- `.pill-btn.dark`
- `.gh-star-left`
- `.delight-main-actions .small-btn:last-child`

顶部手机版入口继续使用高对比 `--fg` 实心处理。经典配色的 dark 覆盖位于 `classic.css`；新增类似元素时需同步检查显式 dark 与系统 dark 两层。

---

## 九、重点注意事项

1. **文件二重性**：`desktop/assets/css/app.css`（桌面 Web）和 `web/css/app.css`（插件弹窗）是两个独立文件，互不共享。桌面版 HTML 引用 `/web/assets/css/app.css`，由服务器映射到桌面版。
2. **accent / hue 持久化**：inline `<script>` 必须在两个 stylesheet 之前读取并补写 `obc.accentStyle`；已有 `obc.themeHue` 且没有 accent 记录视为旧用户并迁移到 `modern`，否则默认 `classic`。JS 初始化必须复用同一判定，不能产生首屏与运行时分歧。
3. **`--elev-ring` 禁用**：所有 hover/active/focus 的 `box-shadow` 均不含 `--elev-ring`（已全局替换）。
4. **`--motion-fast`**：150ms（交互过渡基准；调试期曾临时调至 450ms，交付前已恢复）。
5. **slider 彩虹渐变**：`--hue-grad` 用于 `::-webkit-slider-runnable-track` 和 `::-moz-range-track`，滑块轨道呈全色环渐变。
6. **`.probe-btn` 边框**：已从 `border: 1px solid` 改为 `border: 0; box-shadow: 0 0 0 1px` 消除圆角锯齿。
7. **`hue-swatch` active 态**：使用 `border-color: var(--accent)`，hover/active 统一 pattern。
8. **首次引导**：`#themeNotice` 与 `#toastContainer` 分离，8 秒自动收起，hover / focus 时暂停；使用安全的 `storageGet / storageSet` 读写既有 `obc.noticeDismissed`，不能阻塞或改变业务 Toast 的顺序。
9. **缓存失效**：新增或拆分桌面主题资源时，同步更新 `_desktop_asset_version()` 与 HTML 版本参数替换，避免只更新 `classic.css` 时客户端继续命中旧缓存。
