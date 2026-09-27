'use strict';
/* Xoá phông nền logo ngay trên trình duyệt (không gửi ảnh gốc đi đâu, xem trước tức thì).
   Thuật toán: loang (flood fill) từ 4 góc/viền, gặp pixel có màu gần màu góc (trong ngưỡng) thì coi là nền -> alpha=0,
   rồi làm mềm viền. Hợp với logo trên nền đồng màu (trắng, đen, màu trơn). Không dành cho nền phức tạp/ảnh chụp. */
function removeBackgroundData(d, w, h, tolPct) {
  for (let i = 3; i < d.length; i += 4 * 61) if (d[i] < 250) return false;       // đã có phần trong suốt: giữ nguyên
  const px = (x, y) => (y * w + x) * 4;
  const corners = [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]].map(([x, y]) => { const i = px(x, y); return [d[i], d[i + 1], d[i + 2]]; });
  const thr2 = Math.pow((tolPct / 100) * 441.67, 2);
  const isBg = i => {
    for (const c of corners) {
      const dr = d[i] - c[0], dg = d[i + 1] - c[1], db = d[i + 2] - c[2];
      if (dr * dr + dg * dg + db * db <= thr2) return true;
    }
    return false;
  };
  const seen = new Uint8Array(w * h), q = new Int32Array(w * h);
  let qh = 0, qt = 0;
  const push = (x, y) => { const p = y * w + x; if (seen[p] || !isBg(p * 4)) return; seen[p] = 1; q[qt++] = p; };
  for (let x = 0; x < w; x++) { push(x, 0); push(x, h - 1); }
  for (let y = 0; y < h; y++) { push(0, y); push(w - 1, y); }
  while (qh < qt) {
    const p = q[qh++], x = p % w, y = (p / w) | 0;
    if (x > 0) push(x - 1, y); if (x < w - 1) push(x + 1, y);
    if (y > 0) push(x, y - 1); if (y < h - 1) push(x, y + 1);
  }
  for (let p = 0; p < seen.length; p++) if (seen[p]) d[p * 4 + 3] = 0;
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {           // viền mềm: điểm giữ lại nhưng sát nền
    const p = y * w + x;
    if (!seen[p] && (seen[p - 1] || seen[p + 1] || seen[p - w] || seen[p + w])) d[p * 4 + 3] = 170;
  }
  return true;
}
if (typeof module !== 'undefined') module.exports = { removeBackgroundData };
