// Exercise the shipped editor's drawing and navigation with a small DOM host.
// Layout and real-browser appearance are checked separately in the review packet.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const M=require('../src/karaoke_generator/static/manual-model.js');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/karaoke_generator/static/manual-editor.js'),'utf8');
async function harness(height){
  const elements=new Map(),events={},texts=[];let position=10,scrollLeft=0;
  const document={activeElement:null};
  function element(){return {style:{},handlers:{},dataset:{},clientWidth:1280,scrollLeft:0,scrollTop:0,value:'',classList:{add(){},remove(){},toggle(){}},
    set id(value){elements.set(`#${value}`,this);},addEventListener(name,fn){this.handlers[name]=fn;},replaceChildren(){},after(){},focus(){document.activeElement=this;},scrollIntoView(){},getBoundingClientRect(){return {left:0,top:0};},querySelectorAll(){return [];}};}
  const node=selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector);};
  Object.assign(document,{querySelector:node,querySelectorAll:()=>[],createElement:element,body:element()});
  const project={project_id:'draw-song',revision:0,duration:300,source_analysis:{kind:'manual-auto'},roles:[],reviews:[],occurrences:[],annotations:Array.from({length:5},(_,index)=>({annotation_id:`row-${index}`,occurrence_id:`occ-${index}`,unit:'word',text:`row ${index}`,start:10,end:12,role_id:null}))};
  const window={ManualLyricsModel:M,innerHeight:height,addEventListener(name,fn){events[name]=fn;}};
  const env={window,document,localStorage:{getItem:()=>null},fetch:async()=>({ok:true,json:async()=>project})};
  vm.createContext(env);vm.runInContext(source,env);
  const editor=window.ManualEditor;
  editor.init({position:()=>position,pps:()=>60,rate:()=>1,format:t=>`${t}s`,invalidate(){},draw(){},karaoke(){},seek:t=>{position=t;},scroll:value=>{scrollLeft=value;},scrollLeft:()=>scrollLeft,drawText:(_ctx,text)=>texts.push(text)});
  await editor.load('fixture');
  const action=name=>node('#manual-workspace').handlers.click({target:{closest:()=>({dataset:{manual:name}})}});
  const ctx=new Proxy({}, {get:(_target,key)=>()=>{},set:()=>true});
  function draw(){texts.length=0;editor.draw(ctx,{width:1280,scrollLeft:0,position});return texts.slice();}
  return {editor,node,action,draw,events,window,setPosition:value=>{position=value;},scroll:()=>scrollLeft};
}
test('canvas draw, hit targets and next-row counter agree at 600 and 720 desktop heights',async()=>{
  for(const [height,count] of [[600,2],[720,3]]){
    const h=await harness(height);
    assert.deepEqual(h.draw(),Array.from({length:count},(_,i)=>`row ${i}`));
    assert.match(h.node('#manual-role-filter').innerHTML,new RegExp(`Строки наложений 1–${count} / 5`));
    assert.equal(h.editor.hit(680,57+count*34,0),false,'partial next row is neither drawn nor clickable');
    h.action('common-down');
    assert.deepEqual(h.draw(),Array.from({length:count},(_,i)=>`row ${i+1}`));
    assert.match(h.node('#manual-role-filter').innerHTML,new RegExp(`Строки наложений 2–${count+1} / 5`));
    h.window.innerHeight=height===600?720:600;h.events.resize();
    assert.equal(h.draw().length,height===600?3:2);
  }
});
test('entering the editor, explicit insertion and playback reset reveal time on a ruler independent of role scroll',async()=>{
  const h=await harness(600);h.setPosition(100);h.action('edit');
  const scroll=h.node('#manual-lanes-scroll'),ruler=h.node('#manual-ruler');
  assert.equal(scroll.scrollLeft,5397);assert.equal(ruler.style.transform,'translateX(-5397px)');
  assert.ok(ruler.innerHTML.includes('100s'));assert.ok(!h.node('#manual-lanes').innerHTML.includes('manual-ruler'));
  scroll.scrollTop=150;scroll.handlers.scroll();
  assert.equal(ruler.style.transform,'translateX(-5397px)');assert.equal(h.editor.dirty(),false);
  const input=h.node('#manual-cursor');input.value='200';input.handlers.change({target:input});
  assert.equal(h.scroll(),11397);assert.equal(scroll.scrollTop,150);
  h.setPosition(123);h.action('cursor-reset');assert.equal(h.scroll(),6777);
  assert.equal(input.value,'123.000');assert.equal(h.editor.dirty(),false);
});
