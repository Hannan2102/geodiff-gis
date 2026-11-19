import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { ApiError, LiveAdapter, liveSession, workspaceApi } from './adapters/live';
import { loadSampleBundle, resetSample, SampleAdapter, type SampleBundle } from './adapters/sample';
import DecisionBar from './components/DecisionBar';
import Evidence from './components/Evidence';
import FallbackTable from './components/FallbackTable';
import Queue from './components/Queue';
import Toolbar, { type PresetControl } from './components/Toolbar';
import Tour from './components/Tour';
import UploadDialog from './components/UploadDialog';
import { parseRoute, replaceRoute, type Route } from './lib/route';
import { webglAvailable } from './map/webgl';
import { ReviewProvider, useReview } from './state/review';
import type { ComparisonInfo } from './types';

// MapLibre is large; load it after the shell so the queue and details render first.
const MapView = lazy(() => import('./components/MapView'));

type Layout = 'wide' | 'medium' | 'narrow';

function subscribeResize(cb: () => void) {
  window.addEventListener('resize', cb);
  return () => window.removeEventListener('resize', cb);
}
const currentLayout = (): Layout => (window.innerWidth < 768 ? 'narrow' : window.innerWidth < 1200 ? 'medium' : 'wide');

function useLayout(): Layout {
  return useSyncExternalStore(subscribeResize, currentLayout, () => 'wide');
}

