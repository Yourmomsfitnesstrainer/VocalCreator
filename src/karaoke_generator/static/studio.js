const $ = selector => document.querySelector(selector);
const stages = [
  ['preparation','Подготовка аудио и текста'],['separation','Выделение вокала и минуса'],
  ['alignment','Привязка точного текста'],['pitch','Анализ высоты голоса'],
  ['melody','Сегментация нот'],['piano','Синтез пианино']
];
const state = {
  job:null, melody:null, alignment:null, words:[], peaks:[], duration:0, position:0,
  playing:false, starting:false, playRequest:0, startedAt:0, startPosition:0, zoom:1,
  context:null, master:null, buffers:{}, sources:{}, gains:{}, muted:{vocals:false,piano:false,instrumental:false},
  volumes:{vocals:.75,piano:.55,instrumental:.7}, poll:null, frame:null, inspectorKey:null,
  learning:null, mode:null, pendingMode:null, modeRequest:0, jobRequest:0, openRequest:0, modeAbort:null, loadingAudio:false,
  modeAvailability:{}, modeCache:new Map(), tempoCache:new Map(), originalBuffers:{},
  rate:1,pendingRate:null,desiredRate:1,desiredMode:null,v3Enabled:false,canonicalText:'',canonicalWords:[],
  tempoPreparation:null,heightZoom:1,midiMin:48,midiMax:72,baseRow:12,selectedWord:null,wordNodes:new Map()
};
const trackMeta = {
  vocals:{label:'Вокал',file:'vocals.wav'},piano:{label:'Пианино',file:'piano.wav'},instrumental:{label:'Минус',file:'instrumental.wav'}
};

function formatTime(value, precise=true){
  const safe=Math.max(0,Number(value)||0), minutes=Math.floor(safe/60), seconds=safe-minutes*60;
  return `${minutes}:${seconds.toFixed(precise?3:1).padStart(precise?6:4,'0')}`;
}
function setConnection(online){const node=$('#connection');node.classList.toggle('offline',!online);node.lastChild.textContent=online?'Локальный сервис':'Нет соединения';}
function showError(message){const node=$('#form-error');node.textContent=message;node.hidden=false;}
function clearError(){$('#form-error').hidden=true;}
function showTransportStatus(message,{info=false}={}){const node=$('#transport-status');node.textContent=message;node.className=`transport-status${info?' info':''}`;node.hidden=false;}
function clearTransportStatus(){$('#transport-status').hidden=true;}
function json(url,options={}){return fetch(url,{cache:'no-store',...options}).then(async response=>{if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||`HTTP ${response.status}`);}return response.json();});}

function renderStages(job){
  const list=$('#stage-list');list.replaceChildren();
  for(const [key,label] of stages){
    const item=document.createElement('li'), marker=document.createElement('i'), copy=document.createElement('span');
    const status=job.stages?.[key]?.status||((job.current_stage===key||job.stage_label===label)?'running':'queued');
    item.className=status;copy.textContent=job.stages?.[key]?.detail?`${label}: ${job.stages[key].detail}`:label;
    item.append(marker,copy);list.append(item);
  }
  $('#progress-section').hidden=false;$('#progress-title').textContent=job.status==='complete'?'Анализ завершён':job.status==='partial'?'Частичный результат':job.status==='failed'?'Анализ остановлен':'Идёт анализ';
  $('#progress-detail').textContent=job.stage_label||'Текущая стадия неизвестна';
}

function libraryButton(job){
  const button=document.createElement('button');button.type='button';button.className='library-item';button.dataset.id=job.id;
  const name=document.createElement('strong');name.textContent=job.input?.audio_name||`Задание ${job.id.slice(0,8)}`;
  const status=document.createElement('small');status.textContent={complete:'Готово',partial:'Частично',failed:'Ошибка',interrupted:'Прервано',running:'В работе',queued:'В очереди'}[job.status]||job.status;
  const date=document.createElement('span');date.textContent=job.updated_at?new Date(job.updated_at).toLocaleDateString('ru-RU',{day:'2-digit',month:'short'}):'';
  button.append(name,status,date);button.addEventListener('click',()=>openJob(job.id));return button;
}
async function loadLibrary(){
  try{
    const data=await json('/api/studio/jobs');setConnection(true);const list=$('#library-list');list.replaceChildren();
    if(!data.jobs.length){const empty=document.createElement('p');empty.className='empty-copy';empty.textContent='Пока нет результатов.';list.append(empty);return;}
    data.jobs.forEach(job=>list.append(libraryButton(job)));
  }catch(error){setConnection(false);showError(`Не удалось открыть библиотеку: ${error.message}`);}
}

$('#audio-input').addEventListener('change',event=>{$('#audio-name').textContent=event.target.files[0]?.name||'До 5 минут: нормальный рабочий пример';});
$('#lyrics-input').addEventListener('change',event=>{$('#lyrics-name').textContent=event.target.files[0]?.name||'Слова и повторы не переписываются';});
$('#studio-form').addEventListener('submit',async event=>{
  event.preventDefault();clearError();const button=$('#analyze-button');button.disabled=true;button.querySelector('span').textContent='Проверяю файлы…';
  try{
    const response=await fetch('/api/studio/jobs',{method:'POST',body:new FormData(event.currentTarget)}),body=await response.json();
    if(!response.ok)throw new Error(body.detail||'Не удалось начать анализ');
    await watchJob(body.job_id);
  }catch(error){showError(error.message);setConnection(false);}finally{button.disabled=false;button.querySelector('span').textContent='Начать анализ';}
});

async function watchJob(jobId){
  clearInterval(state.poll);let busy=false;
  const check=async()=>{
    if(busy)return;busy=true;
    try{
      const job=await json(`/api/studio/jobs/${jobId}`);setConnection(true);state.job=job;renderStages(job);
      if(['complete','partial','failed','interrupted'].includes(job.status)){
        clearInterval(state.poll);state.poll=null;await loadLibrary();
if(window.location?.search){
  const params=new URLSearchParams(window.location.search),initialJob=params.get('job');
  if(initialJob)openJob(initialJob).then(()=>{
    if(state.job?.id!==initialJob)return;
    for(const [key,selector,min,max] of [['zoom','#zoom-slider',.5,32],['height','#height-slider',1,6]]){
      const value=Number(params.get(key));if(!params.has(key)||!Number.isFinite(value)||value<min||value>max)continue;
      $(selector).value=String(value);$(selector).dispatchEvent(new Event('input',{bubbles:true}));
    }
    const position=Number(params.get('t'));
    if(params.has('t')&&Number.isFinite(position)&&position>=0&&position<=state.duration){
      seek(position);const scroll=$('#timeline-scroller');scroll.scrollLeft=Math.max(0,position*pixelsPerSecond()-(scroll.clientWidth-74)*.35);
      const note=activeNotes()[activeNoteIndex(position)];if(note)scroll.scrollTop=Math.max(0,scroll.scrollTop+midiY(note.midi)-(scroll.clientHeight+80)/2);
    }
    drawAll();
  });
}
        if(Object.keys(job.artifacts||{}).length)await applyJob(job);
      }
    }catch(error){setConnection(false);$('#progress-detail').textContent=`Соединение потеряно: ${error.message}. Повторяю…`;}finally{busy=false;}
  };
  await check();if(!['complete','partial','failed','interrupted'].includes(state.job?.status))state.poll=setInterval(check,900);
}
async function openJob(jobId){
  const request=++state.openRequest;clearInterval(state.poll);state.poll=null;
  clearError();try{const job=await json(`/api/studio/jobs/${jobId}`);if(request!==state.openRequest)return;setConnection(true);state.job=job;renderStages(job);document.querySelectorAll('.library-item').forEach(item=>item.classList.toggle('active',item.dataset.id===jobId));if(['queued','running'].includes(job.status))return watchJob(jobId);await applyJob(job);}catch(error){showError(`Не удалось открыть результат: ${error.message}`);setConnection(false);}
}

