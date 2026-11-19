import type { AssetDetail, Component, Kind, QueueItem, Scalar } from '../types';

/** Display only; classification always uses full precision (mirrors geodesy.display_distance). */
export function displayDistance(m: number): string {
  if (m < 10) return `${m.toFixed(2)} m`;
  if (m < 1000) return `${m.toFixed(1)} m`;
  return `${(m / 1000).toFixed(2)} km`;
}

const POINTS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
export function compass(bearing: number): string {
  return POINTS[Math.round((((bearing % 360) + 360) % 360) / 22.5) % 16] ?? 'N';
}

export function fmtCoord([lon, lat]: [number, number]): string {
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
}

/** Show JSON types explicitly: "2019" (text) is not 2019 (number); null is not absent. */
export function fmtValue(v: Scalar | undefined, present = true): { text: string; type: string } {
  if (!present) return { text: 'absent', type: 'absent' };
  if (v === null || v === undefined) return { text: 'null', type: 'null' };
  if (typeof v === 'string') return { text: `“${v}”`, type: 'text' };
  if (typeof v === 'number') return { text: String(v), type: 'number' };
  return { text: String(v), type: 'boolean' };
}

export const KIND_LABEL: Record<Kind, string> = {
  addition: 'Addition',
  deletion: 'Removal',
  move: 'Move',
  attribute: 'Attribute edit',
};

export function componentTitle(c: Component): string {
  if (c.kind === 'attribute') return `Edit · ${c.field}`;
  if (c.kind === 'move') return `Move · ${displayDistance(c.evidence.distance_m ?? 0)}`;
  return KIND_LABEL[c.kind];
}

export function primaryKind(kinds: Kind[]): Kind {
  for (const k of ['move', 'addition', 'deletion', 'attribute'] as Kind[]) if (kinds.includes(k)) return k;
  return 'attribute';
}

export function itemSummary(item: QueueItem): string {
  return item.components
    .map((c) => {
      if (c.kind === 'move') return `moved ${displayDistance(c.evidence.distance_m ?? 0)}`;
      if (c.kind === 'attribute') return c.field ?? 'field';
      return c.kind === 'addition' ? 'new in field survey' : 'missing from field survey';
    })
    .join(' · ');
}

/** One readable sentence describing what the field inventory reports. */
export function explain(a: AssetDetail): string {
  if (a.status === 'added') return `${a.asset_id} appears in the field inventory but not in the approved inventory.`;
  if (a.status === 'deleted') return `${a.asset_id} is in the approved inventory but missing from the field inventory.`;
  const parts: string[] = [];
  for (const c of a.components) {
    if (c.kind === 'move' && c.evidence.distance_m !== undefined) {
      parts.push(`its reported position moved ${displayDistance(c.evidence.distance_m)} ${compass(c.evidence.bearing_deg ?? 0)}`);
    } else if (c.kind === 'attribute') {
      const o = fmtValue(c.evidence.old, c.evidence.old_present);
      const n = fmtValue(c.evidence.new, c.evidence.new_present);
      parts.push(`${c.field} changed from ${o.text} to ${n.text}`);
    }
  }
  if (parts.length === 0) return `${a.asset_id} changed.`;
  const last = parts.pop();
  return `The field inventory says ${parts.length ? parts.join(', ') + ' and ' : ''}${last}.`;
}

export function unresolvedCount(components: Component[]): number {
  return components.filter((c) => c.decision.disposition === 'pending' || c.decision.disposition === 'flagged').length;
}
