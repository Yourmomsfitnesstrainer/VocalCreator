/* Real browser contract runner, served only by scripts/check_studio_browser.py. */
const checkPanel=document.createElement('section');
checkPanel.style.cssText='position:fixed;right:12px;top:120px;z-index:100;background:#131720;border:1px solid #8587f7;padding:16px;width:390px;max-height:75vh;overflow:auto;color:#f4f6fb';
checkPanel.innerHTML='<button id="run-browser-checks" type="button">Запустить проверки браузера</button><pre id="browser-check-output" style="white-space:pre-wrap;font:12px/1.5 monospace">Изолированные калибровочные сигналы. Пользовательские файлы не используются.</pre>';
document.body.append(checkPanel);

async function runBrowserChecks(){
  const output=document.querySelector('#browser-check-output'),report={checks:[],measurements:{},limitations:['Software/Web Audio calibration only; physical speaker latency and human singing acceptance are separate.']};
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const check=async(name,fn)=>{try{await fn();report.checks.push({name,status:'passed'});}catch(error){report.checks.push({name,status:'failed',error:error.message});}output.textContent=JSON.stringify(report,null,2);};
  const context=await ensureContext();await context.resume();
  pause();setKaraoke(false);state.duration=303.726;state.position=0;state.mode='pro';state.rate=1;state.pendingRate=null;state.modeCache.clear();state.tempoCache.clear();state.v3Enabled=true;state.loadingAudio=false;state.modeAvailability={};state.job={id:'browser-calibration',artifacts:{}};
  const buffer=context.createBuffer(1,Math.ceil(state.duration*context.sampleRate),context.sampleRate);
  // A quiet calibration impulse, the same sample in all three tracks.
  for(const time of [0.1,150,303.2])buffer.getChannelData(0)[Math.round(time*context.sampleRate)]=0.05;
  state.buffers={vocals:buffer,piano:buffer,instrumental:buffer};state.originalBuffers={...state.buffers};state.melody={pitch_frames:[],notes:[],word_note_links:[]};state.learning={mode:'pro',notes:[],words:[],word_note_links:[],diagnostics:{},artifacts:{}};state.words=[];
  $('#workspace-empty').hidden=true;$('#studio-result').hidden=false;$('#play-button').disabled=false;$('#zoom-slider').disabled=false;setRangeBounds();resizeTimeline();drawAll();
  const starts=[],create=context.createBufferSource.bind(context);
  context.createBufferSource=()=>{const source=create(),start=source.start.bind(source);source.start=(when,offset,...rest)=>{starts.push({when,offset,source});start(when,offset,...rest);};return source;};
  await check('P1: ten real Web Audio Play/Pause cycles and visible icon geometry',async()=>{
    for(let index=0;index<10;index++){
      $('#play-button').click();await wait(35);assert(state.playing,'Play failed');
      assert($('#play-button use').getAttribute('href')==='#icon-pause','Pause icon missing');
      assert($('#play-button svg').getBBox().width>0,'SVG geometry empty');
      const batch=starts.slice(-3);assert(batch.length===3&&batch.every(s=>s.when===batch[0].when&&s.offset===batch[0].offset),'Tracks not on shared clock');
      $('#play-button').click();assert(!state.playing,'Pause failed');assert(Object.keys(state.sources).length===0,'Sources leaked');
      assert($('#play-button').title==='Воспроизвести','Title stale');
    }
  });
  await check('P2: real end, restart and injected resume failure',async()=>{
    state.position=state.duration-.05;await play();await wait(120);tick();assert(!state.playing,'Did not stop at end');
    await play();assert(state.startPosition===0,'Restart was not at zero');pause();
    const resume=context.resume.bind(context);context.resume=async()=>{throw Error('calibration-device-error');};await play();context.resume=resume;
    assert(!state.playing&&$('#play-button').getAttribute('aria-label')==='Воспроизвести','Error did not restore Play');
    assert($('#transport-status').textContent.includes('calibration-device-error'),'Error not visible');await play();pause();
  });
  await check('P3: no A/B controls or source looping',async()=>{
    assert(!document.querySelector('#set-a,#set-b,#loop-button,#clear-loop'),'A/B still exposed');assert(starts.every(s=>s.source.loop===false),'Hidden looping remains');
  });
  await check('Z1-Z2: two axes, native bars, wheel units, Page keys and both zoom anchors',async()=>{
    const scroller=$('#timeline-scroller'),position=state.position;
    state.heightZoom=3;updatePitchRange();resizeTimeline();
    assert(scroller.scrollHeight>scroller.clientHeight,'No vertical scroll extent');
    assert(scroller.scrollWidth>scroller.clientWidth,'No horizontal scroll extent');
    assert(getComputedStyle(scroller).overflowY==='auto','Native vertical scrollbar absent');
    for(const zoom of [.5,1,3]){
      state.zoom=zoom;resizeTimeline();
      for(const [x,y,mode,shift] of [[0,50,0,false],[60,20,0,false],[20,60,0,false],[0,2,1,false],[0,1,2,false],[0,50,0,true]]){
        scroller.scrollLeft=200;scroller.scrollTop=100;
        const event=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaX:x,deltaY:y,deltaMode:mode,shiftKey:shift});scroller.dispatchEvent(event);
        const dx=(shift&&!x?y:x)*(mode===1?16:mode===2?scroller.clientWidth-74:1),dy=(shift?0:y)*(mode===1?16:mode===2?scroller.clientHeight-80:1);
        assert(event.defaultPrevented,'Gesture did not pan');
        assert(Math.abs(scroller.scrollLeft-(200+dx))<1,'Horizontal gesture applied incorrectly');
        assert(Math.abs(scroller.scrollTop-Math.min(scroller.scrollHeight-scroller.clientHeight,100+dy))<1,'Vertical gesture applied incorrectly');
        assert(state.position===position&&state.zoom===zoom,'Gesture changed transport/zoom');
      }
    }
    scroller.scrollTop=0;const edge=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-50});scroller.dispatchEvent(edge);assert(!edge.defaultPrevented&&scroller.scrollTop===0,'Top boundary captured');
    const system=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:50,ctrlKey:true});scroller.dispatchEvent(system);assert(!system.defaultPrevented&&scroller.scrollTop===0,'System zoom captured');
    scroller.dispatchEvent(new KeyboardEvent('keydown',{key:'PageDown',bubbles:true,cancelable:true}));assert(scroller.scrollTop>0,'PageDown did not pan height');
    const beforeX=scroller.scrollLeft;scroller.dispatchEvent(new KeyboardEvent('keydown',{key:'PageDown',shiftKey:true,bubbles:true,cancelable:true}));assert(scroller.scrollLeft>beforeX,'Shift PageDown did not pan time');
    const center=(scroller.scrollLeft+(scroller.clientWidth-74)/2)/pixelsPerSecond();$('#zoom-slider').value='4';$('#zoom-slider').dispatchEvent(new Event('input',{bubbles:true}));
    assert(Math.abs((scroller.scrollLeft+(scroller.clientWidth-74)/2)/pixelsPerSecond()-center)<.1,'Time zoom center moved');
    const screenY=(scroller.clientHeight+80)/2,anchor=state.midiMax+.5-(screenY+scroller.scrollTop-126)/rowHeight();
    $('#height-slider').value='4';$('#height-slider').dispatchEvent(new Event('input',{bubbles:true}));
    assert(Math.abs(state.midiMax+.5-(screenY+scroller.scrollTop-126)/rowHeight()-anchor)<.15,'Height anchor moved');
    await play();scroller.scrollLeft=400;scroller.scrollTop=100;await wait(100);assert(scroller.scrollLeft===400&&scroller.scrollTop===100,'Playback overrode manual scroll');pause();
    const canvasRect=$('#timeline-canvas').getBoundingClientRect(),scrollRect=scroller.getBoundingClientRect();assert(Math.abs(canvasRect.left-scrollRect.left)<2&&Math.abs(canvasRect.top-scrollRect.top)<2,'Canvas ruler/keyboard not pinned');
  });
  await check('M7-M8: actual source replacement, latest result and failure rollback',async()=>{
    const originalFetch=window.fetch.bind(window),realDecode=context.decodeAudioData.bind(context);
    const payload=mode=>({mode,notes:[{id:mode,start:0,end:1,midi:60,cents:0,confidence:.9,intervals:[{start:0,end:1}]}],words:[],word_note_links:[],diagnostics:{},artifacts:{'piano.wav':`/__calibration-${mode}.wav`,'learning.json':'/__calibration.json'}});
    let delayed,requests=0,fail=false;
    window.fetch=async(url,options)=>{
      if(String(url).endsWith('/melody')){
        if(fail)throw Error('melody-prepare-failed');
        if(++requests===1)return new Promise(resolve=>{delayed=()=>resolve({ok:true,json:async()=>payload('stale')});});
        return {ok:true,json:async()=>payload('full')};
      }
      if(String(url).endsWith('.wav'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(0)};
      return originalFetch(url,options);
    };
    context.decodeAudioData=async()=>buffer;
    try{
      state.position=150;await play();const vocals=state.sources.vocals,backing=state.sources.instrumental,old=state.sources.piano;
      const pending=requestMode('full');await requestMode('full');delayed();await pending;
      assert(state.mode==='full'&&state.learning.notes[0].id==='full','Late preparation won');assert(state.sources.vocals===vocals&&state.sources.instrumental===backing,'Voice/backing restarted');assert(state.sources.piano!==old,'Piano not replaced');
      const piano=state.sources.piano;state.modeCache.clear();fail=true;await requestMode('full');assert(state.mode==='full'&&state.sources.piano===piano,'Failed preparation replaced active source');fail=false;pause();
      state.position=123;await play();const scheduled=starts.at(-3);await requestMode('full');const replacement=starts.at(-1);
      assert(replacement.when>=scheduled.when,'Cached piano started before the common scheduled start');
      const switchError=Math.abs((replacement.offset-replacement.when)-(scheduled.offset-scheduled.when))*1000;
      report.measurements.mode_switch_timeline_error_ms=switchError;
      assert(switchError<.001,'Cached mode switch shifted the shared audio timeline');pause();
    }finally{window.fetch=originalFetch;context.decodeAudioData=realDecode;}
  });
  await check('R1-R4: real audio sources at every rate, 12 switches, shared clock and paused speed',async()=>{
    const originalFetch=window.fetch.bind(window),decode=context.decodeAudioData.bind(context),stretched=new Map();
    window.fetch=async url=>{
      const value=String(url);
      if(value.includes('/tempo/')){const rate=Number(value.split('/').at(-1));return {ok:true,json:async()=>({rate,source_duration:state.duration,artifacts:Object.fromEntries(['vocals','piano','instrumental'].map(key=>[`${key}.wav`,`/__rate/${rate}/${key}.wav`]))})};}
      if(value.startsWith('/__rate/'))return {ok:true,arrayBuffer:async()=>({rate:Number(value.split('/')[2])})};
      return originalFetch(url);
    };
    context.decodeAudioData=async value=>{if(!stretched.has(value.rate))stretched.set(value.rate,context.createBuffer(1,Math.ceil(state.duration/value.rate*context.sampleRate),context.sampleRate));return stretched.get(value.rate);};
    try{
      state.position=120;await play();await wait(40);
      let maximumClockError=0;
      for(const rate of [.25,.5,.75,1,1.25,1.5,1.75,2,.5,1,.25,2]){
        const sourceBefore=state.sources.vocals,before=currentPosition(),at=context.currentTime,previousRate=state.rate;await requestRate(rate);
        assert(state.rate===rate,'Selected speed was not applied');assert(state.sources.vocals!==sourceBefore,'Voice source not replaced');
        const batch=starts.slice(-3);assert(batch.every(s=>s.when===batch[0].when&&s.offset===batch[0].offset),'Speed sources not on same clock');assert(batch.every(s=>s.source.playbackRate.value===1),'Native playbackRate would transpose pitch');
        const elapsed=context.currentTime-at,error=Math.abs(currentPosition()-before-elapsed*previousRate);maximumClockError=Math.max(maximumClockError,error*1000);
        assert(error<.1,'Speed switch reset original time');await wait(25);
      }
      pause();const count=starts.length,position=state.position;await requestRate(.5);assert(starts.length===count&&!state.playing&&state.position===position,'Paused speed started sound');
      report.measurements.speed_switch_clock_error_ms=maximumClockError;
      await requestRate(1);
    }finally{window.fetch=originalFetch;context.decodeAudioData=decode;pause();}
  });
  await check('T1-T6: exact full text, missing words, syllable continuation and no text compression',async()=>{
    const canonical='I, I!\nextraordinary?';state.canonicalText=canonical;state.canonicalWords=[{id:'a',text:'I,',char_start:0,char_end:2},{id:'b',text:'I!',char_start:3,char_end:5},{id:'c',text:'extraordinary?',char_start:6,char_end:20}];
    state.learning={mode:'pro',words:[],word_note_links:[],notes:[{id:'n1',midi:60,start:0,end:.02,labels:[{note_id:'n1',part_id:'p',text:'extraordinary?',start:0,end:.02}]},{id:'n2',midi:62,start:.1,end:.2,labels:[{note_id:'n2',part_id:'p',text:'extraordinary?',continuation:true,start:.1,end:.2}]}],text_parts:[{id:'p',word_id:'c',text:'extraordinary?',kind:'syllable'}],artifacts:{}};
    flattenWords();renderFullLyrics();assert($('#full-lyrics').textContent===canonical,'Canonical text changed');assert($('#full-lyrics').querySelectorAll('button').length===3,'Word occurrence lost');
    assert(noteLabels(activeNotes()[0])[0].part_id===noteLabels(activeNotes()[1])[0].part_id,'Continuation invented a new part');
    const canvas=$('#timeline-canvas'),ctx=canvas.getContext('2d'),fill=ctx.fillText.bind(ctx),calls=[];ctx.fillText=(...args)=>{calls.push(args);fill(...args);};
    state.zoom=16;state.heightZoom=4;updatePitchRange();resizeTimeline();$('#timeline-scroller').scrollLeft=0;$('#timeline-scroller').scrollTop=0;$('#timeline-scroller').scrollTop=Math.max(0,midiY(activeNotes()[0].midi)-($('#timeline-scroller').clientHeight+80)/2);drawAll();ctx.fillText=fill;
    report.measurements.label_fixture={calls:calls.map(args=>args[0]),scroll_top:$('#timeline-scroller').scrollTop,height:$('#timeline-scroller').clientHeight,note_y:midiY(60),row_height:rowHeight()};
    assert(calls.some(args=>args[0].includes('extraordinary?')),'Long note label disappeared');assert(calls.every(args=>args.length<=3),'Canvas compressed label width');
    assert(getComputedStyle($('#full-lyrics')).overflowY==='auto','Full lyrics cannot scroll');assert(getComputedStyle($('#full-lyrics')).whiteSpace==='pre-wrap','Canonical line breaks lost');
    report.measurements.full_text_occurrences=$('#full-lyrics').querySelectorAll('button').length;
  });
  await check('T7: scrolled note text and independent fixed lyric lane',async()=>{
    const canvas=$('#timeline-canvas'),ctx=canvas.getContext('2d'),fill=ctx.fillText.bind(ctx),calls=[];
    ctx.fillText=(...args)=>{calls.push(args);fill(...args);};
    try{
      state.learning={notes:[{id:'held',midi:60,start:1,end:6,labels:[{text:'Home',start:1,end:6}]}],text_parts:[]};
      state.words=[{id:'unpitched',text:'still-here',start:4,end:5,approximate:false}];state.zoom=8;state.heightZoom=2;
      updatePitchRange();resizeTimeline();$('#timeline-scroller').scrollLeft=3*28*8;$('#timeline-scroller').scrollTop=0;
      $('#timeline-scroller').scrollTop=Math.max(0,midiY(60)-($('#timeline-scroller').clientHeight+80)/2);drawTimeline(4);
      const held=calls.find(args=>args[0]==='Home');assert(held&&held[1]>=74&&held[1]<canvas.clientWidth,'Continued note label clipped behind keyboard');
      assert(calls.some(args=>args[0]==='still-here'&&args[2]>=50&&args[2]<80),'No-note word missing from fixed lyric lane');
      report.measurements.text_repair={continued_label_visible:true,no_note_lyric_visible:true};
    }finally{ctx.fillText=fill;}
  });
  await check('T8: melisma Canvas anchors follow both viewport axes without altering source notes',async()=>{
    const canvas=$('#timeline-canvas'),ctx=canvas.getContext('2d'),fill=ctx.fillText,scroller=$('#timeline-scroller'),calls=[];
    ctx.fillText=function(text,x,y,...rest){if(this.font.includes('12px')&&y>=80)calls.push({text,x,y});return fill.call(this,text,x,y,...rest);};
    try{
      const notes=[0,1,2].map(i=>({id:`melisma-${i}`,start:1+i,end:2+i,midi:i===0?65:61-i,
        labels:[{part_id:'part',word_id:'word',text:'held',start:1+i,end:2+i,continuation:i>0,status:'approximate'}]}));
      state.learning={notes,text_parts:[]};state.words=[];state.melody={notes:[],pitch_frames:[]};state.zoom=4;state.heightZoom=2;
      state.midiMax=65;state.midiMin=40;state.baseRow=8;resizeTimeline();scroller.scrollLeft=0;scroller.scrollTop=0;
      const before=JSON.stringify(notes),draw=()=>{calls.length=0;drawTimeline(2.5);return calls.map(call=>call.text).join('|');};
      assert(draw()==='≈ held|≈ ─|≈ ─','Repeated full melisma labels');
      scroller.scrollLeft=2.2*pixelsPerSecond();assert(draw()==='≈ held ─|≈ ─','Horizontal continuation lost its anchor');
      scroller.scrollLeft=0;scroller.scrollTop=50;assert(draw()==='≈ held ─|≈ ─','Hidden first pitch consumed the visible anchor');
      scroller.scrollTop=0;assert(draw()==='≈ held|≈ ─|≈ ─','Return did not restore natural anchor');
      assert(calls.every(call=>call.x>=74&&call.x<canvas.clientWidth&&call.y>=94&&call.y<canvas.clientHeight),'Label escaped visible canvas');
      assert(JSON.stringify(notes)===before,'Rendering mutated original note/label data');
      report.measurements.melisma={horizontal_anchor:true,vertical_anchor:true,return_anchor:true,source_notes_unchanged:true};
    }finally{ctx.fillText=fill;}
  });
  await check('A13: overlapping and legacy links, untimed words and missing notes in the actual DOM/Canvas',async()=>{
    state.selectedWord=null;state.selectedRegion=null;state.inspectorKey=null;state.wordContextKey=null;
    const notes=[{id:'shared',start:1,end:2.5,midi:60,labels:[{word_id:'a',part_id:'pa',text:'alpha',start:1,end:2},{word_id:'b',part_id:'pb',text:'beta',start:1.5,end:2.5}]}];
    state.words=[{id:'a',text:'alpha',start:1,end:2,links:[{note_id:'shared'}]},{id:'b',text:'beta',start:1.5,end:2.5,links:[{note_id:'shared'}]}];
    state.learning={notes,text_parts:[]};state.melody={notes:[],pitch_frames:[]};state.midiMin=40;state.midiMax=65;state.baseRow=12;state.zoom=8;state.heightZoom=1;resizeTimeline();moveTimeline(0,0);drawAll(1.7);
    const overlap=state.timelineHits.find(region=>region.ambiguous);assert(overlap?.labels.length===2,'Overlap lost a word');
    assert(state.labelLayout.labels.some(label=>label.text==='≈ alpha')&&state.labelLayout.labels.some(label=>label.text==='≈ beta'),'Disputed labels not explicit');
    assert($('#inspector-region-detail').textContent.includes('спорная')&&$('#inspector-region-detail').textContent.includes('word_id: b'),'Overlap not accessible');
    const legacy={id:'legacy',start:1,end:2,midi:60,labels:[{text:'old alpha'},{text:'old beta'}]};state.learning={notes:[legacy],text_parts:[]};state.inspectorKey=null;drawAll(1.5);
    assert(state.timelineHits.length===1&&state.timelineHits[0].undivided,'Legacy note divided without timing');assert($('#inspector-region-detail').textContent.includes('Недостаточно временных связей'),'Missing legacy explanation');
    state.learning={notes:[]};state.words=[{id:'untimed',text:'unknown',start:null,end:4}];state.inspectorKey=null;drawAll(2);
    assert($('#inspector-word').textContent==='Нет слова','Null timing became time zero');
    state.words=[{id:'no-note',text:'gamma',start:3,end:4,approximate:false}];state.inspectorKey=null;drawAll(3.5);
    assert($('#inspector-word').textContent==='gamma'&&$('#inspector-note').textContent==='Нет определимой ноты','Word without pitch disappeared');
    state.position=3.5;setKaraoke(true);assert(state.follow==='following'&&!state.playing,'No-note follow started audio');setKaraoke(false);
  });
  await check('Q6: render recorded transport scheduling through OfflineAudioContext',async()=>{
    const rate=8000,duration=303.8,offline=new OfflineAudioContext(3,Math.ceil(duration*rate),rate),merger=offline.createChannelMerger(3);merger.connect(offline.destination);
    const calibration=offline.createBuffer(1,Math.ceil(state.duration*rate),rate);
    for(const time of [0.1,150,303.2])calibration.getChannelData(0)[Math.round(time*rate)]=.5;
    // Use the start times actually scheduled by production play(), normalized to
    // the first source so the offline run is independent of wall-clock uptime.
    const recorded=starts.slice(0,3),base=recorded[0].when;
    recorded.forEach((record,index)=>{const source=offline.createBufferSource();source.buffer=calibration;source.connect(merger,0,index);source.start(record.when-base,record.offset);});
    const rendered=await offline.startRendering(),peaks=[];
    for(let channel=0;channel<3;channel++){const samples=rendered.getChannelData(channel),found=[];for(let i=0;i<samples.length;i++)if(Math.abs(samples[i])>.1)found.push(i);peaks.push(found);}
    assert(peaks.every(p=>p.length===3),'Calibration impulses missing');
    const skew=Math.max(...peaks[0].map((_,i)=>Math.max(...peaks.map(p=>p[i]))-Math.min(...peaks.map(p=>p[i]))))/rate*1000;
    report.measurements.inter_track_skew_ms=skew;report.measurements.calibration_duration_seconds=duration;assert(skew<=20,'Track sync exceeds 20 ms');
    state.position=150;await play();await wait(80);updatePositionUI();
    const text=$('#time-current').textContent,[minute,second]=text.split(':'),shown=Number(minute)*60+Number(second),error=Math.abs(shown-currentPosition())*1000;
    report.measurements.cursor_to_audio_clock_ms=error;assert(error<=50,'Cursor exceeds 50 ms');pause();
  });
  context.createBufferSource=create;pause();
  report.passed=report.checks.every(check=>check.status==='passed');report.browser=navigator.userAgent;
  report.completed_at=new Date().toISOString();output.textContent=JSON.stringify(report,null,2);
  await fetch('/__checks/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(report)});
}
document.querySelector('#run-browser-checks').addEventListener('click',()=>runBrowserChecks().catch(error=>{document.querySelector('#browser-check-output').textContent=`Runner failed: ${error.message}`;}));

// Runs against an already opened reference result. Nothing here submits audio
// or requests new model work; requestMode uses the job's saved v3 variants.
const readingButton=document.createElement('button');readingButton.type='button';readingButton.id='run-reading-checks';readingButton.textContent='Проверить читаемую шкалу и караоке';checkPanel.prepend(readingButton);
async function runReadingChecks(){
  const output=$('#browser-check-output'),report={suite:'reading-karaoke',checks:[],measurements:{viewport:[innerWidth,innerHeight]}};
  const assert=(value,message)=>{if(!value)throw Error(message);},wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const check=async(name,fn)=>{try{await fn();report.checks.push({name,status:'passed'});}catch(error){report.checks.push({name,status:'failed',error:error.message});}output.textContent=JSON.stringify(report,null,2);};
  const jobId=new URLSearchParams(location.search).get('job');assert(jobId,'Open /__checks?job=... with the fixed saved reference');
  pause();await openJob(jobId);assert(state.learning?.notes?.length,'Saved notes did not load');
  const scroll=$('#timeline-scroller'),canvas=$('#timeline-canvas'),ctx=canvas.getContext('2d'),sourceJSON=JSON.stringify(state.learning),canonical=state.canonicalText;
  const initialMode=state.mode,oldFollow=state.follow!=='off';setKaraoke(false);
  const setZoom=(zoom,height)=>{for(const [selector,value] of [['#zoom-slider',zoom],['#height-slider',height]]){const input=$(selector);input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));}};
  const frameAt=(time,zoom=16,height=3)=>{setZoom(zoom,height);seek(time);const note=activeNotes()[activeNoteIndex(time)];moveTimeline(time*pixelsPerSecond()-90,note?scroll.scrollTop+midiY(note.midi)-(scroll.clientHeight+110)/2:undefined);drawAll(time);};
  await check('A01-A02: real word regions, sounding gap, pointer selection and acoustic count',async()=>{
    frameAt(139.3);const note=activeNotes().find(note=>note.id==='p00279'),regions=state.timelineHits.filter(region=>region.note===note);
    assert(regions.length===3,'p00279 must have two words and a gap');
    assert(regions[0].labels[0].text==='When'&&regions[2].labels[0].text==='you','Wrong word identity');
    assert(Math.abs(regions[1].end-regions[1].start-.1309)<1e-9&&regions[1].gap,'Gap timing changed');
    report.measurements.p00279=regions.map(region=>({start:region.start,end:region.end,words:region.labels.map(label=>label.word_id),gap:Boolean(region.gap)}));
    for(const region of regions){
      const rect=canvas.getBoundingClientRect(),time=(region.start+region.end)/2;
      canvas.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:rect.left+74+time*pixelsPerSecond()-scroll.scrollLeft,clientY:rect.top+(region.y+region.endY)/2}));
      assert(state.selectedRegion?.note.id==='p00279','Click lost original event');assert(Math.abs(state.position-time)<.003,'Pointer seek invented a boundary');
      if(region.gap)assert($('#inspector-region-detail').textContent.includes('Звучание продолжается'),'Gap explanation missing');
    }
    frameAt(141.73);const second=state.timelineHits.filter(region=>region.note.id==='p00289');
    assert(second.some(region=>region.labels.some(label=>label.text==='I'))&&second.some(region=>region.labels.some(label=>label.text==='was')),'I/was not independently selectable');
    assert(activeNotes().length===771,'Visual regions became notes');
  });
  await check('A03-A05: real melismas, external collision-free labels, scales and clipped anchors',async()=>{
    const fill=ctx.fillText,calls=[];ctx.fillText=function(...args){calls.push(args);return fill.apply(this,args);};
    try{
      frameAt(140.35,16,3);
      for(const word of ['for','some','more,']){
        const spans=timelinePresentation().spans.filter(span=>span.label.text===word&&span.start>=140&&span.end<142);
        assert(new Set(spans.map(span=>span.note.midi)).size>=2,`${word} pitch change lost`);
        const visible=state.labelLayout.labels.filter(label=>spans.some(span=>span.note.id===label.noteId));
        report.measurements[word]={spans:spans.map(span=>({note:span.note.id,start:span.start,end:span.end,midi:span.note.midi,continued:span.continued})),visible};
        assert(visible.some(label=>label.text.includes(word))&&visible.some(label=>label.text.includes('─')),`${word} anchor/continuation missing`);
      }
      for(const time of [104.9,139.3,140.5,146.9])for(const zoom of [.5,1,8,16,32])for(const height of [1,3,6]){
        calls.length=0;frameAt(time,zoom,height);
        const labels=state.labelLayout.labels;
        assert(calls.every(call=>call.length===3),'Canvas text compressed');
        for(let i=0;i<labels.length;i++){
          const label=labels[i],left=label.x,right=left+label.measured+8,top=label.y-12,bottom=label.y+4;
          assert(left>=74&&right<=canvas.clientWidth+.5&&top>=80&&bottom<=canvas.clientHeight+.5,'Label escaped viewport');
          for(const region of state.timelineHits)assert(right<=region.x||left>=region.endX||bottom<=region.y||top>=region.endY,'Label printed inside a note');
          for(const other of labels.slice(i+1))assert(right<=other.x||left>=other.x+other.measured+8||bottom<=other.y-12||top>=other.y+4,'Labels overlap');
        }
      }
      frameAt(104.9,32,3);moveTimeline(105.2*pixelsPerSecond());drawAll(105.3);
      assert(state.labelLayout.labels.some(label=>label.text.includes('know')&&label.text.includes('─')),'Clipped know anchor missing');
      const copy=state.labelLayout.labels.map(label=>label.text).join('|');drawAll(105.4);assert(state.labelLayout.labels.map(label=>label.text).join('|')===copy,'Cursor alone moved anchor');
    }finally{ctx.fillText=fill;}
  });
  await check('A06-A07: fixed geometry across all notes, long diagnostics and accessible details',async()=>{
    const rect=()=>{const panel=$('#note-inspector').getBoundingClientRect();return [panel.height,canvas.getBoundingClientRect().top,$('#inspector-note').getBoundingClientRect().top,$('#inspector-word').getBoundingClientRect().top];};
    frameAt(139.3);const before=rect();let error=0;
    for(const time of [...activeNotes().map(note=>note.start+.001),...state.words.map(word=>word.start).filter(Number.isFinite),0]){renderInspector(time);const after=rect();error=Math.max(error,...after.map((value,index)=>Math.abs(value-before[index])));}
    assert(error<=1,`Panel geometry changed ${error}px`);report.measurements.inspector_drift_px=error;
    $('#inspector-more').focus();$('#inspector-more').click();assert($('#inspector-dialog').open,'Details did not open');assert($('#inspector-word-detail').textContent.length>0,'Details empty');
    assert(rect().every((value,index)=>Math.abs(value-before[index])<=1),'Details moved timeline');
    $('#inspector-close').click();assert(document.activeElement===$('#inspector-more'),'Dialog did not restore focus');
  });
  await check('A08-A12: real audio follow at all rates/modes, manual suspend, return and clock',async()=>{
    frameAt(139.3,16,4);$('#next-note').focus();const focus=document.activeElement,workspaceTop=$('#main-workspace').scrollTop,lyricsTop=$('#full-lyrics').scrollTop;
    setKaraoke(true);assert(!state.playing,'Enabling started audio');assert(document.activeElement===focus,'Follow stole focus');
    const pairs=new URLSearchParams(location.search).has('full')?[['full',.25],['full',.5],['full',1],['full',2]]:[['full',1]];
    let maxClock=0,minY=Infinity,maxY=-Infinity,lastCursorX=null;
    const move=ctx.moveTo;ctx.moveTo=function(x,y){if(this.strokeStyle==='#58d8ff'&&this.lineWidth===2&&y===0)lastCursorX=x;return move.call(this,x,y);};
    try {
    for(const [mode,rate] of pairs){
      await requestMode(mode);await requestRate(rate);assert(state.mode===mode&&state.rate===rate,'Requested audio pair failed');
      seek(139.2);await play();assert(state.playing,'Audio did not start');const sources={...state.sources},zooms=[state.zoom,state.heightZoom];
      for(let sample=0;sample<12;sample++){
        await wait(45);const time=currentPosition();
        assert(lastCursorX!==null,'Animation did not draw a cursor');
        const error=Math.abs(lastCursorX-(74+(scroll.clientWidth-74)*.35));maxClock=Math.max(maxClock,error);assert(error<=2,`35% anchor error ${error}px`);
        const note=activeNotes()[activeNoteIndex(time)];if(note){const y=midiY(note.midi);minY=Math.min(minY,y);maxY=Math.max(maxY,y);assert(y>=110&&y<scroll.clientHeight,'Current pitch left viewport');}
        assert(state.sources.vocals===sources.vocals&&state.sources.piano===sources.piano&&state.sources.instrumental===sources.instrumental,'Follow rescheduled audio');
      }
      assert(state.zoom===zooms[0]&&state.heightZoom===zooms[1],'Follow changed zoom');
      scroll.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaX:100,deltaY:20}));const manual=[scroll.scrollLeft,scroll.scrollTop];
      assert(state.follow==='suspended','Wheel did not suspend');await wait(100);assert(scroll.scrollLeft===manual[0]&&scroll.scrollTop===manual[1],'Follow fights manual view');
      assert(state.playing,'Manual pan stopped audio');$('#karaoke-return').click();assert(state.follow==='following'&&state.playing,'Return changed playback');pause();
    }
    report.measurements.follow_error_css_px=maxClock;report.measurements.follow_pitch_y=[minY,maxY];report.measurements.audio_pairs=pairs;
    assert($('#full-lyrics').scrollTop===lyricsTop,'Follow moved the lyrics view');
    const x=scroll.scrollLeft,y=scroll.scrollTop;await wait(100);assert(scroll.scrollLeft===x&&scroll.scrollTop===y,'Pause kept moving');
    // Native bar scroll is delivered asynchronously; detect it before the next frame.
    scroll.scrollLeft+=60;await wait(40);assert(state.follow==='suspended','Native scroll did not suspend');
    seek(146.9);assert(state.follow==='suspended','Seek resumed suspended follow');$('#karaoke-return').click();assert(!state.playing&&state.follow==='following','Paused return started audio');
    scroll.dispatchEvent(new KeyboardEvent('keydown',{key:'PageUp',shiftKey:true,bubbles:true,cancelable:true}));assert(state.follow==='suspended','Page key did not suspend');$('#karaoke-return').click();
    const system=new WheelEvent('wheel',{ctrlKey:true,deltaY:20,bubbles:true,cancelable:true});scroll.dispatchEvent(system);assert(!system.defaultPrevented&&state.follow==='following','System zoom suspended follow');
    document.dispatchEvent(new Event('visibilitychange'));assert(state.follow==='following','Visibility changed state');
    seek(state.duration-.03);await play();await wait(140);assert(!state.playing&&state.frame===null,'EOF left active animation/audio');
    } finally {ctx.moveTo=move;pause();}
  });
  await check('A14-A15: immutable result, full lyrics, desktop controls and independent inner view',async()=>{
    await requestMode(initialMode);await requestRate(1);assert(state.canonicalText===canonical,'Canonical text changed');assert(state.words.length===305,'Word occurrence lost');
    assert(JSON.stringify(state.modeCache.get(initialMode).data)===sourceJSON,'Source learning JSON changed');
    assert($('#full-lyrics').textContent===canonical,'Accessible text changed');
    setKaraoke(false);frameAt(139.3,16,3);const workspace=$('#main-workspace');workspace.scrollTop=$('.scale-controls').offsetTop-workspace.offsetTop-$('.transport').offsetHeight;
    await wait(30);
    for(const selector of ['#karaoke-toggle','#height-slider','#inspector-more']){const r=$(selector).getBoundingClientRect();assert(r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,`${selector} inaccessible at desktop size`);}
    report.measurements.controls_visible=true;report.measurements.panel_height=$('#note-inspector').getBoundingClientRect().height;
  });
  await check('BL006-BL009: fixed glyph raster, continuous camera, expanded score and advance reading',async()=>{
    setKaraoke(true);setZoom(16,3);seek(139.3);
    const before=JSON.stringify(activeNotes()),dpr=Math.min(2,devicePixelRatio||1),glyphs=[];
    const image=ctx.drawImage;ctx.drawImage=function(...args){glyphs.push(args);return image.apply(this,args);};
    try{
      drawAll();const layout=state.readingLabels,snapshot=JSON.stringify(layout.labels);
      const vertical=scroll.scrollTop,startX=scroll.scrollLeft;
      for(let i=0;i<60;i++){state.position=139.3+i*.025;followPosition(state.position);drawAll(state.position);}
      assert(scroll.scrollLeft>startX,'Continuous following stopped');assert(scroll.scrollTop===vertical,'Pitch view shifted');
      assert(state.readingLabels===layout&&JSON.stringify(layout.labels)===snapshot,'Labels reflowed with the active word');
      assert(glyphs.length>0&&glyphs.every(args=>Math.abs(args[1]*dpr-Math.round(args[1]*dpr))<1e-6&&Math.abs(args[2]*dpr-Math.round(args[2]*dpr))<1e-6),'Glyphs moved off device pixels');
      for(const rate of [.25,.5,1,2]){
        await requestRate(rate);seek(140);drawAll();
        assert(((scroll.clientWidth-74)*.65-120)/pixelsPerSecond()/state.rate>=3-1e-6,'Real-time reading horizon below 3 seconds');
        const ids=upcomingWords(140).map(word=>word.id);
        assert(ids.length>0&&ids.every(id=>$('#reading-preview').querySelector(`[data-word-id="${id}"]`)),'Next words missing from preview');
        assert($('#reading-preview').scrollWidth<=$('#reading-preview').clientWidth+1,'Reference preview words overflow');
      }
      const rect=scroll.getBoundingClientRect();assert(rect.width>=innerWidth-2,'Sidebar still consumes singing width');assert(rect.bottom<=innerHeight+1&&rect.height>=innerHeight*.6,'Singing canvas does not fit desktop');
      assert($('#note-inspector').getBoundingClientRect().height<=46,'Singing inspector too large');
      assert(!document.querySelector('[name="learning-mode"]')&&!document.querySelector('a[href="/karaoke"]'),'Removed UI still present');
      assert(/^[≈─…∅?к ]*$/u.test($('#inspector-word-status').textContent),'Part repeats status explanations');
      assert(JSON.stringify(activeNotes())===before,'Display changed notes');
      report.measurements.reading={canvas:[rect.width,rect.height],continuous:true,glyph_calls:glyphs.length,stable_layout:true,device_pixel_aligned:true,preview_seconds:3};
    }finally{ctx.drawImage=image;pause();}
  });
  pause();await requestRate(1);setKaraoke(true);seek(139.3);
  report.passed=report.checks.every(item=>item.status==='passed');report.completed_at=new Date().toISOString();report.browser=navigator.userAgent;
  output.textContent=JSON.stringify(report,null,2);await fetch('/__checks/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(report)});
}
readingButton.addEventListener('click',()=>runReadingChecks().catch(error=>{$('#browser-check-output').textContent=`Runner failed: ${error.message}`;}));
