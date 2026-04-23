import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import {
  jsonResult,
  readNumberParam,
  readStringParam,
  wrapWebContent,
} from "openclaw/plugin-sdk/provider-web-search";
import { wrapExternalContent } from "openclaw/plugin-sdk/provider-web-fetch";
import {
  resolveConfiguredSecretInputString,
  resolvePluginConfigObject,
} from "openclaw/plugin-sdk/config-runtime";
import { Type } from "typebox";
import {
  PLUGIN_ID,
  PROVIDER_ID,
  applyFetchSelection,
  applySearchSelection,
  createFetchProviderPayload,
  createSearchProviderPayload,
  resolveDefaults,
  resolvePluginConfig,
  runWrapperCommand,
} from "./plugin-runtime.js";

const GenericSearchSchema = Type.Object(
  {
    query: Type.String({ description: "Search query string." }),
    count: Type.Optional(
      Type.Number({
        description: "Requested result count. Current GrokSearch MCP ignores exact counts and returns backend-sized result sets.",
        minimum: 1,
        maximum: 20,
      })
    ),
  },
  { additionalProperties: false }
);

const GenericFetchSchema = Type.Object(
  {
    url: Type.String({ description: "HTTP or HTTPS URL to fetch." }),
  },
  { additionalProperties: false }
);

const SearchToolSchema = Type.Object(
  {
    query: Type.String({ description: "Search query string." }),
    platform: Type.Optional(Type.String({ description: "Optional platform hint, for example GitHub or Twitter." })),
    model: Type.Optional(Type.String({ description: "Optional Grok model override." })),
    extra_sources: Type.Optional(
      Type.Number({
        description: "Additional source fan-out requested from GrokSearch.",
        minimum: 0,
      })
    ),
  },
  { additionalProperties: false }
);

const SourcesToolSchema = Type.Object(
  {
    session_id: Type.String({ description: "Search session ID returned by GrokSearch." }),
  },
  { additionalProperties: false }
);

const ExtractToolSchema = Type.Object(
  {
    url: Type.String({ description: "HTTP or HTTPS URL to extract." }),
  },
  { additionalProperties: false }
);

const MapToolSchema = Type.Object(
  {
    url: Type.String({ description: "Site root or page URL to map." }),
    instructions: Type.Optional(Type.String({ description: "Optional mapping instructions for the remote mapper." })),
    max_depth: Type.Optional(Type.Number({ minimum: 1, description: "Maximum crawl depth." })),
    max_breadth: Type.Optional(Type.Number({ minimum: 1, description: "Maximum crawl breadth per level." })),
    limit: Type.Optional(Type.Number({ minimum: 1, description: "Maximum number of mapped results." })),
    timeout: Type.Optional(Type.Number({ minimum: 1, description: "Timeout in seconds." })),
  },
  { additionalProperties: false }
);

const ResearchToolSchema = Type.Object(
  {
    query: Type.String({ description: "Research query string." }),
    platform: Type.Optional(Type.String({ description: "Optional platform hint for the initial search." })),
    extra_sources: Type.Optional(Type.Number({ minimum: 0, description: "Extra source fan-out for the initial search." })),
    map_url: Type.Optional(Type.String({ description: "Optional URL to map as part of the research run." })),
    map_instructions: Type.Optional(Type.String({ description: "Optional instructions for the map stage." })),
    max_depth: Type.Optional(Type.Number({ minimum: 1, description: "Maximum crawl depth for the map stage." })),
    max_breadth: Type.Optional(Type.Number({ minimum: 1, description: "Maximum crawl breadth for the map stage." })),
    limit: Type.Optional(Type.Number({ minimum: 1, description: "Maximum map results for the map stage." })),
    timeout: Type.Optional(Type.Number({ minimum: 1, description: "Timeout in seconds for the map stage." })),
  },
  { additionalProperties: false }
);

function toolErrorResult(message, details = {}) {
  return {
    content: [{ type: "text", text: message }],
    details: { status: "failed", error: message, ...details },
  };
}

