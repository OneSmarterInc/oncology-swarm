// Portal pages: Dashboard, Runs history and Doctor directory. Uses helpers from app.js.
const VIEWS = ['dashboard', 'new', 'runs', 'directory'];
let viewTimer = null;

function fmtTime(t) { if (!t) return ''; const d = new Date(t * 1000); return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
function fmtDur(a, b) {
  if (!a) return ''; const s = Math.max(0, Math.round(((b || Date.now() / 1000) - a)));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
}
const STATE_CLASS = { done: 'ok', running: 'ok', paused: 'warn', interrupted: 'warn', stopped: 'warn', failed: 'bad' };
function stateCell(s) { return `<span class="${STATE_CLASS[s] || ''}"><b>${esc(s)}</b></span>`; }

function show(view) {
  if (!VIEWS.includes(view)) view = 'dashboard';
  for (const v of VIEWS) $('view-' + v).hidden = v !== view;
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + view));
  clearInterval(viewTimer);
  if (view === 'dashboard') { loadDashboard(); viewTimer = setInterval(loadDashboard, 5000); }
  if (view === 'runs') { loadRuns(); viewTimer = setInterval(loadRuns, 4000); }
  if (view === 'directory') loadDirectory();
}
window.addEventListener('hashchange', () => show(location.hash.slice(1)));

