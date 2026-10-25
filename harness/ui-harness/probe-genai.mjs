// Read-only: open a ZZ-AI-SPIKE offer's rules screen on the hosted tenant; report the GenAI button and /ai/ calls.
import fs from 'fs';
import puppeteer from 'puppeteer-core';
const state = JSON.parse(fs.readFileSync(new URL('../results/devintam1/offers.json', import.meta.url)));
const id = state[process.argv[2] || 'S14_simple_sanity'].offer_id;
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
const ai = [];
const onReq = (r) => { if (/\/ai\//.test(r.url())) ai.push(`${r.method()} ${r.url().replace(/^https:\/\/[^/]+/, '')}`); };
page.on('request', onReq);
await page.goto(`https://master.devintam1.gravty.info/#/offer/${id}/rules`, { waitUntil: 'domcontentloaded' });
await page.reload({ waitUntil: 'domcontentloaded' });
await new Promise((r) => setTimeout(r, 9000));
const info = await page.evaluate(() => {
  const btn = document.querySelector('[class*="rules-genAi-button"]');
  return { genai_button: !!btn, visible: !!(btn && btn.offsetParent), text: btn?.innerText?.trim().slice(0, 80),
           logo: !!document.querySelector('[class*="gen-ai-main-logo"]') };
});
page.off('request', onReq);
console.log(JSON.stringify(info), '\n' + [...new Set(ai)].join('\n'));
await b.disconnect();
