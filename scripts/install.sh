#!/usr/bin/env bash
#
# OpenBiliClaw one-command installer.
#
# Usage:
#     curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
#
# Environment overrides:
#     INSTALL_DIR      Target directory (default: $HOME/OpenBiliClaw)
#     OPENBILICLAW_REPO_URL  Git repository URL (default: public GitHub)
#     OPENBILICLAW_BRANCH    Git branch to clone (default: main)
#     SKIP_START       Compatibility no-op; installer never starts a backend
#     PORT             API port (default: 8420)
#     HOST             API host  (default: 0.0.0.0)
#
# Examples:
#     INSTALL_DIR=$HOME/obc curl -fsSL .../install.sh | bash
#     SKIP_START=1 curl -fsSL .../install.sh | bash      # prepare only
#
# Works on macOS, Linux, and WSL2. Requires git and python3 (3.11+).
# Native Windows is not supported — use WSL2.

set -euo pipefail

readonly DEFAULT_REPO_URL="https://github.com/whiteguo233/OpenBiliClaw.git"
readonly DEFAULT_BRANCH="main"
readonly DEFAULT_INSTALL_DIR="${HOME}/OpenBiliClaw"
readonly CANDIDATE_SOURCES=(
    "${HOME}/workspace/OpenBiliClaw"
    "${HOME}/OpenBiliClaw"
    "${HOME}/projects/OpenBiliClaw"
    "${HOME}/code/OpenBiliClaw"
)

REPO_URL="${OPENBILICLAW_REPO_URL:-$DEFAULT_REPO_URL}"
BRANCH="${OPENBILICLAW_BRANCH:-$DEFAULT_BRANCH}"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
# Distinguish "user explicitly set REUSE_FROM=" from "not set at all".
if [ "${REUSE_FROM+set}" = "set" ]; then
    _REUSE_FROM_EXPLICIT=1
else
    _REUSE_FROM_EXPLICIT=0
    REUSE_FROM=""
fi
SKIP_START="${SKIP_START:-}"
MODE="${MODE:-auto}"
PORT="${PORT:-8420}"
HOST="${HOST:-0.0.0.0}"

extend_no_proxy_for_localhost() {
    local current="${NO_PROXY:-${no_proxy:-}}"
    local host
    for host in localhost 127.0.0.1 ::1; do
        case ",$current," in
            *",$host,"*) ;;
            *) current="${current:+$current,}$host" ;;
        esac
    done
    export NO_PROXY="$current"
    export no_proxy="$current"
}

extend_no_proxy_for_localhost

# ---------------------------------------------------------------------------
# Logging helpers (ANSI colours only when stdout is a tty)

if [ -t 1 ]; then
    readonly C_CYAN=$'\033[1;36m'
    readonly C_GREEN=$'\033[1;32m'
    readonly C_RED=$'\033[1;31m'
    readonly C_YELLOW=$'\033[1;33m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_CYAN=""
    readonly C_GREEN=""
    readonly C_RED=""
    readonly C_YELLOW=""
    readonly C_RESET=""
fi

log()  { printf '%s[openbiliclaw]%s %s\n' "$C_CYAN"   "$C_RESET" "$*"; }
ok()   { printf '%s[openbiliclaw]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf '%s[openbiliclaw]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf '%s[openbiliclaw]%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; }

# ---------------------------------------------------------------------------
# Prerequisite checks

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "Missing required command: $cmd"
        case "$cmd" in
            git)     err "  Install: https://git-scm.com/downloads" ;;
            python3) err "  Install Python 3.11+: https://www.python.org/downloads/" ;;
        esac
        exit 1
    fi
}

check_python_version() {
    local version
    version=$(python3 -c 'import sys; print("{}.{}".format(sys.version_info[0], sys.version_info[1]))')
    local major minor
    major=${version%.*}
    minor=${version#*.}
    if (( major < 3 )) || (( major == 3 && minor < 11 )); then
        err "Python 3.11+ required, found $version"
        exit 1
    fi
}

check_platform() {
    case "$(uname -s)" in
        Darwin|Linux) ;;
        MINGW*|MSYS*|CYGWIN*)
            err "Native Windows is not supported. Please install WSL2 and re-run this command."
            exit 1
            ;;
        *)
            warn "Unrecognised platform: $(uname -s). Proceeding anyway."
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Source discovery (auto-reuse existing install)

