import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { ApiError, LiveAdapter, liveSession, workspaceApi } from './adapters/live';
import type { ReviewAdapter } from './adapters/types';
import DecisionBar from './components/DecisionBar';
import Evidence from './components/Evidence';
import FallbackTable from './components/FallbackTable';
import Queue from './components/Queue';
import Toolbar from './components/Toolbar';
import Tour from './components/Tour';
import UploadDialog from './components/UploadDialog';
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
}

function Studio(actions: StudioActions) {
  const layout = useLayout();
  const { selectedId } = useReview();
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

function Start({ onUpload, onTour }: { onUpload: () => void; onTour: () => void }) {
  return (
    <main className="start">
      <h1>GeoDiff</h1>
      <p className="lede">
        Reconcile a field inventory of point assets with last month’s approved inventory: review additions, removals, moves and edited fields, then
        export the approved updates.
      </p>
      <div className="dialog-actions">
        <button type="button" className="btn primary" onClick={onUpload} data-testid="try-files">
          Try your own files
        </button>
        <button type="button" className="btn" onClick={onTour}>
          Take the tour
        </button>
      </div>
    </main>
  );
}

type Session = { kind: 'none' } | { kind: 'live'; adapter: LiveAdapter };

function initialSession(): Session {
  const t = liveSession.token();
  const c = liveSession.comparisonId();
  return t && c ? { kind: 'live', adapter: new LiveAdapter(t, c) } : { kind: 'none' };
}

export default function App() {
  const [session, setSession] = useState<Session>(initialSession);
  const [uploading, setUploading] = useState(false);
  const [touring, setTouring] = useState(false);
  const [current, setCurrent] = useState<ComparisonInfo | null>(null);

  const adapter: ReviewAdapter | null = session.kind === 'live' ? session.adapter : null;

  useEffect(() => {
    if (!adapter) return;
    let live = true;
    adapter
      .comparison()
      .then((i) => live && setCurrent(i))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [adapter]);

  const onCompared = useCallback((token: string, info: ComparisonInfo) => {
    setUploading(false);
    setCurrent(info);
    setSession({ kind: 'live', adapter: new LiveAdapter(token, info.comparison_id) });
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
    setSession({ kind: 'none' });
  }, []);

  const key = session.kind === 'live' ? `live:${session.adapter.comparisonId}` : 'none';

  return (
    <>
      {adapter ? (
        <ReviewProvider key={key} adapter={adapter}>
          <Studio onUpload={() => setUploading(true)} onTour={() => setTouring(true)} onReset={() => void resetLive()} />
        </ReviewProvider>
      ) : (
        <Start onUpload={() => setUploading(true)} onTour={() => setTouring(true)} />
      )}
      {uploading && <UploadDialog onClose={() => setUploading(false)} onCompared={onCompared} current={current} />}
      {touring && <Tour onClose={() => setTouring(false)} />}
    </>
  );
}
