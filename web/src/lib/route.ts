// Hash routes keep deep links refresh-safe on static hosting (GitHub Pages has no SPA fallback).
//   #/sample/BENCH-0142?t=2   guided sample, asset, tolerance preset
//   #/live/BENCH-0142         live workspace (comparison from this browser's session)
export interface Route {
  mode: 'sample' | 'live' | null;
  asset: string | null;
  tolerance: number | null;
}

export function parseRoute(hash: string): Route {
  const m = /^#\/(sample|live)(?:\/([^?]*))?(?:\?(.*))?$/.exec(hash);
  if (!m) return { mode: null, asset: null, tolerance: null };
  const params = new URLSearchParams(m[3] ?? '');
  const t = params.get('t');
  let asset: string | null;
  try {
    asset = m[2] ? decodeURIComponent(m[2]) : null;
  } catch {
    asset = null;
  }
  const tol = t !== null && t.trim() !== '' && Number.isFinite(Number(t)) ? Number(t) : null;
  return { mode: m[1] as Route['mode'], asset, tolerance: tol };
}

export function formatRoute(r: Route): string {
  if (!r.mode) return '';
  let h = `#/${r.mode}`;
  if (r.asset) h += `/${encodeURIComponent(r.asset)}`;
  if (r.mode === 'sample' && r.tolerance !== null) h += `?t=${r.tolerance}`;
  return h;
}

/** Replace the hash without adding history entries or triggering a hashchange reload. */
export function replaceRoute(r: Route): void {
  const h = formatRoute(r);
  if (h && h !== window.location.hash) window.history.replaceState(null, '', h);
}
