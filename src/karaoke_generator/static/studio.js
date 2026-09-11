const $ = selector => document.querySelector(selector);
const stages = [
  ['preparation','Подготовка аудио и текста'],['separation','Выделение вокала и минуса'],
  ['alignment','Привязка точного текста'],['pitch','Анализ высоты голоса'],
  ['melody','Сегментация нот'],['piano','Синтез пианино']
];
const state = {
  job:null, melody:null, alignment:null, words:[], peaks:[], duration:0, position:0,
  playing:false, startedAt:0, startPosition:0, zoom:1, loop:false, a:null, b:null,
  context:null, master:null, buffers:{}, sources:{}, gains:{}, muted:{vocals:false,piano:false,instrumental:false},
  volumes:{vocals:.75,piano:.55,instrumental:.7}, poll:null, frame:null, inspectorKey:null
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
function json(url){return fetch(url,{cache:'no-store'}).then(async response=>{if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||`HTTP ${response.status}`);}return response.json();});}

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
        if(Object.keys(job.artifacts||{}).length)await applyJob(job);
      }
    }catch(error){setConnection(false);$('#progress-detail').textContent=`Соединение потеряно: ${error.message}. Повторяю…`;}finally{busy=false;}
  };
  await check();if(!['complete','partial','failed','interrupted'].includes(state.job?.status))state.poll=setInterval(check,900);
}
async function openJob(jobId){
  clearError();try{const job=await json(`/api/studio/jobs/${jobId}`);setConnection(true);state.job=job;renderStages(job);document.querySelectorAll('.library-item').forEach(item=>item.classList.toggle('active',item.dataset.id===jobId));if(['queued','running'].includes(job.status))return watchJob(jobId);await applyJob(job);}catch(error){showError(`Не удалось открыть результат: ${error.message}`);setConnection(false);}
}

async function applyJob(job){
  pause();state.job=job;state.melody=null;state.alignment=null;state.words=[];state.buffers={};state.position=0;state.a=null;state.b=null;state.loop=false;state.inspectorKey=null;clearTransportStatus();
  $('#workspace-empty').hidden=true;$('#studio-result').hidden=false;$('#mixer-section').hidden=false;
  const banner=$('#result-banner'),recovery=$('#result-recovery');banner.className=`result-banner ${job.status}`;$('#result-banner-text').textContent=job.status==='complete'?'Все стадии результата завершены.':job.status==='partial'?'Часть результата доступна; можно изучить сохранённое и повторить анализ.':job.status==='interrupted'?'Анализ прерван; сохранённые файлы доступны.':'Анализ завершился с ошибкой; сохранённые файлы доступны.';recovery.hidden=job.status==='complete';
  $('#result-title').textContent=job.input?.audio_name||'Вокальная партия';
  state.duration=Number(job.timeline?.duration)||0;$('#time-total').textContent=formatTime(state.duration);setRangeBounds();
  const artifacts=job.artifacts||{};
  const requests=[];
  if(artifacts['melody.json'])requests.push(json(artifacts['melody.json']).then(data=>state.melody=data));
  if(artifacts['alignment.json'])requests.push(json(artifacts['alignment.json']).then(data=>state.alignment=data));
  await Promise.allSettled(requests);flattenWords();renderDownloads(artifacts);renderMixer(artifacts);
  await loadAudioTracks(artifacts);state.duration=state.duration||Math.max(0,...Object.values(state.buffers).map(buffer=>buffer.duration));
  $('#time-total').textContent=formatTime(state.duration);setRangeBounds();makePeaks();
  const noteCount=state.melody?.notes?.length||0,wordCount=state.words.length,errors=job.errors||[];
  $('#result-summary').textContent=`${noteCount} нот · ${wordCount} слов · ${formatTime(state.duration,false)}`;
  $('#timeline-message').textContent=errors.length?errors.map(error=>`${error.stage}: ${error.message}`).join(' · '):noteCount?'Фиолетовые полоски: ноты; голубая линия: несглаженный pitch.':'Определимых нот не найдено; пустой таймлайн не заполнен догадками.';
  $('#play-button').disabled=!Object.keys(state.buffers).length;$('#set-a').disabled=false;$('#set-b').disabled=false;$('#clear-loop').disabled=false;$('#zoom-slider').disabled=false;
  resizeTimeline();drawAll();
}

