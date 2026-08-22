#!/usr/bin/env bash
# grok-mcp one-command installer (unofficial community project)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --project "$HOME/code/myapp"
#   bash scripts/install.sh --project /path/to/project
#
# What this does:
#   - ensures Python 3.10+ (via uv if needed)
#   - clones/updates this repo
#   - creates venv + installs package
#   - writes env file + MCP client snippets
#   - runs self-test
# What this CANNOT fully automate:
#   - Grok CLI install/login (interactive product login — you must finish grok login)

set -euo pipefail

REPO_URL="${GROK_MCP_REPO_URL:-https://github.com/zai-one/grok-mcp.git}"
INSTALL_DIR="${GROK_MCP_HOME:-$HOME/.local/share/grok-mcp}"
PROJECT_ROOT=""
LANES_PARENT=""
SKIP_CLONE=0
NONINTERACTIVE=0
ASSUME_YES=0

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLU=$'\033[34m'; BLD=$'\033[1m'; RST=$'\033[0m'

log()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
err()  { printf '%s✗%s %s\n' "$RED" "$RST" "$*" >&2; }
die()  { err "$*"; exit 1; }
header() {
  printf '\n%s%s%s\n' "$BLD" "$*" "$RST"
  printf '%s\n' "────────────────────────────────────────"
}

usage() {
  cat <<'USAGE'
grok-mcp installer (unofficial — not xAI/Grok)

  bash scripts/install.sh [options]
  curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh | bash -s -- [options]

Options:
  --project PATH     Project folder the MCP may touch (required for auto-config)
  --lanes PATH       Worktree lanes parent (optional; default is <project>/.grok/lanes)
  --home PATH        Install location (default: ~/.local/share/grok-mcp)
  --repo URL         Git clone URL
  --yes              Non-interactive defaults where safe
  --help             This help

After install you STILL must:
  1) have Grok CLI on PATH
  2) run: grok login
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT_ROOT="${2:-}"; shift 2 ;;
    --lanes) LANES_PARENT="${2:-}"; shift 2 ;;
    --home) INSTALL_DIR="${2:-}"; shift 2 ;;
    --repo) REPO_URL="${2:-}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; NONINTERACTIVE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# Detect if we are already inside a grok-mcp checkout
if [ -f "pyproject.toml" ] && grep -q 'name = "grok-delegate"' pyproject.toml 2>/dev/null; then
  INSTALL_DIR="$(pwd -P)"
  SKIP_CLONE=1
fi

header "1/7  Unofficial grok-mcp installer"
log "Install dir: $INSTALL_DIR"
log "This is NOT an official xAI/Grok product."
log "No OAuth tokens will be written into config files."

# ---------- Python / uv ----------
header "2/7  Python 3.10+"

have_cmd() { command -v "$1" >/dev/null 2>&1; }

PY=""
pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if have_cmd "$c"; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        PY="$c"
        return 0
      fi
    fi
  done
  return 1
}

ensure_uv() {
  if have_cmd uv; then
    ok "uv found: $(command -v uv)"
    return 0
  fi
  warn "uv not found — installing uv (brings managed Python if needed)"
  if have_cmd curl; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif have_cmd wget; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    die "Need curl or wget to install uv (or install Python 3.10+ yourself)"
  fi
  # shell path for current session
  export PATH="$HOME/.local/bin:$PATH"
  have_cmd uv || die "uv installed but not on PATH — open a new terminal and re-run"
  ok "uv ready"
}

if pick_python; then
  ok "Python OK: $PY ($($PY -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
else
  warn "No Python 3.10+ on PATH"
  ensure_uv
  # uv can provide python
  uv python install 3.12 >/dev/null
  PY="$(uv python find 3.12)"
  ok "Using uv-managed Python: $PY"
fi

# Prefer uv for venv if available
USE_UV=0
if have_cmd uv; then
  USE_UV=1
elif [ -z "${PY:-}" ]; then
  ensure_uv
  USE_UV=1
  pick_python || true
fi

# ---------- clone ----------
header "3/7  Fetch grok-mcp"

if [ "$SKIP_CLONE" -eq 1 ]; then
  ok "Already inside repo checkout: $INSTALL_DIR"
else
  if [ -d "$INSTALL_DIR/.git" ]; then
    ok "Existing install — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only || warn "git pull failed — using existing tree"
  else
    have_cmd git || die "git is required (install git, then re-run)"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned to $INSTALL_DIR"
  fi
fi
cd "$INSTALL_DIR"

# ---------- venv + package ----------
header "4/7  Virtualenv + package"

if [ "$USE_UV" -eq 1 ] && have_cmd uv; then
  uv venv --clear .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -e ".[test]"
else
  "$PY" -m venv --clear .venv 2>/dev/null || { rm -rf .venv; "$PY" -m venv .venv; }
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip setuptools wheel
  python -m pip install -e ".[test]"
fi
ok "Package installed (venv: $INSTALL_DIR/.venv)"

VENV_PY="$INSTALL_DIR/.venv/bin/python"
VENV_GROK_DELEGATE="$INSTALL_DIR/.venv/bin/grok-delegate"
[ -x "$VENV_GROK_DELEGATE" ] || die "grok-delegate entrypoint missing after install"

# ---------- project roots ----------
header "5/7  Project paths"