async function applyJob(job){
  const request=++state.jobRequest;
  pause();state.modeAbort?.abort();++state.modeRequest;state.job=job;state.melody=null;state.alignment=null;state.words=[];state.buffers={};state.position=0;state.inspectorKey=null;
  state.learning=null;state.mode=null;state.pendingMode=null;state.pendingRate=null;state.rate=state.desiredRate=1;state.desiredMode=null;state.v3Enabled=false;state.canonicalText='';state.canonicalWords=[];state.tempoCache.clear();state.originalBuffers={};state.selectedWord=null;state.wordNodes.clear();state.modeCache.clear();state.modeAvailability={};state.loadingAudio=true;clearTransportStatus();
  $('#play-button').disabled=true;renderModes();
  $('#workspace-empty').hidden=true;$('#studio-result').hidden=false;$('#mixer-section').hidden=false;
  const banner=$('#result-banner'),recovery=$('#result-recovery');banner.className=`result-banner ${job.status}`;$('#result-banner-text').textContent=job.status==='complete'?'Все стадии результата завершены.':job.status==='partial'?'Часть результата доступна; можно изучить сохранённое и повторить анализ.':job.status==='interrupted'?'Анализ прерван; сохранённые файлы доступны.':'Анализ завершился с ошибкой; сохранённые файлы доступны.';recovery.hidden=job.status==='complete';
  $('#result-title').textContent=job.input?.audio_name||'Вокальная партия';
  state.duration=Number(job.timeline?.duration)||0;$('#time-total').textContent=formatTime(state.duration);setRangeBounds();
  const artifacts=job.artifacts||{};
  const [melody,alignment,text]=await Promise.allSettled([
    artifacts['melody.json']?json(artifacts['melody.json']):Promise.resolve(null),
    artifacts['alignment.json']?json(artifacts['alignment.json']):Promise.resolve(null),
    json(`/api/studio/jobs/${job.id}/text`)
  ]);
  if(request!==state.jobRequest)return;
  state.melody=melody.status==='fulfilled'?melody.value:null;state.alignment=alignment.status==='fulfilled'?alignment.value:null;
  if(text.status==='fulfilled'){state.canonicalText=text.value.canonical_text||'';state.canonicalWords=text.value.words||[];}
  flattenWords();renderFullLyrics();renderDownloads(artifacts);renderMixer(artifacts);
  state.modeAvailability={pro:state.melody?null:'Нет совместимых данных нот.',light:state.words.length?null:'Нет временной разметки текста.',medium:state.words.length?null:'Нет временной разметки текста.'};
  if(!state.melody)state.modeAvailability.light=state.modeAvailability.medium=state.modeAvailability.pro;
  renderModes();
  const buffers=await loadAudioTracks(artifacts);
  if(request!==state.jobRequest)return;
  state.buffers=buffers;state.originalBuffers={...buffers};state.loadingAudio=false;renderModes();state.duration=state.duration||Math.max(0,...Object.values(state.buffers).map(buffer=>buffer.duration));
  $('#time-total').textContent=formatTime(state.duration);setRangeBounds();makePeaks();
  const noteCount=state.melody?.notes?.length||0,wordCount=state.words.length,errors=job.errors||[];
  $('#result-summary').textContent=`${noteCount} нот · ${wordCount} слов · ${formatTime(state.duration,false)}`;
  $('#timeline-message').textContent=errors.length?errors.map(error=>`${error.stage}: ${error.message}`).join(' · '):noteCount?'Фиолетовые полоски: ноты; голубая линия: несглаженный pitch.':'Определимых нот не найдено; пустой таймлайн не заполнен догадками.';
  $('#play-button').disabled=!Object.keys(state.buffers).length;$('#zoom-slider').disabled=false;$('#height-slider').disabled=false;$('#speed-select').disabled=!Object.keys(state.buffers).length;$('#speed-select').value='1';$('#speed-status').hidden=true;
  updatePitchRange();$('#timeline-scroller').scrollLeft=0;$('#timeline-scroller').scrollTop=0;
  resizeTimeline();drawAll();
  let preferred='light';try{preferred=localStorage.getItem(`vocalcreator:mode:${job.id}`)||'light';}catch(_){}
  if(!['light','medium','pro'].includes(preferred))preferred='light';
  if(state.modeAvailability[preferred])preferred='pro';
  const cached=job.v3_modes||{};state.v3Enabled=Object.keys(cached).length>0;renderModes();
  if(state.v3Enabled){if(!cached[preferred])preferred=Object.keys(cached)[0];await requestMode(preferred);}
}

$('#result-recovery').addEventListener('click',()=>{
  pause();document.querySelector('.setup-section').scrollIntoView({behavior:'smooth',block:'start'});showError('Выберите исходные файлы заново и запустите анализ: браузер не сохраняет содержимое локальных файлов.');$('#audio-input').focus();
});