// ---- dashboard ----------------------------------------------------------------
async function loadDashboard() {
  const [d, r] = await Promise.all([api('/api/dashboard'), api('/api/runs')]);
  if (!d.ok) { $('d-totals').innerHTML = `<p class="msg bad">${esc(d.error)}</p>`; return; }
  const t = d.data.totals;
  const pct = n => t.oncologists ? ` <small>(${Math.round(100 * n / t.oncologists)}%)</small>` : '';
  $('d-totals').innerHTML = [
    ['Oncologists', t.oncologists], ['Hospitals', t.hospitals], ['With doctor email', t.with_email + pct(t.with_email)],
    ['With doctor phone', t.with_phone + pct(t.with_phone)], ['With any contact', t.with_contact + pct(t.with_contact)],
    ['Runs', t.runs + (t.active_runs ? ` <small>(${t.active_runs} active)</small>` : '')],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join('');
  const hs = d.data.hospitals;
  $('d-hosp').innerHTML = hs.length ? hs.map(h => {
    const n = Math.max(1, h.onc), e = 100 * h.email / n, p = 100 * Math.max(0, h.phone - h.email) / n, o = 100 * Math.max(0, h.contact - Math.max(h.email, h.phone)) / n;
    return `<tr><td><a href="#directory" data-hosp="${esc(h.h)}">${esc(h.h)}</a></td><td>${h.onc}</td><td>${h.email}</td><td>${h.phone}</td><td>${h.contact}</td>
      <td><div class="cov" title="${Math.round(100 * h.contact / n)}% have some contact"><i class="e" style="width:${e}%"></i><i class="p" style="width:${p}%"></i><i class="o" style="width:${o}%"></i></div></td></tr>`;
  }).join('') + `<tr><td colspan="6" class="legend">Coverage:<b style="background:#1d7a3c"></b>doctor email<b style="background:#3f8fd6"></b>doctor phone<b style="background:#9bb7bd"></b>desk contact only</td></tr>`
    : `<tr><td colspan="6" class="empty">No oncologists saved yet. Start a run under <a href="#new">New run</a>.</td></tr>`;
  document.querySelectorAll('#d-hosp a[data-hosp]').forEach(a => a.onclick = () => { dirState.hospital = a.dataset.hosp; $('x-hosp').dataset.want = a.dataset.hosp; });
  if (r.ok) $('d-runs').innerHTML = r.data.runs.slice(0, 8).map(x => `<tr><td>${fmtTime(x.started)}</td><td>${esc(x.title)}</td><td>${stateCell(x.state)}</td><td>${x.oncologists_saved}</td></tr>`).join('')
    || `<tr><td colspan="4" class="empty">No runs yet.</td></tr>`;
}

// ---- runs history -----------------------------------------------------------------
const armed = new Set();
async function loadRuns() {
  const r = await api('/api/runs');
  if (!r.ok) { say($('r-msg'), r.error, 'bad'); return; }
  $('r-rows').innerHTML = r.data.runs.map(x => {
    const live = x.state === 'running' || x.state === 'paused';
    const acts = [];
    if (live) acts.push(`<button data-act="open" data-id="${x.id}" data-kind="${x.kind}">Open</button>`);
    acts.push(`<button class="secondary" data-act="dl" data-id="${x.id}">Excel</button>`);
    if (!live && x.kind === 'batch' && x.has_upload && x.state !== 'done') acts.push(`<button data-act="resume" data-id="${x.id}">Resume</button>`);
    if (!live && x.kind === 'crawl') acts.push(`<button class="secondary" data-act="rerun" data-id="${x.id}">Run again</button>`);
    if (!live) acts.push(`<button class="secondary" data-act="del" data-id="${x.id}">${armed.has(x.id) ? 'Click again to delete' : 'Delete'}</button>`);
    return `<tr><td>${fmtTime(x.started)}</td><td>${x.kind === 'batch' ? 'Hospital list' : 'One website'}</td><td>${esc(x.title)}</td>
      <td>${stateCell(x.state)}${x.error ? `<div class="muted">${esc(x.error)}</div>` : ''}</td><td>${fmtDur(x.started, live ? null : x.finished)}</td>
      <td>${x.oncologists_saved}</td><td class="actions">${acts.join('')}</td></tr>`;
  }).join('') || `<tr><td colspan="7" class="empty">No runs yet. Start one under <a href="#new">New run</a>.</td></tr>`;
}
$('r-rows').onclick = async e => {
  const b = e.target.closest('button[data-act]'); if (!b) return;
  const id = b.dataset.id, act = b.dataset.act;
  if (act === 'open') { location.hash = '#new'; b.dataset.kind === 'batch' ? attachBatch(id) : attachJob(id); return; }
  if (act === 'dl') { download(`/api/runs/${id}/download.xlsx`, $('r-msg'), b); return; }
  if (act === 'del') {
    if (!armed.has(id)) { armed.add(id); b.textContent = 'Click again to delete'; setTimeout(() => armed.delete(id), 5000); return; }
    const r = await api(`/api/runs/${id}`, { method: 'DELETE' });
    say($('r-msg'), r.ok ? 'Run deleted.' : r.error, r.ok ? 'ok' : 'bad'); armed.delete(id); loadRuns(); return;
  }
  b.disabled = true;
  const r = await postJSON(`/api/runs/${id}/${act}`);
  b.disabled = false;
  if (!r.ok) { say($('r-msg'), r.error, 'bad'); return; }
  location.hash = '#new';
  if (act === 'resume') { say($('b-msg'), `Resumed: finished hospitals are kept, ${r.data.hospitals} in the list.`, 'ok'); attachBatch(r.data.id); }
  else { showRows([], 'this website crawl'); attachJob(r.data.id); }
};

// ---- doctor directory ------------------------------------------------------------------
const dirState = { page: 1, hospital: '' };
function dirQuery() {
  const p = new URLSearchParams();
  const v = { q: $('x-q').value, city: $('x-city').value, hospital: $('x-hosp').value || dirState.hospital, specialty: $('x-spec').value };
  for (const [k, x] of Object.entries(v)) if (x) p.set(k, x);
  for (const [k, id] of [['has_email', 'x-email'], ['has_phone', 'x-phone'], ['has_contact', 'x-contact'], ['all_doctors', 'x-all']])
    if ($(id).checked) p.set(k, 'true');
  return p;
}
function fillSelect(sel, values, label) {
  const want = sel.dataset.want || sel.value;
  sel.innerHTML = `<option value="">${label}</option>` + values.map(v => `<option${v === want ? ' selected' : ''}>${esc(v)}</option>`).join('');
  delete sel.dataset.want;
}
async function loadDirectory() {
  const p = dirQuery(); p.set('page', dirState.page);
  const r = await api('/api/directory?' + p);
  if (!r.ok) { say($('x-msg'), r.error, 'bad'); return; }
  const j = r.data; say($('x-msg'), '');
  fillSelect($('x-city'), j.facets.cities, 'All cities');
  fillSelect($('x-hosp'), j.facets.hospitals, 'All hospitals');
  dirState.hospital = '';
  $('x-count').textContent = `${j.total} doctor${j.total === 1 ? '' : 's'}`;
  $('x-page').textContent = `Page ${j.page} of ${j.pages}`;
  $('x-prev').disabled = j.page <= 1; $('x-next').disabled = j.page >= j.pages;
  $('x-rows').innerHTML = j.doctors.map(d => `<tr><td><b>${esc(d.name)}</b></td><td>${esc(d.specialty || list(d.sub_specialties))}</td><td>${esc(d.designation)}</td>
    <td class="contact">${esc(d.phone)}</td><td class="contact">${esc(d.email)}</td><td class="muted">${esc([d.contact_phone, d.contact_email].filter(Boolean).join('; '))}</td>
    <td>${esc(d.source_hospital || d.hospital)}</td><td>${esc(d.city)}</td><td>${esc(list(d.qualifications))}</td>
    <td><a href="${esc(safeUrl(d.profile_url || d.source_url))}" target="_blank" rel="noopener">open</a></td></tr>`).join('')
    || `<tr><td colspan="10" class="empty">${j.total ? '' : 'No doctors match. Clear the filters or run a crawl first.'}</td></tr>`;
}
let dirDebounce = null;
const dirChanged = () => { dirState.page = 1; clearTimeout(dirDebounce); dirDebounce = setTimeout(loadDirectory, 300); };
for (const id of ['x-q', 'x-spec']) $(id).oninput = dirChanged;
for (const id of ['x-city', 'x-hosp', 'x-email', 'x-phone', 'x-contact', 'x-all']) $(id).onchange = dirChanged;
$('x-prev').onclick = () => { dirState.page--; loadDirectory(); };
$('x-next').onclick = () => { dirState.page++; loadDirectory(); };
$('x-reset').onclick = () => {
  for (const id of ['x-q', 'x-spec', 'x-city', 'x-hosp']) $(id).value = '';
  for (const id of ['x-email', 'x-phone', 'x-contact', 'x-all']) $(id).checked = false;
  dirChanged();
};
$('x-xlsx').onclick = () => download('/api/directory/download.xlsx?' + dirQuery(), $('x-msg'), $('x-xlsx'));
$('x-csv').onclick = () => download('/api/directory/download.csv?' + dirQuery(), $('x-msg'), $('x-csv'));

show(location.hash.slice(1) || 'dashboard');
