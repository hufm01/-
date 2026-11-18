import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync, rmSync } from 'node:fs';
const [,, html, outDir, fps = '30', dur = '15.5', only] = process.argv;
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const port = process.env.CDP_PORT || 9555;  // Chrome started separately with --remote-debugging-port
const prof = null; const ch = { kill() {} };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let targets;
for (let i = 0; i < 50; i++) { try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (targets.find(t => t.type === 'page')) break; } catch {} await sleep(200); }
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map(); const events = [];
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else events.push(d); };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1080, height: 1920, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: 'file:///' + html.split(String.fromCharCode(92)).join('/') });
for (let i = 0; i < 100 && !events.find(e => e.method === 'Page.loadEventFired'); i++) await sleep(100);
await send('Runtime.evaluate', { expression: 'document.fonts.ready.then(()=>Promise.all([...document.images].map(i=>i.decode())))', awaitPromise: true });
mkdirSync(outDir, { recursive: true });
const n = Math.round(parseFloat(dur) * parseFloat(fps));
const frames = only ? only.split(',').map(s => Math.round(parseFloat(s) * fps)) : [...Array(n).keys()];
for (const f of frames) {
  const r = await send('Runtime.evaluate', { expression: `render(${f / fps});1` });
  if (r.result?.exceptionDetails) { console.log(JSON.stringify(r.result.exceptionDetails)); break; }
  const s = await send('Page.captureScreenshot', { format: only ? 'png' : 'jpeg', quality: only ? undefined : 94 });
  writeFileSync(`${outDir}/f${String(f).padStart(4, '0')}.${only ? 'png' : 'jpg'}`, Buffer.from(s.result.data, 'base64'));
}
console.log('frames', frames.length);
ws.close(); ch.kill(); await sleep(500); 
process.exit(0);
