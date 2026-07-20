/**
 * Descriptor-driven DOM renderers shared by the desktop and mobile web
 * model-route editors. The setup wizard consumes the escapeHtml primitive;
 * its descriptor/credential/connection-type renderers stay wizard-local for
 * layout reasons (full adoption is plan §10). The extension popup cannot
 * import outside extension/ and keeps its own copies, drift-guarded by
 * tests/js/model-config-parity.test.mjs.
 *
 * All renderers are pure string builders: they take the DOM-free state from
 * `model-config-state.js` plus the backend descriptor registry and return HTML
 * strings. Surfaces inject their own class-name prefix and callbacks so the
 * same descriptor set produces structurally identical markup everywhere.
 */

const CATEGORY_LABELS = {
  api_protocol: "API 协议",
  local_runtime: "本地 Runtime",
  oauth: "OAuth 连接",
};

export function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>'"]/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    })[character],
  );
}

export function disabledMarkup(disabled) {
  return disabled ? ' disabled aria-disabled="true"' : "";
}

export function categoryLabel(category) {
  return CATEGORY_LABELS[category] || String(category || "");
}

/**
 * Render one descriptor field (non-credential) for a route item.
 *
 * @param {object} options
 * @param {object} options.record hydrated route item (chat connection or embedding provider)
 * @param {object} options.descriptor ConnectionTypeDescriptorOut for record.type
 * @param {object} options.field DescriptorFieldOut to render
 * @param {string} options.kind "chat" | "embedding"
 * @param {boolean} options.locked route-level override lock
 * @param {(recordId: string, field: string) => string} options.errorMarkup inline-error renderer
 * @param {string} [options.fieldClass] label class, e.g. "settings-field" | "mobile-model-field" | "model-field"
 * @param {boolean} [options.fullWidthFields] whether base_url-style fields span the grid
 */
