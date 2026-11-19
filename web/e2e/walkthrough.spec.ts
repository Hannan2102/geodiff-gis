import { expect, test, type Page } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Records docs/media/walkthrough.webm (about 75 s). Run only on demand:
//   WALKTHROUGH=1 npx playwright test e2e/walkthrough.spec.ts
// The committed file was then re-encoded to 12 fps VP9 (about 1 MB) with Playwright's bundled ffmpeg:
//   ffmpeg -i walkthrough.webm -r 12 -c:v libvpx-vp9 -deadline good -cpu-used 5 -row-mt 1 -crf 42 -b:v 0 -an out.webm
const here = dirname(fileURLToPath(import.meta.url));
const golden = join(here, '..', '..', 'fixtures', 'golden');
const OUT = join(here, '..', '..', 'docs', 'media', 'walkthrough.webm');

test.skip(process.env.WALKTHROUGH !== '1', 'set WALKTHROUGH=1 to record the walkthrough video');
test.use({ video: { mode: 'on', size: { width: 1280, height: 800 } }, viewport: { width: 1280, height: 800 } });

async function caption(page: Page, text: string, ms = 4500) {
  await page.evaluate((t) => {
    let el = document.getElementById('wt-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'wt-caption';
      el.setAttribute('aria-hidden', 'true');
      Object.assign(el.style, {
        position: 'fixed', left: '50%', bottom: '24px', transform: 'translateX(-50%)', zIndex: '9999',
        background: 'rgba(20,24,28,.88)', color: '#fff', font: '600 17px/1.35 system-ui, sans-serif',
        padding: '10px 16px', borderRadius: '6px', maxWidth: '900px', textAlign: 'center', pointerEvents: 'none',
      });
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
  await page.waitForTimeout(ms);
}

test('walkthrough video', async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/');
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  await caption(page, 'GeoDiff reconciles a field inventory of point assets with last month’s approved inventory.', 5000);
  await caption(page, 'The guided sample is synthetic and runs in the browser. It opens on bench BENCH-0142.', 5000);
  await caption(page, 'The field report moves it 119.6 m across 11th St, out of the park, and marks it damaged.', 5500);
  const cond = page.locator('[data-component="BENCH-0142:attr:condition"]');
  const move = page.locator('[data-component="BENCH-0142:move"]');
  await caption(page, 'Each change is a separate decision. Accept the condition update…', 2500);
  await cond.getByRole('button', { name: /Accept/ }).click();
  await caption(page, '…and reject the implausible move. The approved position is kept.', 2500);
  await move.getByRole('button', { name: /Reject/ }).click();
  await caption(page, 'Moves within the 2 m tolerance are GPS noise and never enter the queue.', 4500);
  await page.getByTestId('preset').selectOption('1');
  await caption(page, 'A 1 m tolerance surfaces more candidates (91 decisions instead of 54).', 4500);
  await page.getByTestId('preset').selectOption('2');
  await page.getByTestId('export').click();
  await caption(page, 'A draft export is always available. The final export unlocks when every change is decided.', 5000);
  await page.keyboard.press('Escape');

  await page.getByTestId('try-files').first().click();
  await expect(page.getByTestId('api-status')).toContainText('Live API ready', { timeout: 20_000 });
  await caption(page, 'Live mode: upload your own GeoJSON to a private, temporary workspace (FastAPI + PostGIS).', 4500);
  await page.getByTestId('file-baseline').setInputFiles(join(golden, 'baseline.geojson'));
  await page.getByTestId('file-proposed').setInputFiles(join(golden, 'proposed.geojson'));
  await expect(page.getByTestId('summary-proposed')).toContainText('13 features');
  await caption(page, 'Files are validated, the ID field is suggested, and you choose which fields to compare.', 5000);
  await page.getByTestId('run-compare').click();
  await expect(page.getByTestId('mode')).toHaveText('Live workspace', { timeout: 30_000 });
  await caption(page, 'The server compares by stable ID, never by position, and measures moves geodesically.', 5000);
  await page.locator('#queue-search').fill('BENCH-0142');
  await page.locator('#q-BENCH-0142').click();
  await page.locator('#queue-search').fill('');
  await caption(page, 'Decisions are saved on the server with versions, history and undo.', 3000);
  await page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ }).click();
  await page.locator('[data-component="BENCH-0142:move"]').getByRole('button', { name: /Reject/ }).click();
  await caption(page, 'The export holds only approved updates, plus a report with lineage, parameters and hashes.', 5000);
  await caption(page, 'Source files are never modified.', 3500);

  // Remove the demo workspace from the server.
  page.once('dialog', (d) => void d.accept());
  await page.getByTestId('reset').click();
  await expect(page.getByTestId('mode')).toHaveText('Guided sample');
  const video = page.video();
  await page.context().close();
  await video?.saveAs(OUT);
});
