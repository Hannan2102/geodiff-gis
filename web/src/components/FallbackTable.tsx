import { displayDistance, fmtCoord, itemSummary, primaryKind } from '../lib/format';
import { useReview } from '../state/review';
import { KindSymbol } from './Symbols';

/** Replaces the map when WebGL is unavailable; every review action stays available. */
export default function FallbackTable() {
  const { items, select, selectedId } = useReview();
  return (
    <div className="fallback" data-testid="no-webgl">
      <p role="status" className="fallback-note">
        <strong>Map unavailable:</strong> this browser cannot start WebGL. The table lists every change with its
        coordinates; select a row to review it in the details panel.
      </p>
      <div className="fallback-scroll">
        <table>
          <caption className="sr-only">Changed assets with coordinates</caption>
          <thead>
            <tr>
              <th scope="col">Asset</th>
              <th scope="col">Change</th>
              <th scope="col">Location (lat, lon)</th>
              <th scope="col">Distance</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const move = it.components.find((c) => c.kind === 'move');
              return (
                <tr key={it.asset_id} aria-selected={it.asset_id === selectedId}>
                  <td>
                    <button type="button" className="link mono" onClick={() => select(it.asset_id, 'queue')}>
                      {it.asset_id}
                    </button>
                  </td>
                  <td>
                    <KindSymbol kind={primaryKind(it.components.map((c) => c.kind))} /> {itemSummary(it)}
                  </td>
                  <td className="mono">{fmtCoord(it.location)}</td>
                  <td className="mono">{move ? displayDistance(move.evidence.distance_m ?? 0) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
