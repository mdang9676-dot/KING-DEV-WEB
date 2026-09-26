'use strict';
const LANGS = [['vi', 'Tiếng Việt'], ['en', 'English'], ['zh', '中文'], ['ja', '日本語'], ['ko', '한국어'], ['fr', 'Français'], ['de', 'Deutsch'],
  ['es', 'Español'], ['pt', 'Português'], ['ru', 'Русский'], ['th', 'ไทย'], ['id', 'Indonesia'], ['ar', 'العربية'], ['hi', 'हिन्दी']];
const S = { caps: {}, file: null, videoId: null, cfg: { enabled: false }, aiSaved: null, voice: null, audio: null, audioBtn: null,
  logoCanvas: null, logoDirty: false, logoId: null, pollId: null, resultJob: null, showingResult: false, inited: false };
let player, authMode = 'login';
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const opts = (list, sel) => list.map(([v, t]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${esc(t)}</option>`).join('');

/* ================= ĐĂNG NHẬP / ĐĂNG KÝ ================= */
function showAuth(msg = '') {
  $('#appView').hidden = true; $('#authView').hidden = false; $('#authErr').textContent = msg; S.pollId = null;
}
async function showApp(user) {
  $('#authView').hidden = true; $('#appView').hidden = false; $('#userEmail').textContent = user.email;
  if (!S.inited) { S.inited = true; await initApp(); }
}
window.onAuthLost = () => showAuth('Phiên đăng nhập đã hết hạn, hãy đăng nhập lại.');

function setAuthMode(m) {
  authMode = m;
  $('#tabLogin').classList.toggle('on', m === 'login'); $('#tabRegister').classList.toggle('on', m === 'register');
  $('#authSubmit').textContent = m === 'login' ? 'Đăng nhập' : 'Tạo tài khoản';
  $('#passHint').hidden = m === 'login'; $('#authPass').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  $('#nameField').hidden = m === 'login'; $('#confirmField').hidden = m === 'login';
  if (m === 'login') { $('#authName').value = ''; $('#authPass2').value = ''; }
  $('#authErr').textContent = '';
}
$('#tabLogin').onclick = () => setAuthMode('login');
$('#tabRegister').onclick = () => setAuthMode('register');
$('#togglePass').onclick = () => { const p = $('#authPass'); p.type = p.type === 'password' ? 'text' : 'password'; };
$('#togglePass2').onclick = () => { const p = $('#authPass2'); p.type = p.type === 'password' ? 'text' : 'password'; };
$('#authForm').onsubmit = async e => {
  e.preventDefault();
  const btn = $('#authSubmit'); $('#authErr').textContent = '';
  if (authMode === 'register' && $('#authPass').value !== $('#authPass2').value) {
    $('#authErr').textContent = 'Mật khẩu xác nhận không khớp.'; return;
  }
  btn.disabled = true;
  try {
    const payload = { email: $('#authEmail').value.trim(), password: $('#authPass').value };
    if (authMode === 'register') payload.name = $('#authName').value.trim();
    const u = await api(authMode === 'login' ? '/auth/login' : '/auth/register', { method: 'POST', json: payload });
    $('#authPass').value = ''; $('#authPass2').value = '';
    await showApp(u);
  } catch (err) { $('#authErr').textContent = err.message; }
  finally { btn.disabled = false; }
};
$('#btnLogout').onclick = async () => { try { await api('/auth/logout', { method: 'POST' }); } catch { /* bỏ qua */ } location.reload(); };

/* ================= 1. NGUỒN VIDEO ================= */
function showDl(text) { $('#dlBox').hidden = false; $('#dlText').textContent = text; $('#dlResult').textContent = ''; setDl(0); }
function setDl(f) { const p = Math.max(1, Math.min(100, Math.round(f * 100))); $('#dlPct').textContent = p + '%'; $('#dlBar').style.width = p + '%'; }
function dlOk(msg) { setDl(1); $('#dlResult').innerHTML = `<span class="ok">✅ ${esc(msg)}</span>`; }
function dlFail(msg) { $('#dlResult').innerHTML = `<span class="err">❌ Thất bại: ${esc(msg)}</span>`; }

async function setVideo(id, okMsg) {
  S.videoId = id; S.showingResult = false;
  const info = await api('/videos/' + id);
  updateLongNote(info.duration_sec);
  player.marks = []; player.sub = null; player.render();
  player.load(`${API}/videos/${id}/stream`);
  dlOk(okMsg); renderSummary();
}

function updateLongNote(sec) {                // video dài -> báo trước (OCR + dịch + render rất tốn thời gian/CPU)
  const el = $('#longNote'), min = Math.round((sec || 0) / 60);
  el.hidden = !(sec >= 1800);
  if (!el.hidden) el.textContent = `⚠ Video dài ${min} phút: xuất sẽ mất rất lâu và tốn nhiều CPU. Nên thử đoạn ngắn trước, hoặc tắt bớt thao tác (OCR/nâng độ hoạ) nếu không cần.`;
}

$('#btnPick').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = e => {
  const f = e.target.files[0]; S.file = f || null;
  $('#fileName').textContent = f ? `${f.name} (${(f.size / 1048576).toFixed(1)} MB)` : 'Chưa chọn file (MP4, MOV, WEBM…)';
  $('#btnUpload').disabled = !f;
};
$('#btnUpload').onclick = async () => {
  if (!S.file) return;
  $('#btnUpload').disabled = true; showDl('Đang tải video lên…');
  try {
    const fd = new FormData(); fd.append('file', S.file);
    const d = await upload('/upload', fd, setDl);
    await setVideo(d.video_id, 'Đã tải video lên thành công');
  } catch (e) { dlFail(e.message); }
  finally { $('#btnUpload').disabled = !S.file; }
};

$('#btnLink').onclick = async () => {
  const url = $('#linkInput').value.trim();
  if (!url) return toast('Hãy dán link video.');
  $('#btnLink').disabled = true; showDl('Đang kiểm tra link…');
  try {
    const d = await api('/import-url', { method: 'POST', json: { url } });
    $('#dlText').textContent = `Đang tải video từ link: ${d.metadata.title} (${d.metadata.provider})`;
    for (;;) {
      const j = await api('/import-url/' + d.job_id);
      setDl(j.progress / 100);
      if (j.status === 'COMPLETED') { await setVideo(d.video_id, 'Đã tải video thành công'); break; }
      if (j.status === 'FAILED') throw new Error(j.error_message || 'Không tải được video');
      await sleep(1000);
    }
  } catch (e) { dlFail(e.message); }
  finally { $('#btnLink').disabled = false; }
};

/* ================= 2. KẾT NỐI AI ================= */
let detectTimer;
$('#aiShow').onclick = () => { const k = $('#aiKey'); k.type = k.type === 'password' ? 'text' : 'password'; };
$('#aiKey').oninput = () => { clearTimeout(detectTimer); detectTimer = setTimeout(detectKey, 350); $('#btnAiSave').disabled = true; };

async function detectKey() {
  const k = $('#aiKey').value.trim();
  if (k.length < 8) { $('#aiNote').textContent = ''; return; }
  try {
    const d = await api('/ai/detect', { method: 'POST', json: { api_key: k } });
    if (!d.candidates.length) { $('#aiNote').innerHTML = '<span class="warn">Không nhận ra loại key này — hãy chọn nhà cung cấp thủ công.</span>'; return; }
    $('#aiProvider').value = d.candidates[0].provider;
    const names = d.candidates.map(c => c.label).join(' / ');
    $('#aiNote').innerHTML = d.needs_choice
      ? `<span class="warn">Có thể là: ${esc(names)}. Hãy xác nhận nhà cung cấp rồi bấm “Kiểm tra key”.</span>`
      : `<span class="ok">Nhận diện: ${esc(names)} ✓</span>`;
  } catch { /* bỏ qua lỗi nhận diện */ }
}

const CHEAP = /(^|[-_ ])(flash|mini|haiku|lite|small|instant|nano)([-_. ]|$)|(^|[-_ ])8b/i;   // khớp nguyên từ (không khớp 'gemini')     // ưu tiên model rẻ -> tiết kiệm token
$('#btnModels').onclick = async () => {
  const provider = $('#aiProvider').value, key = $('#aiKey').value.trim();
  if (!provider) return toast('Hãy chọn nhà cung cấp AI.');
  $('#btnModels').disabled = true; $('#aiNote').textContent = 'Đang kiểm tra key…';
  try {
    const d = await api('/ai/models', { method: 'POST', json: { provider, api_key: key || undefined } });
    if (!d.models.length) throw new Error('Nhà cung cấp không trả về model nào.');
    const keep = S.aiSaved && S.aiSaved.provider === provider ? S.aiSaved.model : null;
    const pick = d.models.includes(keep) ? keep : (d.models.find(m => CHEAP.test(m)) || d.models[0]);
    $('#aiModel').innerHTML = opts(d.models.map(m => [m, m]), pick);
    $('#btnAiSave').disabled = false;
    $('#aiNote').innerHTML = `<span class="ok">Key hợp lệ ✓ — ${d.models.length} model. Chọn model rồi bấm Lưu.</span>`;
  } catch (e) { $('#aiNote').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  finally { $('#btnModels').disabled = false; }
};
$('#btnAiSave').onclick = async () => {
  const provider = $('#aiProvider').value, model = $('#aiModel').value, key = $('#aiKey').value.trim();
  if (!provider || !model) return toast('Hãy kiểm tra key và chọn model trước.');
  $('#btnAiSave').disabled = true;
  try {
    await api('/ai/settings', { method: 'PUT', json: { provider, model, api_key: key || undefined } });
    $('#aiKey').value = ''; $('#aiNote').innerHTML = '<span class="ok">Đã lưu ✓</span>';
    await refreshAi(); loadVoices();
  } catch (e) { $('#aiNote').innerHTML = `<span class="err">${esc(e.message)}</span>`; $('#btnAiSave').disabled = false; }
};
$('#btnAiClear').onclick = async () => { await api('/ai/settings', { method: 'DELETE' }); await refreshAi(); loadVoices(); };

async function refreshAi() {
  const s = await api('/ai/settings');
  S.aiSaved = s.configured ? s : null;
  $('#aiStatus').innerHTML = s.configured
    ? `<span class="ok">✅ Đang dùng: ${esc(s.label)} · ${esc(s.model)} · <code>${esc(s.key_masked)}</code></span>`
    : 'Chưa kết nối AI — cần có để dịch phụ đề.';
  $('#btnAiClear').hidden = !s.configured;
  if (s.configured) { $('#aiProvider').value = s.provider; $('#aiModel').innerHTML = opts([[s.model, s.model]], s.model); }
  renderSummary();
}

/* ================= 3. CẤU HÌNH VIDEO ================= */
$('#cfgSharp').oninput = () => { $('#cfgSharpVal').textContent = $('#cfgSharp').value + '%'; };
$('#btnCfg').onclick = () => {
  S.cfg = { enabled: true, res: +$('#cfgRes').value, sharp: +$('#cfgSharp').value / 100, denoise: $('#cfgDenoise').checked };
  $('#cfgNote').innerHTML = `<span class="ok">✓ Đã áp dụng</span>`; renderSummary();
};

/* ================= 4. PHỤ ĐỀ ================= */
function fillSubLang() {
  const whisper = $('#subSource').value === 'whisper';
  const cur = $('#subLang').value;
  $('#subLang').innerHTML = opts((whisper ? [['', 'Tự nhận']] : []).concat(LANGS.filter(l => l[0] !== 'vi').concat([['vi', 'Tiếng Việt']])), cur || (whisper ? '' : 'en'));
}
$('#subSource').onchange = () => { fillSubLang(); $('#whisperRow').hidden = $('#subSource').value !== 'whisper'; };
$('#subSize').oninput = () => { $('#subSizeVal').textContent = $('#subSize').value + '%'; };
$('#btnDrawRegion').onclick = () => { if (!S.videoId) return toast('Hãy thêm video trước.'); player.setMode(player.mode === 'sub' ? null : 'sub'); };
$('#btnAutoRegion').onclick = async () => {
  if (!S.videoId) return toast('Hãy thêm video trước.');
  const b = $('#btnAutoRegion'); b.disabled = true; $('#regionNote').textContent = '⏳ Đang phân tích ~24 khung hình (có thể mất tới 1 phút)…';
  try {
    const r = await api('/subtitle/detect-region', { method: 'POST', json: { video_id: S.videoId, language: $('#subLang').value || 'en', engine: $('#subOcr').value } });
    player.reveal();
    player.setSub({ x: r.x / r.frame_width, y: r.y / r.frame_height, w: r.width / r.frame_width, h: r.height / r.frame_height });
    $('#regionNote').innerHTML = r.fallback
      ? `<span class="warn">⚠ Không thấy phụ đề rõ ràng (${esc(r.reason)}). Đây chỉ là vùng đoán — hãy kéo chỉnh trên màn hình chiếu.</span>`
      : `<span class="ok">✓ Đã tìm thấy vùng phụ đề (tin cậy ${Math.round(r.confidence * 100)}%). Kéo để chỉnh nếu lệch.</span>`;
  } catch (e) { $('#regionNote').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  finally { b.disabled = false; }
};

/* ================= 5. LOGO ================= */
$('#btnPickMark').onclick = () => { if (!S.videoId) return toast('Hãy thêm video trước.'); player.setMode(player.mode === 'logo' ? null : 'logo'); };
document.addEventListener('playermode', e => {
  $('#btnPickMark').textContent = e.detail === 'logo' ? '🎯 Đang chọn… (bấm để dừng)' : '🎯 Chọn logo trên màn hình';
  $('#btnDrawRegion').textContent = e.detail === 'sub' ? '✏️ Đang vẽ… (bấm để dừng)' : '✏️ Vẽ vùng thủ công';
});
function renderMarks(st) {
  $('#markList').innerHTML = '';
  st.marks.forEach((m, i) => {
    const row = document.createElement('div');
    row.innerHTML = `<span>Logo ${i + 1} — ${m.state === 'removed' ? '<span class="ok">đã xoá ✓</span>' : 'đang chọn (bấm ✕ trên video)'}</span>`;
    const b = document.createElement('button'); b.className = 'btn small'; b.textContent = 'Bỏ';
    b.onclick = () => player.removeMark(m.id); row.appendChild(b); $('#markList').appendChild(row);
  });
}

async function loadLogoFile(file) {
  const bmp = await createImageBitmap(file).catch(() => null);
  if (!bmp) return toast('Không đọc được ảnh này.');
  S.logoBitmap = bmp; drawLogo();
  $('#logoCtl').hidden = false;
  $('#logoNote').textContent = 'Kéo logo trên màn hình chiếu để đổi vị trí, kéo góc để đổi cỡ.';
  player.reveal();
}
function drawLogo() {
  const bmp = S.logoBitmap; if (!bmp) return;
  const sc = Math.min(1, 640 / Math.max(bmp.width, bmp.height));
  const c = document.createElement('canvas'); c.width = Math.max(1, Math.round(bmp.width * sc)); c.height = Math.max(1, Math.round(bmp.height * sc));
  const ctx = c.getContext('2d', { willReadFrequently: true }); ctx.drawImage(bmp, 0, 0, c.width, c.height);
  if ($('#logoBgOn').checked) {
    const img = ctx.getImageData(0, 0, c.width, c.height);
    if (removeBackgroundData(img.data, c.width, c.height, +$('#logoTol').value)) ctx.putImageData(img, 0, 0);
  }
  S.logoCanvas = c; S.logoDirty = true;
  const pv = $('#logoPreview'); pv.width = c.width; pv.height = c.height; pv.getContext('2d').drawImage(c, 0, 0);
  player.setLogo(c.toDataURL('image/png'), c.height / c.width, +$('#logoSize').value / 100, +$('#logoOpacity').value / 100);
}
$('#btnLogoUpload').onclick = () => $('#logoFile').click();
$('#logoFile').onchange = e => { if (e.target.files[0]) loadLogoFile(e.target.files[0]); e.target.value = ''; };
let tolTimer;
$('#logoTol').oninput = () => { $('#logoTolVal').textContent = $('#logoTol').value; clearTimeout(tolTimer); tolTimer = setTimeout(drawLogo, 120); };
$('#logoBgOn').onchange = drawLogo;
$('#logoSize').oninput = () => { $('#logoSizeVal').textContent = $('#logoSize').value + '%'; player.updateLogo({ w: +$('#logoSize').value / 100 }); };
$('#logoOpacity').oninput = () => { $('#logoOpVal').textContent = $('#logoOpacity').value + '%'; player.updateLogo({ opacity: +$('#logoOpacity').value / 100 }); };
$('#btnLogoRemove').onclick = () => { S.logoBitmap = null; S.logoCanvas = null; S.logoId = null; player.clearLogo(); $('#logoCtl').hidden = true; };

async function ensureLogoUploaded() {          // tải logo lên đúng 1 lần khi xuất (không tải mỗi lần kéo thanh trượt)
  if (!S.logoCanvas) return null;
  if (S.logoId && !S.logoDirty) return S.logoId;
  const blob = await new Promise(r => S.logoCanvas.toBlob(r, 'image/png'));
  const fd = new FormData(); fd.append('file', blob, 'logo.png');
  const d = await api('/assets/logo', { method: 'POST', form: fd });
  S.logoId = d.logo_id; S.logoDirty = false;
  return S.logoId;
}

/* ================= 6. GIỌNG ================= */
$('#dubOrig').oninput = () => { $('#dubOrigVal').textContent = $('#dubOrig').value + '%'; };
$('#dubProvider').onchange = loadVoices;
async function loadVoices(selectId) {
  const provider = $('#dubProvider').value, list = $('#voiceList');
  $('#cloneBox').hidden = provider !== 'vieneu';
  list.innerHTML = '<div class="mut">Đang tải danh sách giọng…' + (provider === 'vieneu' ? ' (lần đầu nạp model có thể mất vài chục giây)' : '') + '</div>'; S.voice = null;
  try {
    const lang = provider === 'edge_tts' ? ($('#subTarget').value || 'vi') : '';
    const v = await api(`/ai/voices?provider=${provider}${lang ? '&lang=' + encodeURIComponent(lang) : ''}`);
    if (!v.length) { list.innerHTML = '<div class="mut">Không có giọng cho ngôn ngữ này.</div>'; return; }
    list.innerHTML = '';
    const pick = typeof selectId === 'string' ? Math.max(0, v.findIndex(x => x.id === selectId)) : 0;
    v.forEach((x, i) => {
      const row = document.createElement('label'); row.className = 'voice' + (x.cloned ? ' cloned' : '');
      row.innerHTML = `<input type="radio" name="voice"><span class="nm">${x.cloned ? '🎙 ' : ''}Giọng ${i + 1} — ${esc(x.name)}${x.gender ? ' · ' + esc(x.gender) : ''}</span>`;
      const play = document.createElement('button'); play.type = 'button'; play.className = 'btn small'; play.textContent = '▶ Nghe thử';
      play.onclick = ev => { ev.preventDefault(); previewVoice(provider, x.id, play); };
      row.appendChild(play);
      if (x.cloned) {
        const del = document.createElement('button'); del.type = 'button'; del.className = 'del'; del.textContent = '✕'; del.title = 'Xoá giọng nhân bản này';
        del.onclick = async ev => {
          ev.preventDefault();
          if (!confirm('Xoá giọng nhân bản "' + x.name + '"? Không thể khôi phục.')) return;
          try { await api('/voice-clones/' + x.clone_id, { method: 'DELETE' }); toast('Đã xoá giọng.'); loadVoices(); } catch (e) { toast(e.message, 5000); }
        };
        row.appendChild(del);
      }
      $('input', row).onchange = () => { S.voice = { provider, id: x.id, name: x.name }; $$('.voice', list).forEach(r => r.classList.toggle('sel', r === row)); renderSummary(); };
      list.appendChild(row);
      if (i === pick) { $('input', row).checked = true; $('input', row).onchange(); }
    });
  } catch (e) { list.innerHTML = `<div class="warn">${esc(e.message)}</div>`; }
}
$('#btnClone').onclick = async () => {
  const name = $('#cloneName').value.trim(), f = $('#cloneFile').files[0], msg = $('#cloneMsg');
  if (!name) return toast('Hãy đặt tên cho giọng.');
  if (!f) return toast('Hãy chọn file ghi âm giọng nói.');
  if (!$('#cloneConsent').checked) return toast('Bạn cần tích xác nhận quyền sử dụng giọng.', 5000);
  const b = $('#btnClone'); b.disabled = true; msg.textContent = 'Đang xử lý mẫu giọng…';
  try {
    const fd = new FormData(); fd.append('file', f); fd.append('name', name); fd.append('consent', 'true');
    const r = await upload('/voice-clones', fd, p => { msg.textContent = 'Đang tải lên… ' + Math.round(p * 100) + '%'; });
    msg.textContent = ''; $('#cloneFile').value = ''; $('#cloneName').value = ''; $('#cloneConsent').checked = false;
    toast('Đã tạo giọng nhân bản. Bấm “Nghe thử” để kiểm tra.');
    await loadVoices('clone:' + r.id);
  } catch (e) { msg.textContent = ''; toast(e.message, 6000); }
  finally { b.disabled = false; }
};
async function previewVoice(provider, id, btn) {
  if (S.audio) { S.audio.pause(); if (S.audioBtn) S.audioBtn.textContent = '▶ Nghe thử'; const same = S.audioBtn === btn; S.audio = null; S.audioBtn = null; if (same) return; }
  btn.textContent = '⏳ Đang tạo…'; btn.disabled = true;
  try {
    const res = await api('/ai/voices/preview', { method: 'POST', json: { provider, voice_id: id }, raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = new Audio(url); S.audio = a; S.audioBtn = btn;
    a.onended = () => { btn.textContent = '▶ Nghe thử'; S.audio = null; S.audioBtn = null; URL.revokeObjectURL(url); };
    btn.textContent = '⏸ Dừng'; await a.play();
  } catch (e) { btn.textContent = '▶ Nghe thử'; toast(e.message, 5000); }
  finally { btn.disabled = false; }
}

/* ================= 7. XUẤT VIDEO (chạy ngầm) ================= */
function renderSummary() {
  const chips = [], st = player ? player.getState() : { removed: [], logo: null };
  if ($('#subOn').checked) chips.push(`Phụ đề: ${$('#subSource').value === 'ocr' ? 'OCR' : 'Whisper'} → ${$('#subTarget').value}`);
  if (st.removed.length) chips.push(`Xoá logo: ${st.removed.length}`);
  if (st.logo) chips.push('Logo riêng');
  if ($('#dubOn').checked) chips.push('Lồng tiếng: ' + (S.voice ? S.voice.name.slice(0, 28) : '—'));
  if (S.cfg.enabled) chips.push(`Độ hoạ: ${S.cfg.res || 'gốc'}${S.cfg.res ? 'p' : ''}`);
  if (S.aiSaved) chips.push(`AI: ${S.aiSaved.label}`);
  $('#summary').innerHTML = chips.length ? chips.map(c => `<span class="pill">${esc(c)}</span>`).join('') : '<span class="mut">Chưa chọn thao tác nào.</span>';
}
document.addEventListener('change', () => renderSummary());

async function buildRequest() {
  const st = player.getState(), src = $('#subSource').value;
  const mode = document.querySelector('input[name=subMode]:checked').value;
  const cover = mode === 'solid' ? { mode: 'solid', color: '#000000', opacity: 0.92 } : { mode };
  const req = { video_id: S.videoId };
  req.subtitle = $('#subOn').checked ? {
    enabled: true, source: src, source_lang: $('#subLang').value || null, target_lang: $('#subTarget').value, translate: true,
    region: st.sub ? { x: st.sub.x, y: st.sub.y, w: st.sub.w, h: st.sub.h } : null,
    cover: (st.sub || src === 'ocr') ? cover : { mode: 'none' },
    burn: $('#subBurn').checked, ocr_engine: $('#subOcr').value, whisper_model: $('#subWhisper').value, style_genre: $('#subGenre').value,
    glossary: $('#subGlossary').value, condense: $('#subCondense').checked, max_cps: +$('#subCps').value || 20,
    style: { size_pct: +$('#subSize').value, color: $('#subColor').value, box: $('#subBox').checked, font: $('#subFont').value },
  } : { enabled: false };
  req.logo_remove = { enabled: st.removed.length > 0, regions: st.removed, cover: { mode: $('#markCover').value } };
  if (st.logo) {
    const id = await ensureLogoUploaded();
    req.logo_overlay = { enabled: true, logo_id: id, x: st.logo.x, y: st.logo.y, w: st.logo.w, opacity: st.logo.opacity };
  } else req.logo_overlay = { enabled: false };
  if ($('#dubOn').checked) {
    if (!S.voice) throw new Error('Hãy chọn một giọng lồng tiếng.');
    req.dub = { enabled: true, provider: S.voice.provider, voice_id: S.voice.id, original_volume: +$('#dubOrig').value / 100, fit: $('#dubFit').value };
  } else req.dub = { enabled: false };
  req.enhance = S.cfg.enabled ? { enabled: true, target_height: S.cfg.res, sharpen: S.cfg.sharp, denoise: S.cfg.denoise } : { enabled: false };
  return req;
}

$('#btnExport').onclick = async () => {
  if (!S.videoId) return toast('Hãy thêm video trước.');
  const b = $('#btnExport'); b.disabled = true; $('#exResult').hidden = true; $('#exWarn').textContent = '';
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  try {
    const d = await api('/pipeline', { method: 'POST', json: await buildRequest() });
    toast('Đã bắt đầu xuất video (chạy ngầm trên máy chủ).');
    pollJob(d.job_id);
  } catch (e) { $('#exStage').innerHTML = `<span class="err">${esc(e.message)}</span>`; b.disabled = false; }
};
$('#btnCancel').onclick = async () => { if (S.pollId) { try { await api(`/jobs/${S.pollId}/cancel`, { method: 'POST' }); } catch (e) { toast(e.message); } } };

const ACTIVE = s => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(s);
async function pollJob(id) {
  S.pollId = id; localStorage.setItem('activeJob', id); $('#btnExport').disabled = true; $('#btnCancel').hidden = false;
  let fails = 0;
  while (S.pollId === id) {
    try {
      const j = await api('/jobs/' + id); fails = 0;
      const pct = Math.max(1, Math.min(100, j.progress || 0));
      $('#exPct').textContent = pct + '%'; $('#exBar').style.width = pct + '%'; $('#exStage').textContent = j.stage || j.status;
      if (!ACTIVE(j.status)) { finishJob(id, j); return; }
    } catch (e) {
      if (e.status === 404) { localStorage.removeItem('activeJob'); S.pollId = null; $('#btnExport').disabled = false; $('#btnCancel').hidden = true; return; }
      if (e.code === 'AUTH_REQUIRED') return;
      fails++; $('#exStage').textContent = 'Mất kết nối — đang thử lại… (máy chủ vẫn đang xử lý)';
    }
    await sleep(Math.min(1000 * (1 + fails), 8000));
  }
}
function finishJob(id, j) {
  localStorage.removeItem('activeJob'); S.pollId = null; $('#btnExport').disabled = false; $('#btnCancel').hidden = true;
  if (j.status === 'COMPLETED') {
    $('#exPct').textContent = '100%'; $('#exBar').style.width = '100%'; $('#exStage').innerHTML = '<span class="ok">✅ Xuất video xong</span>';
    S.resultJob = id; $('#exResult').hidden = false;
    $('#dlVideo').hidden = $('#btnWatch').hidden = !j.has_result; $('#dlVideo').href = `${API}/jobs/${id}/download`;
    $('#dlSrt').hidden = !j.has_srt; $('#dlSrt').href = `${API}/jobs/${id}/download?kind=srt`;
    if (j.warnings && j.warnings.length) $('#exWarn').textContent = '⚠ ' + j.warnings.join(' ');
    if (document.hidden && 'Notification' in window && Notification.permission === 'granted') new Notification('KINGDEV TOOL', { body: 'Video đã xuất xong, bấm để tải.' });
  } else if (j.status === 'FAILED') $('#exStage').innerHTML = `<span class="err">❌ ${esc(j.error_message || 'Xuất video thất bại')}</span>`;
  else $('#exStage').textContent = 'Đã huỷ.';
  loadHistory();
}
$('#btnWatch').onclick = () => {
  if (!S.resultJob) return;
  S.showingResult = !S.showingResult;
  player.load(S.showingResult ? `${API}/jobs/${S.resultJob}/stream` : `${API}/videos/${S.videoId}/stream`);
  $('#btnWatch').textContent = S.showingResult ? '↩ Xem lại video gốc' : '▶ Xem kết quả trên màn hình chiếu';
  player.v.play().catch(() => {});
};
async function loadHistory() {
  try {
    const list = (await api('/jobs?limit=6')).filter(j => j.job_type === 'pipeline');
    $('#hist').innerHTML = '';
    list.forEach(j => {
      const row = document.createElement('div');
      const t = new Date(j.created_at).toLocaleString('vi-VN');
      row.innerHTML = `<span>${esc(j.filename || 'video')} · ${esc(t)} · <span class="pill">${esc(j.status)}${ACTIVE(j.status) ? ' ' + j.progress + '%' : ''}</span></span>`;
      if (j.has_result) { const a = document.createElement('a'); a.className = 'dl'; a.href = `${API}/jobs/${j.id}/download`; a.textContent = '⬇ Tải'; row.appendChild(a); }
      $('#hist').appendChild(row);
    });
  } catch { /* bỏ qua */ }
}

/* ================= FONT + KHẢ NĂNG MÁY CHỦ ================= */
async function loadFonts() {
  try {
    const d = await api('/fonts'), sel = $('#subFont');
    const grp = (label, list) => list.length ? `<optgroup label="${label}">${list.map(f => `<option value="${esc(f.family)}">${esc(f.family)}</option>`).join('')}</optgroup>` : '';
    const list = d.fonts || [];
    if (!list.length) return;
    sel.innerHTML = grp('Font của bạn', list.filter(f => f.custom)) + grp('Hỗ trợ tiếng Việt', list.filter(f => !f.custom));
    sel.value = list.some(f => f.family === d.default) ? d.default : list[0].family;
  } catch { /* giữ Arial */ }
}
async function loadCaps() {
  try {
    S.caps = await api('/capabilities');
    for (const o of $$('#subOcr option')) if (!S.caps[o.value]) { o.disabled = true; o.textContent = o.textContent.replace(/ \(.*\)$/, '') + ' (chưa cài)'; }
    const vn = $('#dubProvider option[value=vieneu]'); if (vn && !S.caps.vieneu) { vn.disabled = true; vn.textContent += ' (chưa cài)'; }
    const w = $('#subSource option[value=whisper]'); if (!S.caps.whisper) { w.disabled = true; w.textContent += ' (chưa cài)'; }
    if (!S.caps[$('#subOcr').value]) { const ok = $$('#subOcr option').find(o => !o.disabled); if (ok) $('#subOcr').value = ok.value; }
  } catch { /* bỏ qua */ }
}

/* ================= KHỞI TẠO ================= */
async function initApp() {
  player = new KPlayer();
  player.onChange = st => { renderMarks(st); renderSummary();
    $('#regionNote').textContent = st.sub ? $('#regionNote').textContent || 'Đã chọn vùng phụ đề.' : 'Chưa chọn vùng: với nguồn OCR hệ thống sẽ tự tìm khi xuất.'; };
  $('#subTarget').innerHTML = opts(LANGS, 'vi'); $('#subTarget').onchange = () => { if ($('#dubProvider').value === 'edge_tts') loadVoices(); };
  fillSubLang(); player.paintSeek();
  try { const p = await api('/ai/providers'); $('#aiProvider').innerHTML = '<option value="">— chọn —</option>' + opts(p.map(x => [x.id, x.label])); } catch { /* bỏ qua */ }
  loadFonts(); loadCaps();
  await refreshAi().catch(() => {}); loadVoices(); loadHistory(); renderSummary();
  const a = localStorage.getItem('activeJob'); if (a) pollJob(a);
}

(async () => {
  setAuthMode('login');
  try {
    const cfg = await api('/auth/config'); if (!cfg.registration_open) $('#tabRegister').hidden = true;
    await showApp(await api('/auth/me'));
  } catch { showAuth(); }
})();
