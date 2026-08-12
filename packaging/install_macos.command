#!/bin/zsh
#
# OpenBiliClaw macOS DMG installer.
#
# The DMG itself has no post-install hook. This helper performs a bundle-faithful,
# atomic copy into /Applications, hands the running process from the old bundle
# to the newly installed one, and deliberately leaves Gatekeeper/quarantine
# decisions to macOS and the user.

set -u

readonly APP_NAME="OpenBiliClaw"
readonly BUNDLE_ID="${OPENBILICLAW_INSTALL_BUNDLE_ID:-com.openbiliclaw.desktop}"
readonly SCRIPT_DIR="${0:A:h}"
readonly SOURCE_APP="${OPENBILICLAW_INSTALL_SOURCE_APP:-${SCRIPT_DIR}/${APP_NAME}.app}"
readonly TARGET_APP="${OPENBILICLAW_INSTALL_TARGET_APP:-/Applications/${APP_NAME}.app}"
readonly TARGET_PARENT="${TARGET_APP:h}"
readonly APP_PROCESS_PATTERN="${OPENBILICLAW_INSTALL_APP_PROCESS_PATTERN:-[/]OpenBiliClaw[.]app/Contents/MacOS/OpenBiliClaw}"
readonly GRACEFUL_ATTEMPTS="${OPENBILICLAW_INSTALL_GRACEFUL_ATTEMPTS:-30}"
readonly TERM_ATTEMPTS="${OPENBILICLAW_INSTALL_TERM_ATTEMPTS:-10}"
readonly KILL_ATTEMPTS="${OPENBILICLAW_INSTALL_KILL_ATTEMPTS:-10}"
readonly LAUNCH_ATTEMPTS="${OPENBILICLAW_INSTALL_LAUNCH_ATTEMPTS:-40}"

typeset -a PRIVILEGE_PREFIX
PRIVILEGE_PREFIX=()
WORK_DIR=""
BACKUP_APP=""
BACKUP_ACTIVE=0
NEW_AT_TARGET=0
INSTALL_COMPLETE=0

info() {
  print -r -- "[OpenBiliClaw] $*"
}

fail() {
  print -ru2 -- "[OpenBiliClaw] 安装失败 / Installation failed: $*"
  exit 1
}

validate_attempt_count() {
  local name="$1"
  local value="$2"
  if [[ "${value}" != <-> ]] || (( value <= 0 )); then
    fail "${name} 必须是正整数 / ${name} must be a positive integer."
  fi
}