async function resolvePluginRuntimeConfig(config) {
  const safeConfig = config ?? {};
  const pluginConfig = resolvePluginConfigObject(safeConfig, PLUGIN_ID) ?? resolvePluginConfig(safeConfig);
  const bearer = await resolveConfiguredSecretInputString({
    config: safeConfig,
    env: process.env,
    value: pluginConfig?.mcp?.bearerToken,
    path: "plugins.entries.grok-search.config.mcp.bearerToken",
  });

  return {
    pluginConfig,
    defaults: resolveDefaults(pluginConfig),
    bearerToken: bearer.value ?? "",
  };
}

async function runCommand(config, command, params) {
  const runtime = await resolvePluginRuntimeConfig(config);
  return await runWrapperCommand({
    command,
    params,
    pluginConfig: runtime.pluginConfig,
    bearerToken: runtime.bearerToken,
  });
}

function applySearchDefaults(params, defaults) {
  return {
    query: readStringParam(params, "query", { required: true }),
    platform: readStringParam(params, "platform") || defaults.search.platform,
    model: readStringParam(params, "model") || defaults.search.model,
    extra_sources:
      readNumberParam(params, "extra_sources", { integer: true }) ??
      defaults.search.extraSources ??
      0,
  };
}

function applyMapDefaults(params, defaults) {
  return {
    url: readStringParam(params, "url", { required: true }),
    instructions: readStringParam(params, "instructions") || "",
    max_depth:
      readNumberParam(params, "max_depth", { integer: true }) ??
      defaults.map.maxDepth ??
      1,
    max_breadth:
      readNumberParam(params, "max_breadth", { integer: true }) ??
      defaults.map.maxBreadth ??
      20,
    limit:
      readNumberParam(params, "limit", { integer: true }) ??
      defaults.map.limit ??
      50,
    timeout:
      readNumberParam(params, "timeout", { integer: true }) ??
      defaults.map.timeout ??
      150,
  };
}

function applyResearchDefaults(params, defaults) {
  const mapUrl = readStringParam(params, "map_url") || "";
  const mapInstructions = readStringParam(params, "map_instructions") || "";

  return {
    query: readStringParam(params, "query", { required: true }),
    platform: readStringParam(params, "platform") || defaults.search.platform,
    extra_sources:
      readNumberParam(params, "extra_sources", { integer: true }) ??
      defaults.research.extraSources ??
      defaults.search.extraSources ??
      3,
    map_url: mapUrl,
    map_instructions: mapInstructions,
    max_depth:
      readNumberParam(params, "max_depth", { integer: true }) ??
      defaults.map.maxDepth ??
      1,
    max_breadth:
      readNumberParam(params, "max_breadth", { integer: true }) ??
      defaults.map.maxBreadth ??
      20,
    limit:
      readNumberParam(params, "limit", { integer: true }) ??
      defaults.map.limit ??
      50,
    timeout:
      readNumberParam(params, "timeout", { integer: true }) ??
      defaults.map.timeout ??
      150,
  };
}

function wrapSearchProviderPayload(payload) {
  return {
    query: payload.query,
    provider: payload.provider,
    count: payload.count,
    sessionId: payload.sessionId,
    sourcesCount: payload.sourcesCount,
    externalContent: {
      untrusted: true,
      source: "web_search",
      provider: payload.provider,
      wrapped: true,
    },
    ...(payload.answer
      ? { answer: wrapWebContent(payload.answer, "web_search") }
      : {}),
    results: payload.results.map((result) => ({
      title: wrapWebContent(result.title, "web_search"),
      url: result.url,
      description: result.description ? wrapWebContent(result.description, "web_search") : "",
      ...(result.score !== undefined ? { score: result.score } : {}),
      ...(result.siteName ? { siteName: result.siteName } : {}),
    })),
  };
}

