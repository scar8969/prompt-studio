/**
 * Verifies the conversation management UI: list rendering, switching between
 * conversations, creating a new chat, and deleting one.
 */
const { spawn } = require("child_process");
const http = require("http");
const os = require("os");
const path = require("path");
const WebSocket = require("ws");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP = "http://127.0.0.1:8765/ui.html";
const API = "http://127.0.0.1:8765";
const PORT = 9338;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const get = (url) => new Promise((res, rej) => {
  http.get(url, (r) => { let d = ""; r.on("data", c => d += c); r.on("end", () => res(d)); }).on("error", rej);
});

const api = async (method, p, body) => {
  const data = body ? JSON.stringify(body) : null;
  return new Promise((resolve, reject) => {
    const r = http.request(API + p, {
      method,
      headers: { "Content-Type": "application/json", ...(data ? { "Content-Length": Buffer.byteLength(data) } : {}) },
    }, (resp) => { let d = ""; resp.on("data", c => d += c); resp.on("end", () => { try { resolve(JSON.parse(d || "{}")); } catch (e) { resolve(d); } }); });
    r.on("error", reject);
    if (data) r.write(data);
    r.end();
  });
};

(async () => {
  const results = [];
  const check = (name, ok, detail = "") => {
    results.push({ name, ok });
    console.log((ok ? "PASS  " : "FAIL  ") + name + (ok ? "" : "  -> " + String(detail).slice(0, 250)));
  };

  const project = await api("POST", "/api/projects", { name: "Conv UI Test", description: "", instructions: "" });

  const userData = path.join(os.tmpdir(), "imggen-conv-test");
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${userData}`, "about:blank"], { stdio: "ignore" });

  let ws;
  try {
    let t = null;
    for (let i = 0; i < 40; i++) { await sleep(500); try { t = JSON.parse(await get(`http://127.0.0.1:${PORT}/json/version`)); break; } catch (e) {} }
    ws = new WebSocket(t.webSocketDebuggerUrl, { perMessageDeflate: false, maxPayload: 512 * 1024 * 1024 });
    await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });

    let id = 0;
    const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
      const myId = ++id;
      const h = (data) => { const m = JSON.parse(data); if (m.id === myId) { ws.off("message", h); m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result); } };
      ws.on("message", h);
      const msg = { id: myId, method, params };
      if (sessionId) msg.sessionId = sessionId;
      ws.send(JSON.stringify(msg));
    });

    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    const ev = async (expression) => {
      const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
      if (r.exceptionDetails) throw new Error("JS ERROR: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
      return r.result.value;
    };

    await send("Runtime.enable", {}, sessionId);
    await send("Page.enable", {}, sessionId);
    await ev(`window.__errs=[]; window.addEventListener('error',e=>window.__errs.push(String(e.message))); true;`);
    await send("Page.navigate", { url: APP }, sessionId);
    await sleep(4500);

    // select the project
    await ev(`
      (async () => {
        const list = await (await fetch('/api/projects')).json();
        await window.selectProject(list.find(x => x.name === 'Conv UI Test').id);
      })();
      true;
    `);
    await sleep(2500);

    // seed two conversations via API, then refresh UI
    const c1 = await api("POST", "/api/projects/" + project.id + "/conversations", { title: "First chat" });
    const c2 = await api("POST", "/api/projects/" + project.id + "/conversations", { title: "Second chat" });
    await ev(`window.selectProject(window.STATE.activeProjectId); true`);
    await sleep(2500);

    const listLen = await ev(`document.querySelectorAll('#conversation-list .list-item').length`);
    check("conversation list renders both chats", listLen === 2, listLen);

    const activeIsFirst = await ev(`window.STATE.activeConversationId`);
    check("a conversation is auto-selected", !!activeIsFirst, activeIsFirst);

    // switch to the older conversation
    await ev(`window.switchConversation('${c1.id}'); true`);
    await sleep(1200);
    const nowActive = await ev(`window.STATE.activeConversationId`);
    check("switching conversations updates active id", nowActive === c1.id, nowActive);
    const activeClass = await ev(`document.querySelector('#conversation-list .list-item.active')?.dataset.cid`);
    check("active conversation highlighted in list", activeClass === c1.id, activeClass);

    // create a new chat through the UI
    await ev(`window.newConversation(); true`);
    await sleep(2000);
    const afterNew = await ev(`window.STATE.conversations.length`);
    check("new chat created via UI", afterNew === 3, afterNew);
    const newActive = await ev(`window.STATE.activeConversationId`);
    check("new chat becomes active", newActive !== c1.id && newActive !== c2.id, newActive);
    const clearedMessages = await ev(`window.STATE.messages.length`);
    check("new chat starts empty", clearedMessages === 0, clearedMessages);

    // delete a conversation through the UI (auto-accept confirm)
    await ev(`window.confirm = () => true; true`);
    await ev(`window.deleteConversation('${c2.id}'); true`);
    await sleep(2000);
    const afterDelete = await ev(`window.STATE.conversations.length`);
    check("conversation deleted via UI", afterDelete === 2, afterDelete);
    const stillThere = await ev(`window.STATE.conversations.some(c => c.id === '${c2.id}')`);
    check("deleted conversation removed from state", stillThere === false, stillThere);

    const errs = await ev(`JSON.stringify(window.__errs||[])`);
    check("no JS errors during conversation flow", errs === "[]", errs);

  } catch (e) {
    check("harness completed", false, e.message);
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { chrome.kill(); } catch (e) {}
  }

  await api("DELETE", "/api/projects/" + project.id);

  const passed = results.filter(r => r.ok).length;
  console.log("\n" + "=".repeat(60));
  console.log(`RESULT: ${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})();