auto_detect_reuse_source() {
    # If the user explicitly set REUSE_FROM (even to ""), skip auto-detection.
    if [ "$_REUSE_FROM_EXPLICIT" = "1" ]; then
        if [ -n "$REUSE_FROM" ]; then
            log "REUSE_FROM explicitly set to ${C_GREEN}${REUSE_FROM}${C_RESET}"
        else
            log "REUSE_FROM explicitly set to empty — skipping auto-detection."
        fi
        return
    fi
    local cand
    for cand in "${CANDIDATE_SOURCES[@]}"; do
        if [ "$cand" = "$INSTALL_DIR" ]; then
            continue
        fi
        if [ ! -d "$cand" ]; then
            continue
        fi
        # Valid if it has a config.toml OR a bilibili_cookie.json
        if [ -f "$cand/config.toml" ] || [ -f "$cand/data/bilibili_cookie.json" ]; then
            REUSE_FROM="$cand"
            log "Found existing OpenBiliClaw at ${C_GREEN}${REUSE_FROM}${C_RESET} — will reuse API keys and cookie."
            return
        fi
    done
}

# ---------------------------------------------------------------------------
# Main install steps

is_user_data_only_dir() {
    local dir="$1"
    [ -d "$dir" ] || return 1
    local entry name saw=0
    while IFS= read -r -d '' entry; do
        name=$(basename "$entry")
        [ "$name" = ".DS_Store" ] && continue
        case "$name" in
            config.toml|config.local.toml|data|logs|openbiliclaw.lock)
                saw=1
                ;;
            *)
                return 1
                ;;
        esac
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -print0)
    [ "$saw" = "1" ]
}

clone_into_user_data_root() {
    local parent tmp
    parent=$(dirname "$INSTALL_DIR")
    tmp=$(mktemp -d "$parent/openbiliclaw-clone.XXXXXX")
    log "Target contains existing user data only; cloning source into $INSTALL_DIR without touching config/data/logs."
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$tmp"
    while IFS= read -r -d '' entry; do
        local name
        name=$(basename "$entry")
        if [ -e "$INSTALL_DIR/$name" ]; then
            rm -rf "$tmp"
            err "Cannot merge checkout into $INSTALL_DIR: destination exists: $INSTALL_DIR/$name"
            exit 1
        fi
        mv "$entry" "$INSTALL_DIR/"
    done < <(find "$tmp" -mindepth 1 -maxdepth 1 -print0)
    rmdir "$tmp"
}

