import type {
  AssetDetail,
  BulkScope,
  ComparisonInfo,
  DecisionInput,
  DecisionResult,
  Disposition,
  ExportResult,
  HistoryEvent,
  MapAsset,
  QueueFilter,
  QueueItem,
} from '../types';

export type Mode = 'sample' | 'live';

/** Everything the review studio needs. Implemented by the live API and the static sample. */
export interface ReviewAdapter {
  readonly mode: Mode;
  /** Guided sample only: the asset the walkthrough starts on. */
  readonly focusAsset?: string | null;
  comparison(): Promise<ComparisonInfo>;
  /** All affected assets matching the filter, in asset-ID order (paged internally). */
  queue(filter: QueueFilter, onPage?: (items: QueueItem[], total: number) => void): Promise<QueueItem[]>;
  asset(assetId: string): Promise<AssetDetail>;
  mapAssets(bbox: [number, number, number, number]): Promise<{ assets: MapAsset[]; truncated: boolean }>;
  /** Baseline positions for context; may return [] when not available. */
  baselinePoints(bbox: [number, number, number, number]): Promise<[number, number][]>;
  decide(items: DecisionInput[]): Promise<DecisionResult>;
  bulkPreview(scope: BulkScope): Promise<number>;
  bulk(scope: BulkScope, disposition: Disposition, note: string | null, expectedCount: number): Promise<DecisionResult>;
  undo(expectedRevision: number): Promise<DecisionResult & { components: string[] }>;
  history(): Promise<HistoryEvent[]>;
  exportResult(mode: 'draft' | 'final'): Promise<ExportResult>;
}
