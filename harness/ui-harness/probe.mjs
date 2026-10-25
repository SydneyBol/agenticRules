import puppeteer from 'puppeteer-core';
import fs from 'node:fs'; import path from 'node:path';
import * as M from './fixtures/mocks.mjs';
const HERE = path.dirname(new URL(import.meta.url).pathname);
const b = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true, userDataDir: fs.mkdtempSync(path.join(HERE, 'tmp-profile-mock-')) });
const page = await b.newPage();
await page.setRequestInterception(true);
page.on('request', (req) => { const u = new URL(req.url()); if (u.hostname==='localhost' && u.port==='4200') return req.continue(); if (!/gravty\.info$/.test(u.hostname)) return req.abort();
  const cors={'Access-Control-Allow-Origin':'http://localhost:4200','Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'*'};
  if (req.method()==='OPTIONS') return req.respond({status:204,headers:cors,body:''});
  let body = M.route(req.method(), u, req.postData(), {counters:[]}); if (body===null) { console.log('UNK', req.method(), u.pathname+u.search); body=[]; }
  req.respond({status:200,headers:{...cors,'Content-Type':'application/json'},body:JSON.stringify(body)}); });
page.on('framenavigated', f => { if (f === page.mainFrame()) console.log('NAV', f.url()); });
page.on('console', m => { if (/reload|logout|session/i.test(m.text())) console.log('CONSOLE', m.text().slice(0,200)); });
await page.evaluateOnNewDocument((g) => { if (location.origin!=='http://localhost:4200') return; if (!sessionStorage.getItem('x')) { sessionStorage.setItem('x','1'); localStorage.setItem('globals', JSON.stringify(g)); localStorage.setItem('offe_type','reward'); }
  const r = location.reload.bind(location); }, M.globals);
await page.setCookie({ name: 'XSRF-COOKIE', value: 'mock-xsrf-local', url: 'http://localhost:4200/' });
await page.goto('http://localhost:4200/#/offer/1001/rules', { waitUntil: 'domcontentloaded' });
await new Promise(r => setTimeout(r, 15000));
console.log('FINAL', page.url(), await page.evaluate(() => JSON.stringify(Object.keys(localStorage))));
await page.screenshot({ path: 'probe.png' });
await b.close();
