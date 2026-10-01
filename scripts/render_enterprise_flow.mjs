/**
 * Development-only SVG renderer. The published HTML needs no Node or CDN.
 * From the repository root, install renderer dependencies outside the app:
 *   npm install --prefix .diagram-tools --no-audit --no-fund mermaid@11.17.2 playwright@1.58.2
 * PowerShell:
 *   .diagram-tools/node_modules/.bin/playwright.cmd install chromium
 *   $env:DEER_FLOW_DIAGRAM_DEPS = (Resolve-Path .diagram-tools).Path
 *   node scripts/render_enterprise_flow.mjs
 * macOS:
 *   ./.diagram-tools/node_modules/.bin/playwright install chromium
 *   DEER_FLOW_DIAGRAM_DEPS="$PWD/.diagram-tools" node scripts/render_enterprise_flow.mjs
 * If an approved Chromium is already installed, set DEER_FLOW_DIAGRAM_BROWSER
 * to its executable path. No TLS checks are disabled and no app config is read.
 */
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const deps = process.env.DEER_FLOW_DIAGRAM_DEPS;
if (!deps) throw new Error("Set DEER_FLOW_DIAGRAM_DEPS to your development renderer directory.");
const resolutionPaths = [path.resolve(deps)];
const mermaidRoot = path.dirname(require.resolve("mermaid/package.json", { paths: resolutionPaths }));
const playwrightPath = process.env.DEER_FLOW_DIAGRAM_PLAYWRIGHT
  ? require.resolve(path.resolve(process.env.DEER_FLOW_DIAGRAM_PLAYWRIGHT))
  : require.resolve("playwright", { paths: resolutionPaths });
const { chromium } = require(playwrightPath);
const markdown = await readFile(path.join(root, "docs/enterprise-flow.md"), "utf8");
const blocks = [...markdown.matchAll(/```mermaid\r?\n([\s\S]*?)\r?\n```/g)].map((match) => match[1]);
const names = ["overview", "registry-deployment", "request-execution", "skill-publication", "dependency-recovery", "remote-sandbox"];
if (blocks.length !== names.length) throw new Error("Expected exactly six Mermaid diagrams in enterprise-flow.md.");
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><link rel="icon" href="data:,"></head>
<body><div id="render"></div><script type="module">
import mermaid from '/mermaid/dist/mermaid.esm.min.mjs';
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', htmlLabels: false,
  themeVariables: { fontFamily: 'Segoe UI, Arial, sans-serif', fontSize: '16px',
    primaryColor: '#e7f4ed', primaryTextColor: '#173d30', primaryBorderColor: '#247056',
    lineColor: '#5d727c', clusterBkg: '#f6f8fa', clusterBorder: '#c2cfd5' },
  flowchart: { htmlLabels: false, useMaxWidth: false, curve: 'basis', padding: 18, nodeSpacing: 32, rankSpacing: 52 } });
window.renderDiagram = async (id, source) => {
  const { svg } = await mermaid.render(id, source);
  document.querySelector('#render').innerHTML = svg;
  // HTML serialization can contain unclosed <br> tags in edge-label foreignObjects.
  // Serialize the mounted SVG as XML so standalone files and <img> loads are valid.
  return new XMLSerializer().serializeToString(document.querySelector('#render svg'));
};
</script></body></html>`;
const server = createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, "http://127.0.0.1").pathname;
    if (pathname === "/") {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      response.end(html);
      return;
    }
    if (!pathname.startsWith("/mermaid/")) throw new Error("Unknown renderer path");
    const target = path.resolve(mermaidRoot, decodeURIComponent(pathname.slice("/mermaid/".length)));
    if (!target.startsWith(mermaidRoot + path.sep)) throw new Error("Renderer path outside dependency directory");
    const content = await readFile(target);
    response.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8" });
    response.end(content);
  } catch {
    response.writeHead(404);
    response.end("Not found");
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, ...(process.env.DEER_FLOW_DIAGRAM_BROWSER ? { executablePath: process.env.DEER_FLOW_DIAGRAM_BROWSER } : {}) });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => typeof window.renderDiagram === "function");
  const output = path.join(root, "docs/assets/enterprise-flow");
  await mkdir(output, { recursive: true });
  for (let index = 0; index < blocks.length; index++) {
    const svg = await page.evaluate(async ({ id, source }) => window.renderDiagram(id, source), { id: `enterprise_${names[index].replaceAll("-", "_")}`, source: blocks[index] });
    // Native SVG text keeps exports self-contained and accessible.
    await writeFile(path.join(output, `${names[index]}.svg`), svg + "\n", "utf8");
    const box = await page.locator("#render svg").boundingBox();
    console.log(`${names[index]}.svg: ${Math.round(box.width)} × ${Math.round(box.height)}`);
  }
} finally {
  if (browser) await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
