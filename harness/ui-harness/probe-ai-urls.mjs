// Read-only: open a ZZ offer's rules screen and print the full URL (host included) of every AI-service request.
import fs from 'fs';
import puppeteer from 'puppeteer-core';
const state = JSON.parse(fs.readFileSync(new URL('../results/devintam1/pulsar_offers.json', import.meta.url)));
const id = state[process.argv[2] || 'P01_gold_weekend_stores'].offer_id;
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
const seen = [];
const onReq = (r) => { if (/\/ai\/|pulsar|features/i.test(r.url())) seen.push(`${r.method()} ${r.url().split('?')[0]}`); };
page.on('request', onReq);
await page.goto(`https://master.devintam1.gravty.info/#/offer/${id}/rules`, { waitUntil: 'domcontentloaded' });
await page.reload({ waitUntil: 'domcontentloaded' });
await new Promise((r) => setTimeout(r, 12000));
page.off('request', onReq);
console.log([...new Set(seen)].join('\n'));
console.log('genai button:', await page.evaluate(() => !!document.querySelector('[class*="rules-genAi-button"]')));
await b.disconnect();
