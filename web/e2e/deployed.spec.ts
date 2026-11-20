import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Verifies the public deployment from fresh browser contexts. Runs only on demand:
//   DEPLOYED_URL=https://hannan2102.github.io/geodiff-gis/ \
//   DEPLOYED_API=https://geodiff-api.onrender.com npx playwright test e2e/deployed.spec.ts
// Every workspace it creates is deleted at the end of its test.
const SITE = process.env.DEPLOYED_URL ?? '';
const API = (process.env.DEPLOYED_API ?? '').replace(/\/$/, '');
const shots = process.env.SCREENSHOT_DIR;
const golden = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures', 'golden');
const decisions = JSON.parse(readFileSync(join(golden, 'decisions.json'), 'utf8')) as Record<string, { disposition: 'accepted' | 'rejected' }>;
const expected = JSON.parse(readFileSync(join(golden, 'expected_reviewed.geojson'), 'utf8')) as { features: unknown[] };
const GOLDEN_FIELDS = ['condition', 'install_year', 'material', 'notes'];

test.skip(!SITE || !API, 'set DEPLOYED_URL and DEPLOYED_API to verify a deployment');
test.describe.configure({ mode: 'serial' });

function watch(page: Page) {
  const problems: string[] = [];
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => m.type() === 'error' && problems.push(`console: ${m.text()}`));
  page.on('response', (r) => r.status() >= 400 && !r.url().startsWith(API) && problems.push(`${r.status()} ${r.url()}`));
  return problems;
}