function Notices() {
  const { notice, setNotice } = useReview();
  useEffect(() => {
    if (!notice || notice.tone === 'error') return;
    const t = setTimeout(() => setNotice(null), 7000);
    return () => clearTimeout(t);
  }, [notice, setNotice]);
  return (
    <div className="notices" aria-live="polite" role="status" data-testid="notice">
      {notice && (
        <div className={`notice notice-${notice.tone}`}>
          <p>
            {notice.text} {notice.guidance && <span className="hint">{notice.guidance}</span>}
          </p>
          <button type="button" className="btn ghost" onClick={() => setNotice(null)} aria-label="Dismiss message">
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

function LoadProblem({ onSample }: { onSample?: () => void }) {
  const { loadError, reload, info } = useReview();
  if (!loadError || info) return null;
  return (
    <div className="load-problem" role="alert" data-testid="load-problem">
      <h2>{loadError.offline ? 'Live API unavailable' : 'Could not open this review'}</h2>
      <p>{loadError.message}</p>
      <p className="hint">{loadError.guidance} Your live workspace is kept; nothing was lost or replaced.</p>
      <div className="dialog-actions">
        <button type="button" className="btn primary" onClick={() => void reload()}>
          Retry
        </button>
        {onSample && (
          <button type="button" className="btn" onClick={onSample}>
            Open the guided sample instead
          </button>
        )}
      </div>
    </div>
  );
}

export interface StudioActions {
  onUpload: () => void;
  onTour: () => void;
  onSample?: () => void;
  onReset?: () => void;
  onImport?: () => void;
  onLive?: () => void;
  presets?: PresetControl;
}

function Studio({ route, ...actions }: StudioActions & { route: (asset: string | null) => Route }) {
  const layout = useLayout();
  const { selectedId } = useReview();

  // Keep the URL pointing at the selected asset so a refresh or shared link reopens it.
  useEffect(() => {
    replaceRoute(route(selectedId));
  }, [route, selectedId]);
  const [tab, setTab] = useState<'map' | 'queue' | 'details'>('map');
  const [drawer, setDrawer] = useState(true);
  const gl = useMemo(() => webglAvailable(), []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape' || document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      if (layout === 'medium' && drawer) setDrawer(false);
      else if (layout === 'narrow' && tab === 'details') setTab('map');
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [layout, drawer, tab]);

  const mapArea = gl ? (
    <Suspense fallback={<p className="map-note" role="status">Loading map…</p>}>
      <MapView />
    </Suspense>
  ) : (
    <FallbackTable />
  );
  const openDetails = () => {
    if (layout === 'narrow') setTab('details');
    else setDrawer(true);
  };

  return (
    <div className={`studio layout-${layout}`}>
      <Toolbar {...actions} />
      <LoadProblem onSample={actions.onSample} />
      {layout === 'narrow' ? (
        <>
          <div className="tabs" role="tablist" aria-label="Views">
            {(['map', 'queue', 'details'] as const).map((t) => (
              <button key={t} type="button" role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls={`panel-${t}`} onClick={() => setTab(t)}>
                {t === 'map' ? 'Map' : t === 'queue' ? 'Queue' : 'Details'}
              </button>
            ))}
          </div>
          <main className="tab-panels">
            <div id="panel-map" role="tabpanel" aria-labelledby="tab-map" hidden={tab !== 'map'} className="fill">
              {mapArea}
            </div>
            <div id="panel-queue" role="tabpanel" aria-labelledby="tab-queue" hidden={tab !== 'queue'} className="fill">
              <Queue onOpen={() => setTab('details')} />
            </div>
            <div id="panel-details" role="tabpanel" aria-labelledby="tab-details" hidden={tab !== 'details'} className="fill scroll">
              <Evidence />
            </div>
          </main>
          {tab !== 'details' && selectedId && <DecisionBar onDetails={() => setTab('details')} />}
        </>
      ) : (
        <main className="panes">
          <Queue onOpen={openDetails} />
          <div className="center">{mapArea}</div>
          {layout === 'wide' ? (
            <Evidence />
          ) : (
            selectedId &&
            drawer && (
              <div className="drawer">
                <Evidence onClose={() => setDrawer(false)} />
              </div>
            )
          )}
          {layout === 'medium' && selectedId && !drawer && (
            <button type="button" className="btn primary drawer-open" onClick={() => setDrawer(true)}>
              Details for <span className="mono">{selectedId}</span>
            </button>
          )}
        </main>
      )}
      <Notices />
    </div>
  );
}

const MODE_KEY = 'geodiff.mode';
function rememberMode(mode: 'sample' | 'live') {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* storage unavailable */
  }
}
function rememberedMode(): string | null {
  try {
    return localStorage.getItem(MODE_KEY);
  } catch {
    return null;
  }
}

type Session = { kind: 'sample'; tolerance: number | null; asset: string | null } | { kind: 'live'; adapter: LiveAdapter; asset: string | null };

function initialSession(): Session {
  const r = parseRoute(window.location.hash);
  const t = liveSession.token();
  const c = liveSession.comparisonId();
  const wantsLive = r.mode === 'live' || (r.mode === null && rememberedMode() === 'live');
  if (wantsLive && t && c) return { kind: 'live', adapter: new LiveAdapter(t, c), asset: r.asset };
  return { kind: 'sample', tolerance: r.tolerance, asset: r.mode === 'sample' ? r.asset : null };
}

/** Guided sample: loads the bundled engine output once, then runs fully in this browser. */
function SampleSession({
  tolerance,
  asset,
  onTolerance,
  actions,
}: {
  tolerance: number | null;
  asset: string | null;
  onTolerance: (t: number) => void;
  actions: Omit<StudioActions, 'presets' | 'onReset'>;
}) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ bundle?: SampleBundle; error?: ApiError } | null>(null);
  const [resetCount, setResetCount] = useState(0);

  useEffect(() => {
    let live = true;
    loadSampleBundle()
      .then((bundle) => live && setLoaded({ bundle }))
      .catch((e: unknown) => live && setLoaded({ error: e instanceof ApiError ? e : new ApiError(0, 'sample_unavailable', String(e)) }));
    return () => {
      live = false;
    };
  }, [attempt]);

  const bundle = loaded?.bundle;
  const tol = bundle && bundle.presets.some((p) => p.tolerance_m === tolerance) ? tolerance! : (bundle?.default_tolerance_m ?? 2);
  // A new adapter per preset or reset; resetCount is part of the identity on purpose.
  const adapter = useMemo(() => (bundle ? new SampleAdapter(bundle, tol) : null), [bundle, tol, resetCount]); // eslint-disable-line react-hooks/exhaustive-deps
  const route = useCallback((a: string | null): Route => ({ mode: 'sample', asset: a, tolerance: tol }), [tol]);

  if (loaded?.error) {
    return (
      <div className="load-problem" role="alert">
        <h2>The guided sample could not be loaded</h2>
        <p>{loaded.error.message}</p>
        <div className="dialog-actions">
          <button type="button" className="btn primary" onClick={() => setAttempt((n) => n + 1)}>
            Retry
          </button>
        </div>
      </div>
    );
  }
  if (!bundle || !adapter) {
    return (
      <p className="start" role="status">
        Loading the guided sample…
      </p>
    );
  }
  const reset = () => {
    if (!window.confirm('Reset the guided sample? This clears your sample decisions in this browser (all tolerance presets).')) return;
    resetSample(bundle);
    setResetCount((n) => n + 1);
  };
  return (
    <ReviewProvider key={`sample:${tol}:${resetCount}`} adapter={adapter} initialSelection={asset ?? bundle.focus_asset ?? undefined}>
      <Studio
        {...actions}
        route={route}
        onReset={reset}
        presets={{ values: bundle.presets.map((p) => p.tolerance_m), current: tol, onChange: onTolerance }}
      />
    </ReviewProvider>
  );
}

export default function App() {
  const [session, setSession] = useState<Session>(initialSession);
  const [uploading, setUploading] = useState<false | 'files' | 'public'>(false);
  const [touring, setTouring] = useState(false);
  const [current, setCurrent] = useState<ComparisonInfo | null>(null);

  const liveAdapter = session.kind === 'live' ? session.adapter : null;

  useEffect(() => {
    if (!liveAdapter) return;
    let live = true;
    liveAdapter
      .comparison()
      .then((i) => live && setCurrent(i))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [liveAdapter]);

  const openSample = useCallback(() => {
    rememberMode('sample');
    setSession({ kind: 'sample', tolerance: null, asset: null });
  }, []);

  // Return to this browser's live workspace, if one exists.
  const hasLive = !!(liveSession.token() && liveSession.comparisonId());
  const openLive = useMemo(
    () =>
      hasLive
        ? () => {
            const t = liveSession.token();
            const c = liveSession.comparisonId();
            if (!t || !c) return;
            rememberMode('live');
            setSession({ kind: 'live', adapter: new LiveAdapter(t, c), asset: null });
          }
        : undefined,
    [hasLive],
  );

  const onCompared = useCallback((token: string, info: ComparisonInfo) => {
    setUploading(false);
    setCurrent(info);
    rememberMode('live');
    setSession({ kind: 'live', adapter: new LiveAdapter(token, info.comparison_id), asset: null });
  }, []);

  const resetLive = useCallback(async () => {
    const t = liveSession.token();
    if (!t || !window.confirm('Delete this live workspace with all of its uploads and decisions?')) return;
    try {
      await workspaceApi.remove(t);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) {
        window.alert(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    liveSession.save(null, null);
    setCurrent(null);
    openSample();
  }, [openSample]);

  const liveRoute = useCallback((a: string | null): Route => ({ mode: 'live', asset: a, tolerance: null }), []);
  const common = {
    onUpload: () => setUploading('files'),
    onImport: () => setUploading('public'),
    onTour: () => setTouring(true),
  };

  return (
    <>
      {session.kind === 'live' ? (
        <ReviewProvider key={`live:${session.adapter.comparisonId}`} adapter={session.adapter} initialSelection={session.asset ?? undefined}>
          <Studio {...common} route={liveRoute} onSample={openSample} onReset={() => void resetLive()} />
        </ReviewProvider>
      ) : (
        <SampleSession
          tolerance={session.tolerance}
          asset={session.asset}
          onTolerance={(t) => setSession({ kind: 'sample', tolerance: t, asset: null })}
          actions={{ ...common, onLive: openLive }}
        />
      )}
      {uploading && (
        <UploadDialog onClose={() => setUploading(false)} onCompared={onCompared} current={session.kind === 'live' ? current : null} startWithPublic={uploading === 'public'} />
      )}
      {touring && <Tour onClose={() => setTouring(false)} />}
    </>
  );
}