function wrapFetchProviderPayload(payload) {
  const wrappedText = wrapExternalContent(payload.text, {
    source: "web_fetch",
    includeWarning: false,
  });
  const wrappedWarning = payload.warning
    ? wrapExternalContent(payload.warning, {
        source: "web_fetch",
        includeWarning: false,
      })
    : "";

  return {
    url: payload.url,
    finalUrl: payload.finalUrl,
    extractor: payload.extractor,
    extractMode: payload.extractMode,
    externalContent: {
      untrusted: true,
      source: "web_fetch",
      provider: PROVIDER_ID,
      wrapped: true,
    },
    rawLength: payload.rawLength,
    wrappedLength: wrappedText.length,
    text: wrappedText,
    ...(wrappedWarning ? { warning: wrappedWarning } : {}),
  };
}

function createSearchProvider() {
  return {
    id: PROVIDER_ID,
    label: "GrokSearch",
    hint: "Shared remote MCP-backed web search with Grok answers and source retrieval.",
    requiresCredential: false,
    credentialLabel: "GrokSearch bearer token",
    placeholder: "Bearer token",
    signupUrl: "https://github.com/GuDaStudio/GrokSearch",
    docsUrl: "https://github.com/GuDaStudio/GrokSearch/tree/main/openclaw",
    autoDetectOrder: 60,
    credentialPath: "plugins.entries.grok-search.config.mcp.bearerToken",
    inactiveSecretPaths: ["plugins.entries.grok-search.config.mcp.bearerToken"],
    getCredentialValue: () => undefined,
    setCredentialValue: () => {},
    getConfiguredCredentialValue: (config) => resolvePluginConfig(config)?.mcp?.bearerToken,
    setConfiguredCredentialValue: (configTarget, value) => {
      const plugins = configTarget.plugins ?? (configTarget.plugins = {});
      const entries = plugins.entries ?? (plugins.entries = {});
      const entry = entries[PLUGIN_ID] ?? (entries[PLUGIN_ID] = {});
      entry.enabled ??= true;
      const pluginConfig = entry.config ?? (entry.config = {});
      const mcp = pluginConfig.mcp ?? (pluginConfig.mcp = {});
      mcp.bearerToken = value;
    },
    applySelectionConfig: (config) => applySearchSelection(config),
    createTool: (ctx) => ({
      description:
        "Search the web through the configured GrokSearch MCP endpoint. Returns an answer plus structured source results. Count is best-effort because the remote MCP search is answer-first.",
      parameters: GenericSearchSchema,
      execute: async (args) => {
        const query = typeof args.query === "string" ? args.query : "";
        const runtime = await resolvePluginRuntimeConfig(ctx.config);
        const payload = await runWrapperCommand({
          command: "search",
          params: {
            query,
            platform: runtime.defaults.search.platform,
            model: runtime.defaults.search.model,
            extra_sources: runtime.defaults.search.extraSources ?? 0,
          },
          pluginConfig: runtime.pluginConfig,
          bearerToken: runtime.bearerToken,
        });
        payload.query = query;
        return wrapSearchProviderPayload(createSearchProviderPayload(payload));
      },
    }),
  };
}

