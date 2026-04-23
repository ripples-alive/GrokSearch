export const PLUGIN_ID = "grok-search";
export const PROVIDER_ID = "groksearch";
export const DEFAULT_HTTP_USER_AGENT = "GrokSearch-OpenClaw/0.3";
const DEFAULT_PROTOCOL_VERSION = "2025-11-25";

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function readNested(root, path) {
  let current = root;
  for (const key of path) {
    const record = asRecord(current);
    if (!(key in record)) {
      return undefined;
    }
    current = record[key];
  }
  return current;
}

function parseMaybeNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.trim());
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

export function normalizeString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

export function normalizeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function normalizeInteger(value) {
  const number = normalizeNumber(value);
  return number === undefined ? undefined : Math.trunc(number);
}

function getEnvString(name, fallback = "") {
  const value = process.env[name];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function getEnvNumber(name, fallback) {
  const parsed = parseMaybeNumber(process.env[name]);
  return parsed === undefined ? fallback : parsed;
}

function deriveHealthUrl(mcpUrl) {
  const trimmed = normalizeString(mcpUrl).replace(/\/+$/, "");
  if (!trimmed) {
    return "";
  }
  const slash = trimmed.lastIndexOf("/");
  return slash === -1 ? `${trimmed}/health` : `${trimmed.slice(0, slash)}/health`;
}

function resolveMcpUrl(mcp) {
  const explicit = normalizeString(mcp.url) || getEnvString("GROKSEARCH_MCP_URL");
  if (explicit) {
    return explicit.replace(/\/+$/, "");
  }

  const baseUrl = normalizeString(mcp.baseUrl) || getEnvString("GROKSEARCH_MCP_BASE_URL");
  if (!baseUrl) {
    throw new Error("GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL is required");
  }
  return `${baseUrl.replace(/\/+$/, "")}/mcp`;
}

function resolveRuntimeSettings(pluginConfig = {}, bearerToken = "") {
  const mcp = asRecord(pluginConfig.mcp);
  const mcpUrl = resolveMcpUrl(mcp);
  const healthUrl =
    normalizeString(mcp.healthUrl) ||
    getEnvString("GROKSEARCH_HEALTH_URL") ||
    deriveHealthUrl(mcpUrl);

  return {
    mcpUrl,
    healthUrl: healthUrl.replace(/\/+$/, ""),
    bearerToken: normalizeString(bearerToken) || getEnvString("GROKSEARCH_MCP_BEARER_TOKEN"),
    httpUserAgent:
      normalizeString(mcp.httpUserAgent) ||
      getEnvString("GROKSEARCH_HTTP_USER_AGENT") ||
      DEFAULT_HTTP_USER_AGENT,
    toolTimeoutSeconds:
      parseMaybeNumber(mcp.toolTimeoutSeconds) ??
      getEnvNumber("GROKSEARCH_TOOL_TIMEOUT_SECONDS", 120),
    verifyTimeoutSeconds:
      parseMaybeNumber(mcp.verifyTimeoutSeconds) ??
      getEnvNumber("GROKSEARCH_VERIFY_TIMEOUT_SECONDS", 10),
    researchPageLimit: getEnvNumber("GROKSEARCH_RESEARCH_PAGE_LIMIT", 3),
    researchExcerptChars: getEnvNumber("GROKSEARCH_RESEARCH_EXCERPT_CHARS", 1200),
  };
}

function timeoutSignal(timeoutSeconds) {
  const timeoutMs = Math.max(1, Number(timeoutSeconds || 0)) * 1000;
  return AbortSignal.timeout(timeoutMs);
}

async function readResponseText(response) {
  return await response.text();
}

function truncateText(text, limit = 400) {
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, Math.max(0, limit - 3))}...`;
}

function parseJsonObject(text, emptyValue = {}) {
  if (!normalizeString(text)) {
    return emptyValue;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`failed to parse JSON response: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("response JSON is not an object");
  }
  return parsed;
}

function parseSseJson(text) {
  let lastPayload = null;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line.startsWith("data:")) {
      continue;
    }
    const payload = line.slice(5).trim();
    if (!payload) {
      continue;
    }
    let parsed;
    try {
      parsed = JSON.parse(payload);
    } catch {
      continue;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      continue;
    }
    lastPayload = parsed;
    if ("result" in parsed || "error" in parsed) {
      return parsed;
    }
  }
  if (lastPayload) {
    return lastPayload;
  }
  throw new Error("remote MCP response did not include SSE data payload");
}

