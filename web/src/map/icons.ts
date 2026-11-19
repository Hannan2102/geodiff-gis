import type { Map as MLMap } from 'maplibre-gl';
import { KIND_COLOR } from '../components/Symbols';
import type { Kind } from '../types';

// Map symbols drawn on canvas so the style needs no sprite download. Shapes match Symbols.tsx.
const PX = 2; // device pixel ratio for crisp icons

function draw(size: number, paint: (g: CanvasRenderingContext2D, s: number) => void): ImageData {
  const c = document.createElement('canvas');
  c.width = c.height = size * PX;
  const g = c.getContext('2d');
  if (!g) throw new Error('2D canvas unavailable');
  g.scale(PX, PX);
  paint(g, size);
  return g.getImageData(0, 0, size * PX, size * PX);
}

const PAPER = '#f5f2e9';

function kindIcon(kind: Kind): ImageData {
  const col = KIND_COLOR[kind];
  return draw(26, (g, s) => {
    const m = s / 2;
    g.lineWidth = 2.4;
    if (kind === 'addition') {
      g.fillStyle = col;
      g.strokeStyle = PAPER;
      g.beginPath();
      g.arc(m, m, 10, 0, Math.PI * 2);
      g.fill();
      g.lineWidth = 2;
      g.stroke();
      g.strokeStyle = '#fff';
      g.lineWidth = 2.6;
      g.beginPath();
      g.moveTo(m, m - 5.5);
      g.lineTo(m, m + 5.5);
      g.moveTo(m - 5.5, m);
      g.lineTo(m + 5.5, m);
      g.stroke();
    } else if (kind === 'deletion') {
      g.fillStyle = PAPER;
      g.beginPath();
      g.arc(m, m, 10, 0, Math.PI * 2);
      g.fill();
      g.strokeStyle = col;
      g.beginPath();
      g.arc(m, m, 8.5, 0, Math.PI * 2);
      g.stroke();
      g.beginPath();
      g.moveTo(m - 6, m - 6);
      g.lineTo(m + 6, m + 6);
      g.moveTo(m + 6, m - 6);
      g.lineTo(m - 6, m + 6);
      g.stroke();
    } else if (kind === 'move') {
      g.fillStyle = col;
      g.strokeStyle = PAPER;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(m, m, 8, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.strokeStyle = '#fff';
      g.lineWidth = 2.2;
      g.beginPath();
      g.moveTo(m - 4, m + 4);
      g.lineTo(m + 4, m - 4);
      g.moveTo(m - 1, m - 4.5);
      g.lineTo(m + 4.5, m - 4.5);
      g.lineTo(m + 4.5, m + 1);
      g.stroke();
    } else {
      g.fillStyle = PAPER;
      g.fillRect(m - 9, m - 9, 18, 18);
      g.strokeStyle = col;
      g.lineWidth = 2;
      g.strokeRect(m - 8, m - 8, 16, 16);
      g.fillStyle = col;
      g.beginPath();
      g.moveTo(m - 8, m + 8);
      g.lineTo(m + 8, m - 8);
      g.lineTo(m + 8, m + 8);
      g.closePath();
      g.fill();
    }
  });
}

export function registerIcons(map: MLMap): void {
  for (const k of ['addition', 'deletion', 'move', 'attribute'] as Kind[]) {
    if (!map.hasImage(`k-${k}`)) map.addImage(`k-${k}`, kindIcon(k), { pixelRatio: PX });
  }
  if (!map.hasImage('arrow')) {
    map.addImage(
      'arrow',
      draw(22, (g, s) => {
        const m = s / 2;
        g.fillStyle = KIND_COLOR.move;
        g.strokeStyle = PAPER;
        g.lineWidth = 1.5;
        g.beginPath();
        g.moveTo(m, 2);
        g.lineTo(m + 7, s - 4);
        g.lineTo(m, s - 8);
        g.lineTo(m - 7, s - 4);
        g.closePath();
        g.fill();
        g.stroke();
      }),
      { pixelRatio: PX },
    );
  }
  if (!map.hasImage('registration')) {
    map.addImage(
      'registration',
      draw(44, (g, s) => {
        const m = s / 2;
        g.strokeStyle = '#202a2a';
        g.lineWidth = 1.2;
        g.beginPath();
        g.arc(m, m, 15, 0, Math.PI * 2);
        g.moveTo(m, 1);
        g.lineTo(m, 12);
        g.moveTo(m, s - 12);
        g.lineTo(m, s - 1);
        g.moveTo(1, m);
        g.lineTo(12, m);
        g.moveTo(s - 12, m);
        g.lineTo(s - 1, m);
        g.stroke();
      }),
      { pixelRatio: PX },
    );
  }
}
