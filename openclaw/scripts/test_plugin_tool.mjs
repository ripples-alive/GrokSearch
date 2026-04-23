#!/usr/bin/env node
import process from "node:process";
import { runWrapperCommand } from "../plugin-runtime.js";

function buildPluginConfigFromInput(rawConfig) {
  if (!rawConfig || typeof rawConfig !== "object" || Array.isArray(rawConfig)) {
    return {};
  }
  return rawConfig;
}

function usage() {
  console.error(`Usage: node openclaw/scripts/test_plugin_tool.mjs <command> [json-params] [json-plugin-config]

Examples:
  node openclaw/scripts/test_plugin_tool.mjs probe '{}' '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"token"}}'
  node openclaw/scripts/test_plugin_tool.mjs search '{"query":"OpenAI latest announcements"}' '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"token"}}'
  node openclaw/scripts/test_plugin_tool.mjs extract '{"url":"https://platform.openai.com/docs/overview"}' '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"token"}}'

Pass plugin config explicitly. This helper no longer reads runtime config from shell environment.
`);
}

const [, , command, rawParams, rawPluginConfig] = process.argv;

if (!command) {
  usage();
  process.exit(2);
}

let params = {};
if (rawParams) {
  try {
    params = JSON.parse(rawParams);
  } catch (error) {
    console.error(`Invalid JSON params: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(2);
  }
}

let pluginConfig = {};
if (rawPluginConfig) {
  try {
    pluginConfig = buildPluginConfigFromInput(JSON.parse(rawPluginConfig));
  } catch (error) {
    console.error(`Invalid JSON plugin config: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(2);
  }
}

try {
  const payload = await runWrapperCommand({ command, params, pluginConfig });
  console.log(JSON.stringify(payload, null, 2));
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
