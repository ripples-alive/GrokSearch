import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const PLUGIN_ID = "grok-search";
export const PROVIDER_ID = "groksearch";
export const PYTHON_BIN = process.env.GROKSEARCH_PYTHON_BIN || "python3";
export const WRAPPER_SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "scripts", "groksearch_openclaw.py");

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

export function buildWrapperEnv({ pluginConfig = {}, bearerToken = "" } = {}) {
  const env = { ...process.env };
  const mcp = asRecord(pluginConfig.mcp);

  const mcpUrl = normalizeString(mcp.url);
  const mcpBaseUrl = normalizeString(mcp.baseUrl);
  const healthUrl = normalizeString(mcp.healthUrl);
  const userAgent = normalizeString(mcp.httpUserAgent);
  const toolTimeoutSeconds = normalizeNumber(mcp.toolTimeoutSeconds);
  const verifyTimeoutSeconds = normalizeNumber(mcp.verifyTimeoutSeconds);

  if (mcpUrl) {
    env.GROKSEARCH_MCP_URL = mcpUrl;
  }
  if (mcpBaseUrl) {
    env.GROKSEARCH_MCP_BASE_URL = mcpBaseUrl;
  }
  if (healthUrl) {
    env.GROKSEARCH_HEALTH_URL = healthUrl;
  }
  if (userAgent) {
    env.GROKSEARCH_HTTP_USER_AGENT = userAgent;
  }
  if (toolTimeoutSeconds !== undefined) {
    env.GROKSEARCH_TOOL_TIMEOUT_SECONDS = String(toolTimeoutSeconds);
  }
  if (verifyTimeoutSeconds !== undefined) {
    env.GROKSEARCH_VERIFY_TIMEOUT_SECONDS = String(verifyTimeoutSeconds);
  }
  if (normalizeString(bearerToken)) {
    env.GROKSEARCH_MCP_BEARER_TOKEN = bearerToken;
  }

  return env;
}

function pushStringFlag(args, flag, value) {
  const normalized = normalizeString(value);
  if (normalized) {
    args.push(flag, normalized);
  }
}

function pushIntegerFlag(args, flag, value) {
  const normalized = normalizeInteger(value);
  if (normalized !== undefined) {
    args.push(flag, String(normalized));
  }
}

export function buildWrapperArgs(command, params = {}) {
  const args = [WRAPPER_SCRIPT, "--format", "json", command];
  switch (command) {
    case "probe":
    case "health":
    case "config":
      return args;
    case "sources": {
      const sessionId = normalizeString(params.session_id ?? params.sessionId);
      if (!sessionId) {
        throw new Error("sources requires session_id");
      }
      args.push("--session-id", sessionId);
      return args;
    }
    case "search": {
      const query = normalizeString(params.query);
      if (!query) {
        throw new Error("search requires query");
      }
      args.push("--query", query);
      pushStringFlag(args, "--platform", params.platform);
      pushStringFlag(args, "--model", params.model);
      pushIntegerFlag(args, "--extra-sources", params.extra_sources ?? params.extraSources);
      return args;
    }
    case "extract": {
      const url = normalizeString(params.url);
      if (!url) {
        throw new Error("extract requires url");
      }
      args.push("--url", url);
      return args;
    }
    case "map": {
      const url = normalizeString(params.url);
      if (!url) {
        throw new Error("map requires url");
      }
      args.push("--url", url);
      pushStringFlag(args, "--instructions", params.instructions);
      pushIntegerFlag(args, "--max-depth", params.max_depth ?? params.maxDepth);
      pushIntegerFlag(args, "--max-breadth", params.max_breadth ?? params.maxBreadth);
      pushIntegerFlag(args, "--limit", params.limit);
      pushIntegerFlag(args, "--timeout", params.timeout);
      return args;
    }
    case "research": {
      const query = normalizeString(params.query);
      if (!query) {
        throw new Error("research requires query");
      }
      args.push("--query", query);
      pushStringFlag(args, "--platform", params.platform);
      pushIntegerFlag(args, "--extra-sources", params.extra_sources ?? params.extraSources);
      pushStringFlag(args, "--map-url", params.map_url ?? params.mapUrl);
      pushStringFlag(args, "--map-instructions", params.map_instructions ?? params.mapInstructions);
      pushIntegerFlag(args, "--max-depth", params.max_depth ?? params.maxDepth);
      pushIntegerFlag(args, "--max-breadth", params.max_breadth ?? params.maxBreadth);
      pushIntegerFlag(args, "--limit", params.limit);
      pushIntegerFlag(args, "--timeout", params.timeout);
      return args;
    }
    default:
      throw new Error(`unsupported GrokSearch wrapper command: ${command}`);
  }
}

export async function runWrapperCommand({ command, params = {}, pluginConfig = {}, bearerToken = "" }) {
  const args = buildWrapperArgs(command, params);
  const env = buildWrapperEnv({ pluginConfig, bearerToken });

  return await new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, args, { env });
    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      reject(new Error(`failed to run ${PYTHON_BIN}: ${error.message}`));
    });
    child.on("close", (code) => {
      if (code !== 0) {
        const detail = (stderr || stdout || `wrapper exited with code ${code}`).trim();
        reject(new Error(detail));
        return;
      }

      try {
        const parsed = JSON.parse(stdout);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          reject(new Error("wrapper returned non-object JSON"));
          return;
        }
        resolve(parsed);
      } catch (error) {
        reject(new Error(`failed to parse wrapper JSON: ${error instanceof Error ? error.message : String(error)}`));
      }
    });
  });
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
  const fetch = ensureObject(web, "fetch");
  fetch.provider = PROVIDER_ID;
  return next;
}
