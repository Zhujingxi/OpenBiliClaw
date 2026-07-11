# 原生保存授权 E2E Runbook

> 本文包含 B 站账号写入。只有用户明确授权命名 BV 号或指定测试账号后才能执行。默认 smoke、文档检查和本地浏览器验收不得运行本页写入步骤。

## 验证边界

- 当前 Phase 1 仅注册 Bilibili write adapter。
- 真实目标为 `B站 OpenBiliClaw 收藏夹` 与 `B站稍后再看`。
- 本地 membership 必须先成功；平台失败不回滚本地；本地 `/remove` 不删除平台记录。
- 重复调用 `/sync` 对已 terminal 的本地 task 是 durable no-op，**不能**证明 B 站接口幂等或产生新的 `already_synced`。平台幂等需要另行授权直接 adapter 验证，不包含在默认 runbook。
- 退出登录 / 破坏 Cookie 来验证 `login_required` 会改变登录状态，也不包含在默认 runbook；如需验证，必须另行授权并约定恢复登录的方法。

## 鉴权模式

默认 `trust_loopback=true`、从 `127.0.0.1` 直连且不带任何转发头时，下面命令无需额外鉴权头。

如本机也启用了密码门禁，请先选择一种准确模式：

1. **同源 Cookie**：用 `/api/auth/login` 的 `{"password":"..."}` 获取 cookie jar；随后所有 POST / PUT 请求同时带 `-b/-c <jar>`、`Origin: <与 OBC_BASE 同源>` 和 `X-OBC-Auth: 1`。
2. **已取得 token 的非浏览器 Bearer**：CLI/curl 不带 `Origin`、只带 `Authorization: Bearer <token>` 时有效。
3. **允许来源的跨源 Bearer**：浏览器跨源登录只有 `Origin` 已在 `allowed_bearer_origins` 中且 TTL 有限时才返回 token；后续请求同时带该 `Origin` 与 `Authorization: Bearer <token>`。

把相应参数放入 Bash 数组；可信 loopback 保持空数组：

```bash
set -Eeuo pipefail
command -v bash >/dev/null
command -v curl >/dev/null
command -v jq >/dev/null

export OBC_BASE='http://127.0.0.1:8420'
OBC_HEADERS=()
OBC_COOKIE_JAR=''
OBC_ORIGINAL_AUTO=''
OBC_RESTORE_DONE=0

# 同源 Cookie 示例：
# OBC_COOKIE_JAR="$(mktemp)"
# read -rsp 'OpenBiliClaw password: ' OBC_PASSWORD; echo
# LOGIN_RESPONSE="$(curl --noproxy '*' --connect-timeout 5 --max-time 30 -fsS \
#   -b "$OBC_COOKIE_JAR" -c "$OBC_COOKIE_JAR" \
#   -H "Origin: $OBC_BASE" -H 'Content-Type: application/json' \
#   -d "$(jq -nc --arg password "$OBC_PASSWORD" '{password:$password}')" \
#   "$OBC_BASE/api/auth/login")"
# unset OBC_PASSWORD
# jq -e '.ok == true' <<<"$LOGIN_RESPONSE" >/dev/null
# OBC_HEADERS=(-b "$OBC_COOKIE_JAR" -c "$OBC_COOKIE_JAR" \
#   -H "Origin: $OBC_BASE" -H 'X-OBC-Auth: 1')

# 已取得 token 的非浏览器 Bearer：
# OBC_HEADERS=(-H "Authorization: Bearer $OBC_TOKEN")

# 允许来源的跨源 Bearer：
# OBC_ALLOWED_ORIGIN='https://已列入-allowed_bearer_origins.example'
# OBC_HEADERS=(-H "Origin: $OBC_ALLOWED_ORIGIN" \
#   -H "Authorization: Bearer $OBC_TOKEN")

api() {
  curl --noproxy '*' --connect-timeout 5 --max-time 30 -fsS \
    "${OBC_HEADERS[@]}" "$@"
}
```

