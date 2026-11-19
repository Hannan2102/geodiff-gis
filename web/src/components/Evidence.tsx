import { useCallback, useEffect, useRef, useState } from 'react';
import { compass, componentTitle, displayDistance, explain, fmtCoord, fmtValue } from '../lib/format';
import { useReview } from '../state/review';
import type { AssetDetail, Component, Disposition, Scalar } from '../types';
import { DispositionBadge, KindSymbol } from './Symbols';

function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
}

/** Small schematic: approved ring, reported mark, line with bearing, scale bar. */
function MoveDiagram({ c }: { c: Component }) {
  const d = c.evidence.distance_m ?? 0;
  const brg = ((c.evidence.bearing_deg ?? 0) * Math.PI) / 180;
  const r = 46;
  const cx = 70 - Math.sin(brg) * (r / 2);
  const cy = 60 + Math.cos(brg) * (r / 2);
  const x2 = cx + Math.sin(brg) * r;
  const y2 = cy - Math.cos(brg) * r;
  return (
    <svg className="move-diagram" viewBox="0 0 140 120" role="img" aria-label={`Moved ${displayDistance(d)} toward ${compass(c.evidence.bearing_deg ?? 0)}`}>
      <path d="M8 8h10M8 8v10M132 8h-10M132 8v10M8 112h10M8 112v-10M132 112h-10M132 112v-10" stroke="#202a2a" strokeWidth="1" fill="none" />
      <text x="70" y="16" textAnchor="middle" className="svg-n">N ↑</text>
      <line x1={cx} y1={cy} x2={x2} y2={y2} stroke="#2346a0" strokeWidth="2" strokeDasharray="4 2" />
      <circle cx={cx} cy={cy} r="7" fill="none" stroke="#202a2a" strokeWidth="2" />
      <circle cx={x2} cy={y2} r="6" fill="#2346a0" stroke="#f5f2e9" strokeWidth="2" />
      <text x="70" y="112" textAnchor="middle" className="svg-n mono">{displayDistance(d)} {compass(c.evidence.bearing_deg ?? 0)}</text>
    </svg>
  );
}

function Value({ v, present }: { v: Scalar | undefined; present?: boolean }) {
  const f = fmtValue(v, present ?? true);
  return (
    <span className={`val val-${f.type}`}>
      {f.text}
      {f.type !== 'absent' && f.type !== 'null' && <span className="val-type">{f.type}</span>}
    </span>
  );
}

