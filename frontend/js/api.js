'use strict';
const API = '/api';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmtTime = t => { t = Math.max(0, Math.floor(t || 0)); const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0'); };

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function parseError(status, data) {
  const d = data && data.detail;
  let code = 'ERROR', msg = 'Lỗi ' + status;
  if (typeof d === 'string') msg = d;
  else if (Array.isArray(d)) msg = 'Dữ liệu không hợp lệ: ' + ((d[0] && d[0].msg) || '');
  else if (d && typeof d === 'object') { code = d.code || code; msg = d.message || msg; }
  return new ApiError(status, code, msg);
}

async function api(path, { method = 'GET', json, form, raw = false } = {}) {
  const opt = { method, credentials: 'same-origin', headers: {} };
  if (json !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(json); }
  else if (form) opt.body = form;
  let res;
  try { res = await fetch(API + path, opt); }
  catch { throw new ApiError(0, 'NETWORK', 'Mất kết nối tới máy chủ. Hãy kiểm tra mạng.'); }
  if (raw && res.ok) return res;
  let data = null;
  try { data = await res.json(); } catch { /* không phải JSON */ }
  if (!res.ok) {
    const err = parseError(res.status, data);
    if (res.status === 401 && err.code === 'AUTH_REQUIRED' && window.onAuthLost) window.onAuthLost();
    throw err;
  }
  return data;
}

/* Tải lên có tiến độ (fetch chưa hỗ trợ upload progress) */
function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', API + path);
    x.withCredentials = true;
    x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let d = null; try { d = JSON.parse(x.responseText); } catch { /* bỏ qua */ }
      if (x.status >= 200 && x.status < 300) resolve(d);
      else {
        const err = parseError(x.status, d);
        if (x.status === 401 && err.code === 'AUTH_REQUIRED' && window.onAuthLost) window.onAuthLost();
        reject(err);
      }
    };
    x.onerror = () => reject(new ApiError(0, 'NETWORK', 'Mất kết nối khi tải lên.'));
    x.send(form);
  });
}

function toast(msg, ms = 3500) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg; t.setAttribute('role', 'status');
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