function flattenWords(){
  const links=state.learning?.word_note_links||state.melody?.word_note_links||[],byWord=new Map();
  for(const link of links){const list=byWord.get(link.word_id)||[];list.push(link);byWord.set(link.word_id,list);}
  const aligned=[];
  (state.alignment?.lines||[]).forEach((line,lineIndex)=>(line.words||[]).forEach((word,wordIndex)=>{
    const prefix=`l${String(lineIndex).padStart(4,'0')}-w${String(wordIndex).padStart(4,'0')}-`;
    aligned.push({...word,id:[...byWord.keys()].find(id=>id.startsWith(prefix))||prefix,line_index:lineIndex});
  }));
  const derived=state.learning?.words||[],canonical=state.canonicalWords.length?state.canonicalWords:derived.length?derived:aligned;
  const byId=new Map(derived.map(word=>[word.id,word]));
  state.words=canonical.map((word,index)=>{const extra=derived.length?(byId.get(word.id)||{}):(aligned[index]?.text===word.text?aligned[index]:{});return {...word,...extra,text:word.text,id:word.id||extra.id||`word-${index}`,char_start:word.char_start??extra.char_start,char_end:word.char_end??extra.char_end,links:byWord.get(extra.id||word.id)||[]};});
  if(state.learning?.canonical_text)state.canonicalText=state.learning.canonical_text;
  if(!state.canonicalText)state.canonicalText=state.words.map(word=>word.text).join(' ');
}
function renderFullLyrics(){
  const root=$('#full-lyrics'),scroll=root.scrollTop;root.replaceChildren();state.wordNodes.clear();let cursor=0;const characters=Array.from(state.canonicalText);
  for(const word of state.words){
    const remaining=characters.slice(cursor).join(''),match=remaining.indexOf(word.text);
    let start=Number.isInteger(word.char_start)?word.char_start:match<0?cursor:cursor+Array.from(remaining.slice(0,match)).length;
    if(start<cursor)start=cursor;
    if(start>cursor)root.append(document.createTextNode(characters.slice(cursor,start).join('')));
    const button=document.createElement('button');button.type='button';button.className=`lyric-word${isApproximateWord(word)?' approximate':''}${word.links?.length?'':' no-note'}`;
    button.textContent=word.text;button.dataset.wordId=word.id;
    const timing=Number.isFinite(word.start)?`${isApproximateWord(word)?'≈ ':''}${formatTime(word.start,false)}`:'≈ Время не определено';
    button.title=`${word.text} · ${timing}${word.links?.length?'':' · без ноты'}${word.message?` · ${word.message}`:''}`;
    button.setAttribute('aria-label',button.title);
    button.addEventListener('click',()=>{if(Number.isFinite(word.start))seek(word.start);state.selectedWord=word.id;state.inspectorKey=null;drawAll();});
    root.append(button);state.wordNodes.set(word.id,button);cursor=Number.isInteger(word.char_end)?word.char_end:start+Array.from(word.text).length;
  }
  if(cursor<characters.length)root.append(document.createTextNode(characters.slice(cursor).join('')));
  root.scrollTop=scroll;
  $('#lyrics-summary').textContent=`${state.words.length} слов · ${state.words.filter(word=>!word.links?.length).length} без ноты · ${state.words.filter(isApproximateWord).length} приблизительных`;
}
function renderDownloads(artifacts){
  const allowed=[['vocals.wav','Вокал'],['instrumental.wav','Минус'],['melody.json','Исходные ноты']];
  if(state.learning){artifacts={...artifacts,'mode-piano':state.learning.artifacts?.['piano.wav'],'mode-notes':state.learning.artifacts?.['learning.json']};allowed.push(['mode-piano',`Пианино · ${modeName(state.mode)}`],['mode-notes',`Ноты · ${modeName(state.mode)}`]);}
  else if(artifacts['piano.wav'])allowed.push(['piano.wav','Пианино · исходное']);
  const root=$('#downloads');root.replaceChildren();for(const [name,label] of allowed){if(!artifacts[name])continue;const link=document.createElement('a');link.href=artifacts[name];link.download=name;link.innerHTML=`<svg aria-hidden="true"><use href="#icon-download"/></svg><span>${label}</span>`;root.append(link);}
}

function activeNotes(){return state.learning?.notes||state.melody?.notes||[];}
function intervalsFor(note){return note.intervals||[{start:note.start,end:note.end}];}
function modeName(mode){return {light:'Light',medium:'Medium',pro:'Pro'}[mode]||'Исходный разбор';}
function renderModes(){
  for(const input of document.querySelectorAll('[name="learning-mode"]')){
    input.checked=input.value===(state.pendingMode||state.mode);
    input.disabled=state.loadingAudio||!state.melody||!state.v3Enabled||Boolean(state.modeAvailability[input.value]);
    input.parentElement.title=state.modeAvailability[input.value]||(!state.v3Enabled?'Сначала подготовьте части слов.':'');
  }
  $('#active-mode').textContent=state.mode?`${modeName(state.mode)} · Части слов`:'Исходный разбор';
  $('#learning-explanation').textContent=state.mode?'Все подтверждённые переходы сохранены. На этом этапе режимы используют общий подробный набор нот.':'Исходные ноты, полный текст и обычное воспроизведение доступны.';
  $('#v3-preparation').hidden=Boolean(state.learning);
  $('#prepare-v3').disabled=state.loadingAudio||!state.melody||Boolean(state.pendingMode);
  $('#prepare-v3').textContent=state.pendingMode?'Готовятся части слов…':'Подготовить части слов';
  $('#speed-select').value=String(state.pendingRate??state.rate);
}
function learningStatus(message,error=false){const node=$('#learning-status');node.textContent=message;node.hidden=!message;node.classList.toggle('error',error);}
async function fetchBuffer(url,signal,expected){
  const response=await fetch(url,{signal});if(!response.ok)throw new Error(`Аудио: HTTP ${response.status}`);
  const context=await ensureContext(),buffer=await context.decodeAudioData(await response.arrayBuffer());
  if(Math.abs(buffer.duration-expected)>.05)throw new Error('Длительность дорожки не совпадает с общей шкалой.');
  return buffer;
}
async function requestMode(mode){
  if(state.loadingAudio||state.modeAvailability[mode]||!state.job)return;
  state.desiredMode=mode;return requestPlayback(mode,state.pendingRate??state.rate);
}
async function requestRate(rate){
  if(![.25,.5,.75,1,1.25,1.5,1.75,2].includes(rate)||state.loadingAudio||!state.job)return;
  state.desiredRate=rate;return requestPlayback(state.pendingMode||state.mode,rate);
}
async function requestPlayback(mode,rate){
  const request=++state.modeRequest,job=state.job.id;
  state.modeAbort?.abort();state.modeAbort=new AbortController();const signal=state.modeAbort.signal;
  state.pendingMode=mode;state.pendingRate=rate;state.desiredMode=mode;state.desiredRate=rate;renderModes();
  learningStatus(mode?`Готовится ${modeName(mode)}; сейчас активен ${modeName(state.mode)}.`:'');
  const speedStatus=$('#speed-status');speedStatus.hidden=false;speedStatus.textContent=`Подготовка ${String(rate).replace('.',',')}×. Сейчас звучит ${String(state.rate).replace('.',',')}×; позиция сохранится.`;
  const current=()=>request===state.modeRequest&&job===state.job?.id;
  try{
    let prepared=mode?state.modeCache.get(mode):null;
    if(mode&&!prepared){
      const saved=state.job.v3_modes?.[mode];
      const data=await json(saved||`/api/studio/jobs/${job}/v3/${mode}`,saved?{signal}:{method:'POST',signal});
      if(!current())return;
      const buffer=await fetchBuffer(data.artifacts['piano.wav'],signal,state.duration);
      if(!current())return;prepared={data,buffer};state.modeCache.set(mode,prepared);
    }
    let buffers=mode?{...state.originalBuffers,piano:prepared.buffer}:{...state.originalBuffers};
    // Harnesses and old in-memory sessions may precede originalBuffers.
    if(!Object.keys(buffers).length)buffers={...state.buffers};
    if(rate!==1){
      const key=`${mode||'original'}:${rate}`;let stretched=state.tempoCache.get(key);
      if(!stretched){
        // Aborting HTTP cannot cancel the server's DSP work. Let one tempo
        // preparation finish, then submit only the latest requested pair.
        while(state.tempoPreparation){await state.tempoPreparation.catch(()=>{});if(!current())return;}
        if(!current())return;
        const preparing=json(`/api/studio/jobs/${job}/tempo/${mode||'original'}/${rate}`,{method:'POST'});
        state.tempoPreparation=preparing;let tempo;
        try{tempo=await preparing;}finally{if(state.tempoPreparation===preparing)state.tempoPreparation=null;}
        if(!current())return;
        if(Number(tempo.rate)!==rate||Math.abs(Number(tempo.source_duration)-state.duration)>.05)throw new Error('Подготовленная скорость не соответствует запросу.');
        const entries=await Promise.all(Object.keys(buffers).map(async key=>{
          const url=tempo.artifacts[trackMeta[key].file];if(!url)throw new Error(`Нет дорожки «${trackMeta[key].label}» для выбранной скорости.`);
          return [key,await fetchBuffer(url,signal,state.duration/rate)];
        }));
        if(!current())return;stretched=Object.fromEntries(entries);state.tempoCache.set(key,stretched);
      }
      buffers=stretched;
    }
    if(!current())return;
    // Prepare every source before stopping any working source. A failed start
    // rolls back the replacement and leaves the previous audio clock intact.
    if(state.playing){
      const at=Math.max(state.context.currentTime,state.startedAt),position=Math.min(state.duration,state.startPosition+(at-state.startedAt)*state.rate);
      const keys=rate===1&&state.rate===1?['piano']:Object.keys(buffers),replacement={};
      try{for(const key of keys){if(buffers[key]){const source=startTrack(key,buffers[key],at,position,rate);if(source)replacement[key]=source;}}}
      catch(error){for(const source of Object.values(replacement)){try{source.stop();}catch(_){}source.disconnect();}throw error;}
      for(const key of keys){const old=state.sources[key];if(old){try{old.stop(at);}catch(_){}}if(replacement[key])state.sources[key]=replacement[key];else delete state.sources[key];}
      state.startedAt=at;state.startPosition=position;
    }
    state.buffers=buffers;state.learning=prepared?.data||null;state.mode=mode;state.rate=rate;state.pendingMode=null;state.pendingRate=null;state.inspectorKey=null;
    flattenWords();renderFullLyrics();renderDownloads(state.job.artifacts||{});renderModes();learningStatus('');speedStatus.hidden=true;
    const available={...state.job.artifacts};if(mode)available['piano.wav']=prepared.data.artifacts['piano.wav'];renderMixer(available);
    $('#play-button').disabled=!Object.keys(state.buffers).length;
    $('#result-summary').textContent=`${activeNotes().length} нот · ${state.words.length} слов · ${formatTime(state.duration,false)}`;
    const diagnostics=state.learning?.diagnostics||{},parts=state.learning?.text_parts||[];
    const fallback=parts.filter(part=>part.kind==='whole-word-fallback'||part.status==='fallback').length;
    $('#timeline-message').textContent=`Текст над шкалой сохраняется и без ноты. Внутри нот — части слова; — означает продолжение. Голубая линия — исходная высота голоса.${diagnostics.notes_with_lyric_context?` «Контекст» на ${diagnostics.notes_with_lyric_context} нотах: показано соседнее слово лирики, точное слово на этом участке не подтверждено.`:''}${fallback?` Частей без определённой слоговой границы: ${fallback}; полное слово доступно в тексте и инспекторе.`:''}${diagnostics.words_without_notes?` Слов без ноты: ${diagnostics.words_without_notes}.`:''}`;
    if(mode)try{localStorage.setItem(`vocalcreator:mode:${job}`,mode);}catch(_){}
    drawAll();
  }catch(error){
    if(!current())return;
    state.pendingMode=null;state.pendingRate=null;state.desiredMode=state.mode;state.desiredRate=state.rate;renderModes();
    learningStatus(`Не удалось подготовить ${modeName(mode)} / ${rate}×: ${error.message} Активен ${modeName(state.mode)} / ${state.rate}×. Повторите выбор.`,true);
    speedStatus.hidden=true;
  }
}
document.querySelectorAll('[name="learning-mode"]').forEach(input=>input.addEventListener('change',()=>{if(input.checked)requestMode(input.value);}));
$('#prepare-v3').addEventListener('click',()=>{state.v3Enabled=true;requestMode('light');});
$('#speed-select').addEventListener('change',event=>requestRate(Number(event.target.value)));