run_privileged() {
  if (( ${#PRIVILEGE_PREFIX[@]} > 0 )); then
    "${PRIVILEGE_PREFIX[@]}" "$@"
  else
    "$@"
  fi
}

cleanup() {
  if (( INSTALL_COMPLETE == 0 )); then
    if (( NEW_AT_TARGET == 1 )) && [[ -e "${TARGET_APP}" || -L "${TARGET_APP}" ]]; then
      if run_privileged /bin/rm -rf -- "${TARGET_APP}" >/dev/null 2>&1; then
        NEW_AT_TARGET=0
      fi
    fi
    if (( BACKUP_ACTIVE == 1 && NEW_AT_TARGET == 0 )) &&
      [[ ! -e "${TARGET_APP}" && ! -L "${TARGET_APP}" && -d "${BACKUP_APP}" ]]; then
      if run_privileged /bin/mv -- "${BACKUP_APP}" "${TARGET_APP}" >/dev/null 2>&1; then
        BACKUP_ACTIVE=0
      fi
    fi
  fi
  if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
    if (( BACKUP_ACTIVE == 0 || INSTALL_COMPLETE == 1 )); then
      run_privileged /bin/rm -rf -- "${WORK_DIR}" >/dev/null 2>&1 || true
    else
      print -ru2 -- \
        "[OpenBiliClaw] 旧版本备份保留在 / Previous-version backup retained at: ${BACKUP_APP}"
    fi
  fi
}

trap cleanup EXIT
trap 'cleanup; exit 130' HUP INT TERM

bundle_version() {
  local app_path="$1"
  /usr/libexec/PlistBuddy \
    -c "Print :CFBundleShortVersionString" \
    "${app_path}/Contents/Info.plist" 2>/dev/null
}

app_pids() {
  /usr/bin/pgrep -f "${APP_PROCESS_PATTERN}" 2>/dev/null || true
}

bundled_runtime_pids() {
  /usr/bin/pgrep -f "${APP_PROCESS_PATTERN}" 2>/dev/null || true
}

target_app_pids() {
  /usr/bin/pgrep -f "${TARGET_APP}/Contents/MacOS/OpenBiliClaw" 2>/dev/null || true
}

signal_pids() {
  local signal_name="$1"
  local pids="$2"
  local pid
  while IFS= read -r pid; do
    [[ -n "${pid}" ]] || continue
    /bin/kill "-${signal_name}" "${pid}" >/dev/null 2>&1 || true
  done <<< "${pids}"
}

wait_until_stopped() {
  local pid_source="$1"
  local attempts="$2"
  local current
  local attempt
  for (( attempt = 1; attempt <= attempts; attempt++ )); do
    current="$("${pid_source}")"
    [[ -z "${current}" ]] && return 0
    /bin/sleep 0.5
  done
  return 1
}

request_graceful_quit() {
  local request_pid
  /usr/bin/osascript \
    -e "tell application id \"${BUNDLE_ID}\" to quit" \
    >/dev/null 2>&1 &
  request_pid=$!

  if wait_until_stopped app_pids "${GRACEFUL_ATTEMPTS}"; then
    /bin/kill -KILL "${request_pid}" >/dev/null 2>&1 || true
    wait "${request_pid}" 2>/dev/null || true
    return 0
  fi

  # A hung application can also leave osascript waiting forever for a reply.
  # Bound that request so the TERM/KILL fallback below always remains reachable.
  /bin/kill -KILL "${request_pid}" >/dev/null 2>&1 || true
  wait "${request_pid}" 2>/dev/null || true
  return 1
}

stop_bundled_runtime() {
  local pids
  pids="$(bundled_runtime_pids)"
  [[ -n "${pids}" ]] || return 0

  info "正在清理旧版内置运行时 / Stopping the old bundled runtime ..."
  signal_pids TERM "${pids}"
  if wait_until_stopped bundled_runtime_pids "${TERM_ATTEMPTS}"; then
    return 0
  fi
  signal_pids KILL "$(bundled_runtime_pids)"
  wait_until_stopped bundled_runtime_pids "${KILL_ATTEMPTS}" ||
    fail "无法结束旧版内置运行时 / Could not stop the old bundled runtime."
}

stop_old_instance() {
  local pids
  pids="$(app_pids)"
  if [[ -z "${pids}" ]]; then
    stop_bundled_runtime
    return 0
  fi

  info "正在退出旧版本 / Quitting the old version ..."
  if request_graceful_quit; then
    stop_bundled_runtime
    return 0
  fi

  info "旧版本未及时退出，发送 TERM / Old version did not quit; sending TERM ..."
  signal_pids TERM "$(app_pids)"
  if wait_until_stopped app_pids "${TERM_ATTEMPTS}"; then
    stop_bundled_runtime
    return 0
  fi

  info "旧版本仍在运行，强制结束 / Old version is still running; forcing exit ..."
  signal_pids KILL "$(app_pids)"
  signal_pids KILL "$(bundled_runtime_pids)"
  wait_until_stopped app_pids "${KILL_ATTEMPTS}" ||
    fail "无法结束旧进程，请从菜单栏退出后重试。Could not stop the old process; quit it from the menu bar and retry."
  stop_bundled_runtime
}

rollback_install() {
  if run_privileged /bin/rm -rf -- "${TARGET_APP}" >/dev/null 2>&1; then
    NEW_AT_TARGET=0
  fi
  if (( BACKUP_ACTIVE == 1 && NEW_AT_TARGET == 0 )) &&
    [[ ! -e "${TARGET_APP}" && ! -L "${TARGET_APP}" && -d "${BACKUP_APP}" ]]; then
    if run_privileged /bin/mv -- "${BACKUP_APP}" "${TARGET_APP}" >/dev/null 2>&1; then
      BACKUP_ACTIVE=0
    fi
  fi
}

validate_attempt_count "OPENBILICLAW_INSTALL_GRACEFUL_ATTEMPTS" "${GRACEFUL_ATTEMPTS}"
validate_attempt_count "OPENBILICLAW_INSTALL_TERM_ATTEMPTS" "${TERM_ATTEMPTS}"
validate_attempt_count "OPENBILICLAW_INSTALL_KILL_ATTEMPTS" "${KILL_ATTEMPTS}"
validate_attempt_count "OPENBILICLAW_INSTALL_LAUNCH_ATTEMPTS" "${LAUNCH_ATTEMPTS}"

[[ -d "${SOURCE_APP}" ]] ||
  fail "DMG 中缺少 ${APP_NAME}.app / ${APP_NAME}.app is missing from the DMG."
[[ -f "${SOURCE_APP}/Contents/Info.plist" ]] ||
  fail "新应用缺少 Info.plist / The new app is missing Info.plist."

SOURCE_VERSION="$(bundle_version "${SOURCE_APP}")"
[[ -n "${SOURCE_VERSION}" ]] ||
  fail "无法读取新版本号 / Could not read the new bundle version."

if [[ ! -d "${TARGET_PARENT}" ]]; then
  /bin/mkdir -p -- "${TARGET_PARENT}" 2>/dev/null ||
    fail "无法创建 ${TARGET_PARENT} / Could not create ${TARGET_PARENT}."
fi

if [[ ! -w "${TARGET_PARENT}" ]]; then
  info "写入 ${TARGET_PARENT} 需要管理员权限 / Administrator access is required."
  /usr/bin/sudo -v ||
    fail "未获得管理员权限 / Administrator access was not granted."
  PRIVILEGE_PREFIX=(/usr/bin/sudo)
fi

WORK_DIR="$(
  run_privileged /usr/bin/mktemp -d "${TARGET_PARENT}/.openbiliclaw-install.XXXXXX"
)" || fail "无法创建安装暂存目录 / Could not create the installation staging directory."
run_privileged /bin/chmod 0755 "${WORK_DIR}" ||
  fail "无法准备安装暂存目录 / Could not prepare the installation staging directory."
readonly NEW_APP="${WORK_DIR}/${APP_NAME}.app"
BACKUP_APP="${WORK_DIR}/${APP_NAME}.previous.app"

info "正在校验并暂存 v${SOURCE_VERSION} / Staging and verifying v${SOURCE_VERSION} ..."
run_privileged /usr/bin/ditto --rsrc --extattr --acl "${SOURCE_APP}" "${NEW_APP}" ||
  fail "复制新应用失败 / Could not copy the new app."

/usr/bin/codesign --verify --deep --strict "${NEW_APP}" >/dev/null 2>&1 ||
  fail "新应用签名校验失败 / The new app failed code-signature verification."
[[ "$(bundle_version "${NEW_APP}")" == "${SOURCE_VERSION}" ]] ||
  fail "暂存版本与安装包不一致 / The staged version does not match the DMG."

# Stage and verify first so the old process keeps running if the DMG is damaged.
stop_old_instance

if [[ -e "${TARGET_APP}" || -L "${TARGET_APP}" ]]; then
  run_privileged /bin/mv -- "${TARGET_APP}" "${BACKUP_APP}" ||
    fail "无法备份旧应用 / Could not move the previous app aside."
  BACKUP_ACTIVE=1
fi

if ! run_privileged /bin/mv -- "${NEW_APP}" "${TARGET_APP}"; then
  rollback_install
  fail "无法把新应用移入 Applications，已恢复旧版本 / Could not install the new app; the previous version was restored."
fi
NEW_AT_TARGET=1

INSTALLED_VERSION="$(bundle_version "${TARGET_APP}")"
if [[ "${INSTALLED_VERSION}" != "${SOURCE_VERSION}" ]] ||
  ! /usr/bin/codesign --verify --deep --strict "${TARGET_APP}" >/dev/null 2>&1; then
  rollback_install
  fail "安装后校验失败，已恢复旧版本 / Post-install verification failed; the previous version was restored."
fi

# The new app is now verified at its final path; the backup can be discarded.
INSTALL_COMPLETE=1
if run_privileged /bin/rm -rf -- "${WORK_DIR}"; then
  WORK_DIR=""
  BACKUP_ACTIVE=0
else
  info "旧版备份将在退出时再次清理 / Previous-version backup cleanup will retry on exit."
fi

info "正在启动刚安装的 v${INSTALLED_VERSION} / Launching installed v${INSTALLED_VERSION} ..."
if ! /usr/bin/open -n "${TARGET_APP}"; then
  fail "启动失败；请右键应用选择“打开”。Launch failed; Control-click the app and choose Open."
fi

for (( attempt = 1; attempt <= LAUNCH_ATTEMPTS; attempt++ )); do
  if [[ -n "$(target_app_pids)" ]]; then
    /bin/sleep 1
    if [[ -n "$(target_app_pids)" ]]; then
      info "完成：v${INSTALLED_VERSION} 已从 ${TARGET_APP} 运行。"
      info "Done: v${INSTALLED_VERSION} is running from ${TARGET_APP}."
      exit 0
    fi
  fi
  /bin/sleep 0.5
done

fail "v${INSTALLED_VERSION} 已安装，但 macOS 未允许它启动；请右键应用选择“打开”。v${INSTALLED_VERSION} was installed, but macOS did not allow it to launch; Control-click the app and choose Open."
