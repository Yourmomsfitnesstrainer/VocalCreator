/* One effective score and one revision history for labels directly above notes. */
(function(root,factory){
  const api=factory(typeof module==='object'&&module.exports?require('./syllable-model.js'):root.SyllableModel);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.SyllableEditor=api.createEditor(root);
})(globalThis,function(M){
  'use strict';
  async function request(fetcher,url,options={}){
    const response=await fetcher(url,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...options.headers}}),data=await response.json().catch(()=>({}));
    if(!response.ok){const detail=data.detail,error=Error(typeof detail==='string'?detail:detail?.message||`Ошибка ${response.status}`);error.status=response.status;error.detail=detail;throw error;}return data;
  }
  function createSession(fetcher){
    const session={jobId:null,history:null,state:null,epoch:0,pendingSave:null,saving:false};
    session.endpoint=()=>`/api/studio/jobs/${session.jobId}/syllables`;
    session.load=async jobId=>{
      const epoch=++session.epoch;session.jobId=jobId;session.history=null;session.state=null;session.sourceNotes=null;session.pendingSave=null;session.saving=false;
      const state=await request(fetcher,session.endpoint());if(epoch!==session.epoch)return false;
      let doc=null,snapshot=null;if(state.effective_url)doc=await request(fetcher,state.effective_url);if(state.effective_source_notes_url)snapshot=await request(fetcher,state.effective_source_notes_url);
      if(epoch!==session.epoch)return false;
      if(doc&&(doc.job_id!==jobId||doc.base_analysis_key!==state.base_analysis_key||doc.revision!==state.revision))throw Error('Результат принадлежит другой базе или ревизии. Обновите слоговую партию.');
      if(snapshot&&(snapshot.job_id!==jobId||snapshot.base_analysis_key!==doc?.base_analysis_key||JSON.stringify(snapshot.source)!==JSON.stringify(doc.source)))throw Error('Ноты и слоговая партия относятся к разным анализам.');if(doc)M.validate(doc,snapshot?.notes);session.sourceSnapshot=snapshot;session.sourceNotes=snapshot?.notes||null;session.state=state;session.history=doc?new M.History(doc):null;return true;
    };
    session.save=async()=>{
      if(!session.history||session.saving)return null;session.saving=true;
      const epoch=session.epoch,history=session.history,doc=history.document;
      const pending=session.pendingSave||{request_id:M.id('save'),base_revision:doc.revision,document:M.clone(doc)};session.pendingSave=pending;
      try{const result=await request(fetcher,session.endpoint(),{method:'PUT',body:JSON.stringify(pending)});
        if(epoch!==session.epoch)return null;
        if(result.document.job_id!==session.jobId||result.document.base_analysis_key!==doc.base_analysis_key)throw Error('Ответ сохранения относится к другой партии. Ваши правки сохранены в браузере.');
        history.savedAs(result.document,pending.document);session.pendingSave=null;session.state=result;return result;
      }catch(error){if(error.status&&error.status<500)session.pendingSave=null;throw error;}finally{if(epoch===session.epoch)session.saving=false;}
    };
    return session;
  }
  function createEditor(root){
    const q=s=>root.document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const button=(action,text,extra='')=>`<button type="button" data-score="${action}" ${extra}>${text}</button>`;
    const session=createSession((...args)=>root.fetch(...args));
    const legacy=new URLSearchParams(root.location?.search||'').get('legacy')==='1';
    let host,editing=false,selected=null,splitDraft=null,notes=[],poll=null,serial=0,loadError=null,backup=null,hits=[],positionKey='',labelsIndex=null,byNote=null;
    const document=()=>session.history?.document,active=()=>Boolean(document()),dirty=()=>Boolean(session.history?.dirty),unit=()=>document()?.units.find(u=>u.unit_id===selected?.unit_id);
    const time=value=>Number.isFinite(value)?value.toFixed(3):'';
    const reasonCopy={local_occurrence_ctc_proposal:'границы предложены по звучанию этого слова',orthographic_syllable_proposal:'предполагаемая разбивка по написанию',weak_ctc_boundary_proposal:'акустическое подтверждение границ слабое',character_evidence_unavailable:'нет надёжных границ внутри слова',segmentation_unavailable:'состав слогов пока неизвестен',timing_unavailable:'время пока неизвестно',no_note_link:'подходящая нота не найдена',overlapping_lead_candidates:'варианты времени противоречат друг другу',sparse_ctc_emission_without_pitch_support:'недостаточно данных о звучании и высоте',ctc_boundary_gaps:'между найденными границами есть пробелы',acoustic_note_support:'время уточнено по звучащей ноте',whole_word_fallback:'показано целое слово с неизвестной разбивкой'};
    const reasonText=u=>[...new Set((u.reason_codes||[]).map(code=>reasonCopy[code]||'границы требуют проверки'))].join('; ');
    const linkCount=n=>`${n} ${n%10===1&&n%100!==11?'нотная связь':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'нотные связи':'нотных связей'}`;
    const noteName=n=>n?`${host.noteName(n.midi)} · ${host.format(n.start,false)}–${host.format(n.end,false)}`:'Без ноты';
    function positionPanel(){const panel=q('#score-panel');if(!panel||panel.hidden||!host.editorAnchor)return;const anchor=host.editorAnchor(),width=panel.getBoundingClientRect().width;panel.style.left=`${Math.max(anchor.left+82,anchor.right-width-12)}px`;const top=Math.max(112,Math.min(anchor.top+35,root.innerHeight-260));panel.style.top=`${top}px`;panel.style.maxHeight=`${Math.max(150,root.innerHeight-top-16)}px`;}
    const storageKey=()=>`vocalcreator:syllables:${session.jobId}`;
    function say(message,error=false){const node=q('#score-message');node.textContent=message;node.hidden=!message;node.classList.toggle('error',error);}
    function invalidate(){positionKey='';labelsIndex=null;byNote=null;host.invalidate();}
    function unfinished(){const input=q('#score-text');if(!editing||q('#score-panel').hidden||!input||input.value===(unit()?.text||''))return null;return {selected,text:input.value,start:q('#score-start')?.value,end:q('#score-end')?.value,occurrence:q('#score-occurrence')?.value};}
    function persist(){try{const input=unfinished();if(dirty()||input)root.localStorage.setItem(storageKey(),JSON.stringify({document:document(),pending_save:session.pendingSave,unfinished:input}));else root.localStorage.removeItem(storageKey());}catch(_){say('Не удалось записать резервную копию в браузере. Сохраните или экспортируйте правки перед закрытием.',true);}}
    function changed(result){session.history.commit(result.document||result);if(result.unit_id)selected={unit_id:result.unit_id};splitDraft=null;invalidate();persist();render();host.draw();}
    function operate(fn){try{changed(fn());say('');return true;}catch(error){say(error.message,true);return false;}}
    function exportDocument(){const doc=document()||backup?.document;if(!doc)return;const blob=new root.Blob([JSON.stringify(doc,null,2)],{type:'application/json'}),url=root.URL.createObjectURL(blob),a=root.document.createElement('a');a.href=url;a.download=`syllable-score-${session.jobId}-revision-${doc.revision}.json`;a.click();root.setTimeout(()=>root.URL.revokeObjectURL(url),1000);}
    function init(api){
      host=api;root.document.body.classList.add(legacy?'syllable-archive':'syllable-studio');
      const section=root.document.createElement('section');section.id='score-workspace';section.className='score-workspace';section.hidden=true;
      section.innerHTML=`<div class="score-bar"><div class="score-title"><strong>${legacy?'Архив ручной разметки':'Слоговая партия'}</strong><span id="score-save-state" role="status">Загрузка…</span></div><div class="score-actions">${button('prepare','Подготовить слоги','id="score-prepare" hidden')}${button('edit','Исправить слоги','id="score-edit" disabled')}${button('undo','Отменить','id="score-undo" hidden')}${button('redo','Вернуть','id="score-redo" hidden')}${button('save','Сохранить','id="score-save" hidden')}${button('done','Закончить коррекцию','id="score-done" hidden')}<a id="score-archive" target="_blank" rel="noopener">Архив</a></div></div>
        <p id="score-message" class="score-message" role="status" aria-live="polite" hidden></p>
        <div id="score-recovery" class="score-recovery" hidden>${button('export','Экспортировать мои правки')}${button('restore','Восстановить ввод из браузера','id="score-restore" hidden')}<a id="score-current" target="_blank" rel="noopener">Открыть сохранённую ревизию отдельно</a></div>
        <div id="score-stats" class="score-stats"></div>
        <div id="score-edit-help" class="score-edit-help" hidden>Выберите подпись или ноту. Enter — применить, Tab в поле слога — применить и перейти к следующей области. ${button('current','Исправить у курсора')}</div>
        <div id="score-panel" class="score-panel" hidden></div>
        <details id="score-unresolved" class="score-unresolved" hidden><summary>Нерешённые места <span id="score-unresolved-count"></span></summary><div class="score-search"><label>Найти слово или слог <input id="score-search" type="search"></label>${button('new-unplaced','Добавить слог без ноты')}</div><div id="score-unresolved-list"></div></details>
        <div id="score-legacy-content"></div>`;
      q('.timeline-header').after(section);
      section.addEventListener('click',event=>{const node=event.target.closest('[data-score]');if(node)dispatch(node.dataset.score,event,node);});
      section.addEventListener('submit',event=>{event.preventDefault();submit(event.target);});
      section.addEventListener('input',event=>{if(event.target.id==='score-search')renderUnresolved();else if(event.target.closest('#score-panel')){renderButtons();persist();}});
      section.addEventListener('keydown',event=>{
        if(event.key==='Tab'&&event.target.id==='score-text'){event.preventDefault();event.stopPropagation();if(applyText())nextRegion(event.shiftKey?-1:1);}
        if(event.key==='Escape'&&event.target.closest('#score-panel')){event.preventDefault();event.stopPropagation();splitDraft=null;renderPanel();q('#score-text')?.focus();}
        if(event.target.matches('input,textarea,select'))event.stopPropagation();
      });
      root.document.addEventListener('keydown',event=>{if(!editing||event.target.closest('input,select,textarea,[contenteditable=true]'))return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();if(event.shiftKey)redo();else undo();}});
      root.addEventListener('beforeunload',event=>{persist();if(dirty()||unfinished()){event.preventDefault();event.returnValue='';}});
    }
    async function load(jobId,sourceNotes){
      clearTimeout(poll);const token=++serial;notes=sourceNotes||[];editing=false;selected=null;splitDraft=null;backup=null;loadError=null;invalidate();root.document.body.classList.remove('score-editing');
      q('#score-workspace').hidden=false;q('#score-panel').hidden=true;q('#score-edit-help').hidden=true;q('#score-unresolved').hidden=true;
      q('#score-save-state').textContent='Загрузка слоговой партии…';q('#score-stats').textContent='';say('');q('#score-recovery').hidden=true;
      q('#score-archive').href=`/?job=${encodeURIComponent(jobId)}${legacy?'':'&legacy=1'}`;q('#score-archive').textContent=legacy?'К слоговой партии':'Старые ручные данные';q('#score-current').href=`/?job=${encodeURIComponent(jobId)}`;
      if(legacy){session.jobId=jobId;await loadLegacy(jobId,token);return;}
      try{
        if(!await session.load(jobId)||token!==serial)return;
        if(session.sourceNotes){notes=session.sourceNotes;host.useScoreNotes?.(notes);if(session.sourceSnapshot?.source_piano_url)await host.useScorePiano?.(session.sourceSnapshot);if(token!==serial)return;}
        try{const data=JSON.parse(root.localStorage.getItem(storageKey())||'null');if(data?.document?.job_id===jobId&&(data.unfinished||JSON.stringify(data.document)!==JSON.stringify(document())))backup=data;}catch(_){}
        q('#score-restore').hidden=!backup;q('#score-recovery').hidden=!backup;
        if(backup)say('В браузере остался несохранённый ввод. Его можно восстановить или экспортировать.');
        if(session.state.candidate_available)say('Готово новое автоматическое предложение. Здесь сохранена ваша ручная ревизия на прежней базе.');
        if(session.state.error)say(`Подготовка: ${session.state.error.message||session.state.error}. Прежний сохранённый результат доступен.`,true);
        invalidate();render();host.draw();
        if(['queued','running'].includes(session.state.status))poll=root.setTimeout(()=>load(jobId,notes),1200);
      }catch(error){if(token!==serial)return;loadError=error;say(error.message,true);render();}
    }
    async function loadLegacy(jobId,token){
      q('#score-edit').hidden=true;q('#score-prepare').hidden=true;
      try{const archive=await request((...args)=>root.fetch(...args),`/api/studio/jobs/${jobId}/syllables/legacy`);if(token!==serial)return;const doc=archive.document||archive;
        q('#score-save-state').textContent=`Только просмотр · ревизия ${doc.revision??'архивная'}`;
        q('#score-legacy-content').innerHTML='<p>Старые слова и роли сохранены в исходном формате. Здесь они не преобразуются в слоги.</p><table><thead><tr><th>Текст</th><th>Начало, с</th><th>Конец, с</th><th>Роль</th></tr></thead><tbody>'+ (doc.annotations||[]).map(a=>`<tr><td>${esc(a.text)}</td><td>${time(a.start)}</td><td>${time(a.end)}</td><td>${esc(doc.roles?.find(r=>r.id===a.role_id)?.name||'Без роли')}</td></tr>`).join('')+'</tbody></table>';
      }catch(error){if(token!==serial)return;q('#score-save-state').textContent=error.status===404?'Сохранённой старой разметки нет':'Архив недоступен';say(error.status===404?'Эта песня ещё не имеет старых ручных данных. Автоматическое преобразование не запускалось.':error.message,error.status!==404);}
    }
    async function prepare(){
      if(session.saving)return;const jobId=session.jobId,token=serial;q('#score-prepare').disabled=true;say('Готовлю автоматические слоги и их связи с существующими нотами…');
      try{await request((...args)=>root.fetch(...args),session.endpoint(),{method:'POST',body:JSON.stringify({request_id:M.id('prepare'),retry:['failed','interrupted'].includes(session.state?.status)})});if(token===serial)await load(jobId,notes);}catch(error){if(token===serial){say(error.message,true);q('#score-prepare').disabled=false;}}
    }
    async function save(){
      if(unfinished()&&!applyText())return false;renderButtons();try{const pending=session.save();renderButtons();const result=await pending;if(!result)return false;persist();q('#score-recovery').hidden=!dirty();say(dirty()||unfinished()?'Снимок сохранён. Более поздние изменения остаются в редакторе.':'Слоговая партия сохранена.');renderButtons();host.draw();return true;}
      catch(error){persist();q('#score-recovery').hidden=false;say(error.status===409?'В другой вкладке уже сохранена новая ревизия. Ваш ввод остался здесь: экспортируйте правки и откройте сохранённую версию отдельно.':`Запись не удалась: ${error.message}. Ввод сохранён в редакторе; повторите сохранение или экспортируйте его.`,true);return false;}
      finally{renderButtons();}
    }
    function renderButtons(){
      const h=session.history,busy=['queued','running'].includes(session.state?.status);q('#score-prepare').hidden=Boolean(h)||legacy;q('#score-prepare').disabled=busy;q('#score-prepare').textContent=loadError?'Обновить слоговую партию':busy?'Подготовка слогов…':'Подготовить слоги';
      q('#score-edit').hidden=editing||legacy;q('#score-edit').disabled=!h;
      for(const name of ['undo','redo','save','done'])q(`#score-${name}`).hidden=!editing;
      q('#score-undo').disabled=!h?.past.length||session.saving;q('#score-redo').disabled=!h?.future.length||session.saving;q('#score-save').disabled=session.saving||(!dirty()&&!unfinished());q('#score-done').disabled=session.saving;
      q('#score-save').textContent=session.saving?'Сохраняю…':'Сохранить';q('#score-save-state').textContent=h?unfinished()?'Есть незавершённый ввод':dirty()?'Есть несохранённые правки':document().revision?`Сохранено · ревизия ${document().revision}`:'Автоматическое предложение · без ручных правок':busy?'Идёт подготовка':loadError?'Слоги недоступны':'Слоги ещё не подготовлены';
      q('#score-edit-help').hidden=!editing;q('#score-unresolved').hidden=!h;for(const [name,disabled] of [['undo',!h?.past.length||session.saving],['redo',!h?.future.length||session.saving],['save',session.saving||(!dirty()&&!unfinished())],['done',session.saving]]){const node=q(`#score-inline-${name}`);if(node)node.disabled=disabled;}
    }
    function render(){
      renderButtons();if(!active())return;const d=document(),fallback=d.units.filter(u=>u.kind==='word_fallback').length,unplaced=d.units.filter(u=>!u.intervals.length).length;
      q('#score-stats').textContent=`${d.units.length-fallback} слогов · ${fallback} целых слов с неизвестной разбивкой · ${d.note_links.length} связей · ${unplaced} без времени`;
      renderPanel();renderUnresolved();
    }
    function startEdit(){if(!active())return;editing=true;host.pause();host.suspendFollow?.();root.document.body.classList.add('score-editing');render();host.draw();if(!selected)selectCurrent(false);}
    function selectCurrent(seekPosition=true){const t=host.position(),link=document()?.note_links.find(l=>l.start<=t&&t<l.end);if(link)select({unit_id:link.unit_id,link_id:link.link_id},seekPosition);else{const n=notes.find(n=>n.start<=t&&t<n.end)||notes.find(n=>n.start>=t);if(n)select({source_note_id:n.id,start:n.start,end:n.end},seekPosition);else select({newUnplaced:true},seekPosition);}}
    function select(selection,seekPosition=true){selected=selection;splitDraft=null;if(!editing)startEdit();else renderPanel();const u=unit(),l=document()?.note_links.find(l=>l.link_id===selection.link_id),t=l?.start??u?.start??selection.start;if(seekPosition&&Number.isFinite(t))host.seek(t);host.revealEditor?.({start:t,source_note_id:l?.source_note_id||selection.source_note_id});positionPanel();root.setTimeout(()=>{q('#score-text')?.focus();q('#score-text')?.select();},0);host.draw();}
    function occurrenceOptions(chosen){return '<option value="">Новое ручное исполнение</option>'+document().occurrences.map(o=>`<option value="${esc(o.occurrence_id)}" ${o.occurrence_id===chosen?'selected':''}>${esc(o.source_text||'Ручное слово')} · ${esc(o.source_word_id||o.occurrence_id)}</option>`).join('');}
    function noteOptions(chosen,includeEmpty=true){return (includeEmpty?'<option value="">Без найденной ноты</option>':'')+notes.map(n=>`<option value="${esc(n.id)}" ${n.id===chosen?'selected':''}>${esc(noteName(n))}</option>`).join('');}
    function renderPanel(){
      const panel=q('#score-panel');panel.hidden=!editing||!selected;if(panel.hidden)return;const u=unit(),link=document().note_links.find(l=>l.link_id===selected.link_id),n=notes.find(n=>n.id===(link?.source_note_id||selected.source_note_id));
      if(splitDraft){renderSplit();return;}
      const linked=u?document().note_links.filter(l=>l.unit_id===u.unit_id).sort((a,b)=>a.start-b.start):[],occurrence=document().occurrences.find(o=>o.occurrence_id===u?.occurrence_id),siblings=document().units.filter(v=>v.occurrence_id===u?.occurrence_id).sort((a,b)=>a.order-b.order),neighbor=siblings[siblings.indexOf(u)+1];
      panel.innerHTML=`<div class="score-panel-heading"><strong>${u?'Коррекция слога':'Добавить слог'}${n?` · ${esc(noteName(n))}`:''}</strong>${button('close','Закрыть коррекцию области')}</div>
        ${u?`<p class="score-provenance">${esc(occurrence?.source_text||'Ручное исполнение')} · ${u.origin==='manual'?'ручная правка':u.kind==='word_fallback'?'разбивка неизвестна':'автоматическое предложение'} · ${linkCount(linked.length)}${occurrence?.source_text&&siblings.map(item=>item.text).join('')!==occurrence.source_text?' · ручная группа не составляет исходное слово':''}${u.manual_override?' · текст отличается от TXT':''}</p>`:''}
        <form id="score-text-form" class="score-text-form"><label>${u?'Общий текст слога':'Текст слога'}<input id="score-text" autocomplete="off" value="${esc(u?.text||'')}" required></label>${!u?`<label>Исполнение слова<select id="score-occurrence">${occurrenceOptions(null)}</select></label>`:''}<button type="submit">${u?'Применить ко всем связям':'Добавить слог'}</button>${button('next','Следующая область')}</form>
        ${u?.kind==='word_fallback'?button('split','Разбить слово'):''}<details class="score-advanced" ${!u||!linked.length?'open':''}><summary>Границы, распев и разбиение</summary>${u?.reason_codes?.length?`<p class="score-explanation">${esc(reasonText(u))}.</p>`:''}
        ${u?`<div class="score-unit-actions">${button('split','Разбить на слоги')}${button('merge','Объединить со следующим',neighbor?'':'disabled')}${button('export','Экспортировать партию')}</div>`:''}
        ${link?`<form id="score-link-time-form" class="score-times"><strong>Выбранная область</strong><label>Начало, с<input id="score-start" type="number" step="0.001" value="${time(link.start)}" required></label><label>Конец, с<input id="score-end" type="number" step="0.001" value="${time(link.end)}" required></label><button type="submit">Применить границы</button>${button('split-region','Разделить область')}${button('delete-link','Убрать подпись этой области')}</form>
        <form id="score-reassign-form" class="score-reassign"><label>Другой слог только этой области<input id="score-reassign-text" autocomplete="off" required></label><button type="submit">Переназначить область</button></form>`:''}
        ${u?`<details class="score-links" ${linked.length?'':'open'}><summary>Все области слога (${linked.length}) и интервалы без высоты</summary><div class="score-link-list">${linked.map(l=>button('select-link',`${esc(noteName(notes.find(n=>n.id===l.source_note_id)))} · ${time(l.start)}–${time(l.end)}${l.continuation?' · продолжение':''}`,`data-link="${esc(l.link_id)}"`)).join('')||'<span>Нотных связей нет.</span>'}</div>
        <form id="score-unpitched-form"><label>Интервалы без высоты, по одному «начало конец» на строке<textarea id="score-unpitched-times" rows="2" placeholder="12.300 12.750">${esc(u.intervals.filter(i=>i.support==='unpitched').map(i=>`${time(i.start)} ${time(i.end)}`).join('\n'))}</textarea></label><button type="submit">Сохранить интервалы без ноты</button></form></details>`:''}
        ${u?`<form id="score-attach-form" class="score-times"><label>Добавить связь с нотой<select id="score-attach-note">${noteOptions(n?.id,false)}</select></label><label>Начало, с<input id="score-attach-start" type="number" step="0.001" value="${time(n?.start??u.start)}" required></label><label>Конец, с<input id="score-attach-end" type="number" step="0.001" value="${time(n?.end??u.end)}" required></label><button type="submit">Продолжить этот слог</button></form>`:''}
        ${!u?`<form id="score-new-time" class="score-times"><label>Начало, с<input id="score-start" type="number" step="0.001" value="${time(selected.start)}"></label><label>Конец, с<input id="score-end" type="number" step="0.001" value="${time(selected.end)}"></label><span>${n?'Текстовая область внутри выбранной ноты':'Оставьте оба поля пустыми, если время неизвестно.'}</span></form>`:''}</details><div class="score-inline-actions">${button('undo','Отменить',`id="score-inline-undo" ${session.history.past.length?'':'disabled'}`)}${button('redo','Вернуть',`id="score-inline-redo" ${session.history.future.length?'':'disabled'}`)}${button('save','Сохранить','id="score-inline-save"')}${button('done','Готово','id="score-inline-done"')}</div>`;
      positionPanel();panel.querySelector('.score-advanced')?.addEventListener('toggle',positionPanel);
      q('#score-attach-note')?.addEventListener('change',event=>{const note=notes.find(n=>n.id===event.target.value);if(note){q('#score-attach-start').value=time(note.start);q('#score-attach-end').value=time(note.end);}});
    }
    function renderUnresolved(){
      if(!active())return;const filter=(q('#score-search').value||'').toLocaleLowerCase(),d=document(),issues=new Map();for(const item of d.unresolved||[])if(item.unit_id){const current=issues.get(item.unit_id)||[];current.push(item.code||item.reason_code||item.reason||'');issues.set(item.unit_id,current);}
      const list=d.units.filter(u=>u.kind==='word_fallback'||!d.note_links.some(l=>l.unit_id===u.unit_id)||issues.has(u.unit_id));q('#score-unresolved-count').textContent=`(${list.length})`;
      q('#score-unresolved-list').innerHTML=list.filter(u=>u.text.toLocaleLowerCase().includes(filter)).map(u=>button('unresolved',`${esc(u.text)} <span>${u.start===null?'время неизвестно':host.format(u.start,false)} · ${u.kind==='word_fallback'?'разбивка неизвестна':!d.note_links.some(l=>l.unit_id===u.unit_id)?'без ноты':'требует проверки'}</span>`,`data-unit="${esc(u.unit_id)}"`)).join('')||'<p>Нерешённых мест по этому запросу нет.</p>';
    }
    function applyText(){
      const text=q('#score-text')?.value;if(text===undefined)return true;const u=unit();if(u)return operate(()=>M.rename(document(),u.unit_id,text));
      return operate(()=>M.add(document(),{text,occurrence_id:q('#score-occurrence').value||null,source_note_id:selected.source_note_id||null,start:q('#score-start').value===''?null:Number(q('#score-start').value),end:q('#score-end').value===''?null:Number(q('#score-end').value)},notes));
    }
    function nextRegion(direction=1){
      if(!active())return;const regions=notes.flatMap(n=>regionsFor(n)).sort((a,b)=>a.start-b.start),link=document().note_links.find(l=>l.link_id===selected?.link_id),u=unit(),t=link?.start??u?.start??selected?.start??host.position();let at=regions.findIndex(r=>r.start>t+1e-7);if(direction<0){at=-1;for(let i=regions.length-1;i>=0;i--)if(regions[i].start<t-1e-7){at=i;break;}}if(at<0){say(direction<0?'Это первая нотная область.':'Это последняя нотная область.');return;}select(regions[at].selection);
    }
    function beginSplit(){const u=unit();splitDraft={unit_id:u.unit_id,parts:null};q('#score-panel').innerHTML=`<form id="score-split-parts"><label>Слоги через |<input id="score-parts" value="${esc(u.text)}" autocomplete="off" required></label><p>Сначала состав слогов, затем явное распределение по областям. Ноты и звук сохраняются.</p><button type="submit">Предпросмотр распределения</button>${button('cancel-split','Отмена')}</form>`;positionPanel();q('#score-parts').focus();}
    function previewSplit(){const parts=q('#score-parts').value.split('|').map(p=>p.trim());if(parts.length<2||parts.some(p=>!p)){say('Введите минимум два слога через |.',true);return;}const u=unit(),links=document().note_links.filter(l=>l.unit_id===u.unit_id).sort((a,b)=>a.start-b.start),regions=[...links,...u.intervals.filter(i=>i.support==='unpitched')];splitDraft.parts=parts;splitDraft.regions=regions.length?regions.map((r,i)=>({...r,part_index:parts.length===regions.length?i:null})):parts.map((_,i)=>({part_index:i,unplaced:true}));renderSplit();}
    function renderSplit(){
      const panel=q('#score-panel');if(!splitDraft.parts){beginSplit();return;}const options=choice=>'<option value="">Выберите слог</option>'+splitDraft.parts.map((text,index)=>`<option value="${index}" ${choice===index?'selected':''}>${index+1}. ${esc(text)}</option>`).join('');
      panel.innerHTML=`<form id="score-split-mapping"><strong>Предпросмотр: ${splitDraft.parts.map(esc).join(' · ')}</strong><p>Каждая строка — область одного слога. Повторите слог для распева; добавьте строки с разными границами для одной ноты. Для отсутствующего времени выберите «Без времени».</p><div class="score-map-table"><div class="score-map-head"><span>Слог</span><span>Нота / размещение</span><span>Начало, с</span><span>Конец, с</span><span></span></div>${splitDraft.regions.map((r,i)=>`<div class="score-map-row" data-row="${i}"><select class="score-map-part" aria-label="Слог области ${i+1}" required>${options(r.part_index)}</select><select class="score-map-note" aria-label="Размещение области ${i+1}"><option value="unplaced" ${r.unplaced?'selected':''}>Без времени</option><option value="unpitched" ${!r.unplaced&&!r.source_note_id?'selected':''}>Без найденной ноты</option>${notes.map(n=>`<option value="${esc(n.id)}" ${r.source_note_id===n.id?'selected':''}>${esc(noteName(n))}</option>`).join('')}</select><input class="score-map-start" type="number" step="0.001" value="${time(r.start)}" aria-label="Начало области ${i+1}"><input class="score-map-end" type="number" step="0.001" value="${time(r.end)}" aria-label="Конец области ${i+1}">${button('remove-map','Убрать',`data-row="${i}"`)}</div>`).join('')}</div><div class="score-unit-actions">${button('add-map','Добавить область')}<button type="submit">Применить распределение</button>${button('cancel-split','Отмена')}</div></form>`;
      positionPanel();panel.querySelectorAll('.score-map-note').forEach(select=>select.addEventListener('change',event=>{const n=notes.find(n=>n.id===event.target.value),row=event.target.closest('.score-map-row');if(n){row.querySelector('.score-map-start').value=time(n.start);row.querySelector('.score-map-end').value=time(n.end);}for(const input of row.querySelectorAll('input'))input.disabled=event.target.value==='unplaced';}));
    }
    function readMapping(){return [...q('#score-panel').querySelectorAll('.score-map-row')].map(row=>{const part=row.querySelector('.score-map-part').value,note=row.querySelector('.score-map-note').value;return {part_index:part===''?null:Number(part),unplaced:note==='unplaced',source_note_id:['unplaced','unpitched'].includes(note)?null:note,start:Number(row.querySelector('.score-map-start').value),end:Number(row.querySelector('.score-map-end').value)};});}
    function submit(form){
      const u=unit();switch(form.id){
        case 'score-text-form':applyText();break;
        case 'score-link-time-form':operate(()=>M.setLinkTime(document(),selected.link_id,Number(q('#score-start').value),Number(q('#score-end').value),notes));break;
        case 'score-reassign-form':operate(()=>M.reassign(document(),selected.link_id,{text:q('#score-reassign-text').value},notes));break;
        case 'score-unpitched-form':operate(()=>{const text=q('#score-unpitched-times').value.trim(),intervals=text?text.split('\n').map(line=>{const values=line.trim().split(/\s+/).map(Number);if(values.length!==2||values.some(v=>!Number.isFinite(v)))throw Error('Каждый интервал: начало и конец в секундах, разделённые пробелом.');return {start:values[0],end:values[1]};}):[];return M.setUnpitched(document(),u.unit_id,intervals);});break;
        case 'score-attach-form':operate(()=>M.linkTo(document(),u.unit_id,{source_note_id:q('#score-attach-note').value,start:Number(q('#score-attach-start').value),end:Number(q('#score-attach-end').value)},notes));break;
        case 'score-split-parts':previewSplit();break;
        case 'score-region-split-form':operate(()=>M.splitRegion(document(),selected.link_id,Number(q('#score-boundary').value),q('#score-second-text').value,notes));break;
        case 'score-split-mapping':operate(()=>M.split(document(),splitDraft.unit_id,splitDraft.parts,readMapping(),notes));break;
      }
    }
    function undo(){if(!session.history)return;session.history.undo();selected=null;splitDraft=null;invalidate();persist();render();host.draw();}
    function redo(){if(!session.history)return;session.history.redo();selected=null;splitDraft=null;invalidate();persist();render();host.draw();}
    async function dispatch(action,event,node){
      switch(action){
        case 'prepare':if(loadError)load(session.jobId,notes);else prepare();break;
        case 'edit':startEdit();break;case 'current':selectCurrent();break;case 'save':save();break;
        case 'done':if((dirty()||unfinished())&&(!await save()||dirty()||unfinished()))break;editing=false;selected=null;root.document.body.classList.remove('score-editing');render();host.draw();break;
        case 'undo':undo();break;case 'redo':redo();break;case 'export':exportDocument();break;
        case 'restore':if(backup){if(backup.document.base_analysis_key!==document()?.base_analysis_key){say('Резервный ввод относится к другой нотной базе. Экспортируйте его: автоматический перенос запрещён.',true);break;}session.history.commit(backup.document);session.pendingSave=backup.pending_save||null;const input=backup.unfinished;backup=null;q('#score-restore').hidden=true;invalidate();startEdit();if(input){select(input.selected,false);q('#score-text').value=input.text;for(const [name,value] of [['start',input.start],['end',input.end],['occurrence',input.occurrence]])if(value!==undefined&&q(`#score-${name}`))q(`#score-${name}`).value=value;}persist();renderButtons();host.draw();}break;
        case 'close':selected=null;splitDraft=null;renderPanel();break;case 'next':if(applyText())nextRegion();break;
        case 'new-unplaced':select({newUnplaced:true});break;case 'unresolved':select({unit_id:node.dataset.unit});break;
        case 'select-link':{const l=document().note_links.find(l=>l.link_id===node.dataset.link);if(l)select({unit_id:l.unit_id,link_id:l.link_id});break;}
        case 'pick-region':{const hit=hits[Number(node.dataset.index)];if(hit)select(hit.selection);break;}
        case 'delete-link':operate(()=>M.removeLink(document(),selected.link_id));break;
        case 'split':beginSplit();break;case 'cancel-split':splitDraft=null;renderPanel();break;
        case 'add-map':splitDraft.regions=readMapping();splitDraft.regions.push({part_index:null,unplaced:true});renderSplit();break;
        case 'remove-map':splitDraft.regions=readMapping().filter((_,i)=>i!==Number(node.dataset.row));renderSplit();break;
        case 'merge':{const u=unit(),siblings=document().units.filter(v=>v.occurrence_id===u.occurrence_id).sort((a,b)=>a.order-b.order),next=siblings[siblings.indexOf(u)+1];if(!next)break;const links=document().note_links.filter(l=>[u.unit_id,next.unit_id].includes(l.unit_id));q('#score-panel').innerHTML=`<strong>Предпросмотр объединения: ${esc(u.text+next.text)}</strong><p>${links.length} связей останутся у одного слога. Паузы между областями сохраняются.</p><div class="score-link-list">${links.map(l=>`<span>${time(l.start)}–${time(l.end)} · ${esc(noteName(notes.find(n=>n.id===l.source_note_id)))}</span>`).join('')}</div>${button('apply-merge','Применить объединение',`data-next="${esc(next.unit_id)}"`)}${button('cancel-split','Отмена')}`;break;}
        case 'apply-merge':operate(()=>M.merge(document(),[unit().unit_id,node.dataset.next]));break;
        case 'split-region':{const l=document().note_links.find(l=>l.link_id===selected.link_id);q('#score-panel').innerHTML=`<form id="score-region-split-form"><strong>Разделить текстовую область ${time(l.start)}–${time(l.end)} с</strong><p>Левая часть останется «${esc(unit().text)}», правая получит новый слог. Обе части сохранят ту же ноту.</p><div class="score-times"><label>Граница, с<input id="score-boundary" type="number" step="0.001" required min="${l.start}" max="${l.end}"></label><label>Правый слог<input id="score-second-text" required autocomplete="off"></label><button type="submit">Применить границу и слог</button>${button('cancel-split','Отмена')}</div></form>`;break;}
      }
    }
    function indexLabels(){if(labelsIndex===document())return;labelsIndex=document();byNote=new Map();if(!active())return;const units=new Map(document().units.map(u=>[u.unit_id,u])),occurrences=new Map(document().occurrences.map(o=>[o.occurrence_id,o]));for(const link of document().note_links){const u=units.get(link.unit_id),o=occurrences.get(u.occurrence_id),list=byNote.get(link.source_note_id)||[];list.push({...link,part_id:u.unit_id,word_id:o.source_word_id||o.occurrence_id,text:u.text,fallback:u.kind==='word_fallback',approximate:u.segmentation_status!=='suggested'||u.timing_status!=='suggested',reason:reasonText(u),origin:u.origin,unit_id:u.unit_id});byNote.set(link.source_note_id,list);}for(const list of byNote.values())list.sort((a,b)=>a.start-b.start);}
    function labels(note){indexLabels();return byNote?.get(note.id)||[];}
    function regionsFor(note){const links=labels(note),cuts=[...new Set([note.start,note.end,...links.flatMap(l=>[l.start,l.end])])].sort((a,b)=>a-b);return cuts.slice(0,-1).map((start,i)=>{const end=cuts[i+1],l=links.find(l=>l.start<=start&&l.end>=end);return {start,end,selection:l?{unit_id:l.unit_id,link_id:l.link_id}:{source_note_id:note.id,start,end}};});}
    function hit(candidates,labelCandidates,x,y){
      if(!editing||!active())return false;
      const direct=labelCandidates.filter(l=>x>=l.x&&x<=l.x+l.measured+8&&y>=l.y-14&&y<=l.y+5).map(l=>{const link=document().note_links.find(link=>link.link_id===l.linkId)||document().note_links.find(link=>link.source_note_id===l.noteId&&link.unit_id===l.unitId);return link?{selection:{unit_id:link.unit_id,link_id:link.link_id},start:link.start,end:link.end,note:notes.find(n=>n.id===link.source_note_id)}:null;}).filter(Boolean);
      hits=direct.length?direct:candidates.filter(r=>x>=r.x-5&&x<=r.endX+5&&y>=r.y-5&&y<=r.endY+5).map(r=>{const l=r.labels?.[0];return {...r,selection:l?.unit_id?{unit_id:l.unit_id,link_id:l.link_id}:{source_note_id:r.note.id,start:r.start,end:r.end}};});
      if(!hits.length)return false;if(hits.length===1){select(hits[0].selection);return true;}q('#score-panel').hidden=false;q('#score-panel').innerHTML='<strong>Выберите короткую область у курсора</strong><div class="score-hit-list">'+hits.map((h,index)=>button('pick-region',`${esc(noteName(h.note))} · ${time(h.start)}–${time(h.end)}`,`data-index="${index}"`)).join('')+'</div>';return true;
    }
    function renderPosition(position){
      if(!active())return;const u=M.activeAt(document(),position),o=document().occurrences.find(o=>o.occurrence_id===u?.occurrence_id),key=`${u?.unit_id}:${o?.occurrence_id}`;positionKey=key;
      q('#inspector-syllable').textContent=u?u.text:'Пауза';q('#inspector-syllable').title=u?`${u.kind==='word_fallback'?'Разбивка неизвестна':u.segmentation_status==='suggested'?'Предполагаемый слог':'Приблизительный слог'} · ${u.origin==='manual'?'ручная правка':'автоматическое предложение'}`:'Нет активного слога';
      q('#inspector-word').textContent=o?.source_text||u?.text||'Нет слова';
    }
    function reset(){persist();clearTimeout(poll);serial++;session.epoch++;session.history=null;session.state=null;session.sourceNotes=null;session.pendingSave=null;notes=[];selected=null;editing=false;labelsIndex=null;byNote=null;root.document.body.classList.remove('score-editing');if(q('#score-panel'))q('#score-panel').hidden=true;}
    return {init,load,reset,active,legacy,document,labels,hit,renderPosition,canLeave:()=>{persist();return true;},refresh:invalidate,editing:()=>editing,activeUnit:position=>active()?M.activeAt(document(),position):null,startEdit,selectCurrent,positionPanel,selection:()=>selected,session};
  }
  return {createSession,createEditor};
});