ensure_checkout() {
    if [ -f "$INSTALL_DIR/pyproject.toml" ] && [ -f "$INSTALL_DIR/config.example.toml" ]; then
        log "Using existing checkout at $INSTALL_DIR"
        # Auto-update when safe: clean working tree + fast-forward available.
        # The previous behaviour silently kept any stale ref, so a user who
        # installed weeks ago and re-ran the one-liner thought they got the
        # latest while still running old code.
        if [ -d "$INSTALL_DIR/.git" ]; then
            (
                cd "$INSTALL_DIR" || exit 0
                git fetch --quiet origin "$BRANCH" 2>/dev/null || {
                    log "${C_YELLOW}git fetch failed; skipping update check.${C_RESET}"
                    exit 0
                }
                local_sha=$(git rev-parse HEAD 2>/dev/null)
                remote_sha=$(git rev-parse "origin/$BRANCH" 2>/dev/null)
                if [ -z "$local_sha" ] || [ -z "$remote_sha" ] || [ "$local_sha" = "$remote_sha" ]; then
                    return 0
                fi
                behind=$(git rev-list --count "$local_sha..$remote_sha" 2>/dev/null || echo "?")
                dirty=$(git status --porcelain 2>/dev/null)
                if [ -n "$dirty" ]; then
                    # `uv sync` / `npm ci` during install rewrite uv.lock and
                    # extension/package-lock.json, leaving the tree "dirty" and
                    # permanently blocking auto-update — users got stranded on
                    # weeks-old code (e.g. v0.3.89) without noticing. If the ONLY
                    # dirty paths are these regenerated lockfiles, discard them
                    # (the dependency step rebuilds them) and update anyway.
                    non_lock=$(git status --porcelain 2>/dev/null \
                        | grep -vE '[[:space:]](uv\.lock|extension/package-lock\.json)$' || true)
                    if [ -z "$non_lock" ]; then
                        log "Local changes are only regenerated lockfiles (uv.lock / package-lock.json) — resetting and updating…"
                        # Reset each present lockfile independently: a single
                        # `git checkout -- a b` aborts entirely if any pathspec is
                        # missing (older checkouts lack package-lock.json), leaving
                        # the tree dirty and the pull below failing.
                        for _lf in uv.lock extension/package-lock.json; do
                            git cat-file -e "HEAD:$_lf" 2>/dev/null \
                                && git checkout HEAD -- "$_lf" 2>/dev/null || true
                        done
                        if git pull --ff-only --quiet origin "$BRANCH"; then
                            log "${C_GREEN}✓ Updated to $(git rev-parse --short HEAD)${C_RESET}"
                        else
                            log "${C_YELLOW}git pull failed after resetting lockfiles; keeping current checkout.${C_RESET}"
                        fi
                        return 0
                    fi
                    # Genuine local edits: make the stale-version risk impossible
                    # to miss (a quiet one-line skip is why people ran old code).
                    cur_ver=$(git show "HEAD:pyproject.toml" 2>/dev/null | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
                    new_ver=$(git show "origin/$BRANCH:pyproject.toml" 2>/dev/null | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
                    log "${C_YELLOW}──────────────────────────────────────────────────────────────${C_RESET}"
                    log "${C_YELLOW}⚠  NOT updated: checkout is $behind commits behind origin/$BRANCH and has local edits.${C_RESET}"
                    log "${C_YELLOW}   Version v${cur_ver:-?} → latest v${new_ver:-?}; continuing will run OLD code.${C_RESET}"
                    log "${C_YELLOW}   Locally modified files:${C_RESET}"
                    git status --porcelain 2>/dev/null | while IFS= read -r _line; do log "     $_line"; done
                    log "${C_YELLOW}   Update (keep your edits): cd $INSTALL_DIR && git stash && git pull --ff-only && git stash pop${C_RESET}"
                    log "${C_YELLOW}   Or let the installer do it (auto git stash first): FORCE_UPDATE=1 <rerun the install command>${C_RESET}"
                    log "${C_YELLOW}──────────────────────────────────────────────────────────────${C_RESET}"
                    if [ "${FORCE_UPDATE:-}" = "1" ]; then
                        log "FORCE_UPDATE=1 → stashing local changes and updating…"
                        if git stash push -u -m "install.sh auto-stash before update" >/dev/null 2>&1 \
                            && git pull --ff-only --quiet origin "$BRANCH"; then
                            log "${C_GREEN}✓ Updated to $(git rev-parse --short HEAD)${C_RESET}"
                            if ! git stash pop >/dev/null 2>&1; then
                                log "${C_YELLOW}  Your edits are saved in git stash but auto-restore hit a conflict — run 'cd $INSTALL_DIR && git stash pop' to resolve.${C_RESET}"
                            fi
                        else
                            log "${C_YELLOW}Auto-update failed; checkout unchanged (edits preserved in git stash — 'git stash pop' to restore).${C_RESET}"
                        fi
                    fi
                    return 0
                fi
                log "Updating existing checkout: $behind commits behind origin/$BRANCH — pulling…"
                if git pull --ff-only --quiet origin "$BRANCH"; then
                    log "${C_GREEN}✓ Updated to $(git rev-parse --short HEAD)${C_RESET}"
                else
                    log "${C_YELLOW}git pull failed (non-fast-forward?); keeping current checkout.${C_RESET}"
                    log "  To force a fresh install: rm -rf $INSTALL_DIR && rerun this installer"
                fi
            )
        fi
        return
    fi

    if [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
        if is_user_data_only_dir "$INSTALL_DIR"; then
            clone_into_user_data_root
            return
        fi
        err "Target directory is not empty and not an OpenBiliClaw checkout: $INSTALL_DIR"
        err "Set INSTALL_DIR to an empty or non-existent path, or remove the existing one first."
        exit 1
    fi

    mkdir -p "$(dirname "$INSTALL_DIR")"
    log "Cloning ${REPO_URL} (branch ${BRANCH}) into ${INSTALL_DIR}"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
}

prepare_install() {
    log "Installing OpenBiliClaw from $INSTALL_DIR"
    (
        cd "$INSTALL_DIR"
        python3 -m pip install -e .
        if [ ! -f config.toml ]; then
            cp config.example.toml config.toml
            log "Created config.toml from the current typed example"
        fi
        openbiliclaw check --config config.toml --data-dir data
    )
}

print_install_summary() {
    local api_host="$HOST"
    if [ "$api_host" = "0.0.0.0" ] || [ "$api_host" = "::" ] || [ "$api_host" = "[::]" ]; then
        api_host="127.0.0.1"
    fi
    echo "================================================================"
    echo "OpenBiliClaw installed and composition readiness passed."
    echo "Checkout: $INSTALL_DIR"
    echo "Start:    cd $INSTALL_DIR && openbiliclaw serve --config config.toml --data-dir data"
    echo "Health:   http://${api_host}:${PORT}/v1/runtime/health"
    echo "Web UI:   http://${api_host}:${PORT}/"
    if [ -n "$SKIP_START" ]; then
        echo "SKIP_START is retained as a no-op; this installer never starts a background service."
    fi
    echo "Edit config.toml to enable providers/model routes, then rerun openbiliclaw check."
    echo "================================================================"
}

main() {
    log "OpenBiliClaw one-command installer"
    check_platform
    require_command git
    require_command python3
    check_python_version

    auto_detect_reuse_source
    ensure_checkout
    prepare_install
    print_install_summary
}

main "$@"
