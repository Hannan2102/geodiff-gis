import { useState } from 'react';
import Dialog from './Dialog';

const STEPS = [
  {
    title: 'What GeoDiff checks',
    body: 'Two point inventories: last month’s approved parks inventory and this month’s field survey. GeoDiff finds additions, removals, moves beyond a tolerance, and edited fields, each one shown as its own decision.',
  },
  {
    title: 'Read the map like a proof sheet',
    body: 'An open ring is the approved position. A filled mark is the reported position. A dashed line with an arrow joins them, labelled with the measured geodesic distance. Each change type also has its own shape: plus for added, crossed ring for removed, arrow for moved, split square for edited.',
  },
  {
    title: 'Decide each component',
    body: 'An asset can need several decisions. For BENCH-0142, accept the condition update and reject the move: the export keeps the approved location with the new condition. Flag anything that needs follow-up (a note is required). Undo is always available.',
  },
  {
    title: 'Export',
    body: 'A draft export is available at any time, and unresolved changes keep their approved values. The final export unlocks when every change is accepted or rejected. Both come with a machine-readable report of hashes, parameters, decisions and notes.',
  },
];

export default function Tour({ onClose }: { onClose: () => void }) {
  const [i, setI] = useState(0);
  const s = STEPS[i]!;
  return (
    <Dialog title={`Tour ${i + 1}/${STEPS.length}: ${s.title}`} onClose={onClose}>
      <p>{s.body}</p>
      <div className="dialog-actions">
        <button type="button" className="btn" disabled={i === 0} onClick={() => setI(i - 1)}>Back</button>
        {i < STEPS.length - 1 ? (
          <button type="button" className="btn primary" onClick={() => setI(i + 1)} autoFocus>Next</button>
        ) : (
          <button type="button" className="btn primary" onClick={onClose} autoFocus>Start reviewing</button>
        )}
      </div>
    </Dialog>
  );
}
