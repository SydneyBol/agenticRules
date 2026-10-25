// Hosted engine BIT simulation of the rules currently SAVED on one planning offer, through the stock offer-testing API:
//   1. POST offers/{id}/test-runs/        bolapi propagates the offer to the engine's OFFER_TEST table (status -> RUNTEST)
//   2. POST offer-test-runs/{run}/test-cases/  one per BIT: bolapi -> engine /api/offer/{id}/simulation/bit
//      (the engine validates the BIT first: unknown h_location -> LOCATION_NOT_FOUND, inactive -> LOCATION_NOT_ACTIVE)
//   3. PUT  offers/{id}/test-runs/{run}/  run_status=ERRORED: bolapi puts the offer back to planning
// Each test case is stateless: the member (MA_P attributes, MA_O counters) is mocked in the input; a chain carries the
// counters one BIT leaves (output mtomap) into the next, and a store probe starts from its chain's snapshot.
// usage: node hosted-simulate.mjs --offer=<id> --cases=cases.json [--tenant=devintam1] [--out=hosted_simulation]
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const arg = (n, d) => (process.argv.find((a) => a.startsWith(`--${n}=`)) || '').split('=').slice(1).join('=') || d;
const OFFER = Number(arg('offer'));
const CASES = JSON.parse(fs.readFileSync(arg('cases'), 'utf8'));
const TENANT = arg('tenant', 'devintam1');
const OUT = path.join(ROOT, 'results', ...(TENANT === 'devintam1' ? [] : [TENANT]), arg('out', 'hosted_simulation'));
fs.mkdirSync(OUT, { recursive: true });

const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes(`${TENANT}.gravty.info`));
if (!page) throw new Error(`no ${TENANT} tab open in the debug Chrome`);
const api = (method, rel, body) => page.evaluate(async (method, rel, body) => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const r = await fetch(`/bolapi/v1/${rel}`, { method, body: body ? JSON.stringify(body) : undefined,
    headers: { Authorization: 'JWT ' + cookie('token_key'), 'X-CSRFToken': cookie('XSRF-COOKIE'), 'x-aws-waf-token': cookie('aws-waf-token'), 'Content-Type': 'application/json' } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t.slice(0, 1000) }; }
}, method, rel, body);
const statusOf = async () => { const o = (await api('GET', `offers/${OFFER}/`)).body; return o?.status?.code || o?.status_code || o?.status; };

