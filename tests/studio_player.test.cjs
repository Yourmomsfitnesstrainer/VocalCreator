// Contract harness for the shipped script. Fake Web Audio records scheduling;
// real-browser rendering and audible acceptance are separate checks.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/karaoke_generator/static/studio.js'),'utf8');

class Element{
  constructor(){this.handlers={};this.attributes={};this.children=[];this.style={};this.textContent='';this.value='0';this.hidden=false;this.disabled=false;this.width=1000;this.height=480;this.clientWidth=1000;this.clientHeight=480;this.scrollWidth=4000;this.scrollLeft=0;this.scrollHeight=2400;this.scrollTop=0;this.dataset={};this.lastChild={textContent:''};this.classList={toggle(){},add(){},remove(){}};this.parts=new Map();}
  addEventListener(name,fn){this.handlers[name]=fn;}
  querySelector(name){if(!this.parts.has(name))this.parts.set(name,new Element());return this.parts.get(name);}
  setAttribute(k,v){this.attributes[k]=v;}
  getAttribute(k){return this.attributes[k];}
  replaceChildren(...items){this.children=items;}
  append(...items){this.children.push(...items);}
  closest(){return null;}
  getBoundingClientRect(){return {left:0,top:0,width:1000,height:480};}
  getContext(){return new Proxy({measureText:text=>({width:text.length*7})},{get:(o,key)=>o[key]||(()=>{}),set:(o,key,value)=>(o[key]=value,true)});}
  click(){return this.handlers.click?.({target:this,currentTarget:this});}
}
function harness(initialStorage=[]){
  const elements=new Map(),radios=['light','medium','pro'].map(value=>{const node=new Element();node.value=value;node.parentElement=new Element();return node;});
  const document=new Element();document.querySelector=s=>{if(!elements.has(s))elements.set(s,new Element());return elements.get(s);};document.querySelectorAll=s=>s==='[name="learning-mode"]'?radios:[];document.createElement=()=>new Element();document.createTextNode=text=>{const node=new Element();node.textContent=text;return node;};
  const recorded=[];
  const audio={currentTime:100,state:'running',resume:async()=>{},createGain:()=>({connect(){},gain:{setTargetAtTime(){}}}),decodeAudioData:async value=>value,
    createBufferSource(){const node={buffer:null,stopped:false,connect(){},disconnect(){},start(at,offset){this.at=at;this.offset=offset;recorded.push(this);},stop(at){this.stopped=true;this.stopAt=at;}};return node;}};
  const buffer={duration:303.726,getChannelData:()=>new Float32Array(100)};
  const storage=new Map(initialStorage);
  const env={document,window:{handlers:{},addEventListener(name,fn){this.handlers[name]=fn;},devicePixelRatio:1},AbortController,console,
    requestAnimationFrame:()=>1,cancelAnimationFrame(){},setInterval:()=>1,clearInterval(){},
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    fetch:async()=>({ok:true,json:async()=>({jobs:[]})})};
  vm.createContext(env);vm.runInContext(source,env);
  env.fixtureAudio=audio;env.fixtureBuffer=buffer;
  vm.runInContext(`state.context=fixtureAudio;state.master={};state.duration=303.726;state.buffers={vocals:fixtureBuffer,piano:fixtureBuffer,instrumental:fixtureBuffer};state.melody={notes:[],pitch_frames:[]};state.job={id:'job',artifacts:{}};state.originalBuffers={...state.buffers};`,env);
  return {env,elements,radios,audio,recorded,storage,run:code=>vm.runInContext(code,env),node:s=>document.querySelector(s)};
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function data(mode){return {mode,notes:[{id:mode,start:0,end:1,midi:60,confidence:.9,intervals:[{start:0,end:1}]}],words:[],word_note_links:[],diagnostics:{words_without_notes:0},artifacts:{'piano.wav':`/${mode}.wav`,'learning.json':`/${mode}.json`}};}
function modeFetch(h,overrides={}){
  h.env.fetch=async(url)=>{
    if(url.endsWith('.wav'))return {ok:true,arrayBuffer:async()=>({duration:303.726})};
    const mode=url.split('/').at(-1);
    if(overrides[mode])return overrides[mode];
    return {ok:true,json:async()=>data(mode)};
  };
}

test('10 play/pause cycles schedule all three tracks at one clock and stop every source',async()=>{
  const h=harness();
  for(let cycle=0;cycle<10;cycle++){
    await h.run('play()');
    const starts=h.recorded.slice(-3);
    assert.equal(starts.length,3);assert.equal(new Set(starts.map(s=>s.at)).size,1);assert.equal(new Set(starts.map(s=>s.offset)).size,1);
    assert.equal(h.node('#play-button').attributes['aria-label'],'Пауза');
    assert.equal(h.node('#play-button').querySelector('use').attributes.href,'#icon-pause');
    h.audio.currentTime+=.5;h.run('pause()');
    assert.ok(starts.every(s=>s.stopped));assert.equal(h.run('state.playing'),false);
    assert.equal(h.node('#play-button').attributes['aria-label'],'Воспроизвести');
  }
  assert.equal(h.recorded.length,30);
});

test('end of file restores Play and next launch starts at zero',async()=>{
  const h=harness();h.run('state.position=303.5');await h.run('play()');h.audio.currentTime+=1;h.run('tick()');
  assert.equal(h.run('state.playing'),false);assert.equal(h.run('state.position'),303.726);
  await h.run('play()');assert.equal(h.recorded.at(-1).offset,0);
});

test('resume failure rolls back and permits retry; cancellation prevents duplicate starts',async()=>{
  const h=harness();h.audio.resume=async()=>{throw Error('device failure');};await h.run('play()');
  assert.equal(h.run('state.playing'),false);assert.match(h.node('#transport-status').textContent,/device failure/);
  const pending=deferred();h.audio.resume=()=>pending.promise;
  const first=h.run('play()');h.run('pause()');pending.resolve();await first;
  assert.equal(h.recorded.length,0);h.audio.resume=async()=>{};await Promise.all([h.run('play()'),h.run('play()')]);
  assert.equal(h.recorded.length,3);
});

test('partial source-start failure stops already scheduled tracks',async()=>{
  const h=harness();const create=h.audio.createBufferSource;let calls=0;
  h.audio.createBufferSource=()=>{const node=create();if(++calls===2)node.start=()=>{throw Error('start failed');};return node;};
  await h.run('play()');assert.equal(h.run('state.playing'),false);assert.ok(h.recorded[0].stopped);
  assert.equal(h.node('#play-button').attributes['aria-label'],'Воспроизвести');
});

test('mode commit changes only piano, preserving audio position, view, zoom and mixer',async()=>{
  const h=harness();modeFetch(h);h.run('state.position=123;state.zoom=3;state.muted.piano=true;state.volumes.vocals=.3');h.node('#timeline-scroller').scrollLeft=700;
  await h.run('play()');const old=h.recorded.slice();h.audio.currentTime+=.25;
  const before=h.run('currentPosition()');await h.run("requestMode('medium')");
  assert.equal(h.recorded.length,4);assert.equal(old[0].stopped,false);assert.equal(old[2].stopped,false);assert.equal(old[1].stopped,true);
  assert.equal(h.recorded[3].at,h.audio.currentTime);assert.equal(h.recorded[3].offset,before);
  assert.equal(h.run('currentPosition()'),before);assert.equal(h.run('state.zoom'),3);assert.equal(h.node('#timeline-scroller').scrollLeft,700);
  assert.equal(h.run('state.muted.piano'),true);assert.equal(h.run('state.volumes.vocals'),.3);assert.equal(h.run('activeNotes()[0].id'),'medium');
  assert.equal(h.storage.get('vocalcreator:mode:job'),'medium');
});

test('cached mode switches preserve the scheduled start and shared timeline before audio begins',async()=>{
  for(const elapsed of [0,.005,.019,.02,.025]){
    const h=harness();modeFetch(h);
    await h.run("requestMode('light')");await h.run("requestMode('pro')");
    h.run('state.position=123');await h.run('play()');
    const [vocals,originalPiano,backing]=h.recorded;
    h.audio.currentTime+=elapsed;
    for(const mode of ['light','pro']){
      const previous=h.run('state.sources.piano');await h.run(`requestMode('${mode}')`);
      assert.equal(h.node('#learning-status').textContent,'');assert.equal(h.run('state.mode'),mode);
      const piano=h.recorded.at(-1);
      assert.ok(piano.at>=vocals.at,'replacement must not sound before the common scheduled start');
      assert.ok(Math.abs((piano.offset-piano.at)-(vocals.offset-vocals.at))<1e-9,'replacement must retain the original audio timeline');
      assert.equal(previous.stopAt,piano.at);assert.equal(vocals.stopped,false);assert.equal(backing.stopped,false);
    }
    assert.equal(originalPiano.stopped,true);h.run('pause()');
    assert.ok(h.recorded.every(source=>source.stopped));
  }
});

test('last mode wins even when older preparation completes late; failure keeps working mode',async()=>{
  const h=harness(),late=deferred();modeFetch(h,{light:late.promise});
  const first=h.run("requestMode('light')");await h.run("requestMode('pro')");
  late.resolve({ok:true,json:async()=>data('light')});await first;
  assert.equal(h.run('state.mode'),'pro');assert.equal(h.run('activeNotes()[0].id'),'pro');
  h.env.fetch=async()=>{throw Error('preparation failed');};await h.run("requestMode('medium')");
  assert.equal(h.run('state.mode'),'pro');assert.match(h.node('#learning-status').textContent,/preparation failed/);
});

test('failed piano decode and failed switch scheduling retain old notes and piano',async()=>{
  const h=harness();modeFetch(h);await h.run("requestMode('light')");await h.run('play()');const old=h.run('state.sources.piano');
  h.audio.decodeAudioData=async()=>{throw Error('bad WAV');};await h.run("requestMode('medium')");
  assert.equal(h.run('state.mode'),'light');assert.equal(old.stopped,false);
  h.audio.decodeAudioData=async value=>value;h.audio.createBufferSource=()=>{throw Error('schedule failed');};await h.run("requestMode('pro')");
  assert.equal(h.run('state.mode'),'light');assert.equal(old.stopped,false);
});

test('logical group count and active-note inspector respect sounding gaps',()=>{
  const h=harness();h.run(`state.learning={notes:[{id:'group',start:1,end:5,midi:60,intervals:[{start:1,end:2},{start:4,end:5}]}]}`);
  assert.equal(h.run('activeNotes().length'),1);assert.equal(h.run('activeNoteIndex(1.5)'),0);assert.equal(h.run('activeNoteIndex(3)'),-1);assert.equal(h.run('activeNoteIndex(4.5)'),0);
});

test('wheel applies each axis once for pixel, line, page, diagonal and Shift gestures',()=>{
  const h=harness(),scroll=h.node('#timeline-scroller'),wheel=scroll.handlers.wheel;
  for(const zoom of [.5,1,3])for(const event of [{deltaY:100},{deltaX:80,deltaY:20},{deltaX:20,deltaY:80},{deltaY:3,deltaMode:1},{deltaY:1,deltaMode:2},{deltaY:90,shiftKey:true},{deltaX:90,deltaY:30,shiftKey:true}]){
    h.run(`state.zoom=${zoom};state.position=10`);scroll.scrollLeft=500;scroll.scrollTop=500;let prevented=false;
    const e={deltaX:0,deltaY:0,deltaMode:0,preventDefault(){prevented=true;},...event};wheel(e);
    const unitX=e.deltaMode===1?16:e.deltaMode===2?926:1,unitY=e.deltaMode===1?16:e.deltaMode===2?400:1;
    assert.equal(scroll.scrollLeft,500+(e.shiftKey&&!e.deltaX?e.deltaY:e.deltaX)*unitX);
    assert.equal(scroll.scrollTop,500+(e.shiftKey?0:e.deltaY)*unitY);
    assert.equal(h.run('state.position'),10);assert.equal(h.run('state.zoom'),zoom);assert.equal(prevented,true);
  }
});
test('wheel releases both-axis boundaries, system zoom and fitting content',()=>{
  const h=harness(),scroll=h.node('#timeline-scroller');
  for(const extra of [{scrollTop:0,deltaY:-20},{scrollTop:1920,deltaY:20},{ctrlKey:true},{metaKey:true},{scrollHeight:480,scrollTop:0}]){
    scroll.scrollHeight=extra.scrollHeight??2400;scroll.scrollTop=extra.scrollTop??500;scroll.scrollLeft=500;const before=scroll.scrollTop;let prevented=false;
    scroll.handlers.wheel({deltaY:20,deltaX:0,deltaMode:0,preventDefault(){prevented=true;},...extra});
    assert.equal(scroll.scrollTop,before);assert.equal(scroll.scrollLeft,500);assert.equal(prevented,false);
  }
});
test('Page keys pan vertically, Shift pans time, both zooms preserve anchors and audio time',()=>{
  const h=harness(),scroll=h.node('#timeline-scroller');scroll.scrollLeft=1000;scroll.scrollTop=500;scroll.scrollWidth=100000;scroll.scrollHeight=100000;h.run('state.position=10');
  scroll.handlers.keydown({key:'PageDown',preventDefault(){},stopPropagation(){}});assert.equal(scroll.scrollTop,900);assert.equal(scroll.scrollLeft,1000);
  scroll.handlers.keydown({key:'PageDown',shiftKey:true,preventDefault(){},stopPropagation(){}});assert.equal(scroll.scrollLeft,1926);
  const center=(scroll.scrollLeft+463)/28;
  h.node('#zoom-slider').handlers.input({target:{value:3}});
  assert.ok(Math.abs((scroll.scrollLeft+463)/84-center)<1e-8);
  const midi=h.run('state.midiMax+.5-(280+scroller.scrollTop-126)/rowHeight()');
  h.node('#height-slider').handlers.input({target:{value:3}});
  assert.ok(Math.abs(h.run('state.midiMax+.5-(280+scroller.scrollTop-126)/rowHeight()')-midi)<1e-8);
  assert.equal(h.run('state.position'),10);
});

test('timeline click seeks but fixed keyboard does not',()=>{
  const h=harness(),canvas=h.node('#timeline-canvas');h.node('#timeline-scroller').scrollLeft=280;h.run('state.position=3');
  canvas.handlers.click({currentTarget:canvas,clientX:40});assert.equal(h.run('state.position'),3);
  canvas.handlers.click({currentTarget:canvas,clientX:354});assert.equal(h.run('state.position'),20);
});

function tempoFetch(h,overrides={}){
  h.env.fetch=async url=>{
    if(url.endsWith('.wav')){const rate=Number(url.split('/')[2])||1;return {ok:true,arrayBuffer:async()=>({duration:303.726/rate,getChannelData:()=>new Float32Array(10)})};}
    if(url.includes('/tempo/')){
      const rate=Number(url.split('/').at(-1)),mode=url.split('/').at(-2);
      if(overrides[`${mode}:${rate}`])return overrides[`${mode}:${rate}`];
      return {ok:true,json:async()=>({rate,source_duration:303.726,artifacts:Object.fromEntries(['vocals','piano','instrumental'].map(key=>[`${key}.wav`,`/tempo/${rate}/${key}.wav`]))})};
    }
    return {ok:true,json:async()=>data(url.split('/').at(-1))};
  };
}
test('all speeds and 12 switches preserve source time, gain, view, pitch-rate and single-source ownership',async()=>{
  const h=harness();tempoFetch(h);h.run('state.position=120;state.muted.piano=true;state.volumes.vocals=.3;state.zoom=4;state.heightZoom=3');h.node('#timeline-scroller').scrollTop=200;h.node('#timeline-scroller').scrollLeft=900;
  await h.run('play()');h.audio.currentTime+=.02;
  for(const rate of [.25,.5,.75,1,1.25,1.5,1.75,2,.5,1,.25,2]){
    const old=h.run('Object.values(state.sources)'),before=h.run('currentPosition()');await h.run(`requestRate(${rate})`);
    assert.equal(h.run('state.rate'),rate);assert.ok(Math.abs(h.run('currentPosition()')-before)<1e-8);assert.equal(h.run('state.playing'),true);
    const sources=h.run('Object.values(state.sources)');assert.equal(sources.length,3);assert.equal(new Set(sources.map(s=>s.at)).size,1);
    assert.ok(sources.every(s=>Math.abs(s.offset-before/rate)<1e-8));assert.ok(old.every(s=>s.stopped));
    h.audio.currentTime+=.1;assert.ok(Math.abs(h.run('currentPosition()')-(before+.1*rate))<1e-8);
    assert.equal(h.run('state.muted.piano'),true);assert.equal(h.run('state.volumes.vocals'),.3);assert.equal(h.run('state.zoom'),4);assert.equal(h.run('state.heightZoom'),3);assert.equal(h.node('#timeline-scroller').scrollTop,200);assert.equal(h.node('#timeline-scroller').scrollLeft,900);
  }
  h.run('pause()');assert.ok(h.recorded.every(source=>source.stopped));
});
test('speed in pause never starts sound; seek and EOF use original time at every speed',async()=>{
  for(const rate of [.25,.5,.75,1,1.25,1.5,1.75,2]){
    const h=harness();tempoFetch(h);h.run('state.position=120');await h.run(`requestRate(${rate})`);assert.equal(h.recorded.length,0);assert.equal(h.run('state.position'),120);
    h.run('seek(200)');await h.run('play()');assert.ok(h.recorded.every(source=>Math.abs(source.offset-200/rate)<1e-8));
    h.audio.currentTime+=400/rate;h.run('state.sources.vocals.onended()');assert.equal(h.run('state.playing'),false);assert.equal(h.run('state.position'),303.726);
    await h.run('play()');assert.equal(h.recorded.at(-1).offset,0);
  }
});
test('last speed wins, concurrent mode keeps requested speed, failure and retry retain working pair',async()=>{
  const h=harness(),late=deferred();tempoFetch(h,{'original:0.25':late.promise});h.run('state.position=70');
  const first=h.run('requestRate(.25)'),latestMode=h.run("requestMode('medium')");
  late.resolve({ok:true,json:async()=>({rate:.25,source_duration:303.726,artifacts:{}})});await Promise.all([first,latestMode]);
  assert.equal(h.run('state.mode'),'medium');assert.equal(h.run('state.rate'),.25);assert.equal(h.run('state.position'),70);
  const late2=deferred();tempoFetch(h,{'medium:0.5':late2.promise});const second=h.run('requestRate(.5)'),latestRate=h.run('requestRate(1.5)');late2.resolve({ok:true,json:async()=>({})});await Promise.all([second,latestRate]);
  assert.equal(h.run('state.rate'),1.5);const buffers=h.run('state.buffers');h.env.fetch=async()=>{throw Error('tempo unavailable');};await h.run('requestRate(.75)');assert.equal(h.run('state.buffers'),buffers);assert.equal(h.run('state.rate'),1.5);assert.match(h.node('#learning-status').textContent,/tempo unavailable/);
  tempoFetch(h);await h.run('requestRate(.75)');assert.equal(h.run('state.rate'),.75);
});
test('failed multi-track scheduling leaves previous source set sounding',async()=>{
  const h=harness();tempoFetch(h);await h.run('play()');const old=h.run('Object.values(state.sources)'),create=h.audio.createBufferSource;let count=0;
  h.audio.createBufferSource=()=>{if(++count===2)throw Error('device scheduling failure');return create();};await h.run('requestRate(.5)');
  assert.equal(h.run('state.rate'),1);assert.ok(old.every(source=>!source.stopped));assert.ok(h.recorded.at(-1).stopped);assert.equal(h.run('state.playing'),true);
});
test('canonical lyric occurrences, punctuation and missing times remain complete; note continuations share part IDs',()=>{
  const h=harness();h.run(`state.canonicalText='I, I!\\nextraordinary?';state.canonicalWords=[{id:'a',text:'I,',char_start:0,char_end:2},{id:'b',text:'I!',char_start:3,char_end:5},{id:'c',text:'extraordinary?',char_start:6,char_end:20}];state.learning={words:[],word_note_links:[],notes:[]};flattenWords();renderFullLyrics();`);
  assert.equal(h.node('#full-lyrics').children.map(node=>node.textContent).join(''),'I, I!\nextraordinary?');assert.equal(h.run('state.words.length'),3);
  h.run(`state.learning={text_parts:[{id:'p',word_id:'a',text:'ma',kind:'syllable'}],note_text_links:[{note_id:'n1',part_id:'p',word_id:'a',text:'ma',continuation:false},{note_id:'n2',part_id:'p',word_id:'a',text:'ma',continuation:true}]}`);
  assert.equal(h.run("noteLabels({id:'n1'})[0].part_id"),h.run("noteLabels({id:'n2'})[0].part_id"));assert.equal(h.run("noteLabels({id:'n2'})[0].continuation"),true);
  assert.ok(!/fillText\([^;]+,\s*Math\.max/.test(source),'Text must not use a width-compression argument');
});
test('pitch range contains extreme real notes and frames instead of fixed MIDI limits',()=>{
  const h=harness();h.run('state.melody={notes:[{midi:24},{midi:105}],pitch_frames:[{midi:112},{midi:null}]};updatePitchRange()');assert.equal(h.run('state.midiMin'),22);assert.equal(h.run('state.midiMax'),114);
});

test('a long note keeps its label when its beginning is scrolled offscreen',()=>{
  const h=harness(),calls=[];
  h.node('#timeline-canvas').getContext=()=>new Proxy({measureText:text=>({width:text.length*7}),fillText:(...args)=>calls.push(args)},{get:(o,key)=>o[key]||(()=>{})});
  h.run(`state.zoom=8;state.learning={notes:[{id:'held',midi:60,start:1,end:6,labels:[{text:'Home',start:1,end:6}]}],text_parts:[]};state.words=[]`);
  h.node('#timeline-scroller').scrollLeft=3*28*8;
  h.run('drawTimeline(4)');
  const label=calls.find(args=>args[0]==='Home');
  assert.ok(label,'The continued note must still have a visible label');
  assert.ok(label[1]>=74&&label[1]<1000,'Label must be inside the visible timeline, not behind the keyboard');
});

test('timed lyrics without notes remain in the fixed lyric lane after vertical scrolling',()=>{
  const h=harness(),calls=[];
  h.node('#timeline-canvas').getContext=()=>new Proxy({measureText:text=>({width:text.length*7}),fillText:(...args)=>calls.push(args)},{get:(o,key)=>o[key]||(()=>{})});
  h.run(`state.words=[{id:'a',text:'Still',start:1,end:2,approximate:false},{id:'b',text:'here!',start:2,end:3,approximate:false}];state.learning={notes:[],text_parts:[]};state.zoom=8`);
  h.node('#timeline-scroller').scrollTop=1600;
  h.run('drawTimeline(1.5)');
  for(const word of ['Still','here!'])assert.ok(calls.some(args=>args[0]===word&&args[2]>=50&&args[2]<80));
});

test('lyric context is visibly distinct from a recognized note label',()=>{
  const h=harness(),calls=[];
  h.node('#timeline-canvas').getContext=()=>new Proxy({measureText:text=>({width:text.length*7}),fillText:(...args)=>calls.push(args)},{get:(o,key)=>o[key]||(()=>{})});
  h.run(`state.learning={notes:[{id:'context',midi:60,start:1,end:2,labels:[{text:'Home',start:1,end:2,status:'context'}]}],text_parts:[]};drawTimeline(1.5)`);
  assert.ok(calls.some(args=>args[0]==='Контекст: Home'));
});

test('Python codepoint offsets retain emoji, whitespace and derived word diagnostics',()=>{
  const h=harness();h.run(`state.canonicalText='🙂 hi\\nagain!';state.canonicalWords=[{id:'a',text:'🙂',char_start:0,char_end:1},{id:'b',text:'hi',char_start:2,char_end:4,status:'old'},{id:'c',text:'again!',char_start:5,char_end:11}];state.learning={words:[{id:'b',text:'hi',status:'check_timing',message:'derived warning'}],word_note_links:[]};flattenWords();renderFullLyrics()`);
  assert.equal(h.node('#full-lyrics').children.map(node=>node.textContent).join(''),'🙂 hi\nagain!');assert.equal(h.run('state.words[1].status'),'check_timing');assert.equal(h.run('state.words[1].message'),'derived warning');
});

test('ten rapid speed choices coalesce cold server work to current and latest preparation',async()=>{
  const h=harness(),first=deferred();tempoFetch(h,{'original:0.25':first.promise});const fetch=h.env.fetch,calls=[];
  h.env.fetch=(url,options)=>{if(url.includes('/tempo/')&&!url.endsWith('.wav')){calls.push(url);assert.equal(options.signal,undefined,'Tempo POST must finish despite stale UI request');}return fetch(url,options);};
  h.run('state.position=83');const pending=[h.run('requestRate(.25)')];
  for(const rate of [.5,.75,1.25,1.5,1.75,2,.5,.75,1.5,2])pending.push(h.run(`requestRate(${rate})`));
  assert.equal(calls.length,1);assert.equal(h.run('state.rate'),1);assert.equal(h.run('state.position'),83);
  first.resolve({ok:true,json:async()=>({rate:.25,source_duration:303.726,artifacts:{}})});await Promise.all(pending);
  assert.equal(calls.length,2);assert.ok(calls[1].endsWith('/original/2'));assert.equal(h.run('state.rate'),2);assert.equal(h.run('state.position'),83);assert.equal(h.recorded.length,0);
});

// Observe the shipped Canvas renderer, rather than testing a grouping helper.
function melismaCanvas(notes){
  const h=harness(),text=[],bars=[];
  const ctx=new Proxy({measureText:value=>({width:value.length*7}),
    fillText(value,x,y){if(this.font==='500 12px -apple-system,sans-serif'&&y>=80)text.push({text:value,x,y});},
    fillRect(x,y,w,height){if(['#8587f7','#a9abff','#6669ae'].includes(this.fillStyle))bars.push({x,y,w,height});}
  },{get:(o,key)=>o[key]||(()=>{})});
  h.node('#timeline-canvas').getContext=()=>ctx;
  h.env.fixtureNotes=notes;
  h.run('state.zoom=4;state.heightZoom=2;state.midiMax=65;state.learning={notes:fixtureNotes,text_parts:[]};state.words=[]');
  return {...h,text,bars,draw(position=0){text.length=bars.length=0;h.run(`drawTimeline(${position})`);return text.map(call=>call.text);}};
}
function melismaNote(id,start,end,{midi=60,part='p',word='w',text='ma',continuation=true,...label}={}){
  return {id,start,end,midi,labels:[{text,part_id:part,word_id:word,start,end,continuation,status:'confirmed',...label}]};
}
const threeNoteRun=()=>[melismaNote('a',1,2,{continuation:false}),melismaNote('b',2,3),melismaNote('c',3,4)];

test('melisma draws one full anchor and two continuation marks without changing note geometry',()=>{
  const h=melismaCanvas(threeNoteRun());
  assert.deepEqual(h.draw(),['ma','─','─']);
  assert.deepEqual(h.bars.map(bar=>[bar.x,bar.w]),[[186,112],[298,112],[410,112]]);
  assert.ok(h.text.every(call=>call.x>=74&&call.x<1000&&call.y>=94&&call.y<=476));
});

test('melisma restores an anchor for horizontal entry, a clipped long note and return to the start',()=>{
  const h=melismaCanvas(threeNoteRun()),scroll=h.node('#timeline-scroller');
  scroll.scrollLeft=2.2*112;
  assert.deepEqual(h.draw(2.3),['ma ─','─']);
  assert.ok(h.text[0].x>=74);
  assert.deepEqual(h.draw(3.5),['ma ─','─'],'Player time alone must not move the anchor');
  scroll.scrollLeft=1.5*112;assert.deepEqual(h.draw(),['ma ─','─','─']);
  scroll.scrollLeft=0;assert.deepEqual(h.draw(),['ma','─','─']);
});

test('melisma restores the anchor when the first pitch is above or below the viewport',()=>{
  for(const midi of [72,42]){
    const notes=threeNoteRun();notes[0].midi=midi;
    const h=melismaCanvas(notes);h.run('state.midiMax=72');
    h.node('#timeline-scroller').scrollTop=midi===72?50:0;
    assert.deepEqual(h.draw(),['ma ─','─']);
    h.node('#timeline-scroller').scrollTop=0;
    h.run('state.heightZoom=.5');assert.deepEqual(h.draw(),['ma','─','─']);
    h.run('state.heightZoom=2');h.node('#timeline-scroller').scrollTop=midi===72?50:0;
    assert.deepEqual(h.draw(),['ma ─','─']);
  }
});

test('melisma keeps partially visible pitch and right-edge anchors readable in a low canvas',()=>{
  const h=melismaCanvas(threeNoteRun());
  h.node('#timeline-scroller').scrollTop=140; // First row is clipped by the fixed header.
  assert.deepEqual(h.draw(),['ma','─','─']);
  assert.ok(h.text.every(call=>call.y>=94&&call.y<=476));
  h.node('#timeline-scroller').scrollTop=0;
  h.node('#timeline-canvas').height=200;
  h.run('state.midiMax=60;state.zoom=32');
  assert.deepEqual(h.draw(),['ma']);
  assert.ok(h.text[0].x+2*7<1000,'A tiny visible edge must still show a full anchor');
});

test('melisma never joins repeated words, equal-spelling parts, restarted links or intervening hidden parts',()=>{
  for(const changes of [{word:'second'},{part:'second'},{continuation:false}]){
    const h=melismaCanvas([melismaNote('a',1,2,{continuation:false}),melismaNote('b',2,3,changes)]);
    assert.deepEqual(h.draw(),['ma','ma']);
  }
  const h=melismaCanvas([melismaNote('a',1,2,{continuation:false}),melismaNote('hidden',2,3,{part:'other',midi:90}),melismaNote('b',3,4)]);
  assert.deepEqual(h.draw(),['ma','ma']);
});

test('melisma preserves multiple parts within one note and excludes offscreen parts from anchoring',()=>{
  const a=melismaNote('a',1,3,{continuation:false});
  a.labels=[{...a.labels[0],end:2},{...a.labels[0],part_id:'p2',text:'ro',start:2,end:3}];
  const h=melismaCanvas([a,melismaNote('b',3,4,{part:'p2',text:'ro'})]);
  assert.deepEqual(h.draw(),['ma','ro','─']);
  h.node('#timeline-scroller').scrollLeft=2.2*112;
  assert.deepEqual(h.draw(),['ro ─','─'],'Hidden first part must not leave a phantom label');
});

test('melisma preserves pauses between notes and inside a legacy multi-interval event',()=>{
  const h=melismaCanvas([melismaNote('a',1,2,{continuation:false}),melismaNote('b',3,4)]);
  assert.deepEqual(h.draw(),['ma','ma']);
  assert.deepEqual(h.bars.map(bar=>[bar.x,bar.w]),[[186,112],[410,112]]);
  const held=melismaNote('held',1,4,{continuation:false});held.intervals=[{start:1,end:2},{start:3,end:4}];
  const legacy=melismaCanvas([held]);assert.deepEqual(legacy.draw(),['ma','ma']);
  assert.deepEqual(legacy.bars.map(bar=>[bar.x,bar.w]),[[186,112],[410,112]]);
});

test('melisma leaves context explicit even with continuation and matching IDs, then restarts the anchor',()=>{
  for(const status of [{status:'context'},{kind:'lyric-context'},{context:true}]){
    const h=melismaCanvas([melismaNote('a',1,2,{continuation:false}),melismaNote('b',2,3,status),melismaNote('c',3,4)]);
    assert.deepEqual(h.draw(),['ma','Контекст: ma','ma']);
  }
});

test('melisma retains approximate and fallback markers on every continuation and full inspector diagnostics',()=>{
  for(const status of [{status:'approximate'},{status:'fallback'},{kind:'whole-word-fallback'},{approximate:true}]){
    const notes=threeNoteRun().map(note=>({...note,labels:note.labels.map(label=>({...label,...status,message:'boundary estimate',reason:'weak_character_evidence'}))}));
    const h=melismaCanvas(notes);assert.deepEqual(h.draw(),['≈ ma','≈ ─','≈ ─']);
    h.run(`state.position=2.5;state.words=[{id:'w',text:'mama!',start:1,end:4,links:[{note_id:'a'},{note_id:'b'},{note_id:'c'}]}];renderInspector(2.5)`);
    assert.match(h.node('#inspector-word-detail').textContent,/ma—/);
    assert.match(h.node('#inspector-word-detail').textContent,/boundary estimate/);
    assert.match(h.node('#inspector-word-detail').textContent,/weak_character_evidence/);
    h.node('#next-note').click();assert.match(h.node('#inspector-word-detail').textContent,/ma—/);
  }
});

test('melisma leaves insufficient legacy IDs and word-only fallback labels complete',()=>{
  for(const missing of [{part:undefined},{word:undefined}]){
    const h=melismaCanvas([melismaNote('a',1,2,{...missing,continuation:false}),melismaNote('b',2,3,missing)]);
    // Defaults in the fixture are intentionally removed from the legacy payload.
    for(const note of h.env.fixtureNotes)for(const label of note.labels)delete label['part' in missing?'part_id':'word_id'];
    assert.deepEqual(h.draw(),['ma','ma—']);
  }
  const h=melismaCanvas([{id:'a',midi:60,start:1,end:2},{id:'b',midi:60,start:2,end:3}]);
  h.run(`state.words=[{id:'w',text:'whole!',start:1,end:3,links:[{note_id:'a'},{note_id:'b'}]}]`);
  assert.deepEqual(h.draw(),['≈ whole!','≈ whole!']);
});

test('melisma drawing across scales leaves canonical data and transport scheduling untouched',async()=>{
  const h=melismaCanvas(threeNoteRun());
  const before=h.run('JSON.stringify({learning:state.learning,melody:state.melody,words:state.words})');
  await h.run('play()');const sources=h.recorded.slice(),schedule=sources.map(node=>[node.at,node.offset]);
  for(const zoom of [.5,1,4,16,32])for(const height of [1,2,4,6]){
    h.run(`state.zoom=${zoom};state.heightZoom=${height}`);h.draw(2.5);
    assert.equal(h.run('JSON.stringify({learning:state.learning,melody:state.melody,words:state.words})'),before);
  }
  assert.deepEqual(h.recorded,sources);assert.deepEqual(h.recorded.map(node=>[node.at,node.offset]),schedule);
  assert.ok(sources.every(node=>!node.stopped));
});

function sharedNote(){return {id:'p00279',start:139.26,end:139.72,midi:50,intervals:[{start:139.26,end:139.72}],labels:[
  {word_id:'when',part_id:'when-p',text:'When',start:139.26,end:139.4241,status:'approximate'},
  {word_id:'you',part_id:'you-p',text:'you',start:139.555,end:139.72,status:'approximate'}
]};}
function sharedCanvas(){
  const h=melismaCanvas([sharedNote()]);h.run(`state.midiMax=52;state.zoom=16;state.words=[{id:'when',text:'When',start:139.26,end:139.4241,links:[{note_id:'p00279'}]},{id:'you',text:'you',start:139.555,end:139.72,links:[{note_id:'p00279'}]}]`);
  h.node('#timeline-scroller').scrollLeft=139*448;h.draw(139.3);return h;
}
test('A01 real word areas preserve the 0.1309-second sounding gap and original note contour',()=>{
  const h=sharedCanvas();
  assert.deepEqual(h.draw(139.3),['≈ When','≈ you']);
  const regions=h.run('state.timelineHits');
  assert.deepEqual(Array.from(regions,r=>[r.start,r.end,r.labels[0]?.word_id||null]),[[139.26,139.4241,'when'],[139.4241,139.555,null],[139.555,139.72,'you']]);
  assert.ok(Math.abs(regions[1].end-regions[1].start-.1309)<1e-10);
  assert.ok(regions[1].gap);assert.equal(h.bars.length,1);assert.ok(Math.abs(h.bars[0].w-.46*448)<1e-8);
  assert.equal(h.run('activeNoteIndex(139.5)'),0,'Gap is sounding, not silence');
});
test('A02 clicking either word or the gap selects the source note and uses exact pointer time',()=>{
  const h=sharedCanvas(),canvas=h.node('#timeline-canvas');
  for(const [time,word] of [[139.3,'when'],[139.5,null],[139.6,'you']]){
    h.draw(time);const region=h.run(`state.timelineHits.find(r=>r.start<=${time}&&r.end>${time})`);
    canvas.handlers.click({currentTarget:canvas,clientX:74+(time-139)*448,clientY:(region.y+region.endY)/2});
    assert.equal(h.run('state.selectedRegion.note.id'),'p00279');assert.equal(h.run('state.selectedWord'),word);
    assert.ok(Math.abs(h.run('state.position')-time)<1e-9);assert.equal(h.node('#inspector-position').textContent,'1 / 1');
    if(word)assert.match(h.node('#inspector-region-detail').textContent,new RegExp(`word_id: ${word}`));
    else {assert.equal(h.node('#inspector-word').textContent,'Нет слова');assert.match(h.node('#inspector-region-detail').textContent,/Звучание продолжается/);}
  }
});
test('A04 all labels stay outside every visible bar, at every time and height scale',()=>{
  const h=melismaCanvas(threeNoteRun());
  for(const zoom of [.5,1,4,16,32])for(const height of [1,2,4,6]){
    h.run(`state.zoom=${zoom};state.heightZoom=${height}`);h.draw(2.5);
    const labels=h.run('state.labelLayout.labels');
    for(const label of labels){
      assert.ok(label.y>=94&&label.x>=74&&label.x+label.measured+8<=1000);
      for(const bar of h.bars)assert.ok(label.y+4<=Math.max(110,bar.y)||label.y-12>=bar.y+bar.height||label.x>=bar.x+bar.w||label.x+label.measured+8<=bar.x,'Text overlaps a bar');
    }
    for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++){
      const a=labels[i],b=labels[j];assert.ok(a.x+a.measured+8<=b.x||b.x+b.measured+8<=a.x||a.y+4<=b.y-12||b.y+4<=a.y-12,'Labels overlap');
    }
  }
});
test('A04 a crowded group never leaves orphan continuation marks and prioritizes selection',()=>{
  const notes=Array.from({length:20},(_,i)=>melismaNote(String(i),1+i*.04,1+(i+1)*.04,{word:`w${Math.floor(i/2)}`,part:`p${Math.floor(i/2)}`,text:`long-word-${Math.floor(i/2)}`,continuation:i%2===1}));
  const h=melismaCanvas(notes);h.run("state.zoom=.5;state.selectedWord='w8'");const copy=h.draw(1.1);
  assert.ok(copy.some(text=>text.includes('long-word-8')));assert.ok(h.run('state.labelLayout.crowded'));
  const labels=h.run('state.labelLayout.labels');
  for(const label of labels.filter(label=>label.text==='─'))assert.ok(labels.some(anchor=>anchor.wordId===label.wordId&&anchor.text!=='─'));
});
test('A06 word and neighbor DOM text is retained across notes of the same word',()=>{
  const h=melismaCanvas(threeNoteRun());h.run(`state.words=[{id:'before',text:'before'},{id:'w',text:'mama!',start:1,end:4,links:[]},{id:'next',text:'after'},{id:'later',text:'later'}];renderInspector(1.5)`);
  let writes=0,value=h.node('#inspector-word').textContent;
  Object.defineProperty(h.node('#inspector-word'),'textContent',{get:()=>value,set:v=>{value=v;writes++;}});
  h.run('renderInspector(2.5);renderInspector(3.5)');
  assert.equal(writes,0);assert.equal(h.node('#word-previous').textContent,'before');assert.equal(h.node('#word-next').textContent,'after');assert.equal(h.node('#word-after-next').textContent,'later');
});
test('A13 overlap is explicit, keeps both identities and never silently chooses a boundary',()=>{
  const note=sharedNote();note.labels[1].start=139.35;
  const h=melismaCanvas([note]);h.run('state.midiMax=52;state.zoom=16');h.node('#timeline-scroller').scrollLeft=139*448;
  h.draw(139.4);const region=h.run('state.timelineHits.find(r=>r.ambiguous)');
  assert.equal(region.labels.length,2);assert.ok(h.draw().every(text=>text.startsWith('≈')));
  h.env.overlap=region;h.run('state.selectedRegion=overlap;renderInspector(139.4)');
  assert.match(h.node('#inspector-region-detail').textContent,/Спорная|спорная/);assert.match(h.node('#inspector-region-detail').textContent,/When/);assert.match(h.node('#inspector-region-detail').textContent,/you/);
});
test('A13 legacy links without timing remain whole with an explicit explanation',()=>{
  const note=sharedNote();delete note.labels[0].start;
  const h=melismaCanvas([note]);h.run('state.midiMax=52;state.zoom=16');h.node('#timeline-scroller').scrollLeft=139*448;h.draw();
  const regions=h.run('state.timelineHits');assert.equal(regions.length,1);assert.equal(regions[0].undivided,true);
  h.run('renderInspector(139.4)');assert.match(h.node('#inspector-region-detail').textContent,/Недостаточно временных связей/);
});
function followHarness(){const h=harness();h.node('#timeline-scroller').scrollWidth=300000;h.node('#timeline-scroller').scrollHeight=5000;h.run('state.position=140;state.zoom=8;rememberScroll()');return h;}
test('A08 karaoke starts off, persists preference, and enabling or returning never starts audio',()=>{
  const h=followHarness();assert.equal(h.run('state.follow'),'off');h.run('setKaraoke(true)');
  assert.equal(h.run('state.follow'),'following');assert.equal(h.storage.get('vocalcreator:karaoke-follow'),'true');assert.equal(h.recorded.length,0);
  assert.ok(Math.abs(h.node('#timeline-scroller').scrollLeft-(140*224-926*.35))<1e-8);
  h.run('suspendFollow()');h.node('#karaoke-return').click();assert.equal(h.run('state.follow'),'following');assert.equal(h.recorded.length,0);
  const x=h.node('#timeline-scroller').scrollLeft;h.run('setKaraoke(false)');assert.equal(h.node('#timeline-scroller').scrollLeft,x);assert.equal(h.storage.get('vocalcreator:karaoke-follow'),'false');
});
test('A09 following uses the common source clock at all required rates without rescheduling audio',async()=>{
  for(const rate of [.25,.5,1,2]){
    const h=followHarness();h.run(`state.rate=${rate};setKaraoke(true)`);await h.run('play()');const sources=h.recorded.slice();
    for(const dt of [.02,.1,1,3]){
      h.audio.currentTime+=dt;h.run('tick()');const expected=h.run('74+(scroller.clientWidth-74)*.35');
      assert.ok(Math.abs(h.run('74+currentPosition()*pixelsPerSecond()-scroller.scrollLeft')-expected)<1e-8);
      assert.deepEqual(h.recorded,sources);assert.ok(sources.every(source=>!source.stopped));
    }
    h.run('pause()');const x=h.node('#timeline-scroller').scrollLeft;h.audio.currentTime+=10;h.run('tick()');assert.equal(h.node('#timeline-scroller').scrollLeft,x);
  }
});
test('A10 vertical safe area follows note events, ignores F0, retains zoom and prioritizes current pitch',()=>{
  const h=followHarness();h.env.fixtureNotes=[{id:'now',start:139,end:141,midi:60},{id:'high',start:141,end:142,midi:85}];
  h.run('state.learning={notes:fixtureNotes};state.midiMax=90;state.baseRow=12;state.heightZoom=3;setKaraoke(true)');
  const zoom=h.run('state.heightZoom'),y=h.run('midiY(60)');assert.ok(y>110&&y<480);
  const top=h.node('#timeline-scroller').scrollTop;
  for(const pitch of [0,127,40]){h.run(`state.melody.pitch_frames=[{time:140,midi:${pitch}}];followPosition(140)`);assert.equal(h.node('#timeline-scroller').scrollTop,top);}
  h.run('followPosition(141.5)');assert.ok(h.run('midiY(85)')>110&&h.run('midiY(85)')<480);assert.equal(h.run('state.heightZoom'),zoom);
});
test('A11 actual wheel, native scrollbar and Page movement suspend until explicit return',async()=>{
  for(const kind of ['wheel','bar','page']){
    const h=followHarness();h.run('setKaraoke(true)');await h.run('play()');const sources=h.recorded.slice(),scroll=h.node('#timeline-scroller');
    if(kind==='wheel')scroll.handlers.wheel({deltaX:50,deltaY:0,deltaMode:0,preventDefault(){}});
    if(kind==='bar'){scroll.scrollLeft+=60;scroll.handlers.scroll();}
    if(kind==='page')scroll.handlers.keydown({key:'PageDown',shiftKey:true,preventDefault(){},stopPropagation(){}});
    assert.equal(h.run('state.follow'),'suspended');const x=scroll.scrollLeft;h.audio.currentTime+=20;h.run('tick()');assert.equal(scroll.scrollLeft,x);assert.equal(h.run('state.playing'),true);
    assert.deepEqual(h.recorded,sources);h.node('#karaoke-return').click();assert.equal(h.run('state.follow'),'following');assert.equal(h.run('state.playing'),true);assert.deepEqual(h.recorded,sources);
  }
});
test('A11 pending native scroll is detected before a following frame can overwrite it',()=>{
  const h=followHarness();h.run('setKaraoke(true)');h.node('#timeline-scroller').scrollLeft+=60;const x=h.node('#timeline-scroller').scrollLeft;
  h.run('followPosition(150)');assert.equal(h.run('state.follow'),'suspended');assert.equal(h.node('#timeline-scroller').scrollLeft,x);
});
test('A11 programmatic coalesced scroll and system gestures do not suspend follow',()=>{
  const h=followHarness(),scroll=h.node('#timeline-scroller');h.run('setKaraoke(true);moveTimeline(1200,80);moveTimeline(1300,100)');scroll.handlers.scroll();
  assert.equal(h.run('state.follow'),'following');
  for(const modifier of ['ctrlKey','metaKey'])scroll.handlers.wheel({deltaX:50,deltaY:50,[modifier]:true,preventDefault(){throw Error('System gesture captured');}});
  assert.equal(h.run('state.follow'),'following');
  h.run('moveTimeline(0,0)');scroll.handlers.wheel({deltaX:0,deltaY:-50,deltaMode:0,preventDefault(){throw Error('Edge captured');}});
  assert.equal(h.run('state.follow'),'following');
});
test('A12 seek, both zooms, mode, speed and EOF preserve the follow state',async()=>{
  const h=followHarness();tempoFetch(h);h.run('setKaraoke(true)');await h.run('requestMode("light")');
  h.run('seek(150)');h.node('#zoom-slider').handlers.input({target:{value:16}});h.node('#height-slider').handlers.input({target:{value:4}});
  assert.equal(h.run('state.follow'),'following');assert.ok(Math.abs(h.run('74+currentPosition()*pixelsPerSecond()-scroller.scrollLeft')-(74+926*.35))<1e-8);
  h.run('suspendFollow()');const x=h.node('#timeline-scroller').scrollLeft;h.run('seek(145)');await h.run('requestMode("pro")');await h.run('requestRate(.5)');
  assert.equal(h.run('state.follow'),'suspended');assert.equal(h.node('#timeline-scroller').scrollLeft,x);assert.equal(h.run('state.playing'),false);
  h.node('#karaoke-return').click();h.run('state.position=303.7');await h.run('play()');h.audio.currentTime+=1;h.run('tick()');const end=h.node('#timeline-scroller').scrollLeft;
  h.audio.currentTime+=3;h.run('tick()');assert.equal(h.node('#timeline-scroller').scrollLeft,end);assert.equal(h.run('state.frame'),null);
});
test('A13 missing notes still follow time; unavailable storage leaves toggle functional',()=>{
  const h=followHarness();h.env.localStorage.setItem=()=>{throw Error('storage blocked');};h.run('setKaraoke(true);followPosition(160)');
  assert.equal(h.run('state.follow'),'following');assert.ok(Math.abs(h.node('#timeline-scroller').scrollLeft-(160*224-926*.35))<1e-8);
});

test('A12 reload retains the toggle but never persists suspended state or starts playback',()=>{
  const h=harness([['vocalcreator:karaoke-follow','true']]);assert.equal(h.run('state.follow'),'following');assert.equal(h.run('state.playing'),false);
  h.run('suspendFollow()');assert.equal(h.storage.get('vocalcreator:karaoke-follow'),'true');
  const reopened=harness([...h.storage]);assert.equal(reopened.run('state.follow'),'following');assert.equal(reopened.recorded.length,0);
});
test('A10 reduced motion centers immediately, while notes inside the safe area leave height alone',()=>{
  const h=followHarness();h.env.window.matchMedia=()=>({matches:true});h.env.fixtureNotes=[{id:'low',start:139,end:141,midi:50},{id:'high',start:141,end:142,midi:85}];
  h.run('state.learning={notes:fixtureNotes};state.midiMax=90;state.baseRow=12;state.heightZoom=3;setKaraoke(true);followPosition(141.5)');
  assert.ok(Math.abs(h.run('midiY(85)')-295)<1e-8);
  const top=h.node('#timeline-scroller').scrollTop;h.run('followPosition(141.6)');assert.equal(h.node('#timeline-scroller').scrollTop,top);
});
test('A12 background return uses current audio time without scheduling extra sources or a second animation loop',async()=>{
  const h=followHarness();h.run('setKaraoke(true)');await h.run('play()');const starts=h.recorded.slice();h.audio.currentTime+=30;
  h.env.document.hidden=false;h.env.document.handlers.visibilitychange();
  assert.ok(Math.abs(h.run('74+currentPosition()*pixelsPerSecond()-scroller.scrollLeft')-(74+926*.35))<1e-8);assert.deepEqual(h.recorded,starts);
  const x=h.node('#timeline-scroller').scrollLeft;h.run('pause()');h.audio.currentTime+=10;h.env.document.handlers.visibilitychange();assert.equal(h.node('#timeline-scroller').scrollLeft,x);
});

test('A12 keyboard return keeps its focus target until focus leaves the control',()=>{
  const h=followHarness(),button=h.node('#karaoke-return');h.run('setKaraoke(true);suspendFollow()');h.env.document.activeElement=button;button.click();
  assert.equal(h.run('state.follow'),'following');assert.equal(h.env.document.activeElement,button);assert.equal(button.disabled,false);assert.equal(button.attributes['aria-hidden'],'false');
  h.env.document.activeElement=h.node('#karaoke-toggle');button.handlers.blur();assert.equal(button.disabled,true);assert.equal(button.attributes['aria-hidden'],'true');
});
