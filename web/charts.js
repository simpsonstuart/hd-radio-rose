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
export function drawConstellation(canvas, mer) {
  if (!canvas) return;
  const c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  c.fillStyle = '#0a1218'; c.fillRect(0, 0, w, h);
  c.strokeStyle = '#294049'; c.beginPath(); c.moveTo(w / 2, 0); c.lineTo(w / 2, h); c.moveTo(0, h / 2); c.lineTo(w, h / 2); c.stroke();
  const spread = Math.max(2, 60 * Math.pow(10, -Math.max(0, mer || 0) / 20));
  c.fillStyle = '#a7e8c580';
  for (let i = 0; i < 400; i++) {
    const noise = () => Math.sqrt(-2 * Math.log(Math.max(0.0001, Math.random()))) * Math.cos(2 * Math.PI * Math.random()) * spread;
    c.fillRect((i % 2 ? .7 : .3) * w + noise(), (i % 4 < 2 ? .7 : .3) * h + noise(), 2, 2);
  }
}
