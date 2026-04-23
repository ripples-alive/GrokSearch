from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parent
ROOT_DIR = MODULE_DIR.parent.parent
DEFAULT_HTTP_USER_AGENT = "GrokSearch-OpenClaw/0.2"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_mapping_env(raw_env: dict[str, object]) -> None:
    for key, value in raw_env.items():
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned:
            os.environ.setdefault(key, cleaned)


def _load_openclaw_skill_env() -> None:
    candidates: list[Path] = []
    explicit = os.getenv("OPENCLAW_CONFIG_PATH", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(ROOT_DIR.parents[1] / "openclaw.json")

    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env = (((config.get("skills") or {}).get("entries") or {}).get("grok-search") or {}).get("env") or {}
        if isinstance(env, dict):
            _load_mapping_env(env)
            return


def _bootstrap_runtime_env() -> None:
    _load_openclaw_skill_env()
    _load_env_file(ROOT_DIR / ".env")
    _load_env_file(ROOT_DIR / "runtime" / ".env")


def _get_str(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _derive_health_url(mcp_url: str) -> str:
    trimmed = mcp_url.rstrip("/")
    if "/" not in trimmed:
        return f"{trimmed}/health"
    base, _, _ = trimmed.rpartition("/")
    return f"{base}/health"


_bootstrap_runtime_env()


@dataclass(slots=True)
class GrokSearchConfig:
    mcp_url: str
    health_url: str
    bearer_token: str
    http_user_agent: str
    timeout_seconds: float
    tool_timeout_seconds: float
    verify_timeout_seconds: float
    research_page_limit: int
    research_excerpt_chars: int

    @classmethod
    def from_env(cls) -> "GrokSearchConfig":
        explicit_url = _get_str("GROKSEARCH_MCP_URL")
        if explicit_url:
            mcp_url = explicit_url.rstrip("/")
        else:
            base = _get_str("GROKSEARCH_MCP_BASE_URL")
            if not base:
                raise RuntimeError("GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL is required")
            mcp_url = f"{base.rstrip('/')}/mcp"

        health_url = _get_str("GROKSEARCH_HEALTH_URL")
        if not health_url:
            health_url = _derive_health_url(mcp_url)

        return cls(
            mcp_url=mcp_url,
            health_url=health_url.rstrip("/"),
            bearer_token=_get_str("GROKSEARCH_MCP_BEARER_TOKEN"),
            http_user_agent=_get_str("GROKSEARCH_HTTP_USER_AGENT", default=DEFAULT_HTTP_USER_AGENT),
            timeout_seconds=_get_float("GROKSEARCH_TIMEOUT_SECONDS", 30.0),
            tool_timeout_seconds=_get_float("GROKSEARCH_TOOL_TIMEOUT_SECONDS", 120.0),
            verify_timeout_seconds=_get_float("GROKSEARCH_VERIFY_TIMEOUT_SECONDS", 10.0),
            research_page_limit=_get_int("GROKSEARCH_RESEARCH_PAGE_LIMIT", 3),
            research_excerpt_chars=_get_int("GROKSEARCH_RESEARCH_EXCERPT_CHARS", 1200),
        )

    def headers(self, *, accept_sse: bool = False) -> dict[str, str]:
        headers = {"User-Agent": self.http_user_agent}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if accept_sse:
            headers["Accept"] = "text/event-stream"
        return headers

    def describe(self) -> dict[str, object]:
        return {
            "mcp_url": self.mcp_url,
            "health_url": self.health_url,
            "has_bearer_token": bool(self.bearer_token),
            "http_user_agent": self.http_user_agent,
            "timeout_seconds": self.timeout_seconds,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "verify_timeout_seconds": self.verify_timeout_seconds,
            "research_page_limit": self.research_page_limit,
            "research_excerpt_chars": self.research_excerpt_chars,
        }
