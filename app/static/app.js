'use strict';
let token = '', selectedId = null, selectedVersion = null, currentJob = null;
let draft = null, dirty = false, rawDirty = false, editRevision = 0, offset = 0, pageJobs = [], total = 0;
let sessionEpoch = 0, listSerial = 0, selectSerial = 0, mergeTarget = null;
const selectedBatch = new Map(), pending = new Set();
const checklist = ['check-identity', 'check-evidence', 'check-contacts', 'check-registration', 'check-duplicates'];
const $ = id => document.getElementById(id);
const clone = value => JSON.parse(JSON.stringify(value));
function message(text, bad=false) { $('message').textContent = text; $('message').className = bad ? 'error' : 'notice'; }
function el(tag, text, className) { const node=document.createElement(tag); if (text !== undefined) node.textContent=text; if (className) node.className=className; return node; }
async function request(path, method='GET', body) {
  if (!token) throw Error('Enter your admin token first');
  const epoch=sessionEpoch, headers={'Authorization':'Bearer '+token};
  const options={method, headers, credentials:'omit'};
  if (body !== undefined) { headers['Content-Type']='application/json'; options.body=JSON.stringify(body); }
  const response=await fetch(path, options);
  if (epoch !== sessionEpoch) throw Error('Session changed; response discarded');
  if (!response.ok) { let e; try { e=await response.json(); } catch { e={detail:response.statusText}; } throw Error(typeof e.detail==='string' ? e.detail : JSON.stringify(e.detail)); }
  return response;
}
async function api(path, method='GET', body) { const epoch=sessionEpoch; const data=await (await request(path,method,body)).json(); if (epoch!==sessionEpoch) throw Error('Session changed; response discarded'); return data; }
async function run(fn) { try { return await fn(); } catch(e) { message(e.message,true); } }
function bind(id, fn) { $(id).onclick=()=>run(async()=>{ if (pending.has(id)) return; pending.add(id); $(id).disabled=true; try { await fn(); } finally { pending.delete(id); $(id).disabled=false; updateActions(); } }); }
function clearChecklist() { for (const id of checklist) $(id).checked=false; $('duplicate').checked=false; }
function updateActions() {
  const editable=Boolean(currentJob && currentJob.state==='review');
  const saved=Boolean(currentJob && currentJob.proposal && !dirty && !rawDirty);
  $('save').disabled=!editable || pending.has('save');
  $('load-json').disabled=!editable || pending.has('load-json');
  $('approve').disabled=!editable || !saved || pending.has('approve') || Boolean(currentJob && currentJob.proposal.validation_errors.length);
  $('reject').disabled=!editable || !saved || pending.has('reject');
  $('correct').disabled=!saved || !currentJob || !['approved','withdrawn'].includes(currentJob.state) || pending.has('correct');
  $('withdraw').disabled=!saved || !currentJob || !['approved','merged','review'].includes(currentJob.state) || pending.has('withdraw');
  $('refresh-source').disabled=!saved || !currentJob || currentJob.state!=='approved' || pending.has('refresh-source');
  $('merge').disabled=!saved || !currentJob || currentJob.state!=='approved' || !mergeTarget || pending.has('merge');
  $('unmerge').disabled=!saved || !currentJob || !['merged','withdrawn'].includes(currentJob.state) || !mergeTarget || pending.has('unmerge');
  $('bulk-approve').disabled=!selectedBatch.size || pending.has('bulk-approve');
  $('previous').disabled=offset===0 || pending.has('previous');
  $('next').disabled=offset+pageJobs.length>=total || pending.has('next');
}
function resetDraftChecks() { editRevision++; clearChecklist(); selectedBatch.delete(selectedId); updateBulkCount(); dirty=true; updateActions(); }
function updateBulkCount() { $('bulk-count').textContent=selectedBatch.size+' selected (maximum 25)'; $('bulk-ack').checked=false; }
function queryString() {
  const params=new URLSearchParams({offset:String(offset),limit:$('page-size').value || '25'});
  for (const key of ['q','state','host','validation']) if ($('filter-'+key).value.trim()) params.set(key,$('filter-'+key).value.trim());
  return params.toString();
}
async function refresh() {
  const serial=++listSerial;
  const [status, jobs]=await Promise.all([api('/api/status'),api('/api/jobs?'+queryString())]);
  if (serial!==listSerial) return;
  $('status').textContent=JSON.stringify(status,null,2);
  pageJobs=jobs.items; total=jobs.total;
  // A changed or ineligible version must be explicitly selected again.
  for (const j of pageJobs) if (selectedBatch.has(j.id) && (!j.bulk_eligible || selectedBatch.get(j.id)!==j.version)) selectedBatch.delete(j.id);
  renderJobs(); updateBulkCount(); updateActions();
}
function renderJobs() {
  $('jobs').replaceChildren();
  for (const j of pageJobs) {
    const row=el('div',undefined,'job-row'+(j.id===selectedId?' current':''));
    const button=el('button','#'+j.id+' · '+j.state+' · '+(j.name || j.url),'job secondary');
    button.addEventListener('click',()=>run(()=>select(j.id))); row.append(button);
    row.append(el('p',j.url,'job-meta muted'));
    row.append(el('p',j.version ? 'v'+j.version+' · '+j.validation_error_count+' validation error(s) · '+j.duplicate_job_ids.length+' potential duplicate(s)' : 'No proposal yet','job-meta'));
    if (j.bulk_eligible) {
      const label=el('label',undefined,'checkbox'); const check=el('input'); check.type='checkbox'; check.checked=selectedBatch.has(j.id);
      check.addEventListener('change',()=>{
        if (check.checked) {
          if (selectedBatch.size>=25) {check.checked=false;message('Select at most 25 proposals per batch',true);return;}
          if (j.id===selectedId && (dirty || rawDirty)) {check.checked=false;message('Save and validate this proposal before selecting it',true);return;}
          selectedBatch.set(j.id,j.version);
        } else selectedBatch.delete(j.id);
        updateBulkCount();updateActions();
      });
      label.append(check,el('span','Select reviewed version '+j.version));row.append(label);
    }
    if (j.last_error) row.append(el('p',j.last_error,'error'));
    $('jobs').append(row);
  }
  if (!pageJobs.length) $('jobs').append(el('p','No jobs match these filters.'));
  $('page-summary').textContent=total ? (offset+1)+'–'+Math.min(offset+pageJobs.length,total)+' of '+total+' jobs' : '0 jobs';
}
function setAt(path, value) { let node=draft; for (const key of path.slice(0,-1)) node=node[key]; node[path[path.length-1]]=value; }
function syncJSON() { $('proposal').value=JSON.stringify(draft,null,2); rawDirty=false; }
function evidenceFor(path) { return Array.isArray(draft.evidence) ? draft.evidence.filter(item=>item && item.field===path.join('.')) : []; }
function renderValue(parent, path, value) {
  const field=path.join('.');
  if (value!==null && typeof value==='object') {
    const group=el('fieldset',undefined,'form-section'); group.append(el('legend',field));
    const entries=Object.entries(value);
    if (!entries.length) group.append(el('p','No values recorded. Use advanced JSON to add evidence-backed records.','empty'));
    for (const [key,child] of entries) renderValue(group,[...path,key],child);
    parent.append(group); return;
  }
  const label=el('label',undefined,'field-label'); label.append(el('span',field));
  const isQuote=path[path.length-1]==='support_text';
  const input=el(isQuote?'textarea':'input');
  input.setAttribute('data-field',field);
  if (typeof value==='boolean') { input.type='checkbox'; input.checked=value; }
  else { input.type=typeof value==='number'?'number':'text'; input.value=value===null?'':String(value); }
  if (isQuote) input.rows=3;
  input.disabled=!currentJob || currentJob.state!=='review' || ['schema_version','synthetic'].includes(field);
  input.addEventListener('input',()=>{
    let next;
    if (typeof value==='boolean') next=input.checked;
    else if (typeof value==='number') next=input.value===''?null:Number(input.value);
    else next=input.value==='' && (value===null || field.startsWith('doctor.') && field!=='doctor.name') ? null : input.value;
    setAt(path,next);syncJSON();resetDraftChecks();
  });
  label.append(input);
  if (value===null) label.append(el('span','Not recorded. Only enter a value supported by source evidence.','field-help'));
  parent.append(label);
  if (path[0]!=='evidence') for (const evidence of evidenceFor(path)) {
    parent.append(el('div','Evidence '+evidence.id+': '+evidence.support_text,'evidence-quote'));
  }
}
function renderForm() {
  $('proposal-form').replaceChildren();
  if (!draft || typeof draft!=='object' || Array.isArray(draft)) throw Error('Proposal JSON must be an object');
  for (const [key,value] of Object.entries(draft)) {
    if (key==='evidence') {
      const details=el('details');details.append(el('summary','Edit evidence quotes and provenance ('+(Array.isArray(value)?value.length:0)+')'));renderValue(details,[key],value);$('proposal-form').append(details);
    } else renderValue($('proposal-form'),[key],value);
  }
}
async function select(id, force=false) {
  if (!force && (dirty || rawDirty) && !window.confirm('Discard unsaved proposal edits?')) return;
  const serial=++selectSerial, j=await api('/api/jobs/'+id);
  if (serial!==selectSerial) return;
  selectedId=id;currentJob=j;selectedVersion=j.proposal ? j.proposal.version : null;dirty=false;rawDirty=false;mergeTarget=null;
  $('merge-preview').hidden=true;$('merge-ack').checked=false;$('refresh-charge').checked=false;clearChecklist();
  $('selected').textContent='#'+id+' · '+j.state+(selectedVersion?' · version '+selectedVersion:'');$('review').hidden=!(j.proposal || j.source);
  $('source').textContent='';$('source-link').removeAttribute('href');
  if (j.source) {
    // Never turn an arbitrary proposal string into a navigable link.
    try {const sourceURL=new URL(j.source.url);if(sourceURL.protocol==='https:' && !sourceURL.username && !sourceURL.password) $('source-link').href=sourceURL.href;} catch {}
    $('source').textContent='Retrieved: '+j.source.retrieved_at+'\nSHA-256: '+j.source.content_sha256+'\nSource type: '+(j.source.source_type || 'unspecified')+'\n\n'+j.source.text;
  }
  if (!j.proposal) {draft=null;$('proposal-form').replaceChildren();$('proposal').value='';$('checks').textContent='No proposal produced. Any retained snapshot is shown above; no approval is possible.';updateActions();renderJobs();message(j.last_error || 'Waiting for worker to fetch and prepare a proposal');return;}
  $('checks').textContent=JSON.stringify({errors:j.proposal.validation_errors,possible_duplicate_job_ids:j.proposal.duplicate_job_ids,synthetic:j.source ? j.source.synthetic : null},null,2);
  draft=clone(j.proposal.data);syncJSON();renderForm();updateActions();renderJobs();
}
function reviewBody() {
  const reviewer=$('reviewer').value.trim(), note=$('note').value.trim();
  if (!reviewer || !note) throw Error('Enter a reviewer audit label and meaningful review note');
  if (!selectedVersion) throw Error('Select a proposal first');
  if (dirty || rawDirty) throw Error('Save and validate your edits before reviewing');
  return {expected_version:selectedVersion,reviewer,note,duplicate_acknowledged:$('duplicate').checked};
}
function actorBody(noteId='note') { const reviewer=$('reviewer').value.trim(),note=$(noteId).value.trim();if(!reviewer || !note) throw Error('Enter a reviewer audit label and reason');return {reviewer,note}; }
bind('connect',async()=>{const entered=$('token').value.trim();if(entered && entered!==token){token=entered;sessionEpoch++;} $('token').value='';await refresh();message('Connected. Review each source before approving.');});
$('forget').onclick=()=>{token='';sessionEpoch++;listSerial++;selectSerial++;selectedId=null;selectedVersion=null;currentJob=null;draft=null;dirty=false;rawDirty=false;pageJobs=[];total=0;selectedBatch.clear();$('token').value='';$('jobs').replaceChildren();$('proposal-form').replaceChildren();$('proposal').value='';$('review').hidden=true;$('status').textContent='Disconnected';$('sources').replaceChildren();$('source').textContent='';$('source-link').removeAttribute('href');$('checks').textContent='';$('metrics-output').textContent='';$('discovery-output').textContent='';$('merge-preview').textContent='';$('selected').textContent='Select a job to inspect its source and proposal';updateBulkCount();updateActions();message('Token forgotten');};
$('demo').onclick=()=>{$('urls').value='https://demo.example.invalid/doctors/asha-rao\nhttps://demo.example.invalid/doctors/vikram-sen';};
bind('enqueue',async()=>{const urls=$('urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);const result=await api('/api/jobs','POST',{urls});await refresh();message(JSON.stringify(result));});
for (const [id,paused] of [['pause',true],['resume',false]]) bind(id,async()=>{await api('/api/control','POST',{paused});await refresh();message(paused?'New fetching paused; in-flight work can finish':'Fetching resumed');});
bind('apply-filters',async()=>{offset=0;await refresh();});
bind('clear-filters',async()=>{for(const key of ['q','state','host','validation']) $('filter-'+key).value='';offset=0;await refresh();});
bind('previous',async()=>{offset=Math.max(0,offset-Number($('page-size').value || 25));await refresh();});
bind('next',async()=>{offset+=Number($('page-size').value || 25);await refresh();});
$('page-size').addEventListener('change',()=>run(async()=>{offset=0;await refresh();}));
$('filter-q').addEventListener('keydown',event=>{if(event.key==='Enter') $('apply-filters').click();});
$('proposal').addEventListener('input',()=>{rawDirty=true;resetDraftChecks();});
bind('load-json',async()=>{if(!currentJob || currentJob.state!=='review') throw Error('Only proposals awaiting review can be edited');const parsed=JSON.parse($('proposal').value);if(!parsed || typeof parsed!=='object' || Array.isArray(parsed)) throw Error('Proposal JSON must be an object');draft=parsed;syncJSON();renderForm();resetDraftChecks();message('JSON loaded. Save and validate before review.');});
bind('save',async()=>{
  if(rawDirty) throw Error('Load JSON into form before saving your JSON edits');
  const id=selectedId,version=selectedVersion,revision=editRevision;
  await api('/api/jobs/'+id+'/proposal','PUT',{data:draft,expected_version:version});
  if(selectedId===id){
    if(editRevision===revision){dirty=false;await select(id,true);}
    else {selectedVersion=version+1;currentJob.proposal.version=selectedVersion;}
  }
  await refresh();message(dirty?'Saved the submitted version. Newer edits remain unsaved.':'Saved. Inspect the updated validation checks.');
});
for (const action of ['approve','reject']) bind(action,async()=>{
  const body=reviewBody();if(action==='approve' && !checklist.every(id=>$(id).checked)) throw Error('Complete every review-checklist item before approving');
  const id=selectedId;await api('/api/jobs/'+id+'/'+action,'POST',body);selectedBatch.delete(id);if(selectedId===id)await select(id,true);await refresh();message(action==='approve'?'Approved and included in Excel export':'Rejected; retained in audit history');
});
bind('bulk-approve',async()=>{
  if(!selectedBatch.size) throw Error('Select reviewed proposals first');if(!$('bulk-ack').checked) throw Error('Explicitly acknowledge reviewing every selected version first');
  const body={...actorBody(),items:Array.from(selectedBatch,([id,expected_version])=>({id,expected_version})),human_acknowledged:true};
  const result=await api('/api/jobs/bulk-approve','POST',body);selectedBatch.clear();await refresh();if(selectedId && !dirty && !rawDirty) await select(selectedId,true);message(JSON.stringify(result));
});
$('bulk-clear').onclick=()=>{selectedBatch.clear();updateBulkCount();renderJobs();updateActions();};
for(const action of ['withdraw','correct']) bind(action,async()=>{const body=reviewBody();if(!window.confirm(action==='withdraw'?'Withdraw this doctor from future exports?':'Remove this doctor from exports and reopen for correction?')) return;const id=selectedId;await api('/api/jobs/'+id+'/'+action,'POST',body);if(selectedId===id)await select(id,true);await refresh();message(action==='withdraw'?'Withdrawn from future exports; audit history retained':'Reopened for correction. A new approval is required for export.');});
bind('refresh-source',async()=>{const body={...reviewBody(),acknowledge_possible_additional_model_charge:$('refresh-charge').checked};const result=await api('/api/jobs/'+selectedId+'/refresh','POST',body);await refresh();message('Refresh queued: '+JSON.stringify(result));});
bind('inspect-merge',async()=>{
  const targetId=Number($('merge-target').value);if(!Number.isInteger(targetId) || targetId<1 || targetId===selectedId) throw Error('Enter a different approved target job ID');
  const target=await api('/api/jobs/'+targetId);if(!['approved','withdrawn'].includes(target.state) || !target.proposal) throw Error('Choose an approved merge target or withdrawn split target');
  mergeTarget=target;$('merge-ack').checked=false;$('merge-preview').hidden=false;$('merge-preview').textContent=JSON.stringify({id:target.id,version:target.proposal.version,data:target.proposal.data,source:target.source},null,2);
});
$('merge-target').addEventListener('input',()=>{mergeTarget=null;$('merge-ack').checked=false;updateActions();});
bind('merge',async()=>{if(!mergeTarget || !$('merge-ack').checked) throw Error('Inspect the target and acknowledge the same identity first');const body={...reviewBody(),target_job_id:mergeTarget.id,target_expected_version:mergeTarget.proposal.version};const result=await api('/api/jobs/'+selectedId+'/merge','POST',body);await select(selectedId,true);await refresh();message('Merge result: '+JSON.stringify(result));});
bind('unmerge',async()=>{if(!mergeTarget)throw Error('Inspect the existing merge target first');if(!window.confirm('Split this contributor from the target and withdraw both groups? Each individual needs correction and fresh approval before export.'))return;const body={...reviewBody(),target_job_id:mergeTarget.id,target_expected_version:mergeTarget.proposal.version};await api('/api/jobs/'+selectedId+'/unmerge','POST',body);await select(selectedId,true);await refresh();message('Split completed. Both groups are withdrawn pending correction and fresh approval.');});
bind('metrics',async()=>{$('metrics-output').textContent=JSON.stringify(await api('/api/metrics'),null,2);$('metrics-output').hidden=false;});
bind('reset-budget',async()=>{const body=actorBody('budget-note');if(!window.confirm('Start a new audited budget run? This allows further requests and model calls up to configured limits.')) return;const result=await api('/api/control/reset-budgets','POST',body);await refresh();message('New budget run: '+JSON.stringify(result));});
async function loadSources(){const sources=await api('/api/sources');$('sources').replaceChildren();for(const source of sources.items || sources){$('sources').append(el('p','#'+source.id+' · '+source.name+' · '+source.hostname+' · '+(source.city || '')));}if(!(sources.items || sources).length)$('sources').textContent='No official sources approved yet.';}
bind('load-sources',loadSources);
bind('add-source',async()=>{const body={...actorBody('source-note'),name:$('source-name').value.trim(),city:$('source-city').value.trim(),hostname:$('source-host').value.trim(),list_url:$('source-list-url').value.trim(),sitemap_url:$('source-sitemap').value.trim() || null,profile_url_pattern:$('source-pattern').value.trim() || null};await api('/api/sources','POST',body);await loadSources();message('Official source recorded with approval and reuse note');});
bind('discover',async()=>{const id=Number($('discover-source').value);if(!Number.isInteger(id)||id<1)throw Error('Enter an approved source ID');$('discovery-output').hidden=false;$('discovery-output').textContent='Discovery running…';const result=await api('/api/sources/'+id+'/discover','POST',{max_pages:Number($('discover-pages').value),max_profiles:Number($('discover-profiles').value),use_sitemap:$('discover-sitemap').checked});$('discovery-output').textContent=JSON.stringify(result,null,2);await refresh();message('Discovery finished. Inspect its result for per-page errors and limits.');});
bind('export',async()=>{const epoch=sessionEpoch,response=await request('/api/export.xlsx'),blob=await response.blob();if(epoch!==sessionEpoch)throw Error('Session changed; download discarded');const url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download='approved_directory.xlsx';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Export downloaded; only eligible human-approved records are included');});
document.addEventListener('keydown',event=>{
  if(!event.altKey || event.ctrlKey || event.metaKey || event.repeat) return;
  const key=event.key.toLowerCase();if(!['j','k','s'].includes(key))return;
  event.preventDefault();
  if(key==='s'){if(!$('save').disabled)$('save').click();return;}
  const index=pageJobs.findIndex(job=>job.id===selectedId),next=key==='j'?index+1:(index<0?pageJobs.length-1:index-1);
  if(pageJobs[next])run(()=>select(pageJobs[next].id));
});
updateActions();
