#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$SOURCE_DIR"
COPY_ENV=""

usage() {
  cat <<'EOF'
Usage: install_openclaw_skill.sh [options]

Copy the GrokSearch OpenClaw plugin bundle to another local directory.
This is only a compatibility helper for local staging or debugging.
Preferred installation path: `openclaw plugins install /path/to/GrokSearch/openclaw`

Options:
  --install-to DIR   Copy the plugin bundle into DIR
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
GrokSearch OpenClaw plugin bundle is ready at: $TARGET_DIR

What changed:
1. Runtime is bundled inside the plugin package
2. No remote downloads were performed
3. No other installed plugins or skills were modified

Next steps:
1. Prefer: openclaw plugins install $TARGET_DIR
2. Configure plugins.entries.grok-search.config.mcp.baseUrl or mcp.url
3. Configure plugins.entries.grok-search.config.mcp.bearerToken
4. Optional: set mcp.healthUrl if health is not served next to /mcp
5. Run a plugin-backed probe from OpenClaw after config is loaded
6. Run a plugin-backed search from OpenClaw after config is loaded
7. Optional legacy diagnostic fallback: python3 $TARGET_DIR/scripts/groksearch_openclaw.py health
EOF
