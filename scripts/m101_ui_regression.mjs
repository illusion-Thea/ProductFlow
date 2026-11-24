// Real Chromium interactions against the isolated API. No model workers run.
// --baseline loads the reviewed frontend from Git into Vite without editing files.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(process.env.M101_MANIFEST, "utf8"));
assert.equal(manifest.stub_only, true);
assert.equal(manifest.isolation_preflight_passed, true);
assert.equal(resolve(manifest.workspace), workspace);
const baseline = process.argv.includes("--baseline");
const evidence = resolve(manifest.run_root, "evidence", `ui-${baseline ? "baseline" : "fixed"}-${Date.now()}`);
assert(!relative(resolve(manifest.run_root), evidence).startsWith(".."));
mkdirSync(evidence, { recursive: true });
const seed = JSON.parse(readFileSync(resolve(manifest.run_root, "evidence/seed.json"), "utf8"));
const privateValues = JSON.parse(readFileSync(resolve(manifest.run_root, "private.json"), "utf8"));
const apiBase = `http://127.0.0.1:${manifest.ports.api}`;
const webBase = `http://127.0.0.1:${manifest.ports.web}`;
const identity = await (await fetch(`${apiBase}/api/verification-context`)).json();
assert.equal(identity.environment_id, manifest.environment_id);
const auth = await fetch(`${apiBase}/api/auth/session`, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ admin_key: privateValues.admin }) });
assert.equal(auth.status, 200);
const cookie = auth.headers.get("set-cookie").split(";")[0];
async function api(path, payload, method = "POST") {
  assert(path.startsWith("/api/"));
  const response = await fetch(apiBase + path, { method: payload === undefined ? "GET" : method,
    headers: { Cookie: cookie, "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
    body: payload === undefined ? undefined : JSON.stringify(payload) });
  const value = await response.json();
  assert(response.ok, `${path}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}
// Append a new free-DAG fixture for each browser run; never reset prior runs,
// failed evidence, experiment quotas or the prototype's seed records.
const fixtureImage = await fetch(`${apiBase}/api/source-assets/${seed.source_asset_id}/download`, { headers: { Cookie: cookie } });
assert(fixtureImage.ok);
const fixtureForm = new FormData();
fixtureForm.set("name", `Browser DAG ${Date.now()}`);
fixtureForm.set("image", await fixtureImage.blob(), "synthetic.png");
const fixtureResponse = await fetch(apiBase + "/api/products", { method: "POST", headers: { Cookie: cookie }, body: fixtureForm });
assert.equal(fixtureResponse.status, 201);
const workflowProductId = (await fixtureResponse.json()).id;

const replacements = new Map();
if (baseline) {
  const args = ["-c", `safe.directory=${workspace.replaceAll("\\", "/")}`];
  const changed = execFileSync("git", [...args, "diff", "--name-only", "500ad0c", "--", "web/src"], { cwd: workspace, encoding: "utf8" }).trim().split(/\r?\n/);
  for (const path of changed.filter(Boolean)) {
    replacements.set(resolve(workspace, path).toLowerCase(), execFileSync("git", [...args, "show", `500ad0c:${path}`], { cwd: workspace, encoding: "utf8" }));
  }
}
const server = await createServer({ root: resolve(workspace, "web"), configFile: resolve(workspace, "web/vite.config.ts"),
  cacheDir: resolve(manifest.run_root, "cache", baseline ? "vite-ui-baseline" : "vite-ui-fixed"),
  plugins: [{ name: "reviewed-frontend-baseline", enforce: "pre", load(id) {
    return replacements.get(resolve(id.split("?")[0]).toLowerCase()) ?? null;
  } }], server: { host: "127.0.0.1", port: manifest.ports.web, strictPort: true } });
await server.listen();
const chromePath = "C:/Program Files/Google/Chrome/Application/chrome.exe";
assert(existsSync(chromePath));
const chrome = spawn(chromePath, ["--headless=new", "--disable-gpu", "--disable-background-networking", "--no-first-run",
  "--disable-extensions", "--disable-sync", "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=127.0.0.1;localhost",
  `--remote-debugging-port=${manifest.ports.stub}`, `--user-data-dir=${resolve(evidence, "browser-profile")}`,
  "--window-size=1440,1000", "about:blank"], { windowsHide: true, stdio: "ignore" });
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
async function waitFor(check, label, timeout = 12000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { const result = await check(); if (result) return result; await sleep(75); }
  throw new Error(`Timed out: ${label}`);
}
let socket;
let sequence = 0;
const pending = new Map();
const errors = [];
function cdp(method, params = {}) {
  return new Promise((ok, fail) => {
    const id = ++sequence;
    pending.set(id, { ok, fail });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
}
async function clickText(text, scope = "document") {
  await waitFor(() => evaluate(`(() => { const button = [...${scope}.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)} && !b.disabled); if (!button) return false; button.click(); return true; })()`), `click ${text}`);
}
async function input(selector, value) {
  await evaluate(`(() => { const input = document.querySelector(${JSON.stringify(selector)}); if (!input) throw new Error('input missing');
    Object.getOwnPropertyDescriptor(input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event('input', {bubbles:true})); })()`);
}
async function go(path) {
  await cdp("Page.navigate", { url: webBase + path });
  await waitFor(() => evaluate("document.readyState === 'complete' && !!document.querySelector('main, [data-testid=prototype-panel], textarea')"), "page loaded");
}
async function prototype() {
  const task = await api(`/api/products/${seed.product_id}/prototype-tasks`, { experiment_run_id: seed.experiment_run_id,
    sku: "SERVER", source_asset_ids: [seed.source_asset_id], content_language: "zh-CN",
    output_spec: { width: 64, height: 64, test_only: true } });
  await go(`/products/${seed.product_id}?prototype=${task.id}`);
  await waitFor(() => evaluate("!!document.querySelector('[data-testid=prototype-panel] input')"), "prototype input");
  await evaluate("document.querySelector('[data-testid=prototype-panel]').open = true");
  return task;
}
async function installDraftLoss(commit) {
  await evaluate(`(() => { const original = window.fetch.bind(window); window.__ui = {writes:0, lookups:0};
    window.fetch = async (url, init={}) => { const path=String(url);
      if (path.endsWith('/draft') && init.method === 'PATCH' && ++window.__ui.writes === 1) {
        ${commit ? "await original(url, init);" : ""} throw new TypeError('fixture lost response');
      }
      if (path.includes('/execution-requests/') && ++window.__ui.lookups === 1 && ${commit}) throw new TypeError('fixture lookup unavailable');
      return original(url, init);
    }; })()`);
}
const cases = [];
async function scenario(name, run) {
  try { await run(); cases.push({ name, passed: true }); }
  catch (error) { cases.push({ name, passed: false, error: error.message }); }
  const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
  writeFileSync(resolve(evidence, `${name}.png`), Buffer.from(screenshot.data, "base64"));
  console.log(`${cases.at(-1).passed ? "PASS" : "FAIL"} ${name}: ${cases.at(-1).error ?? "ok"}`);
}
const cleanups = [];
try {
  const target = await waitFor(async () => {
    try { return (await (await fetch(`http://127.0.0.1:${manifest.ports.stub}/json/list`)).json()).find(t => t.type === "page"); }
    catch { return null; }
  }, "owned Chrome");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, fail) => { socket.onopen = ok; socket.onerror = fail; });
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) { const request = pending.get(message.id); pending.delete(message.id);
      if (message.error) request?.fail(new Error(JSON.stringify(message.error))); else request?.ok(message.result); }
    else if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  };
  await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Network.enable");
  await cdp("Network.setCookie", { name: manifest.cookie_name, value: cookie.slice(cookie.indexOf("=") + 1),
    url: webBase, path: "/", httpOnly: true });

  await scenario("R08-new-draft-survives-recovered-response", async () => {
    await prototype(); await installDraftLoss(true);
    const selector = "[data-testid=prototype-panel] input";
    await input(selector, "ORIGINAL-A"); await clickText("保存草稿");
    await waitFor(() => evaluate("window.__ui.lookups === 1 && ![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存草稿')?.disabled"), "first uncertain submission");
    await input(selector, "NEWER-B"); await clickText("保存草稿");
    await waitFor(() => evaluate("window.__ui.lookups >= 2 && ![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存草稿')?.disabled"), "original reconciled");
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(selector)}).value`), "NEWER-B");
    assert.equal(await evaluate("window.__ui.writes"), 1);
  });

  await scenario("R12-reload-explicit-replay", async () => {
    const task = await prototype(); await installDraftLoss(false);
    await input("[data-testid=prototype-panel] input", "REPLAY-A"); await clickText("保存草稿");
    await waitFor(() => evaluate("window.__ui.lookups === 1"), "uncommitted original");
    await go(`/products/${seed.product_id}?prototype=${task.id}`);
    await waitFor(() => evaluate("!!document.querySelector('[data-testid=prototype-panel] input')"), "reloaded draft");
    await evaluate("document.querySelector('[data-testid=prototype-panel]').open = true");
    await input("[data-testid=prototype-panel] input", "KEEP-B"); await clickText("保存草稿");
    await clickText("恢复上次提交");
    await waitFor(async () => (await api(`/api/prototype-tasks/${task.id}`)).draft.sku === "REPLAY-A", "original replay saved");
    assert.equal(await evaluate("document.querySelector('[data-testid=prototype-panel] input').value"), "KEEP-B");
  });

  await scenario("R07-poll-then-cancel-workflow", async () => {
    const path = `/api/products/${workflowProductId}/workflow`;
    await api(path);
    const workflow = await api(`${path}/run`, {});
    const run = workflow.runs[0];
    cleanups.push(async () => { const current = (await api(path)).runs.find(r => r.id === run.id);
      if (current.status !== "cancelled") await api(`${path}/runs/${run.id}/cancel`, { expected_row_version: current.row_version }); });
    await go(`/products/${workflowProductId}`);
    await evaluate(`(() => { const original = window.fetch.bind(window); window.__ui={polls:0}; window.fetch=async(url,init={})=> {
      if(String(url).endsWith('/workflow/status')) window.__ui.polls++;
      const result=await original(url,init);
      if(String(url).endsWith('/cancel') && init.method==='POST') window.__ui.cancel={payload:JSON.parse(init.body), status:result.status};
      return result;
    }; })()`);
    await waitFor(() => evaluate("window.__ui.polls >= 2"), "workflow polling");
    const image = workflow.nodes.find(n => n.node_type === "image_generation");
    await evaluate(`document.querySelector('.react-flow__node[data-id="${image.id}"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
    await clickText("取消");
    await waitFor(() => evaluate("window.__ui.cancel"), "cancel response");
    const cancelled = await evaluate("window.__ui.cancel");
    assert.equal(cancelled.payload.expected_row_version, run.row_version);
    assert.equal(cancelled.status, 200);
    const next = (await api(`${path}/run`, {})).runs[0];
    assert.notEqual(next.id, run.id);
    cleanups.push(() => api(`${path}/runs/${next.id}/cancel`, { expected_row_version: next.row_version }));
  });
  for (const cleanup of cleanups.splice(0)) await cleanup();

  await scenario("R11-workflow-unknown-polling", async () => {
    const path = `/api/products/${workflowProductId}/workflow`;
    const workflow = await api(path);
    const status = await api(path + "/status");
    for (const value of [workflow, status]) {
      for (const node of value.nodes) node.status = "unknown";
      for (const run of value.runs) { run.status = "unknown"; for (const node of run.node_runs) {
        node.status = "unknown"; if(node.execution) {node.execution.state="unknown";node.execution.wait_closed_at=null;}
      } }
    }
    status.has_active_workflow = true;
    const script = await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const original=window.fetch.bind(window); window.__ui={polls:0};
      const close = value => { for(const run of value.runs) for(const node of run.node_runs) if(node.execution) node.execution.wait_closed_at='2026-11-24T00:00:00Z'; return value; };
      window.fetch=async(url,init)=> { const path=String(url); if(path===${JSON.stringify(path)}) {const value=${JSON.stringify(workflow)}; return new Response(JSON.stringify(window.__ui.closed ? close(value) : value),{status:200});}
        if(path===${JSON.stringify(path + "/status")}) {window.__ui.polls++; const value=${JSON.stringify(status)};
          if(window.__ui.closed) {value.has_active_workflow=false; close(value);}
          return new Response(JSON.stringify(value),{status:200});} return original(url,init); }; })()` });
    try {
      await go(`/products/${workflowProductId}`);
      await waitFor(() => evaluate("window.__ui.polls >= 2"), "unknown polling continues", 6500);
      await evaluate("window.__ui.closed=true"); await sleep(1800);
      const before = await evaluate("window.__ui.polls"); await sleep(1800);
      assert.equal(await evaluate("window.__ui.polls"), before);
    } finally { await cdp("Page.removeScriptToEvaluateOnNewDocument", { identifier: script.identifier }); }
  });

  await scenario("R11-image-session-unknown-polling", async () => {
    const root = await api("/api/image-sessions", { title: "Browser unknown fixture" });
    const path = `/api/image-sessions/${root.id}`;
    const detail = await api(path + "/generate", { prompt: "Browser synthetic only", generation_count: 1 });
    const task = detail.generation_tasks[0];
    cleanups.push(() => api(`${path}/generation-tasks/${task.id}/cancel`, {expected_row_version:task.row_version}));
    const status = await api(path + "/status");
    for(const value of [detail,status]) for(const task of value.generation_tasks) {
      task.status="unknown"; task.execution.state="unknown"; task.execution.wait_closed_at=null;
    }
    status.has_active_generation_task=true;
    const script = await cdp("Page.addScriptToEvaluateOnNewDocument", {source:`(() => { const original=window.fetch.bind(window); window.__ui={polls:0};
      const close = value => {for(const task of value.generation_tasks) task.execution.wait_closed_at='2026-11-24T00:00:00Z'; return value;};
      window.fetch=async(url,init)=> { const path=String(url); if(path===${JSON.stringify(path)}) {const value=${JSON.stringify(detail)}; return new Response(JSON.stringify(window.__ui.closed ? close(value) : value),{status:200});}
        if(path===${JSON.stringify(path + "/status")}) {window.__ui.polls++; const value=${JSON.stringify(status)};
          if(window.__ui.closed) {value.has_active_generation_task=false; close(value);}
          return new Response(JSON.stringify(value),{status:200});} return original(url,init); }; })()`});
    try { await go("/image-chat"); await waitFor(()=>evaluate("window.__ui.polls >= 2"),"session unknown polling",6500);
      assert((await evaluate("document.body.innerText")).includes("结果未知"));
      await evaluate("window.__ui.closed=true"); await sleep(2000); const before=await evaluate("window.__ui.polls");
      await sleep(1800); assert.equal(await evaluate("window.__ui.polls"),before);
      await cdp("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
      await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme",value:"dark"}]});
      await sleep(300);
    } finally {await cdp("Page.removeScriptToEvaluateOnNewDocument", {identifier:script.identifier});}
  });
} finally {
  for (const cleanup of cleanups) await cleanup();
  writeFileSync(resolve(evidence,"results.json"),JSON.stringify({baseline,environment_id:manifest.environment_id,fixture:{...seed,workflow_product_id:workflowProductId},cases,errors},null,2));
  socket?.close(); if(chrome.exitCode===null) chrome.kill(); await server.close();
}
console.log(`Browser evidence: ${evidence}`);
if(cases.some(item=>!item.passed)) process.exitCode=1;
