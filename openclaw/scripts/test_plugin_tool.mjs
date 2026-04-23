#!/usr/bin/env node
import process from "node:process";
import { runWrapperCommand } from "../plugin-runtime.js";

function usage() {
  console.error(`Usage: node openclaw/scripts/test_plugin_tool.mjs <command> [json-params]

Examples:
  node openclaw/scripts/test_plugin_tool.mjs probe
  node openclaw/scripts/test_plugin_tool.mjs search '{"query":"OpenAI latest announcements"}'
  node openclaw/scripts/test_plugin_tool.mjs extract '{"url":"https://platform.openai.com/docs/overview"}'

Configuration is read from the current shell environment:
  GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL
  GROKSEARCH_MCP_BEARER_TOKEN
  GROKSEARCH_HEALTH_URL (optional)
`);
}

const [, , command, rawParams] = process.argv;

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

try {
  const payload = await runWrapperCommand({ command, params });
  console.log(JSON.stringify(payload, null, 2));
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
