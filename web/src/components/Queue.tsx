import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { itemSummary, unresolvedCount } from '../lib/format';
import { useReview } from '../state/review';
import type { Kind, QueueFilter } from '../types';
import { KindSymbol } from './Symbols';

const ROW = 64;
const KINDS: { kind?: Kind; label: string }[] = [
  { label: 'All' },
  { kind: 'move', label: 'Moves' },
  { kind: 'attribute', label: 'Edits' },
  { kind: 'addition', label: 'Added' },
  { kind: 'deletion', label: 'Removed' },
];
const STATES: { value?: QueueFilter['disposition']; label: string }[] = [
  { label: 'Any status' },
  { value: 'unresolved', label: 'Needs decision' },
  { value: 'flagged', label: 'Flagged' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
];

export default function Queue({ onOpen }: { onOpen?: () => void }) {
  const { items, total, queueLoading, filter, setFilter, selectedId, select, selectionSource, info } = useReview();
  const scroller = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(480);
  const [search, setSearch] = useState(filter.q ?? '');

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.clientHeight || 480));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Keep the selected row visible when selection comes from the map or keyboard.
  useEffect(() => {
    const el = scroller.current;
    if (!el || !selectedId || selectionSource === 'queue') return;
    const idx = items.findIndex((i) => i.asset_id === selectedId);
    if (idx < 0) return;
    const top = idx * ROW;
    if (top < el.scrollTop || top + ROW > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, top - el.clientHeight / 2);
  }, [selectedId, selectionSource, items]);

  useEffect(() => {
    const t = setTimeout(() => {
      if ((filter.q ?? '') !== search) setFilter({ ...filter, q: search || undefined });
    }, 250);
    return () => clearTimeout(t);
  }, [search, filter, setFilter]);

  const start = Math.max(0, Math.floor(scrollTop / ROW) - 5);
  const end = Math.min(items.length, Math.ceil((scrollTop + height) / ROW) + 5);
  const visible = items.slice(start, end);
  const activeIndex = items.findIndex((i) => i.asset_id === selectedId);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!items.length) return;
    let next: number;
    if (e.key === 'ArrowDown' || e.key === 'j') next = Math.min(items.length - 1, activeIndex + 1);
    else if (e.key === 'ArrowUp' || e.key === 'k') next = Math.max(0, activeIndex - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    else if (e.key === 'Enter') {
      onOpen?.();
      return;
    } else return;
    e.preventDefault();
    const it = items[next];
    if (it) select(it.asset_id, 'keyboard');
  }

  const pending = info?.progress.unresolved_components ?? 0;

  return (
    <section className="queue" aria-label="Change queue">
      <div className="queue-head">
        <div className="chips" role="group" aria-label="Filter by change type">
          {KINDS.map((k) => (
            <button
              key={k.label}
              type="button"
              className="chip"
              aria-pressed={filter.kind === k.kind}
              onClick={() => setFilter({ ...filter, kind: k.kind })}
            >
              {k.kind && <KindSymbol kind={k.kind} size={12} title="" />} {k.label}
              {k.kind && info ? <span className="chip-n">{info.summary.components_by_kind[k.kind]}</span> : null}
            </button>
          ))}
        </div>
        <div className="queue-tools">
          <label className="sr-only" htmlFor="queue-status">Status</label>
          <select id="queue-status" value={filter.disposition ?? ''} onChange={(e) => setFilter({ ...filter, disposition: (e.target.value || undefined) as QueueFilter['disposition'] })}>
            {STATES.map((s) => (
              <option key={s.label} value={s.value ?? ''}>{s.label}</option>
            ))}
          </select>
          <label className="sr-only" htmlFor="queue-search">Search asset ID</label>
          <input id="queue-search" type="search" placeholder="Search ID" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <p className="queue-count" aria-live="polite">
          {queueLoading ? `Loading… ${items.length} of ${total || '?'}` : `${total} asset${total === 1 ? '' : 's'}`} · {pending} decision{pending === 1 ? '' : 's'} left
        </p>
      </div>
      <div
        ref={scroller}
        className="queue-list"
        role="listbox"
        tabIndex={0}
        aria-label="Changed assets"
        aria-activedescendant={selectedId ? `q-${selectedId}` : undefined}
        onKeyDown={onKeyDown}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        {!queueLoading && items.length === 0 && (
          <p className="empty">
            {info && info.summary.components === 0
              ? info.inputs.baseline.sha256 === info.inputs.proposed.sha256
                ? 'No changes: both inputs are byte-identical (same SHA-256). For a public layer, take the second snapshot later, after the source has been edited.'
                : 'No changes beyond the move tolerance or in the compared fields.'
              : 'No changes match these filters.'}
          </p>
        )}
        <div style={{ height: items.length * ROW, position: 'relative' }}>
          {visible.map((it, i) => {
            const idx = start + i;
            const left = unresolvedCount(it.components);
            return (
              <div
                key={it.asset_id}
                id={`q-${it.asset_id}`}
                role="option"
                aria-selected={it.asset_id === selectedId}
                className="q-row"
                style={{ top: idx * ROW, height: ROW }}
                onClick={() => {
                  select(it.asset_id, 'queue');
                  onOpen?.();
                }}
              >
                <span className="q-symbols">
                  {[...new Set(it.components.map((c) => c.kind))].map((k) => (
                    <KindSymbol key={k} kind={k} size={14} />
                  ))}
                </span>
                <span className="q-main">
                  <span className="mono q-id">{it.asset_id}</span>
                  <span className="q-sum">{itemSummary(it)}</span>
                </span>
                <span className={`q-state ${left ? 'open' : 'done'}`}>{left ? `${left} open` : '✓ done'}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
