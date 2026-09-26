'use strict';
/* Hiệu ứng "hố đen vũ trụ" nhỏ khi bấm. Nhẹ: 1 phần tử, chỉ animate transform/opacity (GPU),
   tối đa MAX hiệu ứng cùng lúc, tự gỡ khi kết thúc, tắt nếu người dùng chọn giảm chuyển động. */
(() => {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const MAX = 6;
  let live = 0;
  addEventListener('pointerdown', e => {
    if (live >= MAX) return;
    live++;
    const w = document.createElement('div');
    w.className = 'bh';
    w.style.transform = `translate3d(${e.clientX}px,${e.clientY}px,0)`;
    const i = document.createElement('i');
    w.appendChild(i);
    i.addEventListener('animationend', () => { w.remove(); live--; }, { once: true });
    document.body.appendChild(w);
  }, { passive: true, capture: true });
})();
