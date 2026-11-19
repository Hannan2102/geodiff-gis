import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../adapters/live';
import type { ReviewAdapter } from '../adapters/types';
import type { AssetDetail, ComparisonInfo, DecisionInput, Disposition, QueueFilter, QueueItem } from '../types';

export interface Notice {
  tone: 'info' | 'error' | 'success';
  text: string;
  guidance?: string;
}

interface ReviewState {
  adapter: ReviewAdapter;
  info: ComparisonInfo | null;
  items: QueueItem[];
  total: number;
  queueLoading: boolean;
  loadError: ApiError | null;
  filter: QueueFilter;
  setFilter: (f: QueueFilter) => void;
  selectedId: string | null;
  select: (id: string | null, source?: 'map' | 'queue' | 'keyboard') => void;
  selectionSource: 'map' | 'queue' | 'keyboard' | null;
  detail: AssetDetail | null;
  detailLoading: boolean;
  busy: boolean;
  notice: Notice | null;
  setNotice: (n: Notice | null) => void;
  decide: (items: DecisionInput[]) => Promise<boolean>;
  setDisposition: (componentId: string, disposition: Disposition, note?: string | null) => Promise<boolean>;
  undo: () => Promise<void>;
  reload: () => Promise<void>;
  refreshInfo: () => Promise<void>;
  mapVersion: number;
}

const Ctx = createContext<ReviewState | null>(null);

export function useReview(): ReviewState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useReview outside ReviewProvider');
  return v;
}

function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, 'unexpected', e instanceof Error ? e.message : String(e));
}

export function ReviewProvider({ adapter, initialSelection, children }: { adapter: ReviewAdapter; initialSelection?: string; children: ReactNode }) {
  const [info, setInfo] = useState<ComparisonInfo | null>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [queueLoading, setQueueLoading] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [filter, setFilter] = useState<QueueFilter>({});
  const [selectedId, setSelectedId] = useState<string | null>(initialSelection ?? null);
  const [selectionSource, setSelectionSource] = useState<ReviewState['selectionSource']>(null);
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [mapVersion, setMapVersion] = useState(0);
  const detailReq = useRef(0);

  const refreshInfo = useCallback(async () => {
    try {
      setInfo(await adapter.comparison());
      setLoadError(null);
    } catch (e) {
      setLoadError(asApiError(e));
    }
  }, [adapter]);

  const loadQueue = useCallback(
    async (f: QueueFilter) => {
      setQueueLoading(true);
      try {
        const all = await adapter.queue(f, (page, t) => {
          setItems(page);
          setTotal(t);
        });
        setItems(all);
        setTotal(all.length);
        setLoadError(null);
      } catch (e) {
        setLoadError(asApiError(e));
      } finally {
        setQueueLoading(false);
      }
    },
    [adapter],
  );

  const loadDetail = useCallback(
    async (id: string | null) => {
      const n = ++detailReq.current;
      if (!id) {
        setDetail(null);
        return;
      }
      setDetailLoading(true);
      try {
        const d = await adapter.asset(id);
        if (n === detailReq.current) setDetail(d);
      } catch (e) {
        if (n === detailReq.current) {
          setDetail(null);
          const err = asApiError(e);
          setNotice({ tone: 'error', text: err.message, guidance: err.guidance });
        }
      } finally {
        if (n === detailReq.current) setDetailLoading(false);
      }
    },
    [adapter],
  );

  const reload = useCallback(async () => {
    await refreshInfo();
    await loadQueue(filter);
    await loadDetail(selectedId);
  }, [refreshInfo, loadQueue, loadDetail, filter, selectedId]);

  useEffect(() => {
    // Initial load and adapter switch: fetch data from the adapter (an external system).
    let cancelled = false;
    void (async () => {
      await refreshInfo();
      if (!cancelled) await loadQueue({});
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshInfo, loadQueue]);

  useEffect(() => {
    // Fetch the selected asset from the adapter (an external system) after selection changes.
    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (!cancelled) await loadDetail(selectedId);
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, loadDetail]);

  const applyFilter = useCallback(
    (f: QueueFilter) => {
      setFilter(f);
      void loadQueue(f);
    },
    [loadQueue],
  );

  const select = useCallback((id: string | null, source: 'map' | 'queue' | 'keyboard' = 'queue') => {
    setSelectedId(id);
    setSelectionSource(source);
  }, []);

  /** Refresh one asset everywhere after a change (queue row, detail, map, progress). */
  const syncAsset = useCallback(
    async (assetIds: string[]) => {
      const fresh = await Promise.all(assetIds.map((id) => adapter.asset(id)));
      setItems((prev) =>
        prev.map((it) => {
          const f = fresh.find((x) => x.asset_id === it.asset_id);
          return f ? { ...it, components: f.components } : it;
        }),
      );
      const sel = fresh.find((x) => x.asset_id === selectedId);
      if (sel) setDetail(sel);
      setMapVersion((v) => v + 1);
    },
    [adapter, selectedId],
  );

  const assetOf = useCallback(
    (componentId: string): string => {
      for (const it of items) if (it.components.some((c) => c.component_id === componentId)) return it.asset_id;
      if (detail?.components.some((c) => c.component_id === componentId)) return detail.asset_id;
      return componentId.replace(/:(addition|deletion|move|attr:.*)$/, '');
    },
    [items, detail],
  );

  const decide = useCallback(
    async (inputs: DecisionInput[]) => {
      setBusy(true);
      try {
        const r = await adapter.decide(inputs);
        setInfo((i) => (i ? { ...i, revision: r.revision, progress: r.progress } : i));
        await syncAsset([...new Set(inputs.map((x) => assetOf(x.component_id)))]);
        return true;
      } catch (e) {
        const err = asApiError(e);
        setNotice({ tone: 'error', text: err.message, guidance: err.guidance });
        if (err.code === 'stale_decision') await reload();
        return false;
      } finally {
        setBusy(false);
      }
    },
    [adapter, syncAsset, reload, assetOf],
  );

  const setDisposition = useCallback(
    async (componentId: string, disposition: Disposition, note?: string | null) => {
      const comp = detail?.components.find((c) => c.component_id === componentId);
      if (!comp) return false;
      return decide([{ component_id: componentId, disposition, note: note ?? comp.decision.note, expected_version: comp.decision.version }]);
    },
    [detail, decide],
  );

  const undo = useCallback(async () => {
    if (!info) return;
    setBusy(true);
    try {
      const r = await adapter.undo(info.revision);
      setInfo((i) => (i ? { ...i, revision: r.revision, progress: r.progress } : i));
      await syncAsset([...new Set(r.components.map((c) => assetOf(c)))]);
      setNotice({ tone: 'info', text: `Undid the last decision (${r.components.join(', ')}).` });
    } catch (e) {
      const err = asApiError(e);
      setNotice({ tone: err.code === 'nothing_to_undo' ? 'info' : 'error', text: err.message, guidance: err.guidance });
      if (err.code === 'stale_revision') await reload();
    } finally {
      setBusy(false);
    }
  }, [adapter, info, syncAsset, reload, assetOf]);

  const value = useMemo<ReviewState>(
    () => ({
      adapter, info, items, total, queueLoading, loadError, filter, setFilter: applyFilter,
      selectedId, select, selectionSource, detail, detailLoading, busy, notice, setNotice,
      decide, setDisposition, undo, reload, refreshInfo, mapVersion,
    }),
    [adapter, info, items, total, queueLoading, loadError, filter, applyFilter, selectedId, select, selectionSource, detail, detailLoading, busy, notice, decide, setDisposition, undo, reload, refreshInfo, mapVersion],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
