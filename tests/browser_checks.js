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
  pause();state.duration=303.726;state.position=0;state.mode='pro';state.rate=1;state.pendingRate=null;state.modeCache.clear();state.tempoCache.clear();state.v3Enabled=true;state.loadingAudio=false;state.modeAvailability={};state.job={id:'browser-calibration',artifacts:{}};
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
    const screenY=(scroller.clientHeight+80)/2,anchor=state.midiMax+.5-(screenY+scroller.scrollTop-96)/rowHeight();
    $('#height-slider').value='4';$('#height-slider').dispatchEvent(new Event('input',{bubbles:true}));
    assert(Math.abs(state.midiMax+.5-(screenY+scroller.scrollTop-96)/rowHeight()-anchor)<.15,'Height anchor moved');
    await play();scroller.scrollLeft=400;scroller.scrollTop=100;await wait(100);assert(scroller.scrollLeft===400&&scroller.scrollTop===100,'Playback overrode manual scroll');pause();
    const canvasRect=$('#timeline-canvas').getBoundingClientRect(),scrollRect=scroller.getBoundingClientRect();assert(Math.abs(canvasRect.left-scrollRect.left)<2&&Math.abs(canvasRect.top-scrollRect.top)<2,'Canvas ruler/keyboard not pinned');
  });
  await check('M7-M8: actual source replacement, latest result and failure rollback',async()=>{
    const originalFetch=window.fetch.bind(window),realDecode=context.decodeAudioData.bind(context);
    const payload=mode=>({mode,notes:[{id:mode,start:0,end:1,midi:60,cents:0,confidence:.9,intervals:[{start:0,end:1}]}],words:[],word_note_links:[],diagnostics:{},artifacts:{'piano.wav':`/__calibration-${mode}.wav`,'learning.json':'/__calibration.json'}});
    let delayed;
    window.fetch=async(url,options)=>{
      if(String(url).includes('/v3/light'))return new Promise(resolve=>{delayed=()=>resolve({ok:true,json:async()=>payload('light')});});
      if(String(url).includes('/v3/medium'))throw Error('mode-prepare-failed');
      if(String(url).includes('/v3/pro'))return {ok:true,json:async()=>payload('pro')};
      if(String(url).endsWith('.wav'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(0)};
      return originalFetch(url,options);
    };
    context.decodeAudioData=async()=>buffer;
    try{
      state.position=150;await play();const vocals=state.sources.vocals,backing=state.sources.instrumental,old=state.sources.piano;
      const pending=requestMode('light');await requestMode('pro');delayed();await pending;
      assert(state.mode==='pro'&&state.learning.notes[0].id==='pro','Late Light won');assert(state.sources.vocals===vocals&&state.sources.instrumental===backing,'Voice/backing restarted');assert(state.sources.piano!==old,'Piano not replaced');
      const piano=state.sources.piano;await requestMode('medium');assert(state.mode==='pro'&&state.sources.piano===piano,'Failed mode replaced active source');pause();
      state.position=123;await play();const scheduled=starts.at(-3);await requestMode('pro');const replacement=starts.at(-1);
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