async function httpRequestJson({
  url,
  method = "GET",
  headers = {},
  body,
  timeoutSeconds,
  parseSse = false,
}) {
  const response = await fetch(url, {
    method,
    headers,
    body,
    signal: timeoutSignal(timeoutSeconds),
  });
  const text = await readResponseText(response);
  if (!response.ok) {
    throw new Error(`remote MCP HTTP ${response.status}: ${truncateText(text)}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (parseSse || contentType.includes("text/event-stream")) {
    return {
      payload: parseSseJson(text),
      headers: response.headers,
      status: response.status,
      contentType,
      rawBody: text,
    };
  }
  return {
    payload: parseJsonObject(text),
    headers: response.headers,
    status: response.status,
    contentType,
    rawBody: text,
  };
}

function textFromToolResult(result) {
  const parts = [];
  for (const item of result.content || []) {
    if (item && typeof item === "object" && item.type === "text" && typeof item.text === "string" && item.text) {
      parts.push(item.text);
    }
  }
  return parts.join("\n").trim();
}

function jsonFromToolResult(result) {
  const structured = result.structuredContent;
  if (structured && typeof structured === "object" && !Array.isArray(structured)) {
    return structured;
  }

  const text = textFromToolResult(result);
  if (!text) {
    return {};
  }
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("tool returned JSON that is not an object");
  }
  return parsed;
}

function looksLikeErrorText(text) {
  const lowered = normalizeString(text).toLowerCase();
  const markers = [
    "配置错误",
    "提取失败",
    "映射错误",
    "映射超时",
    "http错误",
    "missing ",
    "not configured",
    "failed",
    "error:",
  ];
  return markers.some((marker) => lowered.includes(marker));
}

class McpHttpSession {
  constructor(settings) {
    this.settings = settings;
    this.requestId = 1;
    this.sessionId = "";
    this.protocolVersion = "";
  }

  baseHeaders() {
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "User-Agent": this.settings.httpUserAgent,
    };
    if (this.settings.bearerToken) {
      headers.Authorization = `Bearer ${this.settings.bearerToken}`;
    }
    if (this.sessionId) {
      headers["mcp-session-id"] = this.sessionId;
    }
    if (this.protocolVersion) {
      headers["mcp-protocol-version"] = this.protocolVersion;
    }
    return headers;
  }

  async postJson(payload, { expectResponse = true } = {}) {
    const response = await fetch(this.settings.mcpUrl, {
      method: "POST",
      headers: this.baseHeaders(),
      body: JSON.stringify(payload),
      signal: timeoutSignal(this.settings.toolTimeoutSeconds),
    });
    const text = await readResponseText(response);
    const nextSessionId = normalizeString(response.headers.get("mcp-session-id"));
    if (nextSessionId) {
      this.sessionId = nextSessionId;
    }

    if (!response.ok) {
      throw new Error(`remote MCP HTTP ${response.status}: ${truncateText(text)}`);
    }
    if (!expectResponse) {
      return {};
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("text/event-stream")) {
      return parseSseJson(text);
    }
    return parseJsonObject(text);
  }

  async initialize() {
    const result = await this.postJson({
      jsonrpc: "2.0",
      id: this.requestId++,
      method: "initialize",
      params: {
        protocolVersion: DEFAULT_PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: "groksearch-openclaw", version: "0.3.0" },
      },
    });
    const initResult = asRecord(result.result);
    this.protocolVersion = normalizeString(initResult.protocolVersion) || DEFAULT_PROTOCOL_VERSION;
    await this.postJson(
      {
        jsonrpc: "2.0",
        method: "notifications/initialized",
      },
      { expectResponse: false }
    );
  }

  async listTools() {
    const result = await this.postJson({
      jsonrpc: "2.0",
      id: this.requestId++,
      method: "tools/list",
      params: {},
    });
    return Array.isArray(result.result?.tools) ? result.result.tools.filter((item) => item && typeof item === "object") : [];
  }

  async callTool(name, argumentsObject = {}) {
    const result = await this.postJson({
      jsonrpc: "2.0",
      id: this.requestId++,
      method: "tools/call",
      params: {
        name,
        arguments: argumentsObject,
      },
    });
    if (result.error) {
      const error = asRecord(result.error);
      throw new Error(`remote MCP error calling ${name}: ${normalizeString(error.message) || "unknown error"}`);
    }
    const payload = result.result;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error(`remote MCP returned invalid tool result for ${name}`);
    }
    if (payload.isError) {
      throw new Error(textFromToolResult(payload) || `remote tool ${name} returned an error`);
    }
    return payload;
  }
}

async function createSession(settings) {
  const session = new McpHttpSession(settings);
  await session.initialize();
  return session;
}

async function probeRuntime(settings) {
  let response;
  let body;
  try {
    response = await fetch(settings.mcpUrl, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        "User-Agent": settings.httpUserAgent,
        ...(settings.bearerToken ? { Authorization: `Bearer ${settings.bearerToken}` } : {}),
      },
      signal: timeoutSignal(settings.verifyTimeoutSeconds),
    });
    body = await readResponseText(response);
  } catch (error) {
    throw new Error(`request failed: ${error instanceof Error ? error.message : String(error)}`);
  }

  const payload = {
    url: settings.mcpUrl,
    status_code: response.status,
    body,
    user_agent: settings.httpUserAgent,
    ok: [200, 400, 401, 406].includes(response.status),
  };
  const lowered = body.toLowerCase();
  if (response.status === 403 && lowered.includes("cloudflare") && lowered.includes("1010")) {
    payload.note =
      "remote endpoint is reachable but blocked by Cloudflare/WAF (error 1010). This is not a local OpenClaw install failure.";
  } else if (response.status === 403 && lowered.includes("cloudflare")) {
    payload.note = "remote endpoint is reachable but blocked by Cloudflare/WAF.";
  }
  return payload;
}

async function healthHttp(settings) {
  try {
    const response = await fetch(settings.healthUrl, {
      method: "GET",
      headers: {
        "User-Agent": settings.httpUserAgent,
        ...(settings.bearerToken ? { Authorization: `Bearer ${settings.bearerToken}` } : {}),
      },
      signal: timeoutSignal(settings.verifyTimeoutSeconds),
    });
    const body = await readResponseText(response);
    return {
      url: settings.healthUrl,
      status_code: response.status,
      body,
      ok: response.status === 200,
    };
  } catch (error) {
    throw new Error(`request failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function listTools(settings) {
  const session = await createSession(settings);
  const tools = await session.listTools();
  return tools
    .map((tool) => normalizeString(tool.name))
    .filter(Boolean);
}

async function getConfigInfo(settings) {
  const session = await createSession(settings);
  const result = await session.callTool("get_config_info", {});
  return jsonFromToolResult(result);
}

async function searchRuntime(settings, params) {
  const query = normalizeString(params.query);
  if (!query) {
    throw new Error("search requires query");
  }

  const session = await createSession(settings);
  const result = await session.callTool("web_search", {
    query,
    platform: normalizeString(params.platform),
    model: normalizeString(params.model),
    extra_sources: normalizeInteger(params.extra_sources ?? params.extraSources) ?? 0,
  });
  const payload = jsonFromToolResult(result);
  const sessionId = normalizeString(payload.session_id);
  if (sessionId) {
    try {
      const sourcesResult = await session.callTool("get_sources", { session_id: sessionId });
      const sources = jsonFromToolResult(sourcesResult);
      payload.sources = Array.isArray(sources.sources) ? sources.sources : [];
      payload.sources_count = normalizeInteger(sources.sources_count) ?? payload.sources?.length ?? payload.sources_count ?? 0;
    } catch (error) {
      payload.sources_error = error instanceof Error ? error.message : String(error);
    }
  } else if (!Array.isArray(payload.sources)) {
    payload.sources = [];
  }
  return payload;
}

async function getSourcesRuntime(settings, params) {
  const sessionId = normalizeString(params.session_id ?? params.sessionId);
  if (!sessionId) {
    throw new Error("sources requires session_id");
  }

  const session = await createSession(settings);
  const result = await session.callTool("get_sources", { session_id: sessionId });
  return jsonFromToolResult(result);
}

async function extractRuntime(settings, params) {
  const url = normalizeString(params.url);
  if (!url) {
    throw new Error("extract requires url");
  }

  const session = await createSession(settings);
  const result = await session.callTool("web_fetch", { url });
  const content = textFromToolResult(result);
  const payload = {
    url,
    content,
    provider: "groksearch-mcp",
  };
  if (content && looksLikeErrorText(content)) {
    payload.warning = content;
  }
  return payload;
}

async function mapRuntime(settings, params) {
  const url = normalizeString(params.url);
  if (!url) {
    throw new Error("map requires url");
  }

  const session = await createSession(settings);
  const result = await session.callTool("web_map", {
    url,
    instructions: normalizeString(params.instructions),
    max_depth: normalizeInteger(params.max_depth ?? params.maxDepth) ?? 1,
    max_breadth: normalizeInteger(params.max_breadth ?? params.maxBreadth) ?? 20,
    limit: normalizeInteger(params.limit) ?? 50,
    timeout: normalizeInteger(params.timeout) ?? 150,
  });

  const structured = asRecord(result.structuredContent);
  const text = normalizeString(structured.result) || textFromToolResult(result);
  if (!text) {
    return { url, results: [] };
  }

  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      parsed.url ??= url;
      if (looksLikeErrorText(text) && !parsed.warning) {
        parsed.warning = text;
      }
      return parsed;
    }
  } catch {
    // fall through to raw payload
  }

  const payload = { url, raw: text };
  if (looksLikeErrorText(text)) {
    payload.warning = text;
  }
  return payload;
}

async function researchRuntime(settings, params) {
  const query = normalizeString(params.query);
  if (!query) {
    throw new Error("research requires query");
  }

  const search = await searchRuntime(settings, {
    query,
    platform: normalizeString(params.platform),
    extra_sources: normalizeInteger(params.extra_sources ?? params.extraSources) ?? 3,
  });

  const sourceItems = Array.isArray(search.sources)
    ? search.sources.filter((item) => item && typeof item === "object" && normalizeString(item.url))
    : [];
  const candidateUrls = [];
  const pageLimit = Math.max(1, normalizeInteger(settings.researchPageLimit) ?? 3);
  for (const item of sourceItems) {
    const sourceUrl = normalizeString(item.url);
    if (!sourceUrl || candidateUrls.includes(sourceUrl)) {
      continue;
    }
    candidateUrls.push(sourceUrl);
    if (candidateUrls.length >= pageLimit) {
      break;
    }
  }

  const pages = [];
  for (const sourceUrl of candidateUrls) {
    try {
      const extracted = await extractRuntime(settings, { url: sourceUrl });
      let excerpt = normalizeString(extracted.content);
      const excerptLimit = Math.max(200, normalizeInteger(settings.researchExcerptChars) ?? 1200);
      if (excerpt.length > excerptLimit) {
        excerpt = `${excerpt.slice(0, excerptLimit - 3)}...`;
      }
      const page = {
        url: sourceUrl,
        provider: extracted.provider || "groksearch-mcp",
        excerpt,
      };
      if (extracted.warning) {
        page.warning = extracted.warning;
      }
      pages.push(page);
    } catch (error) {
      pages.push({
        url: sourceUrl,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const payload = {
    provider: "groksearch-mcp",
    query,
    platform: normalizeString(params.platform),
    search,
    pages,
    evidence: {
      source_count: sourceItems.length,
      page_count: pages.filter((page) => !page.error).length,
      provider: "groksearch-mcp",
      verification: "single-provider",
    },
  };

  const mapUrl = normalizeString(params.map_url ?? params.mapUrl);
  if (mapUrl) {
    payload.map = await mapRuntime(settings, {
      url: mapUrl,
      instructions: normalizeString(params.map_instructions ?? params.mapInstructions),
      max_depth: normalizeInteger(params.max_depth ?? params.maxDepth) ?? 1,
      max_breadth: normalizeInteger(params.max_breadth ?? params.maxBreadth) ?? 20,
      limit: normalizeInteger(params.limit) ?? 50,
      timeout: normalizeInteger(params.timeout) ?? 150,
    });
  }

  return payload;
}

export function resolvePluginConfig(config) {
  return asRecord(readNested(config, ["plugins", "entries", PLUGIN_ID, "config"]));
}

export function resolveDefaults(pluginConfig = {}) {
  const search = asRecord(pluginConfig.defaults?.search);
  const map = asRecord(pluginConfig.defaults?.map);
  const research = asRecord(pluginConfig.defaults?.research);

  return {
    search: {
      platform: normalizeString(search.platform),
      model: normalizeString(search.model),
      extraSources: normalizeInteger(search.extraSources),
    },
    map: {
      maxDepth: normalizeInteger(map.maxDepth),
      maxBreadth: normalizeInteger(map.maxBreadth),
      limit: normalizeInteger(map.limit),
      timeout: normalizeInteger(map.timeout),
    },
    research: {
      extraSources: normalizeInteger(research.extraSources),
    },
  };
}

export async function runWrapperCommand({ command, params = {}, pluginConfig = {}, bearerToken = "" }) {
  const settings = resolveRuntimeSettings(pluginConfig, bearerToken);

  switch (command) {
    case "probe":
      return await probeRuntime(settings);
    case "health": {
      const probe = await probeRuntime(settings);
      let tools = [];
      let toolError = "";
      let remoteConfig = {};
      try {
        tools = await listTools(settings);
        remoteConfig = await getConfigInfo(settings);
      } catch (error) {
        toolError = error instanceof Error ? error.message : String(error);
      }
      let http = null;
      try {
        http = await healthHttp(settings);
      } catch {
        http = null;
      }
      return {
        runtime: {
          mcp_url: settings.mcpUrl,
          health_url: settings.healthUrl,
          has_bearer_token: Boolean(settings.bearerToken),
          http_user_agent: settings.httpUserAgent,
          tool_timeout_seconds: settings.toolTimeoutSeconds,
          verify_timeout_seconds: settings.verifyTimeoutSeconds,
          research_page_limit: settings.researchPageLimit,
          research_excerpt_chars: settings.researchExcerptChars,
        },
        probe,
        ...(http ? { health_http: http } : {}),
        tools,
        tool_error: toolError,
        remote_config: remoteConfig,
      };
    }
    case "config":
      return await getConfigInfo(settings);
    case "sources":
      return await getSourcesRuntime(settings, params);
    case "search":
      return await searchRuntime(settings, params);
    case "extract":
      return await extractRuntime(settings, params);
    case "map":
      return await mapRuntime(settings, params);
    case "research":
      return await researchRuntime(settings, params);
    default:
      throw new Error(`unsupported GrokSearch command: ${command}`);
  }
}

export function createSearchProviderPayload(payload) {
  const sources = Array.isArray(payload.sources) ? payload.sources : [];
  return {
    query: normalizeString(payload.query),
    provider: PROVIDER_ID,
    sessionId: normalizeString(payload.session_id),
    count: sources.length,
    sourcesCount: normalizeInteger(payload.sources_count) ?? sources.length,
    answer: normalizeString(payload.content),
    results: sources.map((item) => {
      const source = asRecord(item);
      return {
        title: normalizeString(source.title) || normalizeString(source.url) || "Untitled Source",
        url: normalizeString(source.url),
        description:
          normalizeString(source.description) ||
          normalizeString(source.content) ||
          normalizeString(source.summary) ||
          normalizeString(source.text) ||
          normalizeString(source.excerpt),
        score: normalizeNumber(source.score),
        siteName: normalizeString(source.provider),
      };
    }),
  };
}

export function createFetchProviderPayload(payload) {
  const content = normalizeString(payload.content);
  return {
    url: normalizeString(payload.url),
    finalUrl: normalizeString(payload.url),
    extractor: normalizeString(payload.provider) || PROVIDER_ID,
    extractMode: "text",
    rawLength: content.length,
    text: content,
    warning: normalizeString(payload.warning),
  };
}

function ensureObject(target, key) {
  const record = asRecord(target[key]);
  if (target[key] === record) {
    return record;
  }
  target[key] = record;
  return record;
}

export function applySearchSelection(config) {
  const next = config ?? {};
  const plugins = ensureObject(next, "plugins");
  const entries = ensureObject(plugins, "entries");
  const pluginEntry = ensureObject(entries, PLUGIN_ID);
  if (pluginEntry.enabled === undefined) {
    pluginEntry.enabled = true;
  }

  const tools = ensureObject(next, "tools");
  const web = ensureObject(tools, "web");
  const search = ensureObject(web, "search");
  search.provider = PROVIDER_ID;
  return next;
}

export function applyFetchSelection(config) {
  const next = config ?? {};
  const plugins = ensureObject(next, "plugins");
  const entries = ensureObject(plugins, "entries");
  const pluginEntry = ensureObject(entries, PLUGIN_ID);
  if (pluginEntry.enabled === undefined) {
    pluginEntry.enabled = true;
  }

  const tools = ensureObject(next, "tools");
  const web = ensureObject(tools, "web");
  const fetchTool = ensureObject(web, "fetch");
  fetchTool.provider = PROVIDER_ID;
  return next;
}