if [ -z "$PROJECT_ROOT" ]; then
  if [ "$NONINTERACTIVE" -eq 1 ]; then
    PROJECT_ROOT="$HOME"
    warn "No --project given; defaulting ALLOWED_ROOTS to \$HOME ($PROJECT_ROOT)"
    warn "Tighten this later — home is broad."
  elif [ -t 0 ]; then
    printf 'Project folder this MCP may edit [%s]: ' "$HOME"
    read -r ans || true
    PROJECT_ROOT="${ans:-$HOME}"
  else
    PROJECT_ROOT="$HOME"
    warn "Non-tty and no --project; using \$HOME"
  fi
fi

# resolve absolute
PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P)" || die "Project path does not exist: set --project to a real folder"
# Omit GROK_DELEGATE_LANES_PARENT unless --lanes was given: the bridge default
# is <project>/.grok/lanes. A sibling of the project used to land unmerged
# work where README and AGENTS.md say it must not go.
LANES_EXPORT=""
if [ -n "$LANES_PARENT" ]; then
  mkdir -p "$LANES_PARENT"
  LANES_PARENT="$(cd "$LANES_PARENT" && pwd -P)" || die "lanes path does not exist: $LANES_PARENT"
  LANES_EXPORT="export GROK_DELEGATE_LANES_PARENT=\"$LANES_PARENT\""
fi

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/grok-mcp"
mkdir -p "$CONFIG_DIR"
ENV_FILE="$CONFIG_DIR/env"
cat > "$ENV_FILE" <<ENV
# Generated by grok-mcp install.sh — NO secrets / OAuth here
# Unofficial community bridge — not xAI/Grok official
export GROK_DELEGATE_ALLOWED_ROOTS="$PROJECT_ROOT"
${LANES_EXPORT}
export GROK_DELEGATE_ECONOMY=1
export GROK_DELEGATE_ECONOMY_COMPACT_POLL=1
ENV
ok "Wrote $ENV_FILE"

# wrapper launcher
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
WRAPPER="$BIN_DIR/grok-mcp"
cat > "$WRAPPER" <<WRAP
#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1090
source "$ENV_FILE"
exec "$VENV_GROK_DELEGATE" "\$@"
WRAP
chmod +x "$WRAPPER"
ok "Launcher: $WRAPPER  (add \$HOME/.local/bin to PATH if needed)"

# MCP client snippets
MCP_DIR="$CONFIG_DIR/mcp"
mkdir -p "$MCP_DIR"
cat > "$MCP_DIR/claude_desktop.snippet.json" <<JSON
{
  "mcpServers": {
    "grok-delegate": {
      "command": "$WRAPPER",
      "args": [],
      "env": {}
    }
  }
}
JSON
cat > "$MCP_DIR/cursor.snippet.json" <<JSON
{
  "mcpServers": {
    "grok-delegate": {
      "command": "$WRAPPER",
      "args": []
    }
  }
}
JSON
ok "Client snippets: $MCP_DIR/"

# ---------- Grok CLI gate ----------
header "6/7  Grok CLI (required — not skippable)"

GROK_OK=0
if have_cmd grok; then
  ok "grok on PATH: $(command -v grok)"
  GROK_OK=1
else
  err "Grok CLI NOT found on PATH"
  log ""
  log "Install Grok CLI from xAI / grok.x.ai docs for your OS, then:"
  log "  grok login"
  log "  grok --version"
  log ""
  log "This installer cannot log you into Grok (browser/device login is interactive)."
fi

# ---------- self-test ----------
header "7/7  Self-test"

# shellcheck disable=SC1090
source "$ENV_FILE"
set +e
"$VENV_PY" -m grok_delegate --self-test
ST=$?
set -e

if [ $ST -eq 0 ]; then
  ok "Self-test PASS"
else
  warn "Self-test did not fully pass (often missing grok login)"
  log "After: grok login   → re-run:  $VENV_PY -m grok_delegate --self-test"
fi

# ---------- done ----------
header "Done — next 60 seconds"
cat <<DONE

${BLD}What you have now${RST}
  Install:   $INSTALL_DIR
  Env:       $ENV_FILE
  Launcher:  $WRAPPER
  Snippets:  $MCP_DIR/

${BLD}If you are not a developer — do only this:${RST}
  1) Install Grok CLI (if step 6 failed)
  2) Run:  ${GRN}grok login${RST}
  3) Run:  ${GRN}$VENV_PY -m grok_delegate --self-test${RST}
     → binary PASS + auth present
  4) Claude Desktop / Cursor:
     merge JSON from $MCP_DIR/claude_desktop.snippet.json
     (restart the app)
  5) In chat: call tool ${GRN}grok_agent_status${RST} then ${GRN}grok_agent_economy${RST}

${BLD}One-liner re-check later${RST}
  source $ENV_FILE && $VENV_PY -m grok_delegate --self-test

${YLW}Unofficial community project — not affiliated with xAI/Grok.${RST}
Docs: $INSTALL_DIR/docs/START_HERE.md

DONE

# Exit 0 only when CLI present AND self-test fully green
if [ "$GROK_OK" -ne 1 ]; then
  warn "Finished install, but Grok CLI is missing — exit 2"
  exit 2
fi
if [ "${ST:-1}" -ne 0 ]; then
  warn "Finished install, but self-test failed — exit 2 (usually: grok login)"
  exit 2
fi
ok "Fully ready"
exit 0
