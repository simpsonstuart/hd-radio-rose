import { waveformColor } from './model.js';
export function drawSpectrum(canvas, bins) {
  if (!canvas || !bins?.length) return;
  const c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  c.clearRect(0, 0, w, h);
  c.strokeStyle = '#294049'; c.lineWidth = 1;
  for (let y = 0; y < h; y += h / 5) { c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
  c.fillStyle = '#8aa2ad'; c.font = '12px monospace';
  for (let i = 0; i < 5; i++) c.fillText(`${-20 - i * 20} dB`, 7, 14 + i * h / 5);
  c.strokeStyle = '#a7e8c5'; c.lineWidth = 1.5; c.beginPath();
  bins.forEach((v, i) => { const x = i * w / (bins.length - 1), y = h * (1 - Math.max(0, Math.min(1, (v + 120) / 100))); if (i) c.lineTo(x, y); else c.moveTo(x, y); });
  c.stroke(); c.lineTo(w, h); c.lineTo(0, h); c.closePath(); c.fillStyle = '#a7e8c516'; c.fill();
}
export function drawWaterfall(canvas, bins) {
  if (!canvas || !bins?.length) return;
  const c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  c.drawImage(canvas, 0, 0, w, h - 1, 0, 1, w, h - 1);
  const row = c.createImageData(w, 1);
  for (let i = 0; i < w; i++) { row.data.set(waveformColor(bins[Math.floor(i * bins.length / w)]), i * 4); row.data[i * 4 + 3] = 255; }
  c.putImageData(row, 0, 0);
}
// Fixed axes preserve measured amplitude and transmitter-induced distortion.
export function constellationPoints(frame, side) {
  if (!frame?.available || ![4, 64].includes(frame.modulation) || !['lower', 'upper'].includes(side)) return [];
  return (Array.isArray(frame[side]) ? frame[side] : []).slice(0, 1024)
    .filter(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite));
}
export function drawConstellation(canvas, frame, side = 'lower') {
  if (!canvas) return;
  const c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  const extent = frame?.modulation === 64 ? 4.5 : 2.5;
  const size = Math.min(w, h) - 56, x0 = (w - size) / 2, y0 = (h - size) / 2;
  const x = v => x0 + size * (v + extent) / (2 * extent);
  const y = v => y0 + size * (extent - v) / (2 * extent);
  c.fillStyle = '#0a1218'; c.fillRect(0, 0, w, h);
  c.lineWidth = 1; c.strokeStyle = '#294049';
  c.beginPath(); c.moveTo(x(-extent), y(0)); c.lineTo(x(extent), y(0));
  c.moveTo(x(0), y(-extent)); c.lineTo(x(0), y(extent)); c.stroke();
  c.strokeRect(x0, y0, size, size);
  c.fillStyle = '#8aa2ad'; c.font = '12px monospace'; c.textAlign = 'center';
  c.fillText('In-phase (I)', w / 2, h - 7); c.fillText('Q', w / 2, 17);
  c.fillText(String(-extent), x(-extent), y(0) + 16); c.fillText(String(extent), x(extent), y(0) + 16);
  const points = constellationPoints(frame, side);
  if (!points.length) {
    c.fillText('No measured symbols', w / 2, h / 2 - 20);
    return;
  }
  const levels = frame.modulation === 64 ? [-3.5,-2.5,-1.5,-.5,.5,1.5,2.5,3.5] : [-1,1];
  c.strokeStyle = '#68828d';
  for (const i of levels) for (const q of levels) {
    c.beginPath(); c.moveTo(x(i) - 3, y(q)); c.lineTo(x(i) + 3, y(q));
    c.moveTo(x(i), y(q) - 3); c.lineTo(x(i), y(q) + 3); c.stroke();
  }
  c.fillStyle = side === 'upper' ? '#91c9f0aa' : '#a7e8c5aa';
  let outside = 0;
  for (const [i, q] of points) {
    if (Math.abs(i) > extent || Math.abs(q) > extent) { outside++; continue; }
    c.fillRect(x(i) - 1, y(q) - 1, 2, 2);
  }
  if (outside) { c.fillStyle = '#8aa2ad'; c.fillText(`${outside} points outside axes`, w / 2, y0 + 16); }
}