function ComponentCard({ c, active, onFocus }: { c: Component; active: boolean; onFocus: () => void }) {
  const { setDisposition, busy } = useReview();
  const [note, setNote] = useState(c.decision.note ?? '');
  const [flagging, setFlagging] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);
  const d = c.decision.disposition;

  async function choose(disp: Disposition) {
    if (disp === 'flagged') {
      if (!note.trim()) {
        setFlagging(true);
        setNoteError('Add a note that says what needs follow-up.');
        return;
      }
    }
    setNoteError(null);
    const ok = await setDisposition(c.component_id, disp, note.trim() || null);
    if (ok) setFlagging(false);
  }

  return (
    <li className={`comp comp-${c.kind} ${active ? 'active' : ''}`} onFocusCapture={onFocus} data-component={c.component_id}>
      <div className="comp-head">
        <KindSymbol kind={c.kind} size={18} />
        <h3>{componentTitle(c)}</h3>
        <DispositionBadge d={d} />
      </div>
      {c.kind === 'attribute' && (
        <dl className="pair">
          <div><dt>Approved</dt><dd><Value v={c.evidence.old} present={c.evidence.old_present} /></dd></div>
          <div><dt>Field report</dt><dd><Value v={c.evidence.new} present={c.evidence.new_present} /></dd></div>
        </dl>
      )}
      {c.kind === 'move' && c.evidence.from && c.evidence.to && (
        <div className="move-body">
          <MoveDiagram c={c} />
          <dl className="pair pair-stack">
            <div><dt>Approved</dt><dd className="mono">{fmtCoord(c.evidence.from)}</dd></div>
            <div><dt>Field report</dt><dd className="mono">{fmtCoord(c.evidence.to)}</dd></div>
            <div>
              <dt>Measured</dt>
              <dd className="mono">
                {displayDistance(c.evidence.distance_m ?? 0)} at {(c.evidence.bearing_deg ?? 0).toFixed(1)}° · tolerance {c.evidence.tolerance_m} m
              </dd>
            </div>
          </dl>
        </div>
      )}
      {(c.kind === 'addition' || c.kind === 'deletion') && (
        <p className="comp-note">
          {c.kind === 'addition'
            ? 'Accept to insert this asset into the reviewed inventory. Rejecting or leaving it pending keeps it out.'
            : 'Accept to remove this asset from the reviewed inventory. Rejecting or leaving it pending keeps it.'}
        </p>
      )}
      <div className="decide" role="group" aria-label={`Decision for ${componentTitle(c)}`}>
        <button type="button" className="btn accept" aria-pressed={d === 'accepted'} disabled={busy} onClick={() => void choose('accepted')}>
          ✓ Accept
        </button>
        <button type="button" className="btn reject" aria-pressed={d === 'rejected'} disabled={busy} onClick={() => void choose('rejected')}>
          ✕ Reject
        </button>
        <button type="button" className="btn flag" aria-pressed={d === 'flagged'} aria-expanded={flagging} disabled={busy} onClick={() => (flagging ? void choose('flagged') : setFlagging(true))}>
          ⚑ {flagging && d !== 'flagged' ? 'Save flag' : 'Flag'}
        </button>
        {d !== 'pending' && (
          <button type="button" className="btn ghost" disabled={busy} onClick={() => void choose('pending')}>
            Reset
          </button>
        )}
      </div>
      {(flagging || d === 'flagged' || c.decision.note) && (
        <div className="note">
          <label htmlFor={`note-${c.component_id}`}>Note{flagging || d === 'flagged' ? ' (required to flag)' : ''}</label>
          <textarea
            id={`note-${c.component_id}`}
            rows={2}
            value={note}
            maxLength={2000}
            aria-invalid={!!noteError}
            aria-describedby={noteError ? `note-err-${c.component_id}` : undefined}
            onChange={(e) => setNote(e.target.value)}
          />
          {noteError && <p id={`note-err-${c.component_id}`} className="field-error" role="alert">{noteError}</p>}
        </div>
      )}
      {c.decision.decided_at && <p className="decided mono">decided {new Date(c.decision.decided_at).toLocaleString()} · v{c.decision.version}</p>}
    </li>
  );
}

function Properties({ a }: { a: AssetDetail }) {
  const side = a.baseline ?? a.proposed;
  if (!side) return null;
  const changed = new Set(a.components.filter((c) => c.kind === 'attribute').map((c) => c.field));
  const rows = Object.entries(side.properties);
  return (
    <details className="props">
      <summary>All properties ({rows.length}) · {a.baseline ? 'from the approved inventory' : 'from the field inventory'}</summary>
      <table>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className={changed.has(k) ? 'changed' : ''}>
              <th scope="row" className="mono">{k}</th>
              <td className="mono">{typeof v === 'object' && v !== null ? JSON.stringify(v) : fmtValue(v as Scalar).text}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Unselected and nested properties are always kept from the approved inventory.</p>
    </details>
  );
}

