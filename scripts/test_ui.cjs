/* Offline DOM-mock regressions. No browser or external network required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map(), documentHandlers = {};
class Element {
  constructor(tag='div',id='') { Object.assign(this,{tag,id,value:'',textContent:'',className:'',hidden:false,disabled:false,checked:false,children:[],handlers:{},attributes:{}}); }
  replaceChildren(...children) {this.children=children;this.textContent='';}
  append(...children) {this.children.push(...children);}
  addEventListener(name,fn) {this.handlers[name]=fn;}
  setAttribute(name,value) {this.attributes[name]=value;}
  removeAttribute(name) {delete this.attributes[name];delete this[name];}
  click() {if(!this.disabled)return this.onclick ? this.onclick() : this.handlers.click?.();}
}
function element(id) {if(!elements.has(id))elements.set(id,new Element('div',id));return elements.get(id);}
function descendants(node) {return node.children.flatMap(x=>[x,...descendants(x)]);}
function field(name) {return descendants(element('proposal-form')).find(x=>x.attributes['data-field']===name);}
const original={schema_version:1,synthetic:true,doctor:{name:'Dr Asha Rao',specialty:'Medical Oncology',city:'Pune',qualifications:'MBBS, MD',experience_years:'18 years',languages:null},affiliations:[],contacts:[],registration:[],evidence:[{id:'e1',field:'doctor.name',value:'Dr Asha Rao',source_url:'https://demo.example.invalid/1',support_text:'Dr Asha Rao',retrieved_at:'2026-10-19T00:00:00Z'}],warnings:[]};
const makeJob=id=>({id,url:'https://demo.example.invalid/'+id,state:'review',source:{url:'https://demo.example.invalid/'+id,text:'SYNTHETIC <script>alert(1)</script>',retrieved_at:'2026-10-19T00:00:00Z',content_sha256:'abc',synthetic:true,source_type:'synthetic'},proposal:{version:1,data:JSON.parse(JSON.stringify(original)),validation_errors:[],duplicate_job_ids:[]}});
let jobs=[makeJob(1),makeJob(2),makeJob(3)];
let expectedToken='test-admin-token', calls=[], allowDiscard=true, deferred=null;
const response=data=>({ok:true,json:async()=>JSON.parse(JSON.stringify(data)),blob:async()=>Buffer.from('PK')});
const error=detail=>({ok:false,statusText:'Conflict',json:async()=>({detail})});
const context={document:{getElementById:element,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>{documentHandlers[name]=fn;}},window:{confirm:()=>allowDiscard},console,setTimeout:fn=>fn(),URL,URLSearchParams,fetch:async(path,opts)=>{
  calls.push({path,opts});
  if(opts.headers.Authorization!=='Bearer '+expectedToken)return error('Admin bearer token required');
  if(deferred && path===deferred.path)return new Promise(resolve=>{deferred.resolve=resolve;});
  const url=new URL(path,'http://local');
  if(url.pathname==='/api/status')return response({mode:'fixture',paused:false});
  if(url.pathname==='/api/jobs' && opts.method==='GET'){
    const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||25);
    return response({offset,limit,total:jobs.length,items:jobs.slice(offset,offset+limit).map(j=>({id:j.id,url:j.url,state:j.state,name:j.proposal.data.doctor.name,version:j.proposal.version,validation_error_count:j.proposal.validation_errors.length,duplicate_job_ids:[],bulk_eligible:j.state==='review'}))});
  }
  if(url.pathname==='/api/jobs/bulk-approve'){
    const body=JSON.parse(opts.body);const results=body.items.map(item=>{const j=jobs.find(x=>x.id===item.id);j.state='approved';j.proposal.version++;return {id:j.id,state:'approved'};});return response({results});
  }
  const match=url.pathname.match(/^\/api\/jobs\/(\d+)(?:\/(.*))?$/);
  if(match){const j=jobs.find(x=>x.id===Number(match[1]));if(!match[2])return response(j);const body=JSON.parse(opts.body);
    if(body.expected_version!==j.proposal.version)return error('Proposal changed; reload before editing');
    if(match[2]==='proposal'){j.proposal.data=body.data;j.proposal.version++;return response({validation_errors:[]});}
    if(match[2]==='approve'){j.state='approved';j.proposal.version++;return response({state:'approved'});}
  }
  return response({});
}};
context.URL.createObjectURL=()=> 'blob:test';context.URL.revokeObjectURL=()=>{};
element('page-size').value='25';element('review').hidden=true;
vm.createContext(context);vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),context);
const reviewChecks=['check-identity','check-evidence','check-contacts','check-registration','check-duplicates'];
const clickJob=async index=>element('jobs').children[index].children[0].handlers.click();
(async()=>{
  element('token').value=expectedToken;await element('connect').onclick();
  assert.equal(element('token').value,'');assert.match(element('status').textContent,/fixture/);
  await element('connect').onclick();assert.match(element('message').textContent,/Connected/);
  await clickJob(0);assert.equal(element('review').hidden,false);assert.match(element('source').textContent,/<script>/);
  assert.equal(element('source-link').href,'https://demo.example.invalid/1');
  assert.equal(field('doctor.experience_years').value,'18 years');
  const beforeEvidence=JSON.stringify(jobs[0].proposal.data.evidence);
  field('doctor.city').value='Chennai';field('doctor.city').handlers.input();
  allowDiscard=false;await clickJob(1);assert.match(element('selected').textContent,/#1/);allowDiscard=true;
  assert.equal(element('approve').disabled,true);await element('save').onclick();
  const saved=JSON.parse(calls.find(x=>x.path==='/api/jobs/1/proposal').opts.body);
  assert.equal(saved.data.doctor.city,'Chennai');assert.equal(saved.data.doctor.qualifications,'MBBS, MD');
  assert.equal(saved.data.doctor.languages,null);assert.equal(JSON.stringify(saved.data.evidence),beforeEvidence);
  element('proposal').value=element('proposal').value.replace('Chennai','Mumbai');element('proposal').handlers.input();
  await element('save').onclick();assert.match(element('message').textContent,/Load JSON into form/);
  await element('load-json').onclick();await element('save').onclick();assert.equal(jobs[0].proposal.data.doctor.city,'Mumbai');
  element('reviewer').value='Offline UI tester';element('note').value='Checked synthetic evidence';
  await element('approve').onclick();assert.match(element('message').textContent,/Complete every review-checklist/);assert.equal(jobs[0].state,'review');
  for(const id of reviewChecks)element(id).checked=true;
  await element('approve').onclick();assert.equal(element('approve').disabled,true);assert.match(element('message').textContent,/Approved/);
  // Selection is explicit and a batch needs a separate human acknowledgement.
  const secondRow=element('jobs').children[1], checkbox=descendants(secondRow).find(x=>x.type==='checkbox');
  checkbox.checked=true;checkbox.handlers.change();await element('bulk-approve').onclick();assert.match(element('message').textContent,/acknowledge/);assert.equal(jobs[1].state,'review');
  element('bulk-ack').checked=true;await element('bulk-approve').onclick();assert.equal(jobs[1].state,'approved');
  const bulk=JSON.parse(calls.find(x=>x.path==='/api/jobs/bulk-approve').opts.body);assert.deepEqual(bulk.items,[{id:2,expected_version:1}]);
  element('page-size').value='1';await element('page-size').handlers.change();assert.match(element('page-summary').textContent,/1–1 of 3/);
  await element('next').onclick();assert.match(element('page-summary').textContent,/2–2 of 3/);
  await element('previous').onclick();assert.match(element('page-summary').textContent,/1–1 of 3/);
  await element('export').onclick();assert.match(element('message').textContent,/Export downloaded/);
  // Invalid raw proposals remain inspectable and correctable.
  jobs[2].proposal.data={doctor:'bad',unknown:123};jobs[2].proposal.validation_errors=['doctor: must be an object'];
  // Newer navigation wins even when an earlier detail request finishes later.
  element('page-size').value='25';await element('page-size').handlers.change();
  deferred={path:'/api/jobs/1'};const slow=clickJob(0);await Promise.resolve();await clickJob(2);deferred.resolve(response(jobs[0]));await slow;deferred=null;assert.match(element('selected').textContent,/#3/);
  assert.equal(field('doctor').value,'bad');assert.equal(field('unknown').value,'123');assert.equal(element('approve').disabled,true);assert.equal(JSON.parse(element('proposal').value).unknown,123);
  // Logout discards pending source responses and clears the sensitive rendered data.
  deferred={path:'/api/jobs/1'};const pendingRead=clickJob(0);await Promise.resolve();element('forget').onclick();deferred.resolve(response(jobs[0]));await pendingRead;deferred=null;
  assert.equal(element('status').textContent,'Disconnected');assert.equal(element('review').hidden,true);assert.equal(element('source').textContent,'');assert.equal(element('proposal').value,'');
  await element('connect').onclick();assert.match(element('message').textContent,/Enter your admin token/);
  assert.equal(fs.readFileSync('app/static/app.js','utf8').includes('innerHTML'),false);
  console.log('PASS: login, filters/pagination, lossless form/evidence, JSON editor, checklist, versioned review, selected bulk acknowledgement, safe source rendering, stale navigation, export and interrupted logout');
})().catch(error=>{console.error(error);process.exitCode=1;});