$('#result-recovery').addEventListener('click',()=>{
  pause();document.querySelector('.setup-section').scrollIntoView({behavior:'smooth',block:'start'});showError('Выберите исходные файлы заново и запустите анализ: браузер не сохраняет содержимое локальных файлов.');$('#audio-input').focus();
});

function flattenWords(){
  state.words=[];const links=state.melody?.word_note_links||[],byWord=new Map();
  links.forEach(link=>{const list=byWord.get(link.word_id)||[];list.push(link);byWord.set(link.word_id,list);});
  (state.alignment?.lines||[]).forEach((line,lineIndex)=>(line.words||[]).forEach((word,wordIndex)=>{
    const digestIdPrefix=`l${String(lineIndex).padStart(4,'0')}-w${String(wordIndex).padStart(4,'0')}-`;
    const linkEntry=[...byWord.entries()].find(([key])=>key.startsWith(digestIdPrefix));
    state.words.push({...word,id:linkEntry?.[0]||digestIdPrefix,links:linkEntry?.[1]||[]});
  }));
}
function renderDownloads(artifacts){
  const allowed=[['vocals.wav','Скачать вокал'],['piano.wav','Скачать пианино'],['instrumental.wav','Скачать минус'],['melody.json','Данные нот']];
  const root=$('#downloads');root.replaceChildren();for(const [name,label] of allowed){if(!artifacts[name])continue;const link=document.createElement('a');link.href=artifacts[name];link.download=name;link.innerHTML=`<svg aria-hidden="true"><use href="#icon-download"/></svg><span>${label}</span>`;root.append(link);}
}

async function ensureContext(){
  if(state.context)return state.context;const Context=window.AudioContext||window.webkitAudioContext;state.context=new Context();state.master=state.context.createDynamicsCompressor();state.master.threshold.value=-9;state.master.knee.value=18;state.master.ratio.value=8;state.master.attack.value=.003;state.master.release.value=.2;state.master.connect(state.context.destination);return state.context;
}
async function loadAudioTracks(artifacts){
  const context=await ensureContext();for(const [key,meta] of Object.entries(trackMeta)){if(!artifacts[meta.file])continue;try{const response=await fetch(artifacts[meta.file]);if(!response.ok)throw new Error(`HTTP ${response.status}`);state.buffers[key]=await context.decodeAudioData(await response.arrayBuffer());}catch(error){showTransportStatus(`Не удалось декодировать дорожку «${meta.label}»: ${error.message}`);}}
}
function renderMixer(artifacts){
  const root=$('#track-controls');root.replaceChildren();for(const [key,meta] of Object.entries(trackMeta)){
    const available=Boolean(artifacts[meta.file]),row=document.createElement('div');row.className=`track-row${available?'':' disabled'}`;
    row.innerHTML=`<div class="track-head"><strong>${meta.label}</strong><output>${Math.round(state.volumes[key]*100)}%</output><button class="mute-button" type="button" aria-label="Отключить ${meta.label.toLowerCase()}" aria-pressed="false" ${available?'':'disabled'}><svg><use href="#icon-volume"/></svg></button></div><input type="range" min="0" max="1" step="0.01" value="${state.volumes[key]}" aria-label="Громкость: ${meta.label}" ${available?'':'disabled'}>`;
    if(!available){const note=document.createElement('small');note.textContent=key==='instrumental'&&state.job?.input?.type==='vocal'?'Сопровождение не было загружено':'Дорожка недоступна';row.append(note);}
    const slider=row.querySelector('input'),output=row.querySelector('output'),mute=row.querySelector('button');
    slider.addEventListener('input',()=>{state.volumes[key]=Number(slider.value);output.textContent=`${Math.round(state.volumes[key]*100)}%`;applyGain(key);});
    mute.addEventListener('click',()=>{state.muted[key]=!state.muted[key];mute.setAttribute('aria-pressed',String(state.muted[key]));mute.setAttribute('aria-label',`${state.muted[key]?'Включить':'Отключить'} ${meta.label.toLowerCase()}`);mute.querySelector('use').setAttribute('href',state.muted[key]?'#icon-muted':'#icon-volume');applyGain(key);});
    root.append(row);
  }
}
function gainFor(key){if(!state.gains[key]){state.gains[key]=state.context.createGain();state.gains[key].connect(state.master);}return state.gains[key];}
function applyGain(key){if(!state.context)return;gainFor(key).gain.setTargetAtTime(state.muted[key]?0:state.volumes[key],state.context.currentTime,.012);}

