import { componentTitle } from '../lib/format';
import { useReview } from '../state/review';

/** Phone-width bar that keeps the next decision reachable from any tab. */
export default function DecisionBar({ onDetails }: { onDetails: () => void }) {
  const { detail, setDisposition, busy } = useReview();
  if (!detail) return null;
  const next = detail.components.find((c) => c.decision.disposition === 'pending' || c.decision.disposition === 'flagged');
  return (
    <div className="decision-bar" role="region" aria-label="Quick decision">
      <button type="button" className="db-asset" onClick={onDetails}>
        <span className="mono">{detail.asset_id}</span>
        <small>{next ? componentTitle(next) : 'All decided'}</small>
      </button>
      {next && (
        <>
          <button type="button" className="btn accept" disabled={busy} onClick={() => void setDisposition(next.component_id, 'accepted')}>
            ✓ Accept
          </button>
          <button type="button" className="btn reject" disabled={busy} onClick={() => void setDisposition(next.component_id, 'rejected')}>
            ✕ Reject
          </button>
        </>
      )}
    </div>
  );
}
