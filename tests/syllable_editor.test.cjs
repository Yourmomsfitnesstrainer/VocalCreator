const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createSession}=require('../src/karaoke_generator/static/syllable-editor.js');
const M=require('../src/karaoke_generator/static/syllable-model.js');
const {fixture}=require('./syllable_fixture.cjs');
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const state=(job='test-job',rev=0)=>({job_id:job,status:'ready',effective_url:`/${job}/score`,base_analysis_key:'test-base',revision:rev});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('S15 GET does not prepare or mutate; no document remains an explicit preparation state',async()=>{
  const calls=[],s=createSession(async(url,options)=>{calls.push([url,options]);return response({...state(),status:'not_prepared',effective_url:null});});await s.load('test-job');assert.equal(s.history,null);assert.equal(calls.length,1);assert.equal(calls[0][1].method,undefined);
});
test('S14 409 retains exact edit and base revision for export instead of overwriting saved state',async()=>{
  const s=createSession(async(url,options)=>options.method==='PUT'?response({detail:{message:'revision conflict',current_revision:7}},409):response(url.endsWith('/score')?fixture():state()));await s.load('test-job');s.history.commit(M.rename(s.history.document,'u1','мя'));const before=M.clone(s.history.document);await assert.rejects(()=>s.save(),error=>error.status===409);assert.deepEqual(s.history.document,before);assert.equal(s.history.dirty,true);
});
test('S14 ambiguous storage failure retries exact request ID and payload without duplicate revision',async()=>{
  const requests=[];let attempt=0;const s=createSession(async(url,options)=>{if(options.method==='PUT'){requests.push(JSON.parse(options.body));if(!attempt++)throw Error('connection dropped after commit');return response({document:{...requests[0].document,revision:1},revision:1});}return response(url.endsWith('/score')?fixture():state());});await s.load('test-job');s.history.commit(M.rename(s.history.document,'u1','мя'));await assert.rejects(()=>s.save());s.history.commit(M.rename(s.history.document,'u1','да'));await s.save();assert.deepEqual(requests[0],requests[1]);assert.equal(s.history.document.units[0].text,'да');assert.equal(s.history.document.revision,1);assert.equal(s.history.dirty,true);
});
test('late job-load response cannot install another song or stale revision',async()=>{
  const slow=deferred();const s=createSession(async url=>{if(url==='/test-job/score')return slow.promise;if(url.endsWith('/score'))return response({...fixture(),job_id:'new-song'});return response(state(url.includes('new-song')?'new-song':'test-job'));});const old=s.load('test-job');await new Promise(resolve=>setImmediate(resolve));await s.load('new-song');slow.resolve(response(fixture()));await old;assert.equal(s.history.document.job_id,'new-song');
});
test('read validates the full job/base/revision triple before adopting an artifact',async()=>{
  for(const mismatch of [{job_id:'wrong'},{base_analysis_key:'wrong'},{revision:12}]){const s=createSession(async url=>response(url.endsWith('/score')?{...fixture(),...mismatch}:state()));await assert.rejects(()=>s.load('test-job'),/другой базе или ревизии/);assert.equal(s.history,null);}
});
test('save completing after navigation cannot corrupt the next song',async()=>{
  const slow=deferred(),s=createSession(async(url,options)=>options.method==='PUT'?slow.promise:response(url.endsWith('/score')?{...fixture(),job_id:url.split('/')[1]}:state(url.includes('new-song')?'new-song':'test-job')));await s.load('test-job');s.history.commit(M.rename(s.history.document,'u1','мя'));const saving=s.save();await s.load('new-song');slow.resolve(response({document:{...fixture(),revision:1}}));assert.equal(await saving,null);assert.equal(s.history.document.job_id,'new-song');assert.equal(s.history.document.revision,0);
});

