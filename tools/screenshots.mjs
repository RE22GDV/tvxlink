// Takes the README screenshots through the Chrome DevTools Protocol (Node 22+, no packages).
// Called by tools/screenshots.py:  node screenshots.mjs PORT scenario.json OUTDIR CHROME LANG [only]
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port, scenFile, outRoot, chrome, lang, only] = process.argv.slice(2);
const scen = JSON.parse(fs.readFileSync(scenFile, 'utf8'));
const outDir = path.join(outRoot, lang);
fs.mkdirSync(outDir, {recursive: true});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const W = 1600;

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'tvxlink-chrome-'));
const dbg = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${dbg}`, `--user-data-dir=${profile}`, '--no-first-run',
  '--no-default-browser-check', '--hide-scrollbars', '--force-color-profile=srgb', `--window-size=${W},1000`, 'about:blank'], {stdio: 'ignore'});

async function getJSON(url){
  for (let i = 0; i < 100; i++){ try { return await (await fetch(url)).json(); } catch(e){ await sleep(150); } }
  throw new Error('Chrome did not start');
}
const page = (await getJSON(`http://127.0.0.1:${dbg}/json/list`)).find(t => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let seq = 0; const pending = new Map();
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)){ const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); } };
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, {res, rej}); ws.send(JSON.stringify({id, method, params})); });
async function js(code){
  const r = await send('Runtime.evaluate', {expression: `(async () => { ${code} })()`, awaitPromise: true, returnByValue: true});
  if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception?.description || JSON.stringify(r.exceptionDetails)).slice(0, 600));
  return r.result.value;
}
async function shot(name, {to, from, pad = 16, height} = {}){
  if (only && !only.split(',').includes(name)) return;
  await sleep(500);
  const box = await js(`window.scrollTo(0, 0);
    const r = s => { const e = document.querySelector(s); const b = e.getBoundingClientRect(); return {top: b.top + scrollY, bottom: b.bottom + scrollY}; };
    return {from: ${from ? `r(${JSON.stringify(from)}).top` : 0}, to: ${to ? `r(${JSON.stringify(to)}).bottom` : 'document.documentElement.scrollHeight'}};`);
  const y = Math.max(0, box.from - (from ? pad : 0));
  const h = height || Math.min(box.to + pad, box.from + 2400) - y;
  const r = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true, clip: {x: 0, y, width: W, height: h, scale: 1}});
  fs.writeFileSync(path.join(outDir, name + '.png'), Buffer.from(r.data, 'base64'));
  console.log(`  ${lang}/${name}.png  ${W}×${Math.round(h)}`);
}
const tab = async t => { await js(`document.querySelector('#tabs button[data-t="${t}"]').click();`); await sleep(1400); };

await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', {width: W, height: 1000, deviceScaleFactor: 1, mobile: false});
await send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-color-scheme', value: 'dark'}]});
await send('Page.navigate', {url: `http://127.0.0.1:${port}/`});
await sleep(2500);
await js(`window.__S = ${JSON.stringify(scen)}; try { localStorage.clear(); } catch(e){}
  await setLang('${lang}', false); setConnected(true, __S.info, false);
  $('rLive').checked = false; clearInterval(window.__noop);`);

// 1 — antenna aiming: the real 19.2°E session (trend decoded from the exchange log)
await tab('aim');
await js(`const pts = __S.aim, last = pts[pts.length - 1];
  $('aimTarget').textContent = '19.2E 11156.00 V, SR 22000, DVB-S QPSK 5/6'; $('bAim').textContent = T('ui.stop');
  peaks = {LV: Math.max(...pts.map(p => p.lv)), CN: Math.max(...pts.map(p => p.cn)), ME: Math.max(...pts.map(p => p.me))};
  showAim({LV: {value: last.lv}, CN: {value: last.cn}, ME: {value: last.me}, status: {locked: true, text: 'MPEG-2 TS DVB-S', st: 6}});
  trend = pts.map(p => ({lv: p.lv, cn: p.cn})); drawTrend();`);
await shot('01-aiming', {to: '#t-aim section'});
await shot('08-look-angles', {from: '#t-aim section:nth-of-type(2)', to: '#t-aim section:nth-of-type(2)'});