## 受保护的配置切换与终态轮询

以下函数会检查 PUT 的有效返回、再次 GET 验证热重载后的值，并用 trap 恢复测试前配置。`suppress_background_llm_work=true` 避免配置热重载额外触发补货 LLM 工作。

```bash
set_auto_sync() {
  local expected="$1" response effective
  response="$(api -X PUT "$OBC_BASE/api/config" \
    -H 'Content-Type: application/json' \
    -d "{\"saved_sync\":{\"auto_sync_enabled\":$expected},\"suppress_background_llm_work\":true}")"
  jq -e --argjson expected "$expected" '
    .ok == true and .reloaded == true and .rollback_applied == false and
    .config.saved_sync.auto_sync_enabled == $expected
  ' <<<"$response" >/dev/null
  effective="$(api "$OBC_BASE/api/config" | jq -r '.saved_sync.auto_sync_enabled')"
  test "$effective" = "$expected"
}

wait_saved_task() {
  local task_id="$1" deadline=$((SECONDS + 360)) snapshot
  test -n "$task_id"
  while (( SECONDS < deadline )); do
    snapshot="$(api "$OBC_BASE/api/saved-sync/tasks/$task_id")"
    if jq -e '
      (.items | length) > 0 and
      ([.items[].status] | all(. != "pending" and . != "syncing"))
    ' \
      <<<"$snapshot" >/dev/null; then
      printf '%s\n' "$snapshot"
      return 0
    fi
    sleep 1
  done
  echo "saved-sync task timed out: $task_id" >&2
  return 1
}

cleanup_native_save_e2e() {
  local status=$? cleanup_status=0
  trap - EXIT INT TERM
  set +e
  if [[ -n "${OBC_ORIGINAL_AUTO:-}" && "${OBC_RESTORE_DONE:-0}" != 1 ]]; then
    set_auto_sync "$OBC_ORIGINAL_AUTO" || cleanup_status=1
  fi
  if [[ -n "${OBC_COOKIE_JAR:-}" ]]; then
    rm -f -- "$OBC_COOKIE_JAR" || cleanup_status=1
  fi
  if (( status == 0 && cleanup_status != 0 )); then status=$cleanup_status; fi
  exit "$status"
}
trap cleanup_native_save_e2e EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

OBC_ORIGINAL_AUTO="$(api "$OBC_BASE/api/config" | jq -r '.saved_sync.auto_sync_enabled')"
[[ "$OBC_ORIGINAL_AUTO" == true || "$OBC_ORIGINAL_AUTO" == false ]]
```

## 明确授权后的写入步骤

使用三个不同、允许测试、当前未保存在 OpenBiliClaw 本地的 BV 号：

```bash
export OBC_FAVORITE_BVID='<AUTHORIZED_FAVORITE_BVID>'
export OBC_WATCH_LATER_BVID='<AUTHORIZED_WATCH_LATER_BVID>'
export OBC_AUTO_BVID='<AUTHORIZED_AUTO_SYNC_BVID>'

for name in OBC_FAVORITE_BVID OBC_WATCH_LATER_BVID OBC_AUTO_BVID; do
  value="${!name}"
  [[ "$value" =~ ^BV[0-9A-Za-z]{10}$ ]] || {
    echo "invalid $name: expected a 12-character BV id" >&2
    exit 2
  }
done
[[ "$OBC_FAVORITE_BVID" != "$OBC_WATCH_LATER_BVID" ]]
[[ "$OBC_FAVORITE_BVID" != "$OBC_AUTO_BVID" ]]
[[ "$OBC_WATCH_LATER_BVID" != "$OBC_AUTO_BVID" ]]
```

先关闭自动同步并再次确认，创建两条仅本地 membership：