export default function Evidence({ onClose }: { onClose?: () => void }) {
  const { detail, detailLoading, selectedId, undo, busy, info, setDisposition } = useReview();
  const [picked, setPicked] = useState<{ asset: string; index: number } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const comps = detail?.components;
  // Start on the first open component of each asset; keep the reviewer's own choice until the asset changes.
  const firstOpen = comps ? Math.max(0, comps.findIndex((c) => c.decision.disposition === 'pending' || c.decision.disposition === 'flagged')) : 0;
  const active = picked && detail && picked.asset === detail.asset_id ? Math.min(picked.index, (comps?.length ?? 1) - 1) : firstOpen;
  const setActive = useCallback(
    (next: number | ((i: number) => number)) => {
      if (!detail) return;
      setPicked({ asset: detail.asset_id, index: typeof next === 'function' ? next(active) : next });
    },
    [detail, active],
  );

  // Keyboard: a accept, r reject, [ ] change component, u undo.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey || !comps?.length) return;
      const c = comps[active];
      if (e.key === 'a' && c) void setDisposition(c.component_id, 'accepted');
      else if (e.key === 'r' && c) void setDisposition(c.component_id, 'rejected');
      else if (e.key === ']') setActive((i) => Math.min(comps.length - 1, i + 1));
      else if (e.key === '[') setActive((i) => Math.max(0, i - 1));
      else if (e.key === 'u') void undo();
      else return;
      e.preventDefault();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [comps, active, setActive, setDisposition, undo]);

  if (!selectedId) {
    return (
      <aside className="evidence empty-state" aria-label="Details">
        <h2>Select a change</h2>
        <p>Choose an asset in the queue or on the map to compare its approved and field-reported values.</p>
        {info && <p className="hint">{info.summary.within_tolerance_moves} small position differences under {info.parameters.tolerance_m} m are treated as unchanged.</p>}
      </aside>
    );
  }
  if (!detail || detail.asset_id !== selectedId) {
    return (
      <aside className="evidence" aria-label="Details" aria-busy="true">
        <p>{detailLoading ? `Loading ${selectedId}…` : `Could not load ${selectedId}.`}</p>
      </aside>
    );
  }

  return (
    <aside className="evidence" aria-label={`Details for ${detail.asset_id}`}>
      <header className="ev-head">
        <div>
          <p className="eyebrow">{detail.status === 'modified' ? 'Changed asset' : detail.status === 'added' ? 'New in field inventory' : 'Missing from field inventory'}</p>
          <h2 ref={heading} className="mono" tabIndex={-1}>{detail.asset_id}</h2>
        </div>
        {onClose && (
          <button type="button" className="btn ghost close" onClick={onClose} aria-label="Close details">
            ✕
          </button>
        )}
      </header>
      <p className="explain">{explain(detail)}</p>
      <ol className="comps">
        {detail.components.map((c, i) => (
          <ComponentCard key={`${c.component_id}:${c.decision.version}`} c={c} active={i === active} onFocus={() => setActive(i)} />
        ))}
      </ol>
      <div className="ev-tools">
        <button type="button" className="btn ghost" onClick={() => void undo()} disabled={busy}>
          ↶ Undo last decision
        </button>
        <span className="hint">Keys: <kbd>a</kbd> accept · <kbd>r</kbd> reject · <kbd>[</kbd>/<kbd>]</kbd> component · <kbd>u</kbd> undo</span>
      </div>
      <Properties a={detail} />
      <section className="audit" aria-label="Audit trail">
        <h3>Audit trail</h3>
        {detail.history.length === 0 ? (
          <p className="hint">No decisions yet.</p>
        ) : (
          <ol>
            {detail.history.map((h, i) => (
              <li key={`${h.batch_id}-${h.component_id}-${i}`} className={h.undone ? 'undone' : ''}>
                <span className="mono">{h.at ? new Date(h.at).toLocaleTimeString() : ''}</span> {h.action === 'undo' ? 'Undo:' : ''} {h.component_id.slice(detail.asset_id.length + 1)} {h.from} → {h.to}
                {h.note ? ` — “${h.note}”` : ''}
                {h.undone ? ' (undone)' : ''}
              </li>
            ))}
          </ol>
        )}
      </section>
    </aside>
  );
}
