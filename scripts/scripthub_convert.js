#!/usr/bin/env node
const fs = require("fs");
const vm = require("vm");

const SCRIPT_HUB_REWRITE_PARSER =
  "https://raw.githubusercontent.com/Script-Hub-Org/Script-Hub/main/Rewrite-Parser.js";

async function fetchText(url, headers = {}) {
  const response = await fetch(url, {
    headers: {
      "User-Agent": "Loon/3.4.0 CFNetwork/1496.0.7 Darwin/23.5.0",
      Accept: "text/plain,application/javascript,*/*",
      Referer: "https://hub.kelee.one/",
      ...headers,
    },
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} for ${url}`);
  }
  return await response.text();
}

async function convertOne(parserCode, item) {
  return await new Promise((resolve) => {
    const requestUrl =
      `http://script.hub/file/_start_/${item.plugin_url}` +
      `/_end_/${item.output_name}?type=loon-plugin&target=surge-module&del=true&jqEnabled=true`;

    const sandbox = {
      $environment: { "surge-version": "5" },
      $request: { url: requestUrl },
      $persistentStore: {
        read: () => null,
        write: () => true,
      },
      $notification: { post: () => {} },
      $httpClient: {
        get: (opts, callback) => {
          const url = typeof opts === "string" ? opts : opts.url;
          fetchText(url, (opts && opts.headers) || {})
            .then((body) => callback(null, { status: 200, statusCode: 200, headers: {} }, body))
            .catch((error) => callback(error));
        },
        post: (_opts, callback) => callback(new Error("POST is not implemented")),
      },
      $done: (payload) => {
        const body = payload && payload.response ? payload.response.body : "";
        resolve({ body });
      },
      console: {
        log: () => {},
        error: () => {},
      },
      setTimeout,
      clearTimeout,
      Promise,
      URL,
      fetch,
    };

    try {
      vm.createContext(sandbox);
      vm.runInContext(parserCode, sandbox, { timeout: 120000 });
    } catch (error) {
      resolve({ error: String(error && error.stack ? error.stack : error) });
    }
  });
}

async function main() {
  const [, , itemsPath, outDir, reportPath, parserPath] = process.argv;
  if (!itemsPath || !outDir || !reportPath) {
    console.error("Usage: scripthub_convert.js <items.json> <out-dir> <report.json>");
    process.exit(2);
  }

  fs.mkdirSync(outDir, { recursive: true });
  const parserCode = parserPath
    ? fs.readFileSync(parserPath, "utf8")
    : await fetchText(SCRIPT_HUB_REWRITE_PARSER);
  const items = JSON.parse(fs.readFileSync(itemsPath, "utf8"));
  const report = [];

  for (const item of items) {
    const outputPath = `${outDir.replace(/[\\/]$/, "")}/${item.output_name}`;
    const entry = {
      name: item.name,
      source: item.plugin_url,
      output: outputPath,
      status: "ok",
      warnings: ["Converted by Script-Hub Rewrite-Parser"],
    };
    try {
      const result = await convertOne(parserCode, item);
      if (result.error) throw new Error(result.error);
      if (!result.body || !result.body.includes("#!name=")) {
        throw new Error(
          "Script-Hub did not return a Surge module: " +
            String(result.body || "").slice(0, 500).replace(/\s+/gu, " ")
        );
      }
      fs.writeFileSync(outputPath, result.body.replace(/\s+$/u, "") + "\n", "utf8");
    } catch (error) {
      entry.status = "failed";
      entry.error = String(error && error.message ? error.message : error);
    }
    report.push(entry);
  }

  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