async function ensureContext(){
  if(state.context)return state.context;const Context=window.AudioContext||window.webkitAudioContext;state.context=new Context();state.master=state.context.createDynamicsCompressor();state.master.threshold.value=-9;state.master.knee.value=18;state.master.ratio.value=8;state.master.attack.value=.003;state.master.release.value=.2;state.master.connect(state.context.destination);return state.context;
}
async function loadAudioTracks(artifacts){
  const context=await ensureContext(),buffers={};for(const [key,meta] of Object.entries(trackMeta)){if(!artifacts[meta.file])continue;try{const response=await fetch(artifacts[meta.file]);if(!response.ok)throw new Error(`HTTP ${response.status}`);buffers[key]=await context.decodeAudioData(await response.arrayBuffer());}catch(error){showTransportStatus(`Не удалось декодировать дорожку «${meta.label}»: ${error.message}`);}}return buffers;
}
function renderMixer(artifacts){
  const root=$('#track-controls');root.replaceChildren();for(const [key,meta] of Object.entries(trackMeta)){
    const available=Boolean(artifacts[meta.file]),row=document.createElement('div');row.className=`track-row${available?'':' disabled'}`;
    row.innerHTML=`<div class="track-head"><strong>${meta.label}</strong><output>${Math.round(state.volumes[key]*100)}%</output><button class="mute-button" type="button" aria-label="Отключить ${meta.label.toLowerCase()}" aria-pressed="false" ${available?'':'disabled'}><svg><use href="#icon-volume"/></svg></button></div><input type="range" min="0" max="1" step="0.01" value="${state.volumes[key]}" aria-label="Громкость: ${meta.label}" ${available?'':'disabled'}>`;
    if(!available){const note=document.createElement('small');note.textContent=key==='instrumental'&&state.job?.input?.type==='vocal'?'Сопровождение не было загружено':'Дорожка недоступна';row.append(note);}
    const slider=row.querySelector('input'),output=row.querySelector('output'),mute=row.querySelector('button');
    mute.setAttribute('aria-pressed',String(state.muted[key]));mute.setAttribute('aria-label',`${state.muted[key]?'Включить':'Отключить'} ${meta.label.toLowerCase()}`);mute.querySelector('use').setAttribute('href',state.muted[key]?'#icon-muted':'#icon-volume');
    slider.addEventListener('input',()=>{state.volumes[key]=Number(slider.value);output.textContent=`${Math.round(state.volumes[key]*100)}%`;applyGain(key);});
    mute.addEventListener('click',()=>{state.muted[key]=!state.muted[key];mute.setAttribute('aria-pressed',String(state.muted[key]));mute.setAttribute('aria-label',`${state.muted[key]?'Включить':'Отключить'} ${meta.label.toLowerCase()}`);mute.querySelector('use').setAttribute('href',state.muted[key]?'#icon-muted':'#icon-volume');applyGain(key);});
    root.append(row);
  }
}
function gainFor(key){if(!state.gains[key]){state.gains[key]=state.context.createGain();state.gains[key].connect(state.master);}return state.gains[key];}
function applyGain(key){if(!state.context)return;gainFor(key).gain.setTargetAtTime(state.muted[key]?0:state.volumes[key],state.context.currentTime,.012);}

