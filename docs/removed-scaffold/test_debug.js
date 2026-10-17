const { spawn } = require("child_process");
const http = require("http");
const os = require("os");
const path = require("path");
const WebSocket = require("ws");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP = "http://127.0.0.1:5173/";
const PORT = 9334;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => { let d = ""; res.on("data", c => d += c); res.on("end", () => resolve(d)); }).on("error", reject);
  });
}

(async () => {
  const userData = path.join(os.tmpdir(), "imggen-dbg");
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${userData}`, "about:blank"], { stdio: "ignore" });
  let ws;
  try {
    let t = null;
    for (let i = 0; i < 40; i++) { await sleep(500); try { t = JSON.parse(await get(`http://127.0.0.1:${PORT}/json/version`)); break; } catch (e) {} }
    ws = new WebSocket(t.webSocketDebuggerUrl, { perMessageDeflate: false, maxPayload: 512 * 1024 * 1024 });
    await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });

    let id = 0;
    const send = (method, params = {}, sessionId = undefined) => new Promise((resolve, reject) => {
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
      if (r.exceptionDetails) return "ERROR: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    };

    await send("Runtime.enable", {}, sessionId);
    await send("Page.enable", {}, sessionId);
    await send("Page.navigate", { url: APP }, sessionId);
    await sleep(5000);

    console.log("=== DIAGNOSTICS ===");
    console.log("URL:", await ev("location.href"));
    console.log("readyState:", await ev("document.readyState"));
    console.log("title:", await ev("document.title"));
    console.log("body len:", await ev("document.body ? document.body.innerHTML.length : 'no body'"));
    console.log("body first 400:", await ev("document.body ? document.body.innerHTML.slice(0,400) : 'none'"));
    console.log("scripts:", await ev("Array.from(document.scripts).map(s=>s.src||'inline').join(' | ')"));
    console.log("window.STATE type:", await ev("typeof window.STATE"));
    console.log("STATE.projects len:", await ev("window.STATE ? window.STATE.projects.length : 'n/a'"));
    console.log("sidebarPanel:", await ev("window.STATE ? window.STATE.sidebarPanel : 'n/a'"));
    console.log("sidebar-content innerHTML len:", await ev("(document.getElementById('sidebar-content')||{}).innerHTML?.length ?? 'none'"));
    console.log("new-project-btn exists:", await ev("!!document.getElementById('new-project-btn')"));
    console.log("snav buttons:", await ev("document.querySelectorAll('.snav-btn').length"));
    console.log("sidebar-content classes:", await ev("(document.getElementById('sidebar-content')||{}).className"));
    console.log("--- sidebar html (first 600) ---");
    console.log(await ev("(document.getElementById('sidebar-content')||{}).innerHTML?.slice(0,600) ?? 'none'"));
    console.log("--- renderSidebar callable:", await ev("typeof window.renderSidebar"));
    console.log("--- init callable:", await ev("typeof window.init"));

    // try forcing a render
    console.log("--- forcing renderSidebar ---");
    console.log(await ev("(typeof window.renderSidebar==='function') ? (window.renderSidebar(), 'rendered; btn=' + !!document.getElementById('new-project-btn')) : 'not exposed'"));

  } catch (e) {
    console.log("HARNESS ERROR:", e.message);
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { chrome.kill(); } catch (e) {}
  }
  process.exit(0);
})();