function createFetchProvider() {
  return {
    id: PROVIDER_ID,
    label: "GrokSearch Fetch",
    hint: "Fetch page text through the shared GrokSearch MCP endpoint.",
    requiresCredential: false,
    credentialLabel: "GrokSearch bearer token",
    placeholder: "Bearer token",
    signupUrl: "https://github.com/GuDaStudio/GrokSearch",
    docsUrl: "https://github.com/GuDaStudio/GrokSearch/tree/main/openclaw",
    autoDetectOrder: 60,
    credentialPath: "plugins.entries.grok-search.config.mcp.bearerToken",
    inactiveSecretPaths: ["plugins.entries.grok-search.config.mcp.bearerToken"],
    getCredentialValue: () => undefined,
    setCredentialValue: () => {},
    getConfiguredCredentialValue: (config) => resolvePluginConfig(config)?.mcp?.bearerToken,
    setConfiguredCredentialValue: (configTarget, value) => {
      const plugins = configTarget.plugins ?? (configTarget.plugins = {});
      const entries = plugins.entries ?? (plugins.entries = {});
      const entry = entries[PLUGIN_ID] ?? (entries[PLUGIN_ID] = {});
      entry.enabled ??= true;
      const pluginConfig = entry.config ?? (entry.config = {});
      const mcp = pluginConfig.mcp ?? (pluginConfig.mcp = {});
      mcp.bearerToken = value;
    },
    applySelectionConfig: (config) => applyFetchSelection(config),
    createTool: (ctx) => ({
      description: "Fetch page text from the configured GrokSearch MCP endpoint.",
      parameters: GenericFetchSchema,
      execute: async (args) => {
        const payload = await runCommand(ctx.config, "extract", {
          url: typeof args.url === "string" ? args.url : "",
        });
        return wrapFetchProviderPayload(createFetchProviderPayload(payload));
      },
    }),
  };
}

function createJsonTool(name, label, description, parameters, executePayload) {
  return {
    name,
    label,
    description,
    parameters,
    async execute(_toolCallId, rawParams) {
      try {
        const payload = await executePayload(rawParams);
        return jsonResult(payload);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return toolErrorResult(message, { tool: name });
      }
    },
  };
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "GrokSearch Plugin",
  description: "Remote GrokSearch MCP plugin for OpenClaw web_search, web_fetch, and explicit GrokSearch tools.",
  register(api) {
    api.registerWebSearchProvider(createSearchProvider());
    api.registerWebFetchProvider(createFetchProvider());

    api.registerTool(
      createJsonTool(
        "groksearch_search",
        "GrokSearch Search",
        "Run a GrokSearch search and return the answer plus structured sources.",
        SearchToolSchema,
        async (rawParams) => {
          const runtime = await resolvePluginRuntimeConfig(api.config);
          return await runWrapperCommand({
            command: "search",
            params: applySearchDefaults(rawParams, runtime.defaults),
            pluginConfig: runtime.pluginConfig,
            bearerToken: runtime.bearerToken,
          });
        }
      )
    );

    api.registerTool(
      createJsonTool(
        "groksearch_sources",
        "GrokSearch Sources",
        "Fetch the full source list for a previous GrokSearch session.",
        SourcesToolSchema,
        async (rawParams) =>
          await runCommand(api.config, "sources", {
            session_id: readStringParam(rawParams, "session_id", { required: true }),
          })
      )
    );

    api.registerTool(
      createJsonTool(
        "groksearch_extract",
        "GrokSearch Extract",
        "Extract page text through the GrokSearch MCP web_fetch capability.",
        ExtractToolSchema,
        async (rawParams) =>
          await runCommand(api.config, "extract", {
            url: readStringParam(rawParams, "url", { required: true }),
          })
      )
    );

    api.registerTool(
      createJsonTool(
        "groksearch_map",
        "GrokSearch Map",
        "Map a site or URL through the GrokSearch MCP web_map capability.",
        MapToolSchema,
        async (rawParams) => {
          const runtime = await resolvePluginRuntimeConfig(api.config);
          return await runWrapperCommand({
            command: "map",
            params: applyMapDefaults(rawParams, runtime.defaults),
            pluginConfig: runtime.pluginConfig,
            bearerToken: runtime.bearerToken,
          });
        }
      )
    );

    api.registerTool(
      createJsonTool(
        "groksearch_research",
        "GrokSearch Research",
        "Run a GrokSearch search, source follow-up, and optional site map in one call.",
        ResearchToolSchema,
        async (rawParams) => {
          const runtime = await resolvePluginRuntimeConfig(api.config);
          return await runWrapperCommand({
            command: "research",
            params: applyResearchDefaults(rawParams, runtime.defaults),
            pluginConfig: runtime.pluginConfig,
            bearerToken: runtime.bearerToken,
          });
        }
      )
    );
  },
});