function currentPosition(){
  if(!state.playing||!state.context)return state.position;
  return Math.min(state.duration,Math.max(state.startPosition,state.startPosition+(state.context.currentTime-state.startedAt)*state.rate));
}
function transportIcon(playing){
  const button=$('#play-button'),label=playing?'Пауза':'Воспроизвести';
  button.setAttribute('aria-label',label);button.title=label;
  button.querySelector('use').setAttribute('href',playing?'#icon-pause':'#icon-play');
}
function startTrack(key,buffer,at,position,rate=state.rate){
  const offset=position/rate;
  if(offset>=buffer.duration)return null;
  const source=state.context.createBufferSource();source.buffer=buffer;
  try{
    source.connect(gainFor(key));applyGain(key);
    source.onended=()=>{source.disconnect();if(state.playing&&state.sources[key]===source&&currentPosition()>=state.duration-.01){pause();state.position=state.duration;updatePositionUI();drawAll();}};
    source.start(at,Math.max(0,offset));return source;
  }catch(error){try{source.stop();}catch(_){}source.disconnect();throw error;}
}
async function play(){
  if(state.playing||state.starting||!Object.keys(state.buffers).length)return;
  const request=++state.playRequest;state.starting=true;clearTransportStatus();
  try{
    const context=await ensureContext();await context.resume();
    if(request!==state.playRequest)return;
    if(context.state!=='running')throw new Error('Аудиоустройство пока недоступно. Попробуйте снова.');
    if(state.position>=state.duration-.005)state.position=0;
    state.startPosition=state.position;state.startedAt=context.currentTime+.02;state.sources={};
    for(const [key,buffer] of Object.entries(state.buffers)){
      const source=startTrack(key,buffer,state.startedAt,state.position);
      if(source)state.sources[key]=source;
    }
    state.playing=true;state.starting=false;transportIcon(true);tick();
  }catch(error){
    if(request!==state.playRequest)return;
    pause();showTransportStatus(`Браузер не запустил звук: ${error.message}`);
  }
}
function stopSources(){Object.values(state.sources).forEach(source=>{try{source.stop();}catch(_){}source.disconnect();});state.sources={};}
function pause(){
  ++state.playRequest;state.starting=false;
  if(state.playing)state.position=currentPosition();state.playing=false;
  stopSources();cancelAnimationFrame(state.frame);transportIcon(false);updatePositionUI();
}
function seek(value){state.selectedWord=null;state.inspectorKey=null;const resume=state.playing||state.starting;if(resume)pause();state.position=Math.max(0,Math.min(state.duration,Number(value)||0));updatePositionUI();drawAll();if(resume)play();}
function tick(){
  if(!state.playing)return;const value=currentPosition();
  if(value>=state.duration){pause();state.position=state.duration;updatePositionUI();drawAll();return;}
  updatePositionUI(value);drawAll(value);state.frame=requestAnimationFrame(tick);
}
function updatePositionUI(value=currentPosition()){state.position=state.playing?state.position:value;$('#time-current').textContent=formatTime(value);$('#seek-slider').value=value;$('#canvas-seek').value=value;renderInspector(value);}

$('#play-button').addEventListener('click',()=>state.playing||state.starting?pause():play());
$('#seek-slider').addEventListener('input',event=>seek(event.target.value));$('#canvas-seek').addEventListener('input',event=>seek(event.target.value));
$('#zoom-slider').addEventListener('input',event=>{const center=($('#timeline-scroller').scrollLeft+($('#timeline-scroller').clientWidth-74)/2)/pixelsPerSecond();state.zoom=Number(event.target.value);$('#zoom-value').textContent=`${state.zoom}×`;resizeTimeline();$('#timeline-scroller').scrollLeft=Math.max(0,center*pixelsPerSecond()-($('#timeline-scroller').clientWidth-74)/2);drawAll();});
$('#height-slider').addEventListener('input',event=>{const scroll=$('#timeline-scroller'),center=(scroll.clientHeight+80)/2,anchor=state.midiMax+.5-(center+scroll.scrollTop-96)/rowHeight();state.heightZoom=Number(event.target.value);$('#height-value').textContent=`${state.heightZoom}×`;resizeTimeline();scroll.scrollTop=Math.max(0,96+(state.midiMax+.5-anchor)*rowHeight()-center);drawAll();});
$('#refresh-library').addEventListener('click',loadLibrary);
document.addEventListener('keydown',event=>{if(event.defaultPrevented||event.repeat||event.ctrlKey||event.metaKey||event.altKey||event.target.closest('input,select,textarea,button,a,[contenteditable=true]'))return;if(event.code==='Space'){event.preventDefault();$('#play-button').click();}if(event.key==='ArrowRight')seek(currentPosition()+2);if(event.key==='ArrowLeft')seek(currentPosition()-2);});