function currentPosition(){
  if(!state.playing||!state.context)return state.position;let value=state.startPosition+(state.context.currentTime-state.startedAt);
  if(state.loop&&validLoop()){const length=state.b-state.a;value=state.a+((value-state.a)%length+length)%length;}
  return Math.min(state.duration,value);
}
function validLoop(){return Number.isFinite(state.a)&&Number.isFinite(state.b)&&state.b-state.a>=.05;}
async function play(){
  if(state.playing||!Object.keys(state.buffers).length)return;clearTransportStatus();const context=await ensureContext();await context.resume();
  if(state.position>=state.duration-.005)state.position=0;if(state.loop&&validLoop()&&(state.position<state.a||state.position>=state.b))state.position=state.a;
  state.startPosition=state.position;state.startedAt=context.currentTime;state.sources={};
  for(const [key,buffer] of Object.entries(state.buffers)){const source=context.createBufferSource();source.buffer=buffer;source.connect(gainFor(key));applyGain(key);if(state.loop&&validLoop()){source.loop=true;source.loopStart=state.a;source.loopEnd=Math.min(state.b,buffer.duration);}source.start(context.currentTime,Math.min(state.position,Math.max(0,buffer.duration-.001)));state.sources[key]=source;}
  state.playing=true;$('#play-button').setAttribute('aria-label','Пауза');$('#play-button use').setAttribute('href','#icon-pause');tick();
}
function stopSources(){Object.values(state.sources).forEach(source=>{try{source.stop();}catch(_){}});state.sources={};}
function pause(){if(state.playing)state.position=currentPosition();state.playing=false;stopSources();cancelAnimationFrame(state.frame);const button=$('#play-button');button.setAttribute('aria-label','Воспроизвести');button.querySelector('use')?.setAttribute('href','#icon-play');updatePositionUI();}
function seek(value){const resume=state.playing;if(resume)pause();state.position=Math.max(0,Math.min(state.duration,Number(value)||0));updatePositionUI();drawAll();if(resume)play();}
function tick(){if(!state.playing)return;const value=currentPosition();if(!state.loop&&value>=state.duration-.005){pause();state.position=state.duration;updatePositionUI();return;}updatePositionUI(value);drawAll(value);state.frame=requestAnimationFrame(tick);}
function updatePositionUI(value=currentPosition()){state.position=state.playing?state.position:value;$('#time-current').textContent=formatTime(value);$('#seek-slider').value=value;$('#canvas-seek').value=value;renderInspector(value);}