```bash
set_auto_sync false

for spec in \
  "favorite:$OBC_FAVORITE_BVID" \
  "watch_later:$OBC_WATCH_LATER_BVID"; do
  kind="${spec%%:*}"; bvid="${spec#*:}"; item_key="bilibili:$bvid"
  api "$OBC_BASE/api/saved/$kind/status?item_key=$item_key" | jq -e '.saved == false'
  api -X POST "$OBC_BASE/api/saved/$kind" \
    -H 'Content-Type: application/json' \
    -d "{\"source_platform\":\"bilibili\",\"content_id\":\"$bvid\",\"content_url\":\"https://www.bilibili.com/video/$bvid\",\"content_type\":\"video\",\"title\":\"Authorized $kind E2E\"}" \
    | jq -e '.saved == true and .sync_task_id == ""'
done
```

这一步结束时应先在 B 站账号中确认没有因本地保存新增记录，然后才执行两个账号写入任务：

```bash
FAVORITE_TASK_ID="$(api -X POST "$OBC_BASE/api/saved/favorite/sync" \
  -H 'Content-Type: application/json' \
  -d "{\"item_keys\":[\"bilibili:$OBC_FAVORITE_BVID\"]}" | jq -er '.task_id')"
FAVORITE_RESULT="$(wait_saved_task "$FAVORITE_TASK_ID")"
jq -e '.items | length == 1 and all(.status == "synced" or .status == "already_synced")' \
  <<<"$FAVORITE_RESULT"

WATCH_TASK_ID="$(api -X POST "$OBC_BASE/api/saved/watch_later/sync" \
  -H 'Content-Type: application/json' \
  -d "{\"item_keys\":[\"bilibili:$OBC_WATCH_LATER_BVID\"]}" | jq -er '.task_id')"
WATCH_RESULT="$(wait_saved_task "$WATCH_TASK_ID")"
jq -e '.items | length == 1 and all(.status == "synced" or .status == "already_synced")' \
  <<<"$WATCH_RESULT"
```

人工确认收藏项位于 `OpenBiliClaw` 收藏夹、稍后再看项位于 B 站稍后再看。随后验证自动同步路径并轮询其返回 task：

```bash
set_auto_sync true
api "$OBC_BASE/api/saved/favorite/status?item_key=bilibili:$OBC_AUTO_BVID" \
  | jq -e '.saved == false'
AUTO_RESPONSE="$(api -X POST "$OBC_BASE/api/saved/favorite" \
  -H 'Content-Type: application/json' \
  -d "{\"source_platform\":\"bilibili\",\"content_id\":\"$OBC_AUTO_BVID\",\"content_url\":\"https://www.bilibili.com/video/$OBC_AUTO_BVID\",\"content_type\":\"video\",\"title\":\"Authorized auto-sync E2E\"}")"
AUTO_TASK_ID="$(jq -er '.sync_task_id | select(length > 0)' <<<"$AUTO_RESPONSE")"
AUTO_RESULT="$(wait_saved_task "$AUTO_TASK_ID")"
jq -e '.items | length == 1 and all(.status == "synced" or .status == "already_synced")' \
  <<<"$AUTO_RESULT"
```

最后只删除 OpenBiliClaw 本地 membership，并人工确认三个平台记录仍保留：

```bash
for spec in \
  "favorite:$OBC_FAVORITE_BVID" \
  "watch_later:$OBC_WATCH_LATER_BVID" \
  "favorite:$OBC_AUTO_BVID"; do
  kind="${spec%%:*}"; bvid="${spec#*:}"
  api -X POST "$OBC_BASE/api/saved/$kind/remove" \
    -H 'Content-Type: application/json' \
    -d "{\"item_key\":\"bilibili:$bvid\"}" | jq -e '.saved == false'
done

set_auto_sync "$OBC_ORIGINAL_AUTO"
OBC_RESTORE_DONE=1
```

`set -Eeuo pipefail` 保证任一 curl / jq / 状态断言失败立即停止。INT / TERM 会先转换成非零退出，再由唯一 EXIT cleanup 恢复原始自动同步值并删除 cookie jar；只有恢复成功后才把 `OBC_RESTORE_DONE` 置为 1。不要用重复 `/sync` 代替真实平台幂等测试。