function setRangeBounds(){for(const selector of ['#seek-slider','#canvas-seek']){const input=$(selector);input.max=Math.max(.001,state.duration);input.disabled=!state.duration;}}
function pixelsPerSecond(){return 28*state.zoom;}
function resizeTimeline(){
  const scroller=$('#timeline-scroller'),canvas=$('#timeline-canvas'),spacer=$('#timeline-spacer'),height=Math.max(200,scroller.clientHeight),width=Math.max(320,scroller.clientWidth);
  spacer.style.width=`${Math.max(width,state.duration*pixelsPerSecond()+82)}px`;spacer.style.height=`${Math.max(height,96+(state.midiMax-state.midiMin+1)*rowHeight()+28)}px`;canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;const dpr=Math.min(2,window.devicePixelRatio||1);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
  const overview=$('#overview-canvas'),overviewWidth=Math.max(180,overview.clientWidth),overviewHeight=Math.max(42,overview.clientHeight);overview.width=Math.round(overviewWidth*dpr);overview.height=Math.round(overviewHeight*dpr);
}
function makePeaks(){
  const buffer=state.buffers.vocals||state.buffers.instrumental||state.buffers.piano;if(!buffer){state.peaks=[];return;}const data=buffer.getChannelData(0),count=1800,step=Math.max(1,Math.floor(data.length/count));state.peaks=[];
  for(let start=0;start<data.length;start+=step){let peak=0;for(let index=start;index<Math.min(data.length,start+step);index+=4)peak=Math.max(peak,Math.abs(data[index]));state.peaks.push(peak);}
}
function setupCanvas(ctx,canvas){const dpr=Math.min(2,window.devicePixelRatio||1);ctx.setTransform(dpr,0,0,dpr,0,0);return {width:canvas.width/dpr,height:canvas.height/dpr};}
function updatePitchRange(){
  const pitches=[...activeNotes().map(note=>note.midi),...(state.melody?.pitch_frames||[]).map(frame=>frame.midi)].filter(value=>Number.isFinite(value));
  state.midiMin=pitches.length?Math.max(0,Math.floor(pitches.reduce((min,pitch)=>Math.min(min,pitch),127))-2):48;
  state.midiMax=pitches.length?Math.min(127,Math.ceil(pitches.reduce((max,pitch)=>Math.max(max,pitch),0))+2):72;
  state.baseRow=Math.max(1,($('#timeline-scroller').clientHeight-124)/(state.midiMax-state.midiMin+1));
}
function rowHeight(){return state.baseRow*state.heightZoom;}
function midiY(midi){return 96+(state.midiMax-midi+.5)*rowHeight()-$('#timeline-scroller').scrollTop;}
function noteLabels(note){
  const links=note.labels||state.learning?.note_text_links?.filter(link=>link.note_id===note.id);
  if(links?.length)return links.map(link=>{const part=state.learning?.text_parts?.find(part=>part.id===link.part_id);return {...link,text:link.text||part?.text||'',context:link.context||link.status==='context'||link.kind==='lyric-context',fallback:link.fallback||link.kind==='whole-word-fallback'||part?.kind==='whole-word-fallback'||part?.status==='fallback'||link.status==='fallback',approximate:link.approximate||['approximate','context'].includes(link.status)||part?.status==='approximate',reason:link.reason||part?.reason,message:link.message||part?.message};});
  const words=state.words.filter(word=>word.links?.some(link=>link.note_id===note.id));
  if(words.length)return words.map(word=>({word_id:word.id,text:word.text,start:Math.max(note.start,word.start??note.start),end:Math.min(note.end,word.end??note.end),fallback:true,reason:'часть слова не определена'}));
  return [{text:'Нет текста',start:note.start,end:note.end,missing:true}];
}
// Presentation only: adjacent links identify a run, never the text or the
// generator's global "seen part" flag alone. A rest starts a fresh anchor.
function timelineLabelSpans(notes){
  const spans=[];let previous=null;
  for(const note of notes){
    const labels=noteLabels(note);
    for(const interval of intervalsFor(note))for(const label of labels){
      const start=Math.max(interval.start,label.start??interval.start),end=Math.min(interval.end,label.end??interval.end);
      if(end<=start)continue;
      const identified=label.part_id!=null&&label.part_id!==''&&label.word_id!=null&&label.word_id!==''&&!label.context&&!label.missing;
      const continued=Boolean(identified&&previous?.identified&&label.continuation===true
        &&label.part_id===previous.label.part_id&&label.word_id===previous.label.word_id
        &&Math.abs(start-previous.end)<1e-6);
      const span={note,label,start,end,identified,continued,group:continued?previous.group:{}};
      spans.push(span);previous=span;
    }
  }
  return spans;
}
function noteName(midi, bilingual=false){const latin=['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'],ru=['До','До♯','Ре','Ре♯','Ми','Фа','Фа♯','Соль','Соль♯','Ля','Ля♯','Си'],pitch=Math.round(midi),octave=Math.floor(pitch/12)-1,index=(pitch%12+12)%12;return bilingual?`${ru[index]}${octave} (${latin[index]}${octave})`:`${latin[index]}${octave}`;}
function activeNoteIndex(position){return activeNotes().findIndex(note=>intervalsFor(note).some(interval=>interval.start<=position&&position<interval.end));}
function isApproximateWord(word){return word?.approximate??(word?.timing?.source!=='manual'&&(!word?.aligned||['interpolated','approximate_split','unknown'].includes(word?.timing?.source)));}
function renderInspector(position=currentPosition()){
  const notes=activeNotes(),index=activeNoteIndex(position),note=index>=0?notes[index]:null,activeWord=state.words.find(word=>word.start<=position&&position<word.end),key=`${note?.id||'none'}:${activeWord?.id||'none'}:${state.selectedWord||''}`;
  const summary=`График содержит ${notes.length} нот выбранного режима и ${state.words.length} слов. Кнопки «Предыдущая» и «Следующая» последовательно открывают каждую ноту, включая короткие.`,positionLabel=index>=0?`${index+1} / ${notes.length}`:`– / ${notes.length}`;
  if($('#timeline-semantic-summary').textContent!==summary)$('#timeline-semantic-summary').textContent=summary;
  if($('#inspector-position').textContent!==positionLabel)$('#inspector-position').textContent=positionLabel;
  let previousIndex=-1;for(let candidate=notes.length-1;candidate>=0;candidate--){if(notes[candidate].start<position-.001){previousIndex=index===candidate?candidate-1:candidate;break;}}
  const nextIndex=index>=0?index+1:notes.findIndex(candidate=>candidate.start>position+.001);
  $('#previous-note').disabled=previousIndex<0;$('#next-note').disabled=nextIndex<0||nextIndex>=notes.length;
  if(state.inspectorKey===key)return;state.inspectorKey=key;
  if(note){
    const cents=Number(note.cents)||0,score=Number(note.confidence),certainty=note.uncertain?'≈ приблизительная нота':state.mode&&state.mode!=='pro'?'учебная высота':'устойчивая нота';
    $('#inspector-note').textContent=noteName(note.midi,true);
    $('#inspector-detail').textContent=`${formatTime(note.start,false)}–${formatTime(note.end,false)} · ${cents>=0?'+':''}${cents.toFixed(1)} cents · score ${Number.isFinite(score)?score.toFixed(3):'нет'} · ${certainty}`;
  }else{
    $('#inspector-note').textContent='Нет определимой ноты';$('#inspector-detail').textContent=`${formatTime(position,false)} · pitch в этой позиции не образует нотное событие.`;
  }
  const linkedWords=note?state.words.filter(word=>word.links?.some(link=>link.note_id===note.id)):[],word=(!state.playing&&state.words.find(item=>item.id===state.selectedWord))||activeWord||linkedWords[0];
  for(const [id,node] of state.wordNodes){node.classList.toggle('active',id===activeWord?.id);node.classList.toggle('selected',id===state.selectedWord);}
  if(word){
    const approximate=isApproximateWord(word),extra=linkedWords.length>1?` · связаны слова: ${linkedWords.map(item=>item.text).join(', ')}`:'';
    const labels=note?noteLabels(note).filter(label=>label.word_id===word.id):[],partCopy=labels.map(label=>`${label.text}${label.continuation?'—':''}${label.context?' · контекст лирики, слово здесь не подтверждено':label.fallback?' · часть слова не определена':label.approximate?' · граница части приблизительная':''}${label.message?` · ${label.message}`:''}${label.reason?` · ${label.reason}`:''}`).join(' / ');
    $('#inspector-word').textContent=`${approximate?'≈ ':''}${word.text}`;$('#inspector-word-detail').textContent=`${Number.isFinite(word.start)?`${formatTime(word.start,false)}–${formatTime(word.end,false)}`:'≈ Время не определено'} · ${word.message||(approximate?'приблизительная привязка':'прямая привязка')}${extra}${partCopy?` · ${partCopy}`:''}${word.links?.length?'':' · Нет определимой ноты.'}`;
  }else{
    $('#inspector-word').textContent='Нет слова';$('#inspector-word-detail').textContent=note?'У этой ноты нет связи с текстом.':'В этой позиции нет слова и ноты.';
  }
}
function navigateNote(direction){
  const notes=activeNotes();if(!notes.length)return;const position=currentPosition(),index=activeNoteIndex(position);let target=-1;
  if(direction>0)target=index>=0?index+1:notes.findIndex(note=>note.start>position+.001);
  else if(index>0)target=index-1;else if(index<0){for(let candidate=notes.length-1;candidate>=0;candidate--){if(notes[candidate].start<position-.001){target=candidate;break;}}}
  if(target<0||target>=notes.length)return;state.selectedWord=null;const note=notes[target];seek(note.start+Math.min(.001,Math.max(0,(note.end-note.start)/2)));$('#timeline-scroller').scrollLeft=Math.max(0,note.start*pixelsPerSecond()-$('#timeline-scroller').clientWidth*.42);const scroll=$('#timeline-scroller'),y=midiY(note.midi);if(y<110||y>scroll.clientHeight-30)scroll.scrollTop=Math.max(0,scroll.scrollTop+y-(scroll.clientHeight+80)/2);drawAll();
}
$('#previous-note').addEventListener('click',()=>navigateNote(-1));$('#next-note').addEventListener('click',()=>navigateNote(1));
$('#timeline-canvas').addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();event.stopPropagation();navigateNote(event.key==='ArrowRight'?1:-1);}});
function drawAll(position=currentPosition()){renderInspector(position);drawTimeline(position);drawOverview(position);}
function drawTimeline(position){
  const canvas=$('#timeline-canvas'),ctx=canvas.getContext('2d'),{width,height}=setupCanvas(ctx,canvas),scroller=$('#timeline-scroller'),pps=pixelsPerSecond(),keyboard=74,startTime=Math.max(0,scroller.scrollLeft/pps),endTime=startTime+(width-keyboard)/pps;
  ctx.clearRect(0,0,width,height);ctx.fillStyle='#090c11';ctx.fillRect(0,0,width,height);
  ctx.save();ctx.beginPath();ctx.rect(keyboard,80,width-keyboard,height-80);ctx.clip();
  for(let midi=state.midiMin;midi<=state.midiMax;midi++){
    const y=midiY(midi+.5),row=rowHeight();if(y+row<80||y>height)continue;
    ctx.fillStyle=[1,3,6,8,10].includes(midi%12)?'rgba(255,255,255,.018)':'rgba(255,255,255,.035)';ctx.fillRect(keyboard,y,width-keyboard,row);ctx.strokeStyle='rgba(255,255,255,.055)';ctx.beginPath();ctx.moveTo(keyboard,y);ctx.lineTo(width,y);ctx.stroke();
  }
  for(let second=Math.floor(startTime);second<=endTime+1;second++){const x=keyboard+second*pps-scroller.scrollLeft;ctx.strokeStyle=second%5===0?'rgba(133,135,247,.3)':'rgba(255,255,255,.055)';ctx.beginPath();ctx.moveTo(x,80);ctx.lineTo(x,height);ctx.stroke();}
  const activeWord=state.words.find(word=>word.start<=position&&position<word.end),highlightedNotes=new Set(activeWord?.links?.map(link=>link.note_id)||[]),labels=[];
  for(const note of activeNotes()){
    if(note.end<startTime||note.start>endTime)continue;
    const y=midiY(note.midi+.38),h=Math.max(3,rowHeight()*.76);if(y+h<80||y>height)continue;
    for(const interval of intervalsFor(note)){
      if(interval.end<startTime||interval.start>endTime)continue;
      const x=keyboard+interval.start*pps-scroller.scrollLeft,w=Math.max(1,(interval.end-interval.start)*pps);
      ctx.fillStyle=highlightedNotes.has(note.id)?'#a9abff':note.uncertain?'#6669ae':'#8587f7';ctx.fillRect(x,y,w,h);
    }
  }
  // Visibility, not playback time or the previous frame, chooses one readable
  // anchor per run. Hidden pitches and hidden parts of a long note do not count.
  const anchored=new Set();
  for(const span of timelineLabelSpans(activeNotes())){
    const {note,label,start,end,group,continued}=span;
    if(end<=startTime||start>=endTime)continue;
    const y=midiY(note.midi+.38),h=Math.max(3,rowHeight()*.76);if(y+h<=80||y>=height)continue;
    const continuationOnly=continued&&anchored.has(group),resumed=continued||start<startTime;
    const prefix=label.context?'Контекст: ':label.fallback||label.approximate?'≈ ':'';
    const suffix=label.context?'':span.identified?(resumed?' ─':''):(label.continuation?'—':'');
    const text=continuationOnly?`${prefix}─`:`${prefix}${label.text}${suffix}`;
    const labelX=Math.max(keyboard,keyboard+start*pps-scroller.scrollLeft),labelEnd=Math.min(width,keyboard+end*pps-scroller.scrollLeft);
    ctx.font='500 12px -apple-system,sans-serif';const measured=ctx.measureText(text).width;
    const inside=measured+8<=labelEnd-labelX&&h>=18&&y>=80&&y+h<=height;
    labels.push({text,x:Math.max(keyboard,Math.min(labelX,width-measured-8)),noteX:labelX,y,h,measured,inside,missing:label.missing});
    anchored.add(group);
  }
  const frames=state.melody?.pitch_frames||[];ctx.strokeStyle='rgba(88,216,255,.6)';ctx.lineWidth=1.4;ctx.beginPath();let drawing=false;const stride=Math.max(1,Math.floor(frames.length/12000));
  for(let i=0;i<frames.length;i+=stride){const frame=frames[i];if(frame.time<startTime||frame.time>endTime||!Number.isFinite(frame.midi)){drawing=false;continue;}const x=keyboard+frame.time*pps-scroller.scrollLeft,y=midiY(frame.midi);if(drawing)ctx.lineTo(x,y);else{ctx.moveTo(x,y);drawing=true;}}ctx.stroke();ctx.lineWidth=1;
  const occupied=[];
  for(const label of labels){
    let baseline=label.inside?label.y+label.h/2+4:label.y-5;
    if(!label.inside){
      // Real note widths never expand for typography. Above-note labels use
      // extra rows; readable close-up is obtained with the independent scales.
      for(let lane=0;lane<8&&occupied.some(box=>Math.abs(box.y-baseline)<14&&label.x<box.end&&label.x+label.measured+8>box.x);lane++)baseline-=15;
      if(baseline<94)baseline=label.y+label.h+15;
      baseline=Math.max(94,Math.min(height-4,baseline));
      occupied.push({x:label.x,end:label.x+label.measured+8,y:baseline});
      ctx.strokeStyle='#8587f7';ctx.beginPath();ctx.moveTo(label.noteX+3,label.y);ctx.lineTo(label.x+3,baseline+2);ctx.stroke();
      ctx.fillStyle='#111827';ctx.fillRect(label.x,baseline-12,label.measured+8,16);
    }
    ctx.fillStyle=label.inside?'#090b11':label.missing?'#aeb5c4':'#f4f6fb';ctx.font='500 12px -apple-system,sans-serif';ctx.fillText(label.text,label.x+4,baseline);
  }
  ctx.restore();
  ctx.save();ctx.beginPath();ctx.rect(0,80,keyboard,height-80);ctx.clip();ctx.fillStyle='#0d1015';ctx.fillRect(0,80,keyboard,height-80);
  for(let midi=state.midiMin;midi<=state.midiMax;midi++){const y=midiY(midi+.5),row=rowHeight();if(y+row<80||y>height)continue;const black=[1,3,6,8,10].includes(midi%12);ctx.fillStyle=black?'#161a21':'#e5e7eb';ctx.fillRect(0,y,72,row);if(black){ctx.fillStyle='#242b38';ctx.fillRect(0,y,44,row);}ctx.strokeStyle='#303541';ctx.strokeRect(0,y,72,row);if(row>=10||midi%12===0){ctx.fillStyle=black?'#e5e7eb':'#252a34';ctx.font='10px -apple-system,sans-serif';ctx.fillText(noteName(midi),47,y+row/2+3);}}
  ctx.restore();
  // The ruler and waveform stay fixed vertically and share the timeline x-axis.
  ctx.fillStyle='#0d1015';ctx.fillRect(0,0,width,80);ctx.save();ctx.beginPath();ctx.rect(keyboard,0,width-keyboard,80);ctx.clip();
  for(let second=Math.floor(startTime);second<=endTime+1;second++){if(second%(pps>100?1:5)!==0)continue;const x=keyboard+second*pps-scroller.scrollLeft;ctx.fillStyle='#aeb5c4';ctx.font='11px -apple-system,sans-serif';ctx.fillText(formatTime(second,false),x+5,18);}
  ctx.strokeStyle='rgba(133,135,247,.65)';ctx.beginPath();for(let x=keyboard;x<width;x++){const time=startTime+(x-keyboard)/pps,index=Math.floor(time/state.duration*state.peaks.length),peak=state.peaks[index]||0;ctx.moveTo(x,35-peak*10);ctx.lineTo(x,35+peak*10);}ctx.stroke();
  // The lyric lane is independent of detected notes and vertical pitch scroll.
  const lyricLanes=[-Infinity,-Infinity];
  for(const word of state.words){
    if(!Number.isFinite(word.start)||!Number.isFinite(word.end)||word.end<startTime||word.start>endTime)continue;
    const x=Math.max(keyboard,keyboard+word.start*pps-scroller.scrollLeft),text=`${isApproximateWord(word)?'≈ ':''}${word.text}`;
    ctx.font='500 12px -apple-system,sans-serif';const measured=ctx.measureText(text).width,lane=lyricLanes[0]<=x?0:lyricLanes[1]<=x?1:lyricLanes[0]<=lyricLanes[1]?0:1;
    ctx.fillStyle=word.id===activeWord?.id?'#b7b9ff':'#e5e7eb';ctx.fillText(text,x+3,60+lane*15);lyricLanes[lane]=x+measured+8;
  }
  ctx.restore();
  const playX=keyboard+position*pps-scroller.scrollLeft;if(playX>=keyboard){ctx.strokeStyle='#58d8ff';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(playX,0);ctx.lineTo(playX,height);ctx.stroke();ctx.lineWidth=1;}
  ctx.strokeStyle='rgba(255,255,255,.16)';ctx.beginPath();ctx.moveTo(keyboard-.5,0);ctx.lineTo(keyboard-.5,height);ctx.moveTo(0,79.5);ctx.lineTo(width,79.5);ctx.stroke();
}
function drawOverview(position){
  const canvas=$('#overview-canvas'),ctx=canvas.getContext('2d'),{width,height}=setupCanvas(ctx,canvas);ctx.clearRect(0,0,width,height);ctx.fillStyle='#0d1015';ctx.fillRect(0,0,width,height);if(!state.duration)return;
  ctx.fillStyle='rgba(133,135,247,.55)';state.peaks.forEach((peak,index)=>{const x=index/state.peaks.length*width,h=peak*(height-10);ctx.fillRect(x,(height-h)/2,Math.max(1,width/state.peaks.length),h);});
  const scroller=$('#timeline-scroller'),visible=Math.min(1,Math.max(0,(scroller.clientWidth-74)/(state.duration*pixelsPerSecond()))),start=scroller.scrollLeft/(state.duration*pixelsPerSecond());ctx.fillStyle='rgba(88,216,255,.08)';ctx.fillRect(start*width,0,visible*width,height);ctx.strokeStyle='rgba(88,216,255,.8)';ctx.strokeRect(start*width+.5,.5,Math.max(2,visible*width-1),height-1);ctx.fillStyle='#58d8ff';ctx.fillRect(position/state.duration*width-1,0,2,height);
}
$('#timeline-scroller').addEventListener('scroll',()=>drawAll());$('#timeline-canvas').addEventListener('click',event=>{const rect=event.currentTarget.getBoundingClientRect(),x=event.clientX-rect.left;if(x<74)return;const time=($('#timeline-scroller').scrollLeft+x-74)/pixelsPerSecond();seek(time);});$('#overview-canvas').addEventListener('click',event=>{const rect=event.currentTarget.getBoundingClientRect(),time=(event.clientX-rect.left)/rect.width*state.duration;seek(time);$('#timeline-scroller').scrollLeft=Math.max(0,time*pixelsPerSecond()-$('#timeline-scroller').clientWidth/2);});
const scroller=$('#timeline-scroller');
scroller.addEventListener('wheel',event=>{
  if(event.ctrlKey||event.metaKey)return;
  const unitX=event.deltaMode===1?16:event.deltaMode===2?Math.max(1,scroller.clientWidth-74):1;
  const unitY=event.deltaMode===1?16:event.deltaMode===2?Math.max(1,scroller.clientHeight-80):1;
  const deltaX=(event.shiftKey&&!event.deltaX?event.deltaY:event.deltaX)*unitX,deltaY=(event.shiftKey?0:event.deltaY)*unitY;
  const beforeX=scroller.scrollLeft,beforeY=scroller.scrollTop;
  const afterX=Math.max(0,Math.min(scroller.scrollWidth-scroller.clientWidth,beforeX+deltaX)),afterY=Math.max(0,Math.min(scroller.scrollHeight-scroller.clientHeight,beforeY+deltaY));
  if(Math.abs(afterX-beforeX)>.01||Math.abs(afterY-beforeY)>.01){event.preventDefault();scroller.scrollLeft=afterX;scroller.scrollTop=afterY;}
},{passive:false});
scroller.addEventListener('keydown',event=>{
  if(!['PageUp','PageDown'].includes(event.key)||event.ctrlKey||event.metaKey||event.altKey)return;
  const direction=event.key==='PageDown'?1:-1,axis=event.shiftKey?'scrollLeft':'scrollTop',page=event.shiftKey?scroller.clientWidth-74:scroller.clientHeight-80,max=event.shiftKey?scroller.scrollWidth-scroller.clientWidth:scroller.scrollHeight-scroller.clientHeight;
  const before=scroller[axis],after=Math.max(0,Math.min(max,before+Math.max(1,page)*direction));
  if(after!==before){event.preventDefault();event.stopPropagation();scroller[axis]=after;}
});
window.addEventListener('resize' ,()=>{resizeTimeline();drawAll();});document.addEventListener('visibilitychange',()=>{if(!document.hidden)drawAll();});
loadLibrary();
if(window.location?.search){
  const params=new URLSearchParams(window.location.search),initialJob=params.get('job');
  if(initialJob)openJob(initialJob).then(()=>{
    if(state.job?.id!==initialJob)return;
    for(const [key,selector,min,max] of [['zoom','#zoom-slider',.5,32],['height','#height-slider',1,6]]){
      const value=Number(params.get(key));if(!params.has(key)||!Number.isFinite(value)||value<min||value>max)continue;
      $(selector).value=String(value);$(selector).dispatchEvent(new Event('input',{bubbles:true}));
    }
    const position=Number(params.get('t'));
    if(params.has('t')&&Number.isFinite(position)&&position>=0&&position<=state.duration){
      seek(position);const scroll=$('#timeline-scroller');scroll.scrollLeft=Math.max(0,position*pixelsPerSecond()-(scroll.clientWidth-74)*.35);
      const note=activeNotes()[activeNoteIndex(position)];if(note)scroll.scrollTop=Math.max(0,scroll.scrollTop+midiY(note.midi)-(scroll.clientHeight+80)/2);
    }
    drawAll();
  });
}
