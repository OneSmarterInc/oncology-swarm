'use strict';
let token='', categories={}, activeView='dashboard', selectedRun=null, directoryRun=null;
let offset=0, historyOffset=0, uploaded=[], uploadRequest=0, uploadPending=false;
let viewRequest=0, runRequest=0, directoryRequest=0, refreshBusy=false, actionKey='', searchTimer;
const $=id=>document.getElementById(id);
const el=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
function attempt(fn){return async(...args)=>{try{await fn(...args);}catch(e){notice(e.message);}};}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Session-Token':token,...options.headers}});
  if(!response.ok){let data;try{data=await response.json();}catch{throw Error(`Request failed (${response.status}).`);}
    const detail=data.detail;
    throw Error(Array.isArray(detail)?detail.map(e=>`${e.loc.filter(x=>x!=='body').join(' / ')}: ${e.msg}`).join('\n'):detail||`Request failed (${response.status}).`);
  }
  return response.json();
}
function button(text,fn,cls='secondary'){const n=el('button',text,cls);n.type='button';n.onclick=attempt(async()=>{n.disabled=true;try{await fn();}finally{n.disabled=false;}});return n;}
function link(text,url){const n=el('a',text);if(/^https?:\/\//i.test(url)){n.href=url;n.target='_blank';n.rel='noopener noreferrer';}return n;}
function badge(status){if(status==='completed_with_issues')status='completed';return el('span',({completed:'Completed'})[status]||status.replaceAll('_',' '),'badge '+status);}
function table(headers,rows){const t=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');headers.forEach(h=>tr.append(el('th',h)));head.append(tr);rows.forEach(cells=>{const r=el('tr');cells.forEach(c=>{const td=el('td');td.append(c instanceof Node?c:document.createTextNode(String(c)));r.append(td);});body.append(r);});if(!rows.length){const r=el('tr'),td=el('td','No results yet.','empty');td.colSpan=headers.length;r.append(td);body.append(r);}t.append(head,body);return t;}
function stat(label,value){const n=el('div','','stat');n.append(el('span',label),el('strong',value));return n;}
function clearUpload(){uploadRequest++;uploaded=[];uploadPending=false;$('source-file').value='';$('upload-status').textContent='';$('upload-preview').replaceChildren();$('upload-preview').hidden=true;}
function freshRun(){clearUpload();$('run-form').reset();$('page-test-form').reset();$('page-test-status').textContent='';$('page-test-results').replaceChildren();$('page-test-results').hidden=true;$('page-test-raw').hidden=true;}
function resetFilters(){clearTimeout(searchTimer);$('search').value='';for(const id of ['filter-org','filter-city','filter-category'])$(id).value='';for(const id of ['email-only','phone-only','contact-only'])$(id).checked=false;offset=0;directoryRequest++;}
async function show(view,{fresh=false,reset=false}={}){
  const request=++viewRequest;notice('');activeView=view;runRequest++;actionKey='';
  if(fresh)freshRun();if(reset)resetFilters();
  if(view==='run-detail'){$('run-summary').replaceChildren(el('p','Loading run…'));$('run-actions').replaceChildren();}
  document.querySelectorAll('.view').forEach(n=>n.hidden=n.id!==view);
  document.querySelectorAll('.nav').forEach(n=>{const on=n.dataset.view===view||(view==='run-detail'&&n.dataset.view==='new');n.classList.toggle('active',on);if(on)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});
  const hash=view==='run-detail'?`#run/${selectedRun}`:`#${view}`;history.replaceState(null,'',hash);
  if(view==='dashboard')await dashboard(request);
  if(view==='runs'){historyOffset=0;await runList(request);}
  if(view==='run-detail')await runDetail();
  if(view==='directory'){await facets();if(request===viewRequest)await directory();}
}
async function openRun(id){selectedRun=id;await show('run-detail');}
function runCard(run){const row=el('div','','run-card'),info=el('div');info.append(el('strong',run.name),el('small',`${run.pages_processed} pages · ${run.websites} websites · ${new Date(run.created_at).toLocaleString()}`));row.append(info,badge(run.status),button('Open',()=>openRun(run.id)));return row;}
async function dashboard(request=viewRequest){const [summary,history]=await Promise.all([api('/api/summary'),api('/api/history?limit=5')]);if(activeView!=='dashboard'||request!==viewRequest)return;
  $('metrics').replaceChildren(stat('People discovered',summary.people),stat('Own emails',summary.individual_emails),stat('Organizations',summary.firms.length));
  $('firm-rows').replaceChildren();for(const firm of summary.firms){const row=el('tr'),name=el('td');name.append(button(firm.organization,async()=>{directoryRun=null;await show('directory',{reset:true});$('filter-org').value=firm.organization;await directory();}));row.append(name);for(const v of [firm.people,firm.email,firm.phone,firm.contact])row.append(el('td',v));const coverage=el('td'),bar=el('progress');bar.max=firm.people||1;bar.value=firm.contact;bar.className='coverage';bar.setAttribute('aria-label',`${firm.organization} contact coverage`);coverage.append(bar,el('small',`${Math.round(100*firm.contact/(firm.people||1))}% have a published contact`));row.append(coverage);$('firm-rows').append(row);}
  if(!summary.firms.length){const r=el('tr'),td=el('td','No organizations yet. Start a new run.','empty');td.colSpan=6;r.append(td);$('firm-rows').append(r);}
  $('recent-runs').replaceChildren(...history.items.map(runCard));$('database-path').textContent=summary.database;
}
async function runList(request=viewRequest){const start=historyOffset;const data=await api(`/api/history?offset=${start}&limit=25`);if(activeView!=='runs'||request!==viewRequest||start!==historyOffset)return;
  const rows=data.items.map(r=>{const actions=el('div','','actions');actions.append(button('Open',()=>openRun(r.id)),button('Excel',()=>download('xlsx',new URLSearchParams({rid:r.id}))));
    actions.append(button('Run again',async()=>{const run=await api(`/api/runs/${r.id}/clone`,{method:'POST'});await openRun(run.id);}));
    if(['paused','stopped','interrupted','failed'].includes(r.status))actions.append(button('Resume',async()=>{await api(`/api/runs/${r.id}/resume`,{method:'POST'});await openRun(r.id);}));
    if(!['queued','running','pausing','stopping'].includes(r.status))actions.append(button('Delete',async()=>{if(!confirm(`Delete “${r.name}” and its saved results?`))return;await api(`/api/runs/${r.id}`,{method:'DELETE'});if(historyOffset&&data.items.length===1)historyOffset=Math.max(0,historyOffset-25);await runList();}));
    return [new Date(r.created_at).toLocaleString(),r.name,badge(r.status),r.pages_processed,r.people,actions];});
  $('run-list').replaceChildren(table(['Started','Run','Status','Pages','People','Actions'],rows));$('runs-page').textContent=`Page ${Math.floor(start/25)+1} · ${data.total} runs`;$('runs-prev').disabled=start===0;$('runs-next').disabled=start+25>=data.total;
}
async function runDetail(){const id=selectedRun,request=++runRequest,view=viewRequest;if(!id)return;
  const [run,pages]=await Promise.all([api(`/api/runs/${id}`),api(`/api/runs/${id}/pages?limit=100`)]);
  if(id!==selectedRun||request!==runRequest||view!==viewRequest||activeView!=='run-detail')return;
  const g=run.progress,done=['completed','completed_with_issues'].includes(run.status),active=['queued','running','pausing','stopping'].includes(run.status);
  const progress=el('progress');progress.max=100;progress.value=g.percent;progress.className='crawl-progress';progress.setAttribute('aria-label','Extraction progress');
  const metrics=el('div','','stats');metrics.append(stat('Pages finished',run.pages_processed),stat('Known remaining',g.waiting+g.reading.length),stat('Successful',g.successful),stat('Failed',g.failed),stat('Elapsed',duration(run.created_at,active?new Date():new Date(run.updated_at))));
  $('run-summary').replaceChildren(el('h2',run.name),badge(run.status),el('h3',active?'Extraction in progress':done?'Extraction completed':`Extraction ${run.status}`),el('p',`${g.percent}% of configured run completed`),progress,metrics,el('p',`${g.discovered} pages discovered · ${g.skipped} skipped · ${run.config.max_pages} page budget per website`,'hint'),el('p','Progress uses each website’s page budget. Finished websites complete their share. 100% means the configured run ended; check errors and stop reasons for coverage limits.','hint'));
  for(const url of g.reading)$('run-summary').append(el('p','Currently reading: '+url,'hint'));
  const key=id+run.status+Boolean(g.failed);if(actionKey!==key){actionKey=key;const actions=[];
    if(run.status==='running')actions.push(button('Pause',()=>control(id,'pause')),button('Stop',()=>control(id,'stop')));
    if(['paused','stopped','interrupted','failed'].includes(run.status))actions.push(button('Resume remaining pages',()=>control(id,'resume')));
    if(!active&&g.failed)actions.push(button('Retry failed pages',()=>control(id,'retry')));
    actions.push(button('Download current Excel',()=>download('xlsx',new URLSearchParams({rid:id}))));
    actions.push(button('View people',async()=>{directoryRun=id;await show('directory',{reset:true});}));
    $('run-actions').replaceChildren(...actions);
  }
  $('site-rows').replaceChildren();run.sites.forEach((s,i)=>{const row=el('tr');[i+1,s.organization,s.status,s.successful,s.reading,s.queued,s.errors,s.skipped,s.reason.replaceAll('_',' ')].forEach(v=>row.append(el('td',v)));$('site-rows').append(row);});
  $('page-log').replaceChildren(...pages.map(p=>{const row=el('div','','log-row');row.append(badge(p.status),el('span',p.url));if(p.note)row.append(el('small',p.note));return row;}));
}
function duration(start,end){const secs=Math.max(0,Math.floor((end-new Date(start))/1000));return secs<60?`${secs}s`:`${Math.floor(secs/60)}m ${secs%60}s`;}
async function control(id,action){await api(`/api/runs/${id}/${action}`,{method:'POST'});if(selectedRun===id&&activeView==='run-detail')await runDetail();}
function filters(){const p=new URLSearchParams({q:$('search').value,organization:$('filter-org').value,city:$('filter-city').value,category:$('filter-category').value,has_email:$('email-only').checked,has_phone:$('phone-only').checked,has_contact:$('contact-only').checked});if(directoryRun)p.set('rid',directoryRun);return p;}
async function facets(){const view=viewRequest,summary=await api('/api/summary');if(view!==viewRequest||activeView!=='directory')return;for(const [id,values,label] of [['filter-org',summary.firms.map(x=>x.organization),'All organizations'],['filter-city',summary.cities,'All cities']]){const current=$(id).value;$(id).replaceChildren(new Option(label,''),...values.map(v=>new Option(v,v)));$(id).value=values.includes(current)?current:'';}}
async function directory(){const request=++directoryRequest,view=viewRequest;const p=filters();p.set('offset',offset);p.set('limit',50);const data=await api('/api/directory?'+p);if(request!==directoryRequest||view!==viewRequest||activeView!=='directory')return;
  if(offset>=data.total&&offset>0){offset=Math.max(0,Math.floor(Math.max(0,data.total-1)/50)*50);return directory();}
  $('result-count').textContent=`${data.total} people`;$('directory-scope').textContent=directoryRun?'Showing the selected run. Filters apply to this run.':'Showing newest records across all runs.';$('all-people').hidden=!directoryRun;
  $('people-rows').replaceChildren();for(const person of data.items){const row=el('tr'),name=el('td');name.append(el('strong',person.name),el('small',person.title));const cats=el('td');person.categories.forEach(c=>cats.append(el('span',categories[c]||c,'tag')));
    const own=el('td'),office=el('td');for(const [target,contacts] of [[own,person.contacts.filter(c=>c.owner==='individual')],[office,person.contacts.filter(c=>c.owner!=='individual')]]){const seen=new Set();for(const c of contacts){const key=c.owner+c.value;if(seen.has(key))continue;seen.add(key);target.append(el('span',c.value,'contact-line'));if(target===office)target.append(el('small',c.owner.replaceAll('_',' ')));}if(!contacts.length)target.append(el('span','Not published','muted'));}
    const org=el('td',person.organization);org.append(el('small',[person.city,person.state].filter(Boolean).join(', ')));const evidence=el('td');evidence.append(button('Inspect',()=>inspect(person)),link('Source ↗',person.profile_url));row.append(name,cats,own,office,org,evidence);$('people-rows').append(row);
  }
  if(!data.items.length){const row=el('tr'),td=el('td','No people match these filters. Use Clear filters to broaden the results.','empty');td.colSpan=6;row.append(td);$('people-rows').append(row);}
  $('prev').disabled=offset===0;$('next').disabled=offset+50>=data.total;$('page-number').textContent=`Page ${Math.floor(offset/50)+1}`;
}
function inspect(person){$('person-name').textContent=person.name;const box=$('person-content');box.replaceChildren(el('p',`${person.organization} · ${person.title}`),link('Open published biography ↗',person.profile_url),el('h3','Published contacts'),el('p','Email delivery, phone reachability and active bar membership are not verified.','hint'));
  for(const c of person.contacts){const item=el('div','','evidence');item.append(el('strong',c.value),el('p',c.owner.replaceAll('_',' ')),el('p',c.evidence),link('Source ↗',c.source_url));box.append(item);}
  if(!person.contacts.length)box.append(el('p','No published contacts found.'));
  box.append(el('h3','Practice area details'));for(const [category,text] of Object.entries(person.classification_evidence)){const item=el('div','','evidence');item.append(el('strong',categories[category]||category),el('p',text));box.append(item);}box.append(el('small','Observed '+new Date(person.observed_at).toLocaleString()));$('person-dialog').showModal();
}
function download(kind,params){location.href='/api/export/'+kind+'?'+params;}
function parseSources(){return $('sources').value.split('\n').map(s=>s.trim()).filter(Boolean).map(line=>{const parts=line.split('|').map(s=>s.trim());const source=parts.length===1?{url:parts[0]}:{organization:parts[0],url:parts[1]};if(parts.length>2||!/^https?:\/\//i.test(source.url||''))throw Error('Enter one complete http:// or https:// URL per line.');return source;}).concat(uploaded);}
document.querySelectorAll('.nav').forEach(n=>n.onclick=attempt(()=>{if(n.dataset.view==='directory')directoryRun=null;return show(n.dataset.view,{fresh:n.dataset.view==='new',reset:n.dataset.view==='directory'});}));
$('all-runs').onclick=attempt(()=>show('runs'));$('close-dialog').onclick=()=>$('person-dialog').close();$('clear-upload').onclick=clearUpload;
$('source-file').onchange=async()=>{const file=$('source-file').files[0],request=++uploadRequest;uploaded=[];uploadPending=Boolean(file);$('upload-preview').hidden=true;$('upload-status').textContent=file?'Checking file…':'';if(!file)return;
  try{if(file.size>2000000)throw Error('Source file must be smaller than 2 MB.');const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);const data=await api('/api/sources/validate',{method:'POST',body:JSON.stringify({filename:file.name,data:btoa(binary)})});if(request!==uploadRequest)return;uploaded=data.sources;$('upload-status').textContent=`${uploaded.length} source rows validated. Combined with URLs below.`;$('upload-preview').replaceChildren(table(['#','Organization','URL','City','State'],uploaded.map((s,i)=>[i+1,s.organization,s.url,s.city,s.state])));$('upload-preview').hidden=false;notice('');}
  catch(e){if(request===uploadRequest){$('upload-status').textContent='File not loaded.';notice(e.message);}}finally{if(request===uploadRequest)uploadPending=false;}
};
$('run-form').onsubmit=attempt(async event=>{event.preventDefault();if(uploadPending)throw Error('Wait for source-file validation to finish.');const sources=parseSources();if(!sources.length)throw Error('Paste at least one URL or upload a source list.');const selected=[...document.querySelectorAll('#category-checks input:checked')].map(n=>n.value);if(!selected.length)throw Error('Choose at least one practice area.');const config={name:$('run-name').value,sources,max_pages:Number($('max-pages').value),max_depth:Number($('max-depth').value),parallel_sites:Number($('parallel').value),delay:Number($('delay').value),categories:selected};$('start-button').disabled=true;try{const run=await api('/api/runs',{method:'POST',body:JSON.stringify(config)});await openRun(run.id);}finally{$('start-button').disabled=false;}});
$('page-test-form').onsubmit=attempt(async event=>{event.preventDefault();const view=viewRequest;$('page-test-button').disabled=true;$('page-test-status').textContent='Reading the page…';$('page-test-results').hidden=true;$('page-test-raw').hidden=true;try{const result=await api('/api/page-test',{method:'POST',body:JSON.stringify({url:$('page-url').value.trim()})});if(view!==viewRequest||activeView!=='new')return;$('page-test-status').textContent=`${result.records.length} people · ${result.links} links · not saved`;$('page-test-results').replaceChildren(table(['Name','Title','Contacts','Evidence'],result.records.map(p=>[p.name,p.title,p.contacts.map(c=>c.value+' ('+c.owner.replaceAll('_',' ')+')').join('; '),button('Inspect',()=>inspect(p))])));$('page-test-results').hidden=false;$('page-test-json').textContent=JSON.stringify(result,null,2);$('page-test-raw').hidden=false;}catch(e){$('page-test-status').textContent='Page test failed.';throw e;}finally{$('page-test-button').disabled=false;}});
for(const id of ['filter-org','filter-city','filter-category','email-only','phone-only','contact-only'])$(id).onchange=attempt(async()=>{offset=0;await directory();});
$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(attempt(async()=>{offset=0;await directory();}),250);};
$('clear-filters').onclick=attempt(async()=>{resetFilters();await directory();});$('all-people').onclick=attempt(async()=>{directoryRun=null;await show('directory',{reset:true});});
$('prev').onclick=attempt(async()=>{offset=Math.max(0,offset-50);await directory();});$('next').onclick=attempt(async()=>{offset+=50;await directory();});
$('runs-prev').onclick=attempt(async()=>{historyOffset=Math.max(0,historyOffset-25);await runList();});$('runs-next').onclick=attempt(async()=>{historyOffset+=25;await runList();});
for(const kind of ['csv','xlsx','json'])$('export-'+kind).onclick=()=>download(kind,filters());
attempt(async()=>{const session=await api('/api/session');token=session.token;categories=session.categories;for(const [key,name] of Object.entries(categories)){$('filter-category').append(new Option(name,key));const label=el('label'),input=el('input');input.type='checkbox';input.value=key;input.checked=true;input.defaultChecked=true;label.append(input,document.createTextNode(name));$('category-checks').append(label);}
  const hash=location.hash.slice(1);if(hash.startsWith('run/')&&/^[a-f0-9]{12}$/.test(hash.slice(4))){selectedRun=hash.slice(4);await show('run-detail');}else await show(['new','runs','directory'].includes(hash)?hash:'dashboard');
  setInterval(async()=>{if(refreshBusy||document.hidden)return;refreshBusy=true;try{if(activeView==='run-detail')await runDetail();else if(activeView==='dashboard')await dashboard();else if(activeView==='runs')await runList();else if(activeView==='directory'&&!$('person-dialog').open)await directory();}catch(e){notice(e.message);}finally{refreshBusy=false;}},2500);
})();
