#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$SOURCE_DIR"
COPY_ENV=""

usage() {
  cat <<'EOF'
Usage: install_openclaw_skill.sh [options]

Prepare the bundled GrokSearch OpenClaw skill for local use.

Options:
  --install-to DIR   Copy the skill bundle into DIR
  --copy-env FILE    Copy FILE to target .env with 0600 permissions
  -h, --help         Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-to)
      TARGET_DIR="${2:?missing dir}"
      shift 2
      ;;
    --copy-env)
      COPY_ENV="${2:?missing env path}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

copy_skill_bundle() {
  if [[ "$TARGET_DIR" == "$SOURCE_DIR" ]]; then
    return
  fi

  mkdir -p "$TARGET_DIR"
  tar -C "$SOURCE_DIR" \
    --exclude='.env' \
    --exclude='__pycache__' \
    -cf - . | tar -C "$TARGET_DIR" -xf -
}

prepare_env_file() {
  if [[ "$SOURCE_DIR/.env.example" != "$TARGET_DIR/.env.example" ]]; then
    install -m 0644 "$SOURCE_DIR/.env.example" "$TARGET_DIR/.env.example"
  fi

  if [[ -n "$COPY_ENV" ]]; then
    install -m 0600 "$COPY_ENV" "$TARGET_DIR/.env"
  fi
}

copy_skill_bundle
prepare_env_file

cat <<EOF
GrokSearch OpenClaw skill is ready at: $TARGET_DIR

Next steps:
1. Prefer injecting env via OpenClaw skill config
2. Minimal setup: GROKSEARCH_MCP_BASE_URL + GROKSEARCH_MCP_BEARER_TOKEN
3. Optional: GROKSEARCH_MCP_URL if your MCP path is not /mcp
4. Run: python3 $TARGET_DIR/scripts/groksearch_openclaw.py probe
EOF
