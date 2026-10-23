// OncoScraper web UI. Plain JavaScript, no build step.
const $ = id => document.getElementById(id);
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function list(v) { return Array.isArray(v) ? v.join(', ') : (v ?? ''); }
function safeUrl(u) { return /^https?:\/\//i.test(u || '') ? u : '#'; }
function say(el, text, kind = 'info') { el.textContent = text || ''; el.className = 'msg ' + kind; }

const FIELD_NAMES = { urls: 'Start URL', max_pages: 'Max pages', max_depth: 'Max link depth', delay_seconds: 'Delay',
  pages_per_site: 'Pages per hospital', render: 'JavaScript option', url: 'URL' };

// Turn any server answer into {ok, data, error} with a readable error.
async function api(url, opts = {}) {
  let r;
  try { r = await fetch(url, opts); }
  catch (e) { return { ok: false, error: 'Cannot reach the scraper. Is the "serve" window still open?' }; }
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (r.ok) return { ok: true, data };
  let error = `Error ${r.status}`;
  const d = data && data.detail;
  if (typeof d === 'string') error = d;
  else if (Array.isArray(d)) error = d.map(x => `${FIELD_NAMES[x.loc?.[x.loc.length - 1]] || x.loc?.[x.loc.length - 1] || 'Value'}: ${x.msg}`).join('; ');
  return { ok: false, error, status: r.status };
}
const postJSON = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

// Download without leaving the page; show a message if the file is not ready.
async function download(url, msgEl, btn) {
  btn && (btn.disabled = true);
  try {
    const r = await fetch(url);
    if (!r.ok) {
      let t = `Download failed (${r.status})`;
      try { const j = await r.json(); if (j.detail) t = String(j.detail); } catch (e) {}
      say(msgEl, t === 'nothing found yet' ? 'Nothing found yet. Try again in a few seconds.' : t, 'bad');
      return;
    }
    const blob = await r.blob();
    const cd = r.headers.get('content-disposition') || '';
    const name = (cd.match(/filename="?([^";]+)"?/) || [])[1] || url.split('/').pop();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    say(msgEl, `Downloaded ${name}`, 'ok');
  } catch (e) {
    say(msgEl, 'Cannot reach the scraper. Is the "serve" window still open?', 'bad');
  } finally { btn && (btn.disabled = false); }
}

// Number boxes: check before sending, with the field name in the message.
function num(id, label, min, max) {
  const v = $(id).value.trim();
  const n = Number(v);
  if (v === '' || !Number.isFinite(n) || n < min || n > max) throw new Error(`${label} must be a number from ${min} to ${max}.`);
  return n;
}

// ---- results table (single crawl or batch) ------------------------------------
let rows = [], resultsFrom = '';
function showRows(newRows, from) {
  rows = newRows || []; resultsFrom = from; $('results-card').hidden = false;
  $('results-from').textContent = from ? `from ${from}` : '';
  render();
}
function render() {
  const q = $('filter').value.trim().toLowerCase();
  const shown = rows.filter(d => !q || JSON.stringify(d).toLowerCase().includes(q));
  $('count').textContent = `${shown.length} doctor${shown.length === 1 ? '' : 's'}`;
  if (!shown.length) {
    $('rows').innerHTML = `<tr><td colspan="11" class="empty">${rows.length ? 'No doctor matches this filter.' : 'No oncologists found yet.'}</td></tr>`;
    return;
  }
  $('rows').innerHTML = shown.map(d => `<tr><td><b>${esc(d.name)}</b>${d.page_type === 'profile' ? '' : ' <span class="pill">card</span>'}</td>
    <td>${esc(d.designation)}</td><td>${esc(d.specialty || list(d.sub_specialties))}</td>
    <td class="contact">${esc(d.phone)}</td><td class="contact">${esc(d.email)}</td>
    <td class="muted">${esc([d.contact_phone, d.contact_email].filter(Boolean).join('; '))}</td>
    <td>${esc(d.hospital || d.source_hospital)}</td><td>${esc(d.city)}</td><td>${esc(list(d.qualifications))}</td><td>${esc(d.experience_years)}</td>
    <td><a href="${esc(safeUrl(d.profile_url || d.source_url))}" target="_blank" rel="noopener">open</a></td></tr>`).join('');
}
$('filter').oninput = render;

// Pause keeps everything found so far and waits; Resume carries on from the same place.
async function togglePause(btn, base, msgEl) {
  const action = btn.textContent === 'Pause' ? 'pause' : 'resume';
  btn.disabled = true;
  const r = await postJSON(`${base}/${action}`);
  btn.disabled = false;
  if (r.ok) btn.textContent = action === 'pause' ? 'Resume' : 'Pause';
  else say(msgEl, r.error, 'bad');
}

// ---- single website crawl -------------------------------------------------------
let jobId = null, timer = null;
function setJobButtons(running) {
  $('start').disabled = running; $('stop').disabled = !running; $('pause').disabled = !running;
  if (!running) $('pause').textContent = 'Pause';
}
function attachJob(id) {
  jobId = id;
  $('progress-card').hidden = false; $('downloads').hidden = false; $('error').textContent = '';
  setJobButtons(true);
  clearInterval(timer); timer = setInterval(poll, 1500); poll();
}
$('start').onclick = async () => {
  const urls = $('urls').value.split(/\s+/).filter(Boolean);
  if (!urls.length) { say($('msg'), 'Enter at least one URL, for example https://www.hospital.in/find-a-doctor', 'bad'); $('urls').focus(); return; }
  let body;
  try {
    body = { urls, max_pages: num('max_pages', 'Max pages', 1, 5000), max_depth: num('depth', 'Max link depth', 0, 10),
      delay_seconds: num('delay', 'Delay', 0, 30), render: $('render').value, all_specialties: $('all').checked,
      use_sitemaps: $('sitemap').checked, include_subdomains: $('subdomains').checked };
  } catch (e) { say($('msg'), e.message, 'bad'); return; }
  $('start').disabled = true;
  const r = await postJSON('/api/jobs', body);
  if (!r.ok) { $('start').disabled = false; say($('msg'), r.error, 'bad'); return; }
  say($('msg'), 'Crawl started.', 'ok');
  $('log').textContent = '';
  showRows([], 'this website crawl');
  attachJob(r.data.id);
};
$('stop').onclick = async () => { if (jobId) await postJSON(`/api/jobs/${jobId}/stop`); $('stop').disabled = true; $('pause').disabled = true; };
$('pause').onclick = () => jobId && togglePause($('pause'), `/api/jobs/${jobId}`, $('msg'));
for (const fmt of ['xlsx', 'csv', 'json'])
  $('dl-' + fmt).onclick = () => jobId && download(`/api/jobs/${jobId}/download.${fmt}`, $('error'), $('dl-' + fmt));

async function poll() {
  const r = await api(`/api/jobs/${jobId}`);
  if (!r.ok) { say($('error'), r.error, 'bad'); return; }
  const j = r.data;
  $('state').textContent = j.state; $('state').className = 'state ' + j.state;
  $('s-fetched').textContent = j.progress.fetched; $('s-queued').textContent = j.progress.queued;
  $('s-doctors').textContent = j.progress.doctors_total; $('s-onco').textContent = j.progress.oncologists;
  $('s-time').textContent = j.elapsed + 's';
  $('bar').style.width = Math.min(100, 100 * j.progress.fetched / j.max_pages) + '%';
  const atBottom = $('log').scrollHeight - $('log').scrollTop - $('log').clientHeight < 30;
  $('log').innerHTML = j.log.map(l => {
    const cls = l.error ? 'x' : (l.type === 'profile' ? 'p' : (l.type === 'listing' ? 'l' : ''));
    return `<span class="${cls}">${esc((l.type || '').padEnd(8))} ${esc(l.status)} ${esc(l.url)}${l.doctors ? '  +' + l.doctors : ''}${l.error ? '  (' + esc(l.error) + ')' : ''}</span>`;
  }).join('\n');
  if (atBottom) $('log').scrollTop = $('log').scrollHeight;  // follow the log unless the user scrolled up
  if (resultsFrom === 'this website crawl' || !resultsFrom) showRows(j.doctors, 'this website crawl');
  $('pause').textContent = j.state === 'paused' ? 'Resume' : 'Pause';
  if (j.state !== 'running' && j.state !== 'paused') {
    clearInterval(timer); setJobButtons(false);
    if (j.error) say($('error'), j.error, 'bad');
    else say($('msg'), `Crawl ${j.state}. ${j.progress.oncologists} oncologists found.`, 'ok');
  }
}

// ---- test one page ---------------------------------------------------------------
$('page-btn').onclick = async () => {
  const url = $('page-url').value.trim();
  if (!/^https?:\/\//i.test(url)) { $('page-sum').hidden = false; $('page-sum').innerHTML = '<p class="msg bad">Enter a full address starting with http:// or https://</p>'; return; }
  $('page-btn').disabled = true; $('page-sum').hidden = false; $('page-sum').innerHTML = '<p class="msg info">Fetching…</p>';
  const r = await postJSON('/api/page', { url, render: $('render').value });
  $('page-btn').disabled = false;
  if (!r.ok) { $('page-sum').innerHTML = `<p class="msg bad">${esc(r.error)}</p>`; return; }
  const j = r.data;
  const docs = j.doctors || [];
  const head = j.error ? `<p class="msg bad">Could not read the page: ${esc(j.error)}</p>`
    : `<p class="msg ok">Page type: <b>${esc(j.page_type)}</b>${j.rendered ? ' (read with a real browser)' : ''} · ${docs.length} doctor${docs.length === 1 ? '' : 's'} found</p>`;
  const one = d => `<dl><dt>Name</dt><dd><b>${esc(d.name)}</b>${d.is_oncology ? ' <span class="pill">oncologist</span>' : ''}</dd>
    ${['designation', 'specialty', 'qualifications', 'experience_years', 'hospital', 'city', 'phone', 'email', 'contact_phone', 'contact_email', 'opd_timings', 'registration']
      .filter(k => d[k] && (!Array.isArray(d[k]) || d[k].length))
      .map(k => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${esc(list(d[k]))}</dd>`).join('')}</dl>`;
  $('page-sum').innerHTML = head + docs.slice(0, 10).map(one).join('<hr>') + (docs.length > 10 ? `<p class="hint">…and ${docs.length - 10} more</p>` : '');
  $('page-raw').hidden = false; $('page-out').textContent = JSON.stringify(j, null, 2);
};
$('page-url').onkeydown = e => { if (e.key === 'Enter') $('page-btn').click(); };

// ---- hospital list batch ------------------------------------------------------------
let batchId = null, batchTimer = null, batchPolls = 0;
$('b-template').onclick = () => download('/api/template.xlsx', $('b-msg'), $('b-template'));
async function fileBody() {
  const f = $('b-file').files[0];
  if (!f) { say($('b-msg'), 'Choose your hospital list (.xlsx or .csv) first.', 'bad'); return null; }
  if (!/\.(xlsx|xlsm|csv)$/i.test(f.name)) { say($('b-msg'), 'The file must be .xlsx or .csv, made from the blank template.', 'bad'); return null; }
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return { filename: f.name, content_base64: btoa(bin) };
}
$('b-file').onchange = () => { $('b-check-out').hidden = true; say($('b-msg'), ''); };
$('b-check').onclick = async () => {
  const body = await fileBody(); if (!body) return;
  $('b-check').disabled = true;
  const r = await postJSON('/api/sources/check', body);
  $('b-check').disabled = false;
  const out = $('b-check-out');
  if (!r.ok) { out.hidden = true; say($('b-msg'), r.error, 'bad'); return; }
  const j = r.data; out.hidden = false; say($('b-msg'), '');
  const skipped = p => /^skipped/.test(p.problem);
  const srcRows = j.sources.map(s => `<tr><td>${s.row}</td><td>${esc(s.hospital)}</td><td>${s.urls.length}</td><td>${esc(s.patterns.join(' '))}</td><td class="warn">${esc(s.warnings.join('; '))}</td></tr>`).join('');
  const probs = j.problems.map(p => `<tr><td>${p.row}</td><td>${esc(p.hospital)}</td><td colspan="3" class="${skipped(p) ? 'muted' : 'bad'}">${esc(p.problem)}</td></tr>`).join('');
  const fix = j.problems.filter(p => !skipped(p)).length, skip = j.problems.length - fix;
  out.innerHTML = `<p><b class="${j.usable ? 'ok' : 'bad'}">${j.usable} hospital${j.usable === 1 ? '' : 's'} ready</b>${fix ? `, <b class="bad">${fix} row${fix === 1 ? '' : 's'} need fixing</b>` : ''}${skip ? `, ${skip} skipped (Enabled = no)` : ''}</p>
    <div class="tablewrap" style="max-height:300px"><table><thead><tr><th>Row</th><th>Hospital</th><th>Start URLs</th><th>Profile pattern</th><th>Warnings / problems</th></tr></thead><tbody>${probs}${srcRows}</tbody></table></div>`;
};
function setBatchButtons(running) {
  $('b-start').disabled = running; $('b-stop').disabled = !running; $('b-pause').disabled = !running;
  if (!running) $('b-pause').textContent = 'Pause';
}
function attachBatch(id) {
  batchId = id; batchPolls = 0;
  $('b-progress').hidden = false; $('b-dl').hidden = false;
  $('b-dl-btn').textContent = 'Download results so far';
  $('b-totals').textContent = 'Partial file: finished hospitals plus the one being crawled now.';
  setBatchButtons(true);
  clearInterval(batchTimer); batchTimer = setInterval(pollBatch, 2000); pollBatch();
}
$('b-start').onclick = async () => {
  const body = await fileBody(); if (!body) return;
  try {
    Object.assign(body, { pages_per_site: num('b-pages', 'Pages per hospital', 1, 5000), delay_seconds: num('b-delay', 'Delay', 0, 30),
      workers: num('b-workers', 'Hospitals at the same time', 1, 8),
      render: $('b-render').value, all_specialties: $('b-all').checked, use_sitemaps: $('b-sitemap').checked });
  } catch (e) { say($('b-msg'), e.message, 'bad'); return; }
  $('b-start').disabled = true;
  const r = await postJSON('/api/batch', body);
  if (!r.ok) { $('b-start').disabled = false; say($('b-msg'), r.error, 'bad'); return; }
  $('b-sites').innerHTML = '';
  say($('b-msg'), `${r.data.hospitals} hospitals queued` + (r.data.problems.length ? `, ${r.data.problems.length} rows skipped` : '') + '.', 'ok');
  attachBatch(r.data.id);
};
$('b-stop').onclick = async () => { if (batchId) await postJSON(`/api/batch/${batchId}/stop`); $('b-stop').disabled = true; $('b-pause').disabled = true; };
$('b-pause').onclick = () => batchId && togglePause($('b-pause'), `/api/batch/${batchId}`, $('b-msg'));
$('b-dl-btn').onclick = () => batchId && download(`/api/batch/${batchId}/download.xlsx`, $('b-msg'), $('b-dl-btn'));
$('b-show').onclick = async () => { await loadBatchDoctors(); $('results-card').scrollIntoView({ behavior: 'smooth' }); };
async function loadBatchDoctors() {
  const r = await api(`/api/batch/${batchId}/doctors`);
  if (r.ok) showRows(r.data.doctors, 'the hospital list batch');
}
async function pollBatch() {
  const r = await api(`/api/batch/${batchId}`);
  if (!r.ok) { say($('b-msg'), r.error, 'bad'); return; }
  const j = r.data;
  $('b-idx').textContent = `${j.sites.length}/${j.total}`; $('b-time').textContent = j.elapsed + 's';
  $('b-pagesnow').textContent = j.progress.fetched || 0; $('b-onconow').textContent = j.progress.oncologists || 0;
  $('b-bar').style.width = (100 * j.sites.length / Math.max(1, j.total)) + '%';
  const now = (j.running || []).map(r => `${r.hospital} (${r.pages} pages)`).join(', ') || j.current;
  $('b-current').textContent = j.state === 'running' ? `Now crawling: ${now}`
    : j.state === 'paused' ? `Paused at: ${j.current} (press Resume to carry on)` : `Batch ${j.state}.`;
  $('b-pause').textContent = j.state === 'paused' ? 'Resume' : 'Pause';
  const wrap = $('b-wrap'), keep = wrap.scrollTop;  // keep the user's scroll position on refresh
  $('b-sites').innerHTML = j.sites.length ? j.sites.map(s => `<tr><td>${s.row}</td><td>${esc(s.hospital)}</td><td class="${(s.status === 'error' || s.status === 'failed') ? 'bad' : 'ok'}">${esc(s.status)}</td>
    <td>${s.pages}</td><td>${s.oncologists}</td><td>${s.with_phone}</td><td>${s.with_email}</td><td>${s.with_any_contact}</td><td class="muted">${esc(s.note)}</td></tr>`).join('')
    : `<tr><td colspan="9" class="empty">First hospital in progress…</td></tr>`;
  wrap.scrollTop = keep;
  if (resultsFrom === 'the hospital list batch' && (++batchPolls % 3 === 0)) loadBatchDoctors();  // refresh shown doctors
  if (j.state !== 'running' && j.state !== 'paused') {
    clearInterval(batchTimer); setBatchButtons(false);
    if (j.error) say($('b-msg'), j.error, 'bad');
    if (j.stats) {
      $('b-dl-btn').textContent = 'Download combined Excel';
      $('b-totals').textContent = `${j.stats.oncologists} oncologists: ${j.stats.oncologists_with_doctor_phone} with doctor phone, ${j.stats.oncologists_with_doctor_email} with doctor email, ${j.stats.oncologists_with_any_contact} with any contact.`;
    }
    if (resultsFrom === 'the hospital list batch') loadBatchDoctors();
  }
}

// ---- after a page reload, pick up runs that are still going --------------------------
(async () => {
  const r = await api('/api/active');
  if (!r.ok) return;
  const { job, batch } = r.data;
  if (batch) {
    attachBatch(batch.id);
    if (batch.state === 'running' || batch.state === 'paused')
      say($('b-msg'), 'Reconnected to the batch that is still running.', 'info');
  }
  if (job) {
    attachJob(job.id);
    if (job.state === 'running' || job.state === 'paused') say($('msg'), 'Reconnected to the crawl that is still running.', 'info');
  }
})();
