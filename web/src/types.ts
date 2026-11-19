// Shapes shared by the live API and the static sample (schema geodiff.comparison/1).

export type Kind = 'addition' | 'deletion' | 'move' | 'attribute';
export type Disposition = 'pending' | 'accepted' | 'rejected' | 'flagged';
export type AssetStatus = 'added' | 'deleted' | 'modified';
export type LngLat = [number, number];
export type Scalar = string | number | boolean | null;

export interface Patch {
  op: 'insert' | 'remove' | 'set_coordinates' | 'set_property' | 'delete_property';
  feature?: GeoFeature;
  coordinates?: LngLat;
  key?: string;
  value?: Scalar;
}

export interface Evidence {
  // move
  from?: LngLat;
  to?: LngLat;
  distance_m?: number;
  bearing_deg?: number;
  tolerance_m?: number;
  // addition / deletion
  coordinates?: LngLat;
  // attribute
  field?: string;
  old?: Scalar;
  new?: Scalar;
  old_present?: boolean;
  new_present?: boolean;
}

export interface Decision {
  disposition: Disposition;
  note: string | null;
  decided_at: string | null;
  version: number;
}

export interface Component {
  component_id: string;
  kind: Kind;
  field: string | null;
  evidence: Evidence;
  patch: Patch;
  decision: Decision;
}

export interface AssetSide {
  coordinates: LngLat;
  properties: Record<string, unknown>;
}

export interface QueueItem {
  asset_id: string;
  status: AssetStatus;
  location: LngLat;
  components: Component[];
}

export interface HistoryEvent {
  batch_id: string;
  action: 'decide' | 'bulk' | 'undo';
  component_id: string;
  from: Disposition;
  to: Disposition;
  note: string | null;
  revision: number;
  undone: boolean;
  at: string | null;
}

export interface AssetDetail {
  asset_id: string;
  status: AssetStatus;
  revision: number;
  baseline: AssetSide | null;
  proposed: AssetSide | null;
  components: Component[];
  history: HistoryEvent[];
}

export interface Progress {
  components: number;
  by_disposition: Record<Disposition, number>;
  resolved_components: number;
  unresolved_components: number;
  affected_assets: number;
  resolved_assets: number;
  complete: boolean;
}

export interface InputInfo {
  name: string;
  sha256: string;
  feature_count: number;
  dataset_id?: string;
  source?: Record<string, unknown>;
}

export interface ComparisonInfo {
  comparison_id: string;
  parent_comparison_id: string | null;
  revision: number;
  created_at: string;
  parameters: {
    tolerance_m: number;
    baseline_id_field: string;
    proposed_id_field: string;
    compared_fields: string[];
    distance_method: string;
    units: string;
  };
  inputs: { baseline: InputInfo; proposed: InputInfo };
  summary: {
    affected_assets: number;
    components: number;
    components_by_kind: Record<Kind, number>;
    within_tolerance_moves: number;
    unchanged_assets: number;
    matched_assets: number;
    baseline_assets: number;
    proposed_assets: number;
  };
  warnings: { code: string; message: string; guidance: string }[];
  progress: Progress;
  extent: [number, number, number, number] | null;
}

export interface QueueFilter {
  kind?: Kind;
  disposition?: Disposition | 'unresolved';
  q?: string;
}

export interface DecisionInput {
  component_id: string;
  disposition: Disposition;
  note?: string | null;
  expected_version: number;
}

export interface DecisionResult {
  revision: number;
  progress: Progress;
}

export interface BulkScope {
  kind?: Kind;
  field?: string;
  current_disposition?: Disposition;
  asset_ids?: string[];
}

export interface GeoFeature {
  type: 'Feature';
  id?: string | number;
  geometry: { type: 'Point'; coordinates: LngLat };
  properties: Record<string, unknown>;
}

export interface FeatureCollection {
  type: 'FeatureCollection';
  features: GeoFeature[];
}

export interface ExportResult {
  geojson: FeatureCollection;
  report: Record<string, unknown> & {
    complete: boolean;
    counts: { unresolved_components: number; output_features: number };
    notes: string[];
  };
}

export interface MapAsset {
  asset_id: string;
  status: AssetStatus;
  kinds: Kind[];
  dispositions: Disposition[];
  from: LngLat | null;
  to: LngLat | null;
  distance_m: number | null;
}

export interface PublicSource {
  id: string;
  title: string;
  owner: string;
  layer_url: string;
  item_url: string;
  id_field: string;
  id_note: string;
  reuse: string;
  attribution: string;
  verified_on: string;
  suggested_compare_fields: string[];
}

/** Provenance recorded with an imported public-layer snapshot. */
export interface SourceRecord {
  source_id: string;
  title: string;
  owner: string;
  layer_url: string;
  item_url: string;
  attribution: string;
  reuse: string;
  retrieved_at: string;
  feature_count: number;
  id_field: string;
  id_note: string;
  service_last_edit: string | null;
}

export interface DatasetSummary {
  dataset_id: string;
  name: string;
  sha256: string;
  size_bytes: number;
  feature_count: number;
  valid: boolean;
  duplicate?: boolean;
  source?: SourceRecord | null;
  summary: {
    fields: { name: string; present: number; null: number; coverage: number; types: string[]; scalar: boolean }[];
    candidate_id_fields: string[];
    declared_crs: string | null;
    validation: {
      error_count: number;
      warning_count: number;
      counts: Record<string, number>;
      sample: { code: string; severity: string; message: string; guidance: string; feature_index?: number }[];
      sampled: boolean;
    };
  };
}

export interface FieldSuggestion {
  suggested: string[];
  fields: { name: string; baseline_coverage: number; proposed_coverage: number; comparable: boolean; suggested: boolean; note: string | null }[];
}