$('#play-button').addEventListener('click',()=>state.playing?pause():play().catch(error=>showTransportStatus(`Браузер не запустил звук: ${error.message}`)));
$('#seek-slider').addEventListener('input',event=>seek(event.target.value));$('#canvas-seek').addEventListener('input',event=>seek(event.target.value));
$('#set-a').addEventListener('click',()=>{clearTransportStatus();state.a=currentPosition();if(state.b!==null&&state.a>=state.b)state.b=null;updateLoopUI();drawAll();});
$('#set-b').addEventListener('click',()=>{state.b=currentPosition();if(state.a!==null&&state.b<=state.a){showTransportStatus('Точка B должна быть позже точки A. Установите B на более поздней позиции.');state.b=null;}else clearTransportStatus();updateLoopUI();drawAll();});
$('#loop-button').addEventListener('click',()=>{if(!validLoop()){showTransportStatus('Сначала установите точки A и B с интервалом не меньше 0,05 секунды.');return;}clearTransportStatus();state.loop=!state.loop;$('#loop-button').setAttribute('aria-pressed',String(state.loop));const resume=state.playing;if(resume){pause();seek(state.a);play().catch(error=>showTransportStatus(`Не удалось запустить повтор: ${error.message}`));}});
$('#clear-loop').addEventListener('click',()=>{clearTransportStatus();state.loop=false;state.a=null;state.b=null;updateLoopUI();drawAll();});
function updateLoopUI(){$('#a-value').textContent=state.a===null?'·':formatTime(state.a,false);$('#b-value').textContent=state.b===null?'·':formatTime(state.b,false);$('#loop-button').disabled=!validLoop();$('#loop-button').setAttribute('aria-pressed',String(state.loop));}
$('#zoom-slider').addEventListener('input',event=>{const center=($('#timeline-scroller').scrollLeft+$('#timeline-scroller').clientWidth/2)/pixelsPerSecond();state.zoom=Number(event.target.value);$('#zoom-value').textContent=`${state.zoom}×`;resizeTimeline();$('#timeline-scroller').scrollLeft=Math.max(0,center*pixelsPerSecond()-$('#timeline-scroller').clientWidth/2);drawAll();});
$('#refresh-library').addEventListener('click',loadLibrary);
document.addEventListener('keydown',event=>{if(['INPUT','SELECT','TEXTAREA','BUTTON'].includes(event.target.tagName))return;if(event.code==='Space'){event.preventDefault();$('#play-button').click();}if(event.key==='ArrowRight')seek(currentPosition()+2);if(event.key==='ArrowLeft')seek(currentPosition()-2);});

