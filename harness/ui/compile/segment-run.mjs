// Load generated member-segment XML into the REAL gravty-ui segment canvas (Select Members > new segment) against the
// mocked backend, and capture what the screen would save: the re-serialised XML, target_json and the DRL its own
// generator writes (SegmentationBlockly.generateDrlCode, the same call add-segment's getXMLDRL makes before saving).
// Member attributes, tiers and tier classes come from the tenant's dumps (default devintam1), so the dropdowns know the codes.
// usage: node segment-run.mjs --in=<dir of *.xml> --out=<dir> [--tenant=devintam1] [--headful --pause=<ms>]
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import { openScreen, loadSegmentInScreen, CHROME, CHROME_ARGS, tempProfile } from './screen.mjs';

const args = Object.fromEntries(process.argv.slice(2).map((a) => { const [k, v] = a.replace(/^--/, '').split('='); return [k, v ?? true]; }));
const IN = path.resolve(args.in), OUT = path.resolve(args.out);
fs.mkdirSync(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: args.headful ? false : true,
  userDataDir: tempProfile('segment'), args: CHROME_ARGS,
});
const report = {};
for (const f of fs.readdirSync(IN).filter((x) => x.endsWith('.xml')).sort()) {
  const name = f.replace(/\.xml$/, ''), xml = fs.readFileSync(path.join(IN, f), 'utf8');
  const s = await openScreen(browser, { tenant: args.tenant || 'devintam1', ctx: { counters: [], drlGet: [], posted: [] }, screen: 'segment' });
  const res = { segment: name, loaded: false };
  const out = s.error ? { loaded: false, error: s.error } : await loadSegmentInScreen(s.page, xml);
  if (!out.loaded) {
    res.error = out.error;
    await s.page.screenshot({ path: path.join(OUT, name + '.timeout.png') });
    report[name] = res; await s.context.close(); continue;
  }
  Object.assign(res, out, { saved_xml: out.canonical });
  res.console = s.console.slice(0, 20);
  fs.writeFileSync(path.join(OUT, name + '.drl'), res.drl || '');
  await s.page.screenshot({ path: path.join(OUT, name + '.png') });
  if (args.pause) { await s.page.bringToFront(); console.log(`  showing ${name}`); await new Promise((ok) => setTimeout(ok, Number(args.pause))); }
  console.log(`${name.padEnd(36)} blocks ${res.blocks?.length ?? '-'} error ${res.error || '-'} exceptions ${res.exceptions?.length ?? '-'} drl ${(res.drl || '').length}b`);
  report[name] = res;
  await s.context.close();
}
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
await browser.close();
