'use strict';
/* Màn hình chiếu + lớp chỉnh sửa trên video.
   Mọi toạ độ lưu CHUẨN HOÁ 0..1 theo vùng hiển thị THẬT của video (đã trừ viền đen letterbox),
   nên khớp đúng với server (FFmpeg) dù đổi kích thước cửa sổ hay bật toàn màn hình. */
class KPlayer {
  constructor() {
    this.root = $('#player'); this.v = $('#vid'); this.ov = $('#overlay');
    this.marks = []; this.sub = null; this.logo = null; this.mode = null;
    this.vAspect = 16 / 9; this.onChange = () => {}; this.idleTimer = 0; this.seq = 0;
    this.bindControls(); this.bindOverlay();
    new ResizeObserver(() => this.layout()).observe(this.root);
    ['fullscreenchange', 'webkitfullscreenchange'].forEach(ev => document.addEventListener(ev, () => this.layout()));
  }

  /* ---------- nạp video ---------- */
  load(src) {
    this.v.src = src; this.v.load();
    $('#playerEmpty').hidden = true; $('#ctrl').hidden = false; $('#bigPlay').hidden = false;
    $('#btnPlay').textContent = '▶'; this.root.classList.remove('playing');
  }

  /* ---------- điều khiển ---------- */
  bindControls() {
    const v = this.v, seek = $('#seek');
    const toggle = () => (v.paused ? v.play().catch(() => {}) : v.pause());
    v.addEventListener('click', toggle);
    $('#bigPlay').addEventListener('click', toggle);
    $('#btnPlay').addEventListener('click', toggle);
    v.addEventListener('play', () => { $('#btnPlay').textContent = '⏸'; $('#bigPlay').hidden = true; this.root.classList.add('playing'); this.poke(); });
    v.addEventListener('pause', () => { $('#btnPlay').textContent = '▶'; $('#bigPlay').hidden = !v.currentSrc; this.root.classList.remove('playing', 'idle'); });
    v.addEventListener('loadedmetadata', () => { $('#tDur').textContent = fmtTime(v.duration); this.layout(); });
    v.addEventListener('error', () => toast('Không phát được video này trong trình duyệt (định dạng lạ). Bạn vẫn có thể xử lý và xuất bình thường.', 5000));
    let raf = 0;
    v.addEventListener('timeupdate', () => {
      if (raf) return;                         // giới hạn cập nhật UI theo khung hình
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!seek.dataset.drag && v.duration) seek.value = (v.currentTime / v.duration) * 1000;
        this.paintSeek(); $('#tCur').textContent = fmtTime(v.currentTime);
      });
    });
    seek.addEventListener('input', () => { seek.dataset.drag = '1'; if (v.duration) v.currentTime = (seek.value / 1000) * v.duration; this.paintSeek(); });
    seek.addEventListener('change', () => delete seek.dataset.drag);

    const vol = $('#vol'), mute = $('#btnMute');
    const icon = () => { mute.textContent = v.muted || v.volume === 0 ? '🔇' : v.volume < 0.5 ? '🔉' : '🔊'; };
    vol.addEventListener('input', () => { v.volume = +vol.value; v.muted = v.volume === 0; icon(); });
    mute.addEventListener('click', () => { v.muted = !v.muted; icon(); });

    const menu = $('#speedMenu');
    [0.5, 0.75, 1, 1.25, 1.5, 2].forEach(s => {
      const b = document.createElement('button'); b.textContent = `Tốc độ ${s}x`; b.dataset.s = s; if (s === 1) b.className = 'on';
      b.addEventListener('click', () => { v.playbackRate = s; $$('button', menu).forEach(x => x.classList.toggle('on', x === b)); menu.hidden = true; });
      menu.appendChild(b);
    });
    $('#btnSpeed').addEventListener('click', e => { e.stopPropagation(); menu.hidden = !menu.hidden; });
    document.addEventListener('click', () => { menu.hidden = true; });

    $('#btnFs').addEventListener('click', () => this.fullscreen());
    v.addEventListener('dblclick', () => this.fullscreen());
    this.root.addEventListener('mousemove', () => this.poke());
    this.root.addEventListener('keydown', e => {
      if (e.target.matches('input,select')) return;
      const k = e.key.toLowerCase();
      if (k === ' ' || k === 'k') { e.preventDefault(); toggle(); }
      else if (k === 'arrowleft') v.currentTime = Math.max(0, v.currentTime - 5);
      else if (k === 'arrowright') v.currentTime = Math.min(v.duration || 0, v.currentTime + 5);
      else if (k === 'f') this.fullscreen();
      else if (k === 'm') mute.click();
    });
  }

  paintSeek() {
    const s = $('#seek'), p = (s.value / 1000) * 100;
    s.style.background = `linear-gradient(90deg,#a855f7 ${p}%,#ffffff33 ${p}%)`;
  }

  poke() {                                     // ẩn thanh điều khiển sau 2.5s không di chuột (khi đang phát)
    this.root.classList.remove('idle'); clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => this.root.classList.add('idle'), 2500);
  }

  fullscreen() {
    const r = this.root, on = document.fullscreenElement || document.webkitFullscreenElement || r.classList.contains('fake');
    if (on) {
      r.classList.remove('fake');
      (document.exitFullscreen || document.webkitExitFullscreen || (() => {})).call(document);
    } else if (r.requestFullscreen) r.requestFullscreen().catch(() => r.classList.add('fake'));
    else if (r.webkitRequestFullscreen) r.webkitRequestFullscreen();
    else r.classList.add('fake');              // iOS Safari: giả lập toàn màn hình để lớp chỉnh sửa vẫn hiện
    setTimeout(() => this.layout(), 60);
  }

  /* ---------- bố cục lớp chỉnh sửa khớp đúng vùng video ---------- */
  layout() {
    const vw = this.v.videoWidth, vh = this.v.videoHeight;
    if (!vw || !vh) { this.ov.style.display = 'none'; return; }
    const r = this.root.getBoundingClientRect();
    const s = Math.min(r.width / vw, r.height / vh), w = vw * s, h = vh * s;
    this.vAspect = vw / vh;
    Object.assign(this.ov.style, { display: 'block', width: w + 'px', height: h + 'px', left: (r.width - w) / 2 + 'px', top: (r.height - h) / 2 + 'px' });
    if (this.logo) this.logo.h = this.logo.w * this.logo.ar * this.vAspect;
    this.render();
  }

  /* ---------- chế độ vẽ ---------- */
  reveal() { this.root.scrollIntoView({ behavior: 'smooth', block: 'center' }); }   // đưa màn hình chiếu vào giữa để thao tác

  setMode(mode) {                              // 'logo' | 'sub' | null
    this.mode = mode; this.ov.classList.toggle('draw', !!mode);
    if (mode) this.reveal();
    if (mode) toast(mode === 'logo' ? 'Bấm hoặc kéo khung quanh logo cần xoá.' : 'Kéo khung quanh vùng phụ đề cũ.');
    document.dispatchEvent(new CustomEvent('playermode', { detail: mode }));
  }

  bindOverlay() {
    this.ov.addEventListener('pointerdown', e => {
      if (!this.mode || e.target !== this.ov) return;
      e.preventDefault();
      const b = this.ov.getBoundingClientRect();
      const p0 = { x: clamp((e.clientX - b.left) / b.width, 0, 1), y: clamp((e.clientY - b.top) / b.height, 0, 1) };
      let cur = { x: p0.x, y: p0.y, w: 0, h: 0 };
      const ghost = document.createElement('div'); ghost.className = 'ghost'; this.ov.appendChild(ghost);
      this.ov.setPointerCapture(e.pointerId);
      const rect = ev => {
        const x = clamp((ev.clientX - b.left) / b.width, 0, 1), y = clamp((ev.clientY - b.top) / b.height, 0, 1);
        cur = { x: Math.min(p0.x, x), y: Math.min(p0.y, y), w: Math.abs(x - p0.x), h: Math.abs(y - p0.y) };
        this.place(ghost, cur);
      };
      const up = ev => {
        this.ov.removeEventListener('pointermove', rect); this.ov.removeEventListener('pointerup', up); this.ov.removeEventListener('pointercancel', up);
        ghost.remove();
        if (cur.w < 0.02 || cur.h < 0.02) {    // chỉ bấm (không kéo): tạo khung mặc định quanh điểm bấm
          const w = this.mode === 'sub' ? 0.7 : 0.14, h = this.mode === 'sub' ? 0.14 : 0.09;
          cur = { x: clamp(p0.x - w / 2, 0, 1 - w), y: clamp(p0.y - h / 2, 0, 1 - h), w, h };
        }
        if (this.mode === 'logo') this.marks.push({ id: ++this.seq, ...cur, state: 'selected' });
        else this.sub = cur;
        this.setMode(null); this.render(); this.emit();
      };
      this.ov.addEventListener('pointermove', rect); this.ov.addEventListener('pointerup', up); this.ov.addEventListener('pointercancel', up);
    });
  }

  /* ---------- vẽ các đối tượng ---------- */
  place(el, o) {
    el.style.left = o.x * 100 + '%'; el.style.top = o.y * 100 + '%';
    el.style.width = o.w * 100 + '%'; el.style.height = o.h * 100 + '%';
  }

  el(cls, html = '') { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; return d; }

  render() {
    [...this.ov.children].forEach(c => { if (!c.classList.contains('ghost')) c.remove(); });
    for (const m of this.marks) {
      const d = this.el('mark ' + m.state, m.state === 'selected'
        ? '<button data-act="apply" title="Bấm để xoá logo này khỏi video" aria-label="Xoá logo">✕</button><i class="handle"></i>'
        : '<span class="tag">đã xoá</span><button data-act="undo" title="Hoàn tác" aria-label="Hoàn tác">↺</button>');
      this.place(d, m); this.ov.appendChild(d);
      $('button', d).addEventListener('pointerdown', e => e.stopPropagation());
      $('button', d).addEventListener('click', () => { m.state = m.state === 'selected' ? 'removed' : 'selected'; this.render(); this.emit(); });
      if (m.state === 'selected') this.drag(d, m);
    }
    if (this.sub) {
      const d = this.el('subreg', '<span class="tag">Vùng phụ đề cũ</span><button title="Bỏ vùng" aria-label="Bỏ vùng">✕</button><i class="handle"></i>');
      this.place(d, this.sub); this.ov.appendChild(d);
      $('button', d).addEventListener('pointerdown', e => e.stopPropagation());
      $('button', d).addEventListener('click', () => { this.sub = null; this.render(); this.emit(); });
      this.drag(d, this.sub);
    }
    if (this.logo) {
      const d = this.el('mylogo', '<img alt="Logo của bạn"><i class="handle"></i>');
      $('img', d).src = this.logo.url; $('img', d).style.opacity = this.logo.opacity;
      this.place(d, this.logo); this.ov.appendChild(d);
      this.drag(d, this.logo, () => this.logo.ar * this.vAspect);
    }
  }

  /* kéo để di chuyển, kéo góc dưới-phải để đổi cỡ (giữ tỉ lệ nếu có hàm aspect) */
  drag(el, obj, aspect) {
    el.addEventListener('pointerdown', e => {
      if (e.target.closest('button')) return;
      e.stopPropagation(); e.preventDefault();
      const handle = e.target.classList.contains('handle');
      const b = this.ov.getBoundingClientRect(), o = { ...obj }, sx = e.clientX, sy = e.clientY;
      el.setPointerCapture(e.pointerId);
      const move = ev => {
        const dx = (ev.clientX - sx) / b.width, dy = (ev.clientY - sy) / b.height;
        if (handle) {
          obj.w = clamp(o.w + dx, 0.03, 1 - o.x);
          obj.h = aspect ? obj.w * aspect() : clamp(o.h + dy, 0.03, 1 - o.y);
          if (obj.y + obj.h > 1) { obj.h = 1 - obj.y; if (aspect) obj.w = obj.h / aspect(); }
        } else { obj.x = clamp(o.x + dx, 0, 1 - obj.w); obj.y = clamp(o.y + dy, 0, 1 - obj.h); }
        this.place(el, obj);
      };
      const up = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up); this.emit(); };
      el.addEventListener('pointermove', move); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    });
  }

  /* ---------- API cho app.js ---------- */
  setSub(rect) { this.sub = rect; this.render(); this.emit(); }
  removeMark(id) { this.marks = this.marks.filter(m => m.id !== id); this.render(); this.emit(); }
  setLogo(url, ar, w = 0.15, opacity = 1) {
    const prev = this.logo;
    this.logo = { url, ar, w, opacity, x: prev ? prev.x : 0.04, y: prev ? prev.y : 0.05, h: w * ar * this.vAspect, id: null };
    if (this.logo.x + w > 1) this.logo.x = 1 - w;
    this.render(); this.emit();
  }
  updateLogo({ w, opacity }) {
    if (!this.logo) return;
    if (w !== undefined) { this.logo.w = w; this.logo.h = w * this.logo.ar * this.vAspect; this.logo.x = clamp(this.logo.x, 0, 1 - w); this.logo.y = clamp(this.logo.y, 0, Math.max(0, 1 - this.logo.h)); }
    if (opacity !== undefined) this.logo.opacity = opacity;
    this.render(); this.emit();
  }
  clearLogo() { this.logo = null; this.render(); this.emit(); }
  emit() { this.onChange(this.getState()); }
  getState() {
    const r = o => ({ x: +o.x.toFixed(4), y: +o.y.toFixed(4), w: +o.w.toFixed(4), h: +o.h.toFixed(4) });
    return {
      marks: this.marks.map(m => ({ id: m.id, state: m.state, ...r(m) })),
      removed: this.marks.filter(m => m.state === 'removed').map(r),
      sub: this.sub ? r(this.sub) : null,
      logo: this.logo ? { id: this.logo.id, x: +this.logo.x.toFixed(4), y: +this.logo.y.toFixed(4), w: +this.logo.w.toFixed(4), opacity: this.logo.opacity } : null,
    };
  }
}