// Small DOM adapter executes the actual editor event handlers. Rendering geometry
// is checked separately in the real desktop browser; this guards keyboard/state.
function editorHarness(){
  const {createEditor}=require('../src/karaoke_generator/static/syllable-editor.js'),elements=new Map(),storage=new Map();
  class Element{
    constructor(tag='div',id=''){this.tagName=tag;this.id=id;this.value='';this.hidden=false;this.dataset={};this.handlers={};this.classList={add(){},remove(){},toggle(){}};}
    set id(value){this.identifier=value;if(value)elements.set(value,this);}
    get id(){return this.identifier;}
    set innerHTML(value){this.html=value;for(const match of value.matchAll(/<([a-z]+)\b([^>]*)>/g)){const id=match[2].match(/\bid="([^"]+)"/);if(!id)continue;const node=new Element(match[1],id[1]);node.value=match[2].match(/\bvalue="([^"]*)"/)?.[1]||'';node.hidden=/\bhidden\b/.test(match[2]);elements.set(node.id,node);}}
    get innerHTML(){return this.html||'';}
    addEventListener(key,fn){this.handlers[key]=fn;}after(){}focus(){}select(){}querySelector(){return null;}querySelectorAll(){return [];}matches(selector){return selector.split(',').includes(this.tagName);}closest(selector){if(selector==='[data-score]')return this.dataset.score?this:null;if(selector==='#score-panel')return this.id==='score-text'?elements.get('score-panel'):null;return null;}
  }
  const document={body:new Element(),addEventListener(){},createElement:tag=>new Element(tag),querySelector:selector=>{if(selector==='.timeline-header')return new Element();return elements.get(selector.slice(1))||null;}};
  elements.set('inspector-syllable',new Element());elements.set('inspector-word',new Element());
  const root={document,location:{search:''},fetch:async url=>response(url.endsWith('/score')?fixture():state()),localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},addEventListener(){},setTimeout:fn=>{fn();return 1;}};
  const host={position:()=>.7,pauseCount:0,seekCalls:[],suspendCount:0,pause(){this.pauseCount++;},suspendFollow(){this.suspendCount++;},seek(t){this.seekCalls.push(t);},draw(){},invalidate(){},format:t=>String(t),noteName:n=>String(n)};
  return {root,host,elements,storage,Element};
}
function componentHarness(){
  const {createEditor}=require('../src/karaoke_generator/static/syllable-editor.js');
  const h=editorHarness(),create=h.root.document.createElement;let section;
  h.root.document.createElement=tag=>{const node=create(tag);if(tag==='section')section=node;return node;};
  h.editor=createEditor(h.root);h.editor.init(h.host);h.click=action=>{const target=new h.Element();target.dataset.score=action;section.handlers.click({target},target);};
  h.key=(key,shiftKey=false)=>{const target=h.elements.get('score-text'),event={key,shiftKey,target,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;}};section.handlers.keydown(event);return event;};return h;
}

test('S08 S20 entering correction pauses at the same source position and suspends follow without seek',async()=>{
  const h=componentHarness();await h.editor.load('test-job',require('./syllable_fixture.cjs').notes);h.click('edit');assert.equal(h.host.pauseCount,1);assert.equal(h.host.suspendCount,1);assert.deepEqual(h.host.seekCalls,[]);assert.equal(h.elements.get('score-text').value,'ма');
});
test('S08 real editor Tab and Shift+Tab apply text then navigate adjacent time regions',async()=>{
  const h=componentHarness();await h.editor.load('test-job',require('./syllable_fixture.cjs').notes);h.click('edit');h.elements.get('score-text').value='мя';const forward=h.key('Tab');assert.ok(forward.defaultPrevented);assert.equal(h.editor.document().units[0].text,'мя');assert.equal(h.host.seekCalls.at(-1),1.2);h.key('Tab',true);assert.equal(h.host.seekCalls.at(-1),.5);
});
test('S08 Esc discards unfinished input and preserves the committed transaction and transport',async()=>{
  const h=componentHarness();await h.editor.load('test-job',require('./syllable_fixture.cjs').notes);h.click('edit');h.elements.get('score-text').value='discard me';const event=h.key('Escape');assert.ok(event.defaultPrevented);assert.equal(h.elements.get('score-text').value,'ма');assert.equal(h.editor.document().units[0].text,'ма');assert.deepEqual(h.host.seekCalls,[]);assert.equal(h.editor.session.history.past.length,0);
});