export function renderDescriptorField(options) {
  const {
    record,
    descriptor,
    field,
    kind,
    locked = false,
    errorMarkup = () => "",
    fieldClass = "settings-field",
    fullWidthFields = true,
  } = options;
  if (!record || !descriptor || !field) return "";
  if (field.name === "credential") return "";
  if (field.capabilities?.length && !field.capabilities.includes(kind)) return "";
  if (field.presets?.length && !field.presets.includes(record.preset)) return "";
  const required = field.required ? " required" : "";
  const disabled = disabledMarkup(locked);
  const help = field.help ? `<small>${escapeHtml(field.help)}</small>` : "";
  const wrapClass = fieldClass;
  const full = fullWidthFields && field.name === "base_url" ? " full" : "";

  if (field.name === "preset") {
    const presets = (descriptor.preset_definitions || []).filter(
      (preset) => !preset.capabilities?.length || preset.capabilities.includes(kind),
    );
    return `<label class="${wrapClass}${full}"><span>${escapeHtml(field.label)}</span>
      <select data-model-field="preset"${required}${disabled}>${presets.map((preset) => `<option value="${escapeHtml(preset.id)}"${preset.id === record.preset ? " selected" : ""}>${escapeHtml(preset.label)}</option>`).join("")}</select>
      ${help}${errorMarkup(record.id, "preset")}</label>`;
  }
  if (field.input_type === "select") {
    return `<label class="${wrapClass}${full}"><span>${escapeHtml(field.label)}</span>
      <select data-model-field="${escapeHtml(field.name)}"${required}${disabled}>${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice)}"${String(record[field.name] || "") === choice ? " selected" : ""}>${escapeHtml(field.name === "reasoning_effort" && choice === "" ? "disabled" : choice)}</option>`).join("")}</select>
      ${help}${errorMarkup(record.id, field.name)}</label>`;
  }
  const type = field.input_type === "number" ? "number" : "text";
  const numericAttributes = field.name === "num_ctx"
    ? ' min="0" step="1" inputmode="numeric"'
    : "";
  const describedBy = field.name === "num_ctx" && options.numCtxDescribedBy
    ? ` aria-describedby="${escapeHtml(options.numCtxDescribedBy)}"`
    : "";
  return `<label class="${wrapClass}${full}"><span>${escapeHtml(field.label)}</span>
    <input type="${type}" data-model-field="${escapeHtml(field.name)}" value="${escapeHtml(record[field.name] ?? "")}" placeholder="${escapeHtml(field.placeholder || "")}" autocomplete="off"${required}${disabled}${numericAttributes}${describedBy}>
    ${help}${errorMarkup(record.id, field.name)}</label>`;
}

/**
 * Render the credential editor for a route item. OAuth descriptors render a
 * read-only status line (decision 10 of the redesign plan).
 *
 * @param {object} options
 * @param {string} [options.classPrefix] class prefix for the action row and
 *   buttons, e.g. "model" (desktop) or "mobile-model" (mobile). The mobile
 *   stylesheet styles `.mobile-model-credential-actions` and its pressed
 *   buttons; the desktop stylesheet styles `.model-credential-actions` and
 *   `.model-credential-action`.
 */
export function renderCredentialEditor(options) {
  const {
    record,
    descriptor,
    kind,
    locked = false,
    errorMarkup = () => "",
    fieldClass = "settings-field",
    credentialValueId = "modelCredentialValue",
    noteClass = "settings-note-inline",
    classPrefix = "model",
  } = options;
  if (!record) return { hidden: true, html: "" };
  const definition = descriptor?.fields?.find((field) => field.name === "credential");
  if (!definition) return { hidden: true, html: "" };
  const credential = record.credential;
  const status = credential.status || {};
  const disabled = disabledMarkup(locked);
  if (descriptor.category === "oauth") {
    const importedReference = status.credential_ref || definition.choices?.[0] || descriptor.label;
    return {
      hidden: false,
      html: `
      <strong>已导入 OAuth 凭据</strong>
      <p class="${noteClass}">${status.oauth_logged_in ? "已登录" : "尚未检测到登录"} · ${escapeHtml(importedReference)}</p>
      <input type="hidden" data-model-credential-action="keep" value="keep">
      ${errorMarkup(record.id, "credential")}`,
    };
  }
  const actions = [
    ["keep", "保留现有凭据"],
    ["set", "设置 API Key"],
    ["env", "环境变量"],
    ["clear", "清除"],
  ];
  const sourceLabel = status.configured
    ? `当前来源：${status.source}${status.env_name ? ` (${status.env_name})` : ""}`
    : "当前未配置凭据。";
  const needsValue = credential.action === "set" || credential.action === "env";
  return {
    hidden: false,
    html: `
    <strong>凭据来源</strong>
    <p class="${noteClass}">${escapeHtml(sourceLabel)}</p>
    <div class="${classPrefix}-credential-actions">${actions.map(([action, label]) => `<button class="${classPrefix}-credential-action" type="button" data-model-credential-action="${action}" aria-pressed="${credential.action === action ? "true" : "false"}"${disabled}>${label}</button>`).join("")}</div>
    ${needsValue ? `<label class="${fieldClass}"><span>${credential.action === "env" ? "环境变量名" : "新 API Key"}</span><input id="${escapeHtml(credentialValueId)}" type="${credential.action === "set" ? "password" : "text"}" value="${escapeHtml(credential.value || "")}" autocomplete="new-password"${disabled}></label>` : ""}
    ${errorMarkup(record.id, "credential")}`,
  };
}

/**
 * Render the grouped, searchable connection-type listbox. The caller owns the
 * search-query state; this renders from the *filtered* descriptor groups.
 *
 * @param {object} options
 * @param {string} [options.classPrefix] class prefix for the group, title,
 *   option, and empty-state classes, e.g. "model" (desktop) or "mobile-model"
 *   (mobile). The mobile stylesheet styles `.mobile-model-type-group`,
 *   `.mobile-model-type-option`, and `.mobile-model-connection-type-groups`;
 *   the desktop stylesheet styles `.model-type-group`, `.model-type-option`,
 *   and `.model-empty-types`.
 */
export function renderConnectionTypeGroups(options) {
  const {
    groups,
    record,
    kind,
    locked = false,
    query = "",
    emptyLabel = "没有匹配的连接类型。",
    classPrefix = "model",
  } = options;
  if (!record) return "";
  const needle = String(query || "").trim().toLowerCase();
  const blocks = [];
  for (const group of groups || []) {
    const matches = (group.connection_types || []).filter((descriptor) => {
      if (!descriptor.capabilities?.includes(kind)) return false;
      const searchText = [
        descriptor.id,
        descriptor.label,
        descriptor.help,
        ...(descriptor.preset_definitions || []).map(
          (preset) => `${preset.id} ${preset.label}`,
        ),
      ].join(" ").toLowerCase();
      return !needle || searchText.includes(needle);
    });
    if (!matches.length) continue;
    blocks.push(`
      <section class="${classPrefix}-type-group" data-model-type-category="${escapeHtml(group.category)}">
        <p class="${classPrefix}-type-group-title">${escapeHtml(categoryLabel(group.category))}</p>
        ${matches.map((descriptor) => `
          <button class="${classPrefix}-type-option" type="button" role="option" tabindex="-1" data-model-type="${escapeHtml(descriptor.id)}" aria-selected="${descriptor.id === record.type ? "true" : "false"}"${disabledMarkup(locked)}>
            <span><strong>${escapeHtml(descriptor.label)}</strong><small>${escapeHtml(descriptor.help)}</small></span>
            <small>${escapeHtml(descriptor.category === "oauth" ? "OAuth" : descriptor.id)}</small>
          </button>`).join("")}
      </section>`);
  }
  return blocks.join("") || `<p class="${classPrefix}-empty-types">${escapeHtml(emptyLabel)}</p>`;
}

/** Keyboard roving-tabindex handler for the connection-type listbox. */
export function moveTypeOptionFocus(event) {
  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  const options = [
    ...event.currentTarget.querySelectorAll('[role="option"]:not(:disabled)'),
  ];
  if (!options.length) return;
  const current = event.target.closest('[role="option"]');
  let index = Math.max(0, options.indexOf(current));
  if (event.key === "Home") index = 0;
  else if (event.key === "End") index = options.length - 1;
  else if (event.key === "ArrowUp") index = Math.max(0, index - 1);
  else index = Math.min(options.length - 1, index + 1);
  event.preventDefault();
  for (const option of options) option.tabIndex = option === options[index] ? 0 : -1;
  options[index].focus();
}

/** After rendering, give the selected (or first) option the roving tabindex. */
export function applyTypeOptionRovingTabindex(host) {
  if (!host) return;
  const options = [...host.querySelectorAll('[role="option"]:not(:disabled)')];
  if (!options.length) return;
  const selected = options.find(
    (option) => option.getAttribute("aria-selected") === "true",
  );
  const roving = selected || options[0];
  if (roving) roving.tabIndex = 0;
}
