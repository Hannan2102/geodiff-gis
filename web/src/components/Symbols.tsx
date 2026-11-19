import type { Disposition, Kind } from '../types';

// Every kind has a distinct shape and label in addition to its color.
export const KIND_COLOR: Record<Kind, string> = {
  addition: '#0f6b62',
  deletion: '#a4441f',
  move: '#2346a0',
  attribute: '#6b2f63',
};

export function KindSymbol({ kind, size = 16, title }: { kind: Kind; size?: number; title?: string }) {
  const c = KIND_COLOR[kind];
  // An empty title marks the symbol as decorative (its meaning is already in adjacent text).
  const label = title ?? kind;
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true as const };
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" {...a11y} focusable="false" className="kind-symbol">
      {kind === 'addition' && (
        <>
          <circle cx="8" cy="8" r="7" fill={c} />
          <path d="M8 4v8M4 8h8" stroke="#fff" strokeWidth="2" />
        </>
      )}
      {kind === 'deletion' && (
        <>
          <circle cx="8" cy="8" r="6.2" fill="none" stroke={c} strokeWidth="1.8" />
          <path d="M4.3 4.3l7.4 7.4M11.7 4.3l-7.4 7.4" stroke={c} strokeWidth="1.8" />
        </>
      )}
      {kind === 'move' && (
        <>
          <circle cx="4" cy="12" r="2.4" fill="none" stroke={c} strokeWidth="1.4" />
          <path d="M5.8 10.2 12 4" stroke={c} strokeWidth="1.8" />
          <path d="M8.2 3.4h4.4v4.4" fill="none" stroke={c} strokeWidth="1.8" />
        </>
      )}
      {kind === 'attribute' && (
        <>
          <rect x="1.5" y="1.5" width="13" height="13" fill="none" stroke={c} strokeWidth="1.6" />
          <path d="M1.5 14.5 14.5 1.5V14.5Z" fill={c} />
        </>
      )}
    </svg>
  );
}

const DISP_TEXT: Record<Disposition, string> = {
  pending: 'Pending',
  accepted: 'Accepted',
  rejected: 'Rejected',
  flagged: 'Flagged',
};

export function DispositionBadge({ d }: { d: Disposition }) {
  const glyph = { pending: '○', accepted: '✓', rejected: '✕', flagged: '⚑' }[d];
  return (
    <span className={`disp disp-${d}`}>
      <span aria-hidden="true">{glyph}</span> {DISP_TEXT[d]}
    </span>
  );
}