const result = { offer: OFFER, status_before: await statusOf(), cases: [] };
if (!/planning/i.test(JSON.stringify(result.status_before))) {
  console.error(`offer ${OFFER} is not in planning (${JSON.stringify(result.status_before)}); not starting a test run`);
  process.exit(2);
}
const runs = (await api('GET', `offers/${OFFER}/test-runs/?page_size=100`)).body;
const seqNo = 1 + Math.max(0, ...((runs?.results || runs || []).map((r) => r.sequence_number || 0)));
// as the stock Offer Testing screen does: the run is created with the whole offer, then stays PENDING while bolapi
// copies the offer into the engine's test table; test cases are accepted only once it has left PENDING (and the offer
// is out of 'Running Test')
const offerBody = (await api('GET', `offers/${OFFER}/`)).body;
const run = await api('POST', `offers/${OFFER}/test-runs/`, { ...offerBody, offer: OFFER, sequence_number: seqNo });
result.test_run = { status: run.status, id: run.body?.id, sequence_number: seqNo, error: run.status >= 300 ? run.body : undefined };
try {
  if (run.status >= 300) throw new Error('test run not created: HTTP ' + run.status + ' ' + JSON.stringify(run.body).slice(0, 300));
  result.status_during = await statusOf();
  const t0 = Date.now();
  let rs = run.body?.run_status;
  while (Date.now() - t0 < 600000) {
    rs = (await api('GET', `offers/${OFFER}/test-runs/${run.body.id}/`)).body?.run_status;
    if (rs !== 'PENDING' && !/runtest/i.test(JSON.stringify(await statusOf()))) break;
    await new Promise((ok) => setTimeout(ok, 5000));
  }
  result.run_ready = { run_status: rs, offer_status: await statusOf(), waited_s: Math.round((Date.now() - t0) / 1000) };
  console.log('test run ready:', JSON.stringify(result.run_ready));
  // the run may already hold generated test cases: manual cases go after them (test_run + sequence_number is unique)
  const existing = (await api('GET', `offer-test-runs/${run.body.id}/test-cases/?page_size=1000`)).body;
  const firstSeq = 1 + Math.max(0, ...((existing?.results || existing || []).map((c) => c.sequence_number || 0)));
  result.generated_cases = firstSeq - 1;
  // each simulated BIT is stateless: a sequence (same chain) gets the counters the previous BIT left as its member MA_O
  // a store probe starts from the counters its chain had before the BIT it replays (snapshots[chain][step])
  const carried = {}, snapshots = {};
  for (const [i, c] of CASES.entries()) {
    if (c.chain) (snapshots[c.chain] ||= [])[c.step] = { ...(carried[c.chain] || {}) };
    const start = c.chain ? carried[c.chain] : c.from ? snapshots[c.from[0]]?.[c.from[1]] : null;
    if (start) c.input.member.MA_O = { ...c.input.member.MA_O, ...start };
    const body = { test_run: run.body.id, sequence_number: firstSeq + i, description: c.description, is_manual: true,
      test_type: c.expect_reward ? 'POSITIVE' : 'NEGATIVE', input: c.input };
    let r = await api('POST', `offer-test-runs/${run.body.id}/test-cases/`, body);
    // the run is briefly 'Running Test' while the previous case is processed: wait and add again
    for (let k = 0; k < 5 && r.status === 400 && /invalid_offertestcase_mode/.test(JSON.stringify(r.body)); k++) {
      await new Promise((ok) => setTimeout(ok, 3000));
      r = await api('POST', `offer-test-runs/${run.body.id}/test-cases/`, body);
    }
    const out = r.body?.output ?? r.body;
    const rp = out?.points_rewarded_in_bit ?? out?.points_rewarded;
    const pts = typeof rp === 'number' ? rp : typeof rp === 'object' && rp ? Object.values(rp).reduce((t, v) => t + Number(v || 0), 0) : 0;
    const privs = (out?.assigned_privileges || []).map((x) => String(x.product_id ?? x.privilege_product_id ?? x.product ?? x.id));
    const err = r.body?.error?.code;
    const got = r.status < 300 ? (privs.length ? privs.join(',') + (pts ? ` +${pts} points` : '') : pts) : err;
    // exact: the expected points and no privilege; the expected privilege and no points; null = nothing at all;
    // {points, privilege | privileges} = those points and exactly those privileges (several rewards on one BIT)
    const ok = r.status < 300;
    const same = (want) => ok && Math.abs(pts - want.points) < 0.01 && [...privs].sort().join() === want.ids.map(String).sort().join();
    const pass = typeof c.expect === 'number' ? same({ points: c.expect, ids: [] })
      : c.expect === null ? same({ points: 0, ids: [] })
      : typeof c.expect === 'object' ? same({ points: c.expect.points || 0, ids: c.expect.privileges || (c.expect.privilege != null ? [c.expect.privilege] : []) })
      : /^\d+$/.test(String(c.expect)) ? same({ points: 0, ids: [c.expect] })
      : (!ok && (err === c.expect || (c.expect === 'LOCATION_NOT_FOUND' && /location code/i.test(JSON.stringify(r.body)))));
    if (c.chain && r.status < 300) {
      // output mtomap is {offer_id: {counter: value}}; the mocked member takes MA_O as {counter: value}
      const m = Object.assign({}, ...Object.values(out?.mtomap || {}).filter((v) => v && typeof v === 'object'));
      if (m && Object.keys(m).length) carried[c.chain] = { ...(carried[c.chain] || {}), ...m };
    }
    result.cases.push({ ...c, http: r.status, got, pass, output: out });
    console.log(`${String(i + 1).padStart(2)} ${c.description.padEnd(60)} HTTP ${r.status} got ${JSON.stringify(got)} ${pass ? 'PASS' : 'FAIL'}`);
  }
} finally {
  if (result.test_run.id) {
    const done = await api('PUT', `offers/${OFFER}/test-runs/${result.test_run.id}/`, { offer: OFFER, sequence_number: seqNo, run_status: 'ERRORED' });
    result.revert = { status: done.status };
  }
  // a run that reached review leaves the offer in review: move it back to planning, as a manager would
  if (/review/i.test(JSON.stringify(await statusOf()))) {
    const back = await api('POST', 'offers/status/', { offer_id: OFFER, status: 'planning', comment: 'AI rules QA: back to planning after a test run' });
    result.back_to_planning = back.status;
    for (let k = 0; k < 20 && !/planning/i.test(JSON.stringify(await statusOf())); k++) await new Promise((ok) => setTimeout(ok, 2000));
  }
  result.status_after = await statusOf();
  result.passed = result.cases.filter((c) => c.pass).length;
  result.scores = Object.fromEntries(['scenario', 'store', 'validation'].map((k) => {
    const of = result.cases.filter((c) => (c.kind || 'scenario') === k);
    return [k, `${of.filter((c) => c.pass).length}/${of.length}`];
  }));
  console.log(`scores: scenario ${result.scores.scenario}, store probes ${result.scores.store}, input validation ${result.scores.validation}`);
  console.log(`simulation: ${result.passed}/${result.cases.length} cases as expected`);
  fs.writeFileSync(path.join(OUT, `offer_${OFFER}_run_${seqNo}.json`), JSON.stringify(result, null, 1));
  console.log(`offer status: before ${JSON.stringify(result.status_before)}, during ${JSON.stringify(result.status_during)}, after ${JSON.stringify(result.status_after)}`);
  await b.disconnect();
}
