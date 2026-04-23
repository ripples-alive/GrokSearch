import os
import json
from pathlib import Path


class Config:
    _instance = None
    _SETUP_COMMAND = (
        'claude mcp add-json grok-search --scope user '
        '\'{"type":"stdio","command":"uvx","args":["--from",'
        '"git+https://github.com/GuDaStudio/GrokSearch","grok-search"],'
        '"env":{"GROK_API_URL":"your-api-url","GROK_API_KEY":"your-api-key"}}\''
    )
    _DEFAULT_MODEL = "grok-4-fast"
    _DEFAULT_MCP_TRANSPORT = "stdio"
    _DEFAULT_MCP_HOST = "127.0.0.1"
    _DEFAULT_MCP_PORT = 8000
    _DEFAULT_MCP_STREAMABLE_HTTP_PATH = "/mcp"
    _DEFAULT_MCP_SSE_PATH = "/sse"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config_file = None
            cls._instance._cached_model = None
        return cls._instance

    @staticmethod
    def _first_env(*names: str) -> str | None:
        for name in names:
            value = os.getenv(name)
            if value is not None and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _normalize_path(path: str, default: str) -> str:
        value = path.strip() or default
        if not value.startswith("/"):
            return f"/{value}"
        return value

    @staticmethod
    def _env_bool(*names: str, default: bool = False) -> bool:
        value = Config._first_env(*names)
        if value is None:
            return default
        return value.lower() in ("true", "1", "yes", "on")

    @property
    def config_file(self) -> Path:
        if self._config_file is None:
            config_dir = Path.home() / ".config" / "grok-search"
            try:
                config_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                config_dir = Path.cwd() / ".grok-search"
                config_dir.mkdir(parents=True, exist_ok=True)
            self._config_file = config_dir / "config.json"
        return self._config_file

    def _load_config_file(self) -> dict:
        if not self.config_file.exists():
            return {}
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_config_file(self, config_data: dict) -> None:
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            raise ValueError(f"无法保存配置文件: {str(e)}")

    @property
    def debug_enabled(self) -> bool:
        return os.getenv("GROK_DEBUG", "false").lower() in ("true", "1", "yes")

    @property
    def retry_max_attempts(self) -> int:
        return int(os.getenv("GROK_RETRY_MAX_ATTEMPTS", "3"))

    @property
    def retry_multiplier(self) -> float:
        return float(os.getenv("GROK_RETRY_MULTIPLIER", "1"))

    @property
    def retry_max_wait(self) -> int:
        return int(os.getenv("GROK_RETRY_MAX_WAIT", "10"))

    @property
    def mcp_transport(self) -> str:
        transport = (
            self._first_env("GROK_MCP_TRANSPORT", "MCP_TRANSPORT")
            or self._DEFAULT_MCP_TRANSPORT
        ).lower()
        if transport not in {"stdio", "http", "streamable-http", "sse"}:
            return self._DEFAULT_MCP_TRANSPORT
        return transport

    @property
    def mcp_host(self) -> str:
        return self._first_env("GROK_MCP_HOST", "MCP_HOST") or self._DEFAULT_MCP_HOST

    @property
    def mcp_port(self) -> int:
        value = self._first_env("GROK_MCP_PORT", "MCP_PORT")
        if value is None:
            return self._DEFAULT_MCP_PORT
        try:
            return int(value)
        except ValueError:
            return self._DEFAULT_MCP_PORT

    @property
    def mcp_streamable_http_path(self) -> str:
        value = (
            self._first_env(
                "GROK_MCP_STREAMABLE_HTTP_PATH",
                "MCP_STREAMABLE_HTTP_PATH",
            )
            or self._DEFAULT_MCP_STREAMABLE_HTTP_PATH
        )
        return self._normalize_path(value, self._DEFAULT_MCP_STREAMABLE_HTTP_PATH)

    @property
    def mcp_sse_path(self) -> str:
        value = self._first_env("GROK_MCP_SSE_PATH", "MCP_SSE_PATH") or self._DEFAULT_MCP_SSE_PATH
        return self._normalize_path(value, self._DEFAULT_MCP_SSE_PATH)

    @property
    def mcp_stateless_http(self) -> bool:
        return self._env_bool("GROK_MCP_STATELESS_HTTP", "MCP_STATELESS_HTTP", default=False)

    @property
    def mcp_bearer_token(self) -> str | None:
        return self._first_env("GROK_MCP_BEARER_TOKEN", "MCP_BEARER_TOKEN")

    @property
    def grok_api_url(self) -> str:
        url = os.getenv("GROK_API_URL")
        if not url:
            raise ValueError(
                f"Grok API URL 未配置！\n"
                f"请使用以下命令配置 MCP 服务器：\n{self._SETUP_COMMAND}"
            )
        return url

    @property
    def grok_api_key(self) -> str:
        key = os.getenv("GROK_API_KEY")
        if not key:
            raise ValueError(
                f"Grok API Key 未配置！\n"
                f"请使用以下命令配置 MCP 服务器：\n{self._SETUP_COMMAND}"
            )
        return key

    @property
    def tavily_enabled(self) -> bool:
        return os.getenv("TAVILY_ENABLED", "true").lower() in ("true", "1", "yes")

    @property
    def tavily_api_url(self) -> str:
        return os.getenv("TAVILY_API_URL", "https://api.tavily.com")

    @property
    def tavily_api_key(self) -> str | None:
        return os.getenv("TAVILY_API_KEY")

    @property
    def firecrawl_api_url(self) -> str:
        return os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2")

    @property
    def firecrawl_api_key(self) -> str | None:
        return os.getenv("FIRECRAWL_API_KEY")

    @property
    def log_level(self) -> str:
        return os.getenv("GROK_LOG_LEVEL", "INFO").upper()

    @property
    def log_dir(self) -> Path:
        log_dir_str = os.getenv("GROK_LOG_DIR", "logs")
        log_dir = Path(log_dir_str)
        if log_dir.is_absolute():
            return log_dir

        home_log_dir = Path.home() / ".config" / "grok-search" / log_dir_str
        try:
            home_log_dir.mkdir(parents=True, exist_ok=True)
            return home_log_dir
        except OSError:
            pass

        cwd_log_dir = Path.cwd() / log_dir_str
        try:
            cwd_log_dir.mkdir(parents=True, exist_ok=True)
            return cwd_log_dir
        except OSError:
            pass

        tmp_log_dir = Path("/tmp") / "grok-search" / log_dir_str
        tmp_log_dir.mkdir(parents=True, exist_ok=True)
        return tmp_log_dir

    def _apply_model_suffix(self, model: str) -> str:
        try:
            url = self.grok_api_url
        except ValueError:
            return model
        if "openrouter" in url and ":online" not in model:
            return f"{model}:online"
        return model

    @property
    def grok_model(self) -> str:
        if self._cached_model is not None:
            return self._cached_model

        model = (
            os.getenv("GROK_MODEL")
            or self._load_config_file().get("model")
            or self._DEFAULT_MODEL
        )
        self._cached_model = self._apply_model_suffix(model)
        return self._cached_model

    def set_model(self, model: str) -> None:
        config_data = self._load_config_file()
        config_data["model"] = model
        self._save_config_file(config_data)
        self._cached_model = self._apply_model_suffix(model)

    @staticmethod
    def _mask_api_key(key: str) -> str:
        """脱敏显示 API Key，只显示前后各 4 个字符"""
        if not key or len(key) <= 8:
            return "***"
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

    def get_config_info(self) -> dict:
        """获取配置信息（API Key 已脱敏）"""
        try:
            api_url = self.grok_api_url
            api_key_raw = self.grok_api_key
            api_key_masked = self._mask_api_key(api_key_raw)
            config_status = "✅ 配置完整"
        except ValueError as e:
            api_url = "未配置"
            api_key_masked = "未配置"
            config_status = f"❌ 配置错误: {str(e)}"

        return {
            "GROK_MCP_TRANSPORT": self.mcp_transport,
            "GROK_MCP_HOST": self.mcp_host,
            "GROK_MCP_PORT": self.mcp_port,
            "GROK_MCP_STREAMABLE_HTTP_PATH": self.mcp_streamable_http_path,
            "GROK_MCP_SSE_PATH": self.mcp_sse_path,
            "GROK_MCP_STATELESS_HTTP": self.mcp_stateless_http,
            "GROK_MCP_BEARER_TOKEN": self._mask_api_key(self.mcp_bearer_token) if self.mcp_bearer_token else "未配置",
            "GROK_API_URL": api_url,
            "GROK_API_KEY": api_key_masked,
            "GROK_MODEL": self.grok_model,
            "GROK_DEBUG": self.debug_enabled,
            "GROK_LOG_LEVEL": self.log_level,
            "GROK_LOG_DIR": str(self.log_dir),
            "TAVILY_API_URL": self.tavily_api_url,
            "TAVILY_ENABLED": self.tavily_enabled,
            "TAVILY_API_KEY": self._mask_api_key(self.tavily_api_key) if self.tavily_api_key else "未配置",
            "FIRECRAWL_API_URL": self.firecrawl_api_url,
            "FIRECRAWL_API_KEY": self._mask_api_key(self.firecrawl_api_key) if self.firecrawl_api_key else "未配置",
            "config_status": config_status
        }


config = Config()