function setRangeBounds(){for(const selector of ['#seek-slider','#canvas-seek']){const input=$(selector);input.max=Math.max(.001,state.duration);input.disabled=!state.duration;}}
function pixelsPerSecond(){return 28*state.zoom;}
function resizeTimeline(){
  const scroller=$('#timeline-scroller'),canvas=$('#timeline-canvas'),spacer=$('#timeline-spacer'),height=Math.max(390,scroller.clientHeight),width=Math.max(320,scroller.clientWidth);
  spacer.style.width=`${Math.max(width,state.duration*pixelsPerSecond()+82)}px`;canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;const dpr=Math.min(2,window.devicePixelRatio||1);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
  const overview=$('#overview-canvas'),overviewWidth=Math.max(180,overview.clientWidth),overviewHeight=Math.max(42,overview.clientHeight);overview.width=Math.round(overviewWidth*dpr);overview.height=Math.round(overviewHeight*dpr);
}
function makePeaks(){
  const buffer=state.buffers.vocals||state.buffers.instrumental||state.buffers.piano;if(!buffer){state.peaks=[];return;}const data=buffer.getChannelData(0),count=1800,step=Math.max(1,Math.floor(data.length/count));state.peaks=[];
  for(let start=0;start<data.length;start+=step){let peak=0;for(let index=start;index<Math.min(data.length,start+step);index+=4)peak=Math.max(peak,Math.abs(data[index]));state.peaks.push(peak);}
}
function setupCanvas(ctx,canvas){const dpr=Math.min(2,window.devicePixelRatio||1);ctx.setTransform(dpr,0,0,dpr,0,0);return {width:canvas.width/dpr,height:canvas.height/dpr};}
function midiY(midi,height){const top=96,bottom=height-88,min=42,max=86;return bottom-(midi-min)/(max-min)*(bottom-top);}
function noteName(midi, bilingual=false){const latin=['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'],ru=['До','До♯','Ре','Ре♯','Ми','Фа','Фа♯','Соль','Соль♯','Ля','Ля♯','Си'],pitch=Math.round(midi),octave=Math.floor(pitch/12)-1,index=(pitch%12+12)%12;return bilingual?`${ru[index]}${octave} (${latin[index]}${octave})`:`${latin[index]}${octave}`;}
function activeNoteIndex(position){return (state.melody?.notes||[]).findIndex(note=>note.start<=position&&position<note.end);}
function isApproximateWord(word){return !word?.aligned||['interpolated','approximate_split'].includes(word?.timing?.source);}
function renderInspector(position=currentPosition()){
  const notes=state.melody?.notes||[],index=activeNoteIndex(position),note=index>=0?notes[index]:null,activeWord=state.words.find(word=>word.start<=position&&position<word.end),key=`${note?.id||'none'}:${activeWord?.id||'none'}`;
  const summary=`График содержит ${notes.length} распознанных нот и ${state.words.length} слов. Кнопки «Предыдущая» и «Следующая» последовательно открывают каждую ноту, включая короткие.`,positionLabel=index>=0?`${index+1} / ${notes.length}`:`– / ${notes.length}`;
  if($('#timeline-semantic-summary').textContent!==summary)$('#timeline-semantic-summary').textContent=summary;
  if($('#inspector-position').textContent!==positionLabel)$('#inspector-position').textContent=positionLabel;
  let previousIndex=-1;for(let candidate=notes.length-1;candidate>=0;candidate--){if(notes[candidate].start<position-.001){previousIndex=index===candidate?candidate-1:candidate;break;}}
  const nextIndex=index>=0?index+1:notes.findIndex(candidate=>candidate.start>position+.001);
  $('#previous-note').disabled=previousIndex<0;$('#next-note').disabled=nextIndex<0||nextIndex>=notes.length;
  if(state.inspectorKey===key)return;state.inspectorKey=key;
  if(note){
    const cents=Number(note.cents)||0,score=Number(note.confidence),certainty=note.uncertain?'неуверенная сегментация':'устойчивая нота';
    $('#inspector-note').textContent=noteName(note.midi,true);
    $('#inspector-detail').textContent=`${formatTime(note.start,false)}–${formatTime(note.end,false)} · ${cents>=0?'+':''}${cents.toFixed(1)} cents · score ${Number.isFinite(score)?score.toFixed(3):'нет'} · ${certainty}`;
  }else{
    $('#inspector-note').textContent='Нет определимой ноты';$('#inspector-detail').textContent=`${formatTime(position,false)} · pitch в этой позиции не образует нотное событие.`;
  }
  const linkedWords=note?state.words.filter(word=>word.links?.some(link=>link.note_id===note.id)):[],word=activeWord||linkedWords[0];
  if(word){
    const approximate=isApproximateWord(word),extra=linkedWords.length>1?` · связаны слова: ${linkedWords.map(item=>item.text).join(', ')}`:'';
    $('#inspector-word').textContent=`${approximate?'≈ ':''}${word.text}`;$('#inspector-word-detail').textContent=`${formatTime(word.start,false)}–${formatTime(word.end,false)} · ${approximate?'приблизительная':'прямая'} привязка${extra}`;
  }else{
    $('#inspector-word').textContent='Нет слова';$('#inspector-word-detail').textContent=note?'У этой ноты нет связи с текстом.':'В этой позиции нет слова и ноты.';
  }
}
function navigateNote(direction){
  const notes=state.melody?.notes||[];if(!notes.length)return;const position=currentPosition(),index=activeNoteIndex(position);let target=-1;
  if(direction>0)target=index>=0?index+1:notes.findIndex(note=>note.start>position+.001);
  else if(index>0)target=index-1;else if(index<0){for(let candidate=notes.length-1;candidate>=0;candidate--){if(notes[candidate].start<position-.001){target=candidate;break;}}}
  if(target<0||target>=notes.length)return;const note=notes[target];seek(note.start+Math.min(.001,Math.max(0,(note.end-note.start)/2)));$('#timeline-scroller').scrollLeft=Math.max(0,note.start*pixelsPerSecond()-$('#timeline-scroller').clientWidth*.42);drawAll();
}
$('#previous-note').addEventListener('click',()=>navigateNote(-1));$('#next-note').addEventListener('click',()=>navigateNote(1));
$('#timeline-canvas').addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();event.stopPropagation();navigateNote(event.key==='ArrowRight'?1:-1);}});
function drawAll(position=currentPosition()){renderInspector(position);drawTimeline(position);drawOverview(position);}
function drawTimeline(position){
  const canvas=$('#timeline-canvas'),ctx=canvas.getContext('2d'),{width,height}=setupCanvas(ctx,canvas),scroller=$('#timeline-scroller'),pps=pixelsPerSecond(),keyboard=74,startTime=Math.max(0,scroller.scrollLeft/pps),endTime=startTime+(width-keyboard)/pps;
  ctx.clearRect(0,0,width,height);ctx.fillStyle='#090c11';ctx.fillRect(0,0,width,height);
  ctx.save();ctx.beginPath();ctx.rect(keyboard,0,width-keyboard,height);ctx.clip();
  for(let second=Math.floor(startTime);second<=endTime+1;second++){const x=keyboard+second*pps-scroller.scrollLeft;ctx.strokeStyle=second%5===0?'rgba(133,135,247,.3)':'rgba(255,255,255,.055)';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke();if(second%5===0){ctx.fillStyle='#858d9d';ctx.font='11px -apple-system,sans-serif';ctx.fillText(formatTime(second,false),x+5,18);}}
  const waveformBottom=72;ctx.strokeStyle='rgba(133,135,247,.65)';ctx.beginPath();for(let x=keyboard;x<width;x++){const time=startTime+(x-keyboard)/pps,index=Math.floor(time/state.duration*state.peaks.length),peak=state.peaks[index]||0,y=44-peak*22;ctx.moveTo(x,y);ctx.lineTo(x,44+peak*22);}ctx.stroke();ctx.strokeStyle='rgba(255,255,255,.12)';ctx.beginPath();ctx.moveTo(keyboard,waveformBottom);ctx.lineTo(width,waveformBottom);ctx.stroke();
  for(let midi=42;midi<=86;midi++){const y=midiY(midi,height),next=midiY(midi+1,height);ctx.fillStyle=[1,3,6,8,10].includes(midi%12)?'rgba(255,255,255,.018)':'rgba(255,255,255,.035)';ctx.fillRect(keyboard,y,next-y,width-keyboard);ctx.strokeStyle='rgba(255,255,255,.045)';ctx.beginPath();ctx.moveTo(keyboard,y);ctx.lineTo(width,y);ctx.stroke();}
  const activeWord=state.words.find(word=>word.start<=position&&position<word.end),activeNotes=new Set(activeWord?.links?.map(link=>link.note_id)||[]);
  for(const note of state.melody?.notes||[]){if(note.end<startTime||note.start>endTime)continue;const x=keyboard+note.start*pps-scroller.scrollLeft,w=Math.max(3,(note.end-note.start)*pps),y=midiY(note.midi+.42,height),row=Math.abs(midiY(note.midi+1,height)-midiY(note.midi,height)),h=Math.max(6,row*.72);ctx.fillStyle=activeNotes.has(note.id)?'#a9abff':note.uncertain?'rgba(133,135,247,.48)':'#7779e8';ctx.fillRect(x,y,w,h);if(w>=28){ctx.fillStyle='#090b11';ctx.font='600 8px -apple-system,sans-serif';ctx.fillText(noteName(note.midi,w>=76),x+3,y+h-1,Math.max(8,w-6));}if(note.uncertain){ctx.fillStyle='#f4f6fb';ctx.font='10px -apple-system,sans-serif';ctx.fillText('?',x+4,y+10);}}
  const frames=state.melody?.pitch_frames||[];ctx.strokeStyle='#58d8ff';ctx.lineWidth=1.7;ctx.beginPath();let drawing=false;const stride=Math.max(1,Math.floor(frames.length/12000));for(let i=0;i<frames.length;i+=stride){const frame=frames[i];if(frame.time<startTime||frame.time>endTime||frame.midi===null){drawing=false;continue;}const x=keyboard+frame.time*pps-scroller.scrollLeft,y=midiY(frame.midi,height);if(drawing)ctx.lineTo(x,y);else{ctx.moveTo(x,y);drawing=true;}}ctx.stroke();ctx.lineWidth=1;
  const lyricTop=height-70;ctx.fillStyle='rgba(17,25,43,.96)';ctx.fillRect(keyboard,lyricTop,width-keyboard,70);for(const word of state.words){if(word.end<startTime||word.start>endTime)continue;const x=keyboard+word.start*pps-scroller.scrollLeft,w=Math.max(24,(word.end-word.start)*pps),approx=!word.aligned||['interpolated','approximate_split'].includes(word.timing?.source);ctx.fillStyle=word===activeWord?'rgba(88,216,255,.2)':'rgba(133,135,247,.09)';ctx.fillRect(x,lyricTop,w,70);ctx.strokeStyle='rgba(88,216,255,.22)';ctx.strokeRect(x,lyricTop,w,70);ctx.fillStyle=word===activeWord?'#d6f7ff':'#cbd1df';ctx.font=`${word===activeWord?'600':'500'} 12px -apple-system,sans-serif`;ctx.fillText(`${approx?'≈ ':''}${word.text}`,x+6,lyricTop+39,Math.max(10,w-10));}
  if(validLoop()){const ax=keyboard+state.a*pps-scroller.scrollLeft,bx=keyboard+state.b*pps-scroller.scrollLeft;ctx.fillStyle='rgba(133,135,247,.07)';ctx.fillRect(ax,0,bx-ax,height);ctx.strokeStyle='rgba(133,135,247,.75)';for(const x of [ax,bx]){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke();}}
  const playX=keyboard+position*pps-scroller.scrollLeft;ctx.strokeStyle='#58d8ff';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(playX,0);ctx.lineTo(playX,height);ctx.stroke();ctx.restore();
  ctx.fillStyle='#0d1015';ctx.fillRect(0,0,keyboard,height);ctx.strokeStyle='rgba(255,255,255,.1)';ctx.beginPath();ctx.moveTo(keyboard-.5,0);ctx.lineTo(keyboard-.5,height);ctx.stroke();
  for(let midi=42;midi<=86;midi++){const y=midiY(midi,height),next=midiY(midi+1,height),black=[1,3,6,8,10].includes(midi%12);ctx.fillStyle=black?'#161a21':'#e5e7eb';ctx.fillRect(black?0:0,y,black?45:72,next-y);ctx.strokeStyle='#303541';ctx.strokeRect(0,y,black?45:72,next-y);if(midi%12===0){ctx.fillStyle=black?'#fff':'#252a34';ctx.font='9px -apple-system,sans-serif';ctx.fillText(`C${midi/12-1}`,50,y+10);}}
}
function drawOverview(position){
  const canvas=$('#overview-canvas'),ctx=canvas.getContext('2d'),{width,height}=setupCanvas(ctx,canvas);ctx.clearRect(0,0,width,height);ctx.fillStyle='#0d1015';ctx.fillRect(0,0,width,height);if(!state.duration)return;
  ctx.fillStyle='rgba(133,135,247,.55)';state.peaks.forEach((peak,index)=>{const x=index/state.peaks.length*width,h=peak*(height-10);ctx.fillRect(x,(height-h)/2,Math.max(1,width/state.peaks.length),h);});
  const scroller=$('#timeline-scroller'),visible=Math.min(1,Math.max(0,(scroller.clientWidth-74)/(state.duration*pixelsPerSecond()))),start=scroller.scrollLeft/(state.duration*pixelsPerSecond());ctx.fillStyle='rgba(88,216,255,.08)';ctx.fillRect(start*width,0,visible*width,height);ctx.strokeStyle='rgba(88,216,255,.8)';ctx.strokeRect(start*width+.5,.5,Math.max(2,visible*width-1),height-1);ctx.fillStyle='#58d8ff';ctx.fillRect(position/state.duration*width-1,0,2,height);
}
$('#timeline-scroller').addEventListener('scroll',()=>drawAll());$('#timeline-canvas').addEventListener('click',event=>{const rect=event.currentTarget.getBoundingClientRect(),x=event.clientX-rect.left,time=($('#timeline-scroller').scrollLeft+Math.max(0,x-74))/pixelsPerSecond();seek(time);});$('#overview-canvas').addEventListener('click',event=>{const rect=event.currentTarget.getBoundingClientRect(),time=(event.clientX-rect.left)/rect.width*state.duration;seek(time);$('#timeline-scroller').scrollLeft=Math.max(0,time*pixelsPerSecond()-$('#timeline-scroller').clientWidth/2);});
window.addEventListener('resize',()=>{resizeTimeline();drawAll();});document.addEventListener('visibilitychange',()=>{if(!document.hidden)drawAll();});
loadLibrary();
