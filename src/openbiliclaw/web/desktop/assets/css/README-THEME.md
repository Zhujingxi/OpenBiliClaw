# 主题色彩系统交接文档

## 一、架构概览

```
--hue-primary (单一控制点)
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

所有色彩均以 `oklch()` 定义，仅依赖 `--hue-primary`、`--light-base`、`--chroma-base` 三个参数。

---

## 二、文件分布

| 文件 | 作用 |
|------|------|
| `assets/css/app.css` | 全部主题变量、派生色、12 色相预设、交互态样式 |
| `assets/js/app.js` | `applyThemeHue` / `setThemeHue` / `renderThemeHueControls` — hue 状态管理 |
| `index.html` | 12 色块 + slider + number 输入（settings 面板），inline `<script>` 恢复 localStorage |

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
| `applyThemeHue(hue)` | `app.js` | 设置 `document.documentElement.style.--hue-primary`、更新所有控件状态、持久化到 `obc.themeHue` |
| `setThemeHue(hue)` | `app.js` | 设置 hue + 同步 slider + number input + swatch active |
| `renderThemeHueControls()` | `app.js` | 渲染 hue 选择器 DOM（swatches + slider + number） |

数据流：
```
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

---

## 八、Dark 模式特殊处理

Dark mode 使用两层定义：

1. `:root[data-theme="dark"]` — 变量覆盖（line 155）
2. `@media (prefers-color-scheme: dark)` 内 `:root:not([data-theme="light"])` — 兜底（line 208+）

部分元素在 dark 下需要从 `#000` 改为 `--accent-subtle` 或 `--accent-light` 作为非强调基底：
- `.search button`
- `.top-mobile-btn`
- `.pill-btn.dark`
- `.gh-star-left`
- `.delight-main-actions .small-btn:last-child`

已在两层中都做了覆盖。新增类似元素时需同步处理。

---

## 九、重点注意事项

1. **文件二重性**：`desktop/assets/css/app.css`（桌面 Web）和 `web/css/app.css`（插件弹窗）是两个独立文件，互不共享。桌面版 HTML 引用 `/web/assets/css/app.css`，由服务器映射到桌面版。
2. **hue 持久化**：`obc.themeHue` localStorage → 页面加载时 inline `<script>` 恢复。JS 未完全加载时初始色块可能短暂显示错误 active 态。
3. **`--elev-ring` 禁用**：所有 hover/active/focus 的 `box-shadow` 均不含 `--elev-ring`（已全局替换）。
4. **`--motion-fast`**：150ms → 450ms，3× 减速便于调试。交付前确认是否需要恢复。
5. **slider 彩虹渐变**：`--hue-grad` 用于 `::-webkit-slider-runnable-track` 和 `::-moz-range-track`，滑块轨道呈全色环渐变。
6. **`.probe-btn` 边框**：已从 `border: 1px solid` 改为 `border: 0; box-shadow: 0 0 0 1px` 消除圆角锯齿。
7. **`hue-swatch` active 态**：使用 `border-color: var(--accent)`，hover/active 统一 pattern。