// 2 — the meter tab: identification, tuning and readings of the real self-test (13°E, Camera dei Deputati)
await tab('dev');
await js(`await loadSat('13E'); const t = sat.transponders.find(t => t.pol === 'V' && Math.abs(t.freq_mhz - 10992.1) < 0.6); fillTune(t);
  $('tuneMsg').className = 'okc';
  $('tuneMsg').textContent = tuneText({locked: true, status: 'MPEG-2 TS DVB-S', if_mhz: 1242.1, sent: ['*SA3', '*LB2', '*FR1242100', '*CR01'], skipped: ['*RA27500', '*SI0']});
  $('usb').style.display = 'block'; $('usb').textContent = __S.usb + T('usb.tusb3410_name');
  const s = __S.series, M = [['*?FR','meas.freq','FR'],['*?LV','meas.level','LV'],['*?CN','meas.cn','CN'],['*?ME','meas.mer','ME'],['*?CB','meas.cber','CB'],['*?VB','meas.vber','VB']];
  renderTiles({values: M.map(([cmd, l, k]) => ({cmd, label: T(l), raw: s[k].raw, value: s[k].value, note: s[k].note})), status: {locked: true, text: 'MPEG-2 TS DVB-S', st: 6}});`);
await shot('02-meter', {to: '#t-dev'});

// 3 — spectrum: the meter's own sweep around 13°E 10992 V with the real lock result
await tab('sp');
await js(`await loadSat('13E'); $('sPol').value = 'V'; $('sBand').value = 'low';
  const sw = (await api('/api/sessions?kind=sweep')).sessions[0].name; await openSweep(sw); await refreshSaved(); $('sSaved').value = sw;
  $('sweepMsg').textContent = T('ui.sp.opened', {dir: '~/TVXLink/sessions/' + sw});
  for (const [k, v] of Object.entries(__S.scans)) if (k.startsWith('13E|')) results[v.freq_mhz + '|' + v.pol + '|' + v.sr] = v;
  tunedF = 10992.1; lockState = true; const s = __S.series;
  $('rLV').textContent = s.LV.value.toFixed(1); $('rCN').textContent = s.CN.value.toFixed(1); $('rME').textContent = s.ME.value.toFixed(1);
  $('rLock').textContent = 'MPEG-2 TS DVB-S'; $('rLock').className = 'st good'; showTuned(tpAt(10992.1));`);
await shot('03-spectrum', {to: '#t-sp section'});

// 4 — channel database of 19.2°E with the real scan/pair results on top
await tab('ch');
await js(`await loadSat('19.2E');
  for (const [k, v] of Object.entries(__S.scans)) if (k.startsWith('19.2E|')) results[v.freq_mhz + '|' + v.pol + '|' + v.sr] = v;
  for (const v of __S.pair) results[v.freq_mhz + '|' + v.pol + '|' + v.sr] = v;
  sortKey = 'r.locked'; sortDir = -1; renderTable(); renderBest();`);
await shot('04-channels', {height: 1180});

// 5 — lab: recommended channels, the real protocol and journal
await tab('lab');
await js(`$('kSat').value = '19.2E'; $('pSat').value = '19.2E'; await loadRec(); await loadPairs(); await loadLab(); await loadJournal();
  labCurrent = {pos: '19.2E', freq_mhz: 11156.0, pol: 'V', sr: 22000, system: 'DVB-S', modulation: 'QPSK', fec: '5/6', channels: ['Canal Sur Andalucia', 'DAZN 2 Spain', 'Antena 3']};
  renderLab(); $('labCsv').textContent = '~/TVXLink/lab/protocol.csv';`);
await shot('05-lab-protocol', {to: '#t-lab section:nth-of-type(2)'});

// 6 — decoding threshold run (71 real readings, two losses of lock), BER(C/N) theory vs readings, QPSK/8PSK pair
await js(`edgeItems = __S.edge.items; edgeExtra = __S.edge.extra; const last = edgeItems[edgeItems.length - 1];
  showLock('eLock', {locked: last.locked, text: last.device_status, st: last.st});
  $('eCN').textContent = f1(last.cn); $('eME').textContent = f1(last.mer);
  $('eCB').textContent = fBer(last.cber_v, last.cber); $('eVB').textContent = fBer(last.vber_v, last.vber);
  $('edgeMsg').textContent = T('ui.edge.stopped', {n: edgeItems.length, dir: '…/sessions/20261112_123022_edge'});
  renderEdgeBox(); drawEdge(); await drawBer();
  pairItems = __S.pair; renderPairRes({running: false});`);
await shot('06-threshold-ber', {from: '#t-lab section:nth-of-type(3)', to: '#t-lab section:nth-of-type(5)'});

// 7 — found channels (every real lock of 2026-11-12)
await tab('found');
await js(`await loadFound(); window.loadFound = async () => {}; $('fCsv').textContent = '~/TVXLink/found/found.csv';`);
await shot('07-found', {to: '#t-found section'});

ws.close(); proc.kill();
await new Promise(r => proc.once('exit', r));
try { fs.rmSync(profile, {recursive: true, force: true, maxRetries: 5}); } catch(e){}