test('sample: HTTPS, assets, map, direct link and a draft export, with no API calls', async ({ page }) => {
  const problems = watch(page);
  const apiHits: string[] = [];
  page.on('request', (r) => r.url().startsWith(API) && apiHits.push(r.url()));
  await page.setViewportSize({ width: 1440, height: 900 });
  const res = await page.goto(SITE);
  expect(res?.url()).toMatch(/^https:\/\//);
  await expect(page.getByTestId('mode')).toHaveText('Guided sample');
  await expect(page.getByRole('heading', { level: 2, name: 'BENCH-0142' })).toBeVisible();
  await expect(page.locator('canvas.maplibregl-canvas')).toBeVisible();
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  await expect(page.locator('.queue-count')).toContainText('54 decisions left');
  if (shots) await page.screenshot({ path: join(shots, 'deployed-sample-1440.png') });

  const files: import('@playwright/test').Download[] = [];
  page.on('download', (d) => files.push(d));
  await page.getByTestId('export').click();
  await page.getByTestId('export-draft').click();
  await expect.poll(() => files.length).toBe(2);
  const geo = files.find((f) => !f.suggestedFilename().includes('report'))!;
  const out = JSON.parse(readFileSync((await geo.path())!, 'utf8')) as { features: unknown[] };
  expect(out.features).toHaveLength(400);

  await page.goto(`${SITE}#/sample/BIKE-0013?t=2`);
  await page.reload();
  await expect(page.getByRole('heading', { level: 2, name: 'BIKE-0013' })).toBeVisible();
  expect(apiHits, 'the sample never calls the API').toEqual([]);
  expect(problems).toEqual([]);
});

test('phone width: map and quick decision bar, no horizontal scroll', async ({ page }) => {
  const problems = watch(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(SITE);
  await expect(page.getByRole('region', { name: 'Quick decision' })).toContainText('119.6 m');
  await expect(page.locator('canvas.maplibregl-canvas')).toBeVisible();
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (shots) await page.screenshot({ path: join(shots, 'deployed-sample-390.png') });
  expect(problems).toEqual([]);
});

test('live: upload the golden pair, decide, final export equals golden, reimport is clean, workspaces are isolated', async ({ page, browser }) => {
  test.setTimeout(240_000);
  const problems = watch(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(SITE);
  await page.getByTestId('try-files').first().click();
  await expect(page.getByTestId('api-status')).toContainText('Live API ready', { timeout: 90_000 });
  await page.getByTestId('file-baseline').setInputFiles(join(golden, 'baseline.geojson'));
  await expect(page.getByTestId('summary-baseline')).toContainText('13 features', { timeout: 30_000 });
  await page.getByTestId('file-proposed').setInputFiles(join(golden, 'proposed.geojson'));
  await expect(page.getByTestId('summary-proposed')).toContainText('13 features', { timeout: 30_000 });
  const fields = page.getByRole('group', { name: 'Fields to compare' });
  await expect(page.getByTestId('run-compare')).toBeEnabled({ timeout: 30_000 }); // field suggestions have arrived
  for (const box of await fields.getByRole('checkbox').all()) {
    if (await box.isDisabled()) continue;
    const name = (await box.locator('xpath=..').innerText()).trim().split(/\s+/)[0]!;
    await box.setChecked(GOLDEN_FIELDS.includes(name));
  }
  await page.getByTestId('run-compare').click();
  await expect(page.getByTestId('mode')).toHaveText('Live workspace', { timeout: 30_000 });
  await expect(page.locator('.queue-count')).toContainText('9 decisions left');

  // The story: accept the condition update, reject the move.
  await page.locator('#queue-search').fill('BENCH-0142');
  await page.locator('#q-BENCH-0142').click();
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  const cond = () => page.locator('[data-component="BENCH-0142:attr:condition"]');
  const move = () => page.locator('[data-component="BENCH-0142:move"]');
  await cond().getByRole('button', { name: /Accept/ }).click();
  await expect(cond().getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
  await move().getByRole('button', { name: /Reject/ }).click();
  await expect(move().getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'true');
  if (shots) await page.screenshot({ path: join(shots, 'deployed-live-1440.png') });

  for (const [cid, d] of Object.entries(decisions)) {
    if (cid.startsWith('BENCH-0142')) continue;
    const asset = cid.split(':')[0]!;
    await page.locator('#queue-search').fill(asset);
    await page.locator(`#q-${asset}`).click();
    const name = d.disposition === 'accepted' ? /Accept/ : /Reject/;
    await page.locator(`[data-component="${cid}"]`).getByRole('button', { name }).click();
    await expect(page.locator(`[data-component="${cid}"]`).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true');
  }
  await page.locator('#queue-search').fill('');
  await expect(page.locator('.queue-count')).toContainText('0 decisions left');

  const files: import('@playwright/test').Download[] = [];
  page.on('download', (d) => files.push(d));
  await page.getByTestId('export').click();
  await page.getByTestId('export-final').click();
  await expect.poll(() => files.length, { timeout: 30_000 }).toBe(2);
  const geo = files.find((f) => f.suggestedFilename().endsWith('.geojson'))!;
  const exported = readFileSync((await geo.path())!);
  expect((JSON.parse(exported.toString()) as { features: unknown[] }).features).toEqual(expected.features);

  // Reimport the export and compare it with the hand-authored expectation: no changes.
  const { token, cid } = await page.evaluate(() => ({
    token: localStorage.getItem('geodiff.live.token')!,
    cid: localStorage.getItem('geodiff.live.comparison')!,
  }));
  const auth = { Authorization: `Bearer ${token}` };
  const up = async (name: string, body: Buffer) => {
    const r = await page.request.post(`${API}/v1/datasets?name=${name}`, { headers: { ...auth, 'Content-Type': 'application/geo+json' }, data: body });
    expect(r.status(), await r.text()).toBe(201);
    return ((await r.json()) as { dataset_id: string }).dataset_id;
  };
  const a = await up('reimported.geojson', exported);
  const b = await up('expected.geojson', readFileSync(join(golden, 'expected_reviewed.geojson')));
  const cmp = await page.request.post(`${API}/v1/comparisons`, {
    headers: auth,
    data: { baseline_dataset_id: a, proposed_dataset_id: b, baseline_id_field: 'asset_id', compared_fields: GOLDEN_FIELDS, tolerance_m: 2 },
  });
  expect(cmp.status(), await cmp.text()).toBe(201);
  const summary = ((await cmp.json()) as { summary: { components: number; matched_assets: number } }).summary;
  expect(summary.components).toBe(0);
  expect(summary.matched_assets).toBe(13);

  // Isolation: a second fresh browser has its own workspace and cannot read this one.
  const other = await browser.newContext();
  const op = await other.newPage();
  const ws = await op.request.post(`${API}/v1/workspaces`);
  expect(ws.status()).toBe(201);
  const otherToken = ((await ws.json()) as { token: string }).token;
  const foreign = await op.request.get(`${API}/v1/comparisons/${cid}`, { headers: { Authorization: `Bearer ${otherToken}` } });
  expect(foreign.status()).toBe(404);
  const exportForeign = await op.request.get(`${API}/v1/comparisons/${cid}/export?mode=final&part=geojson`, { headers: { Authorization: `Bearer ${otherToken}` } });
  expect(exportForeign.status()).toBe(404);
  const anonymous = await op.request.get(`${API}/v1/comparisons/${cid}`);
  expect(anonymous.status()).toBe(401);

  // Clean up both workspaces and confirm they are gone.
  for (const t of [token, otherToken]) {
    const del = await page.request.delete(`${API}/v1/workspace`, { headers: { Authorization: `Bearer ${t}` } });
    expect(del.status()).toBeLessThan(300);
    const gone = await page.request.get(`${API}/v1/workspace`, { headers: { Authorization: `Bearer ${t}` } });
    expect(gone.status()).toBe(401);
  }
  await other.close();
  expect(problems).toEqual([]);
});
