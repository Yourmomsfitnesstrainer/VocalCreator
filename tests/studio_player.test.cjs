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
function harness(){
  const elements=new Map(),radios=['light','medium','pro'].map(value=>{const node=new Element();node.value=value;node.parentElement=new Element();return node;});
  const document=new Element();document.querySelector=s=>{if(!elements.has(s))elements.set(s,new Element());return elements.get(s);};document.querySelectorAll=s=>s==='[name="learning-mode"]'?radios:[];document.createElement=()=>new Element();document.createTextNode=text=>{const node=new Element();node.textContent=text;return node;};
  const recorded=[];
  const audio={currentTime:100,state:'running',resume:async()=>{},createGain:()=>({connect(){},gain:{setTargetAtTime(){}}}),decodeAudioData:async value=>value,
    createBufferSource(){const node={buffer:null,stopped:false,connect(){},disconnect(){},start(at,offset){this.at=at;this.offset=offset;recorded.push(this);},stop(at){this.stopped=true;this.stopAt=at;}};return node;}};
  const buffer={duration:303.726,getChannelData:()=>new Float32Array(100)};
  const storage=new Map();
  const env={document,window:{addEventListener(){},devicePixelRatio:1},AbortController,console,
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
  const h=harness(),scroll=h.node('#timeline-scroller');scroll.scrollLeft=1000;scroll.scrollTop=500;h.run('state.position=10');
  scroll.handlers.keydown({key:'PageDown',preventDefault(){},stopPropagation(){}});assert.equal(scroll.scrollTop,900);assert.equal(scroll.scrollLeft,1000);
  scroll.handlers.keydown({key:'PageDown',shiftKey:true,preventDefault(){},stopPropagation(){}});assert.equal(scroll.scrollLeft,1926);
  const center=(scroll.scrollLeft+463)/28;
  h.node('#zoom-slider').handlers.input({target:{value:3}});
  assert.ok(Math.abs((scroll.scrollLeft+463)/84-center)<1e-8);
  const midi=h.run('state.midiMax+.5-(280+scroller.scrollTop-96)/rowHeight()');
  h.node('#height-slider').handlers.input({target:{value:3}});
  assert.ok(Math.abs(h.run('state.midiMax+.5-(280+scroller.scrollTop-96)/rowHeight()')-midi)<1e-8);
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
