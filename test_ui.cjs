/**
 * Headless UI smoke test.
 * Loads the app in Chrome via CDP, checks for console errors,
 * then drives a real user flow: create project -> create preset ->
 * send chat message -> open gallery.
 */
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP = "http://127.0.0.1:8765/ui.html";
const PORT = 9333;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let d = "";
      res.on("data", (c) => (d += c));
      res.on("end", () => resolve(d));
    }).on("error", reject);
  });
}

async function cdp(ws, method, params = {}, sessionId = undefined) {
  return new Promise((resolve, reject) => {
    const id = ++cdp._id;
    const handler = (raw) => {
      const msg = JSON.parse(raw);
      if (msg.id === id) {
        ws.off("message", handler);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    };
    ws.on("message", handler);
    const envelope = { id, method, params };
    if (sessionId) envelope.sessionId = sessionId;
    ws.send(JSON.stringify(envelope));
  });
}
cdp._id = 0;

async function evaluate(ws, expression) {
  const r = await cdp(ws, "Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (r.exceptionDetails) {
    throw new Error("JS ERROR: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  }
  return r.result.value;
}

(async () => {
  const results = [];
  const check = (name, ok, detail = "") => {
    results.push({ name, ok, detail });
    console.log((ok ? "PASS  " : "FAIL  ") + name + (ok ? "" : "  -> " + String(detail).slice(0, 300)));
  };

  // launch chrome headless with remote debugging
  const userData = path.join(require("os").tmpdir(), "imggen-ui-test");
  const chrome = spawn(CHROME, [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${userData}`,
    "about:blank",
  ], { stdio: "ignore" });

  let ws = null;
  try {
    // wait for devtools endpoint
    let targets = null;
    for (let i = 0; i < 40; i++) {
      await sleep(500);
      try {
        const v = await get(`http://127.0.0.1:${PORT}/json/version`);
        targets = JSON.parse(v);
        break;
      } catch (e) { /* keep waiting */ }
    }
    if (!targets) throw new Error("chrome devtools never became ready");

    const WebSocket = require("ws");
    ws = new WebSocket(targets.webSocketDebuggerUrl, { perMessageDeflate: false, maxPayload: 256 * 1024 * 1024 });
    await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });

    const { targetId } = await cdp(ws, "Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp(ws, "Target.attachToTarget", { targetId, flatten: true });
    const send = (m, p) => cdp(ws, m, p, sessionId);

    const raw = (method, params) => cdp(ws, method, params, sessionId);

    const ev = (expression) => cdp(ws, "Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId).then((r) => {
        if (r.exceptionDetails) {
          throw new Error("JS ERROR: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
        }
        return r.result.value;
      });

    await send("Runtime.enable", {});
    await send("Page.enable", {});
    await send("Log.enable", {});

    // collect console errors
    await ev(`window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message))); true;`);

    await raw("Page.navigate", { url: APP });
    await sleep(4000);

    // 1. page loaded, no console errors
    const errs = await ev(`JSON.stringify(window.__errs || [])`);
    check("no uncaught JS errors on load", errs === "[]", errs);

    // 2. app state initialised
    const hasState = await ev(`typeof STATE === 'object' ? 'yes' : 'no'`);
    check("app state initialised", hasState === "yes", hasState);

    const initRan = await ev(`document.getElementById('project-badge') ? document.getElementById('project-badge').textContent : 'none'`);
    check("badge rendered after init", initRan.length > 0, initRan);

    // 3. create a project through the real UI
    await ev(`document.getElementById('new-project-btn').click(); true`);
    await sleep(400);
    const modalOpen = await ev(`document.getElementById('modal-overlay').classList.contains('on')`);
    check("new-project modal opens", modalOpen === true, modalOpen);

    await ev(`document.getElementById('nproj-name').value = 'UI Test Project'; true`);
    await ev(`document.getElementById('nproj-desc').value = 'created by headless test'; true`);
    await ev(`document.getElementById('save-nproj').click(); true`);
    await sleep(1500);

    const projCount = await ev(`STATE.projects.length`);
    check("project created via UI", projCount >= 1, projCount);

    const badge = await ev(`document.getElementById('project-badge').textContent`);
    check("project badge updated", badge === "UI Test Project", badge);

    // 4. switch to presets panel and create a preset
    await ev(`Array.from(document.querySelectorAll('.snav-btn')).find(b => b.dataset.panel === 'presets').click(); true`);
    await sleep(400);
    await ev(`document.getElementById('new-preset-btn').click(); true`);
    await sleep(400);
    const presetModal = await ev(`!!document.getElementById('pre-name')`);
    check("preset modal opens", presetModal === true, presetModal);

    await ev(`
      document.getElementById('pre-name').value = 'UI Preset';
      document.getElementById('pre-logic-provider').value = 'openai';
      document.getElementById('pre-logic-url').value = 'https://api.openai.com/v1';
      document.getElementById('pre-logic-key').value = 'sk-ui-test-key';
      document.getElementById('pre-logic-model').value = 'gpt-4o';
      document.getElementById('pre-image-provider').value = 'openai';
      document.getElementById('pre-image-url').value = 'https://api.openai.com/v1';
      document.getElementById('pre-image-key').value = 'sk-ui-test-key';
      document.getElementById('pre-image-model').value = 'dall-e-3';
      true;
    `);
    await ev(`document.getElementById('save-pre').click(); true`);
    await sleep(1500);

    const presetCount = await ev(`STATE.presets.length`);
    check("preset created via UI", presetCount >= 1, presetCount);

    // 5. activate preset on the project
    await ev(`Array.from(document.querySelectorAll('.snav-btn')).find(b => b.dataset.panel === 'projects').click(); true`);
    await sleep(400);
    const presetId = await ev(`(STATE.presets.find(p => p.name === 'UI Preset') || {}).id`);
    check("preset id available", !!presetId, presetId);
    await ev(`
      (async () => {
        const proj = STATE.projects.find(p => p.name === 'UI Test Project');
        await fetch('/api/projects/' + proj.id, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: 'UI Test Project',
            description: 'created by headless test',
            instructions: '',
            active_preset_id: ${JSON.stringify(presetId)}
          })
        });
      })();
      true;
    `);
    await sleep(1200);
    await ev(`window.selectProject(STATE.projects.find(p => p.name === 'UI Test Project').id); true`);
    await sleep(1500);

    const presetBadge = await ev(`document.getElementById('preset-badge').textContent`);
    check("preset badge shows active preset", presetBadge === "UI Preset", presetBadge);

    // 6. send a chat message through the UI
    await ev(`document.getElementById('prompt').value = 'make a poster of a red fox'; true`);
    await ev(`document.getElementById('send-btn').click(); true`);
    await sleep(4000);

    const msgCount = await ev(`STATE.messages.length`);
    check("chat message produced conversation turns", msgCount >= 2, msgCount);

    const hasAssistant = await ev(`STATE.messages.some(m => m.role === 'assistant')`);
    check("assistant replied", hasAssistant === true, hasAssistant);

    // 7. switch to gallery view
    await ev(`document.getElementById('switch-gallery').click(); true`);
    await sleep(1200);
    const galleryVisible = await ev(`document.getElementById('gallery-area').classList.contains('on')`);
    check("gallery view opens", galleryVisible === true, galleryVisible);

    // 8. no errors accumulated during the whole flow
    const errs2 = await ev(`JSON.stringify(window.__errs || [])`);
    check("no JS errors during full flow", errs2 === "[]", errs2);

    // 9. cleanup
    await ev(`
      (async () => {
        const projects = await (await fetch('/api/projects')).json();
        for (const p of projects) await fetch('/api/projects/' + p.id, { method: 'DELETE' });
        const presets = await (await fetch('/api/presets')).json();
        for (const p of presets) await fetch('/api/presets/' + p.id, { method: 'DELETE' });
      })();
      true;
    `);
    await sleep(1500);
    const finalProjects = await ev(`(async () => (await (await fetch('/api/projects')).json()).length)()`);
    check("cleanup removed test data", finalProjects === 0, finalProjects);

  } catch (e) {
    check("harness completed without exception", false, e.message);
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { chrome.kill(); } catch (e) { /* ignore */ }
  }

  const passed = results.filter((r) => r.ok).length;
  console.log("\n" + "=".repeat(60));
  console.log(`RESULT: ${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})();
