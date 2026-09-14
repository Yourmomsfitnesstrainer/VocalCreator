/* Manual annotations are a revisioned overlay, not a replacement analysis. */
(function() {
  'use strict';
  const M=window.ManualLyricsModel;
  const q=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const button=(action,text,extra='')=>`<button type="button" data-manual="${action}" ${extra}>${text}</button>`;
  const palette=['#b3a6ed','#80c4b0','#d3b47e','#89bcdf','#e3a3b7','#b4c78d'];
  let host,history=null,jobId=null,loading=0,editing=false,activated=false,selection=new Set(),hidden=new Set(),clipboard=null,insertion=null,saving=false,drag=null,proposal=null,positionKey='',layoutKey='',commonLayout=null,refs=[],commonOffset=0;
  let captionLayout=null,captionRows=0,captionRange=null;
  const project=()=>history?.project, dirty=()=>Boolean(history?.dirty), active=()=>Boolean(activated&&history), roleId=id=>id||'__unassigned__';
  const roles=()=>[{id:null,name:'Без роли',color:'#aeb5c4'},...(project()?.roles||[])];
  const role=id=>roles().find(r=>r.id===id)||roles()[0];
  const visible=()=>project()?.annotations.filter(a=>editing||!hidden.has(roleId(a.role_id)))||[];
  const cursor=()=>insertion??host.position();
  function say(message,error=false) {const node=q('#manual-status');node.textContent=message;node.hidden=!message;node.classList.toggle('error',error);}
  function invalidate(){layoutKey='';positionKey='';host.invalidate();}
  function changed(next,ids=null){history.commit(next);if(ids)selection=new Set(ids);activated=true;invalidate();render();host.draw();}
  function operate(fn){try{const result=fn();if(result?.project)changed(result.project,result.ids);else if(result)changed(result);say('');}catch(error){say(error.message,true);}}
  function options(selected=null){return roles().map(r=>`<option value="${esc(r.id||'')}" ${r.id===selected?'selected':''}>${esc(r.name)}</option>`).join('');}
  async function request(url,options={}) {
    const response=await fetch(url,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...options.headers}}),data=await response.json().catch(()=>({}));
    if(!response.ok){const detail=data.detail;const error=Error(typeof detail==='string'?detail:detail?.message||detail?.error||`Ошибка ${response.status}`);error.status=response.status;error.detail=detail;throw error;}return data;
  }
  const endpoint=tail=>`/api/studio/jobs/${jobId}/manual${tail||''}`;
  function exportJSON(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function init(api) {
    host=api;
    const section=document.createElement('section');section.id='manual-workspace';section.className='manual-workspace';section.hidden=true;
    section.innerHTML=`
      <div class="manual-bar"><div class="manual-title"><strong>Разметка слов</strong><span id="manual-save-state" role="status">Загрузка…</span></div><div class="manual-actions">
        ${button('edit','Редактировать','id="manual-edit"')}${button('undo','Отменить','id="manual-undo" hidden')}${button('redo','Вернуть','id="manual-redo" hidden')}
        ${button('save','Сохранить','id="manual-save" hidden')}${button('done','Готово','id="manual-done" class="manual-primary" hidden')}
        ${button('references','Эталоны','id="manual-references"')}<a id="manual-cycles" href="/static/manual-cycles.html" target="_blank" rel="noopener">Циклы улучшения</a>
      </div></div>
      <p id="manual-status" class="manual-status" role="status" aria-live="polite" hidden></p>
      <div id="manual-conflict" class="manual-recovery" hidden>${button('export','Экспорт моих правок')}<a id="manual-current-link" target="_blank" rel="noopener">Открыть сохранённую версию отдельно</a></div>
      <div id="manual-role-filter" class="manual-role-filter"></div>
      <div id="manual-edit-area" hidden>
        <div class="manual-toolbar"><strong>На шкале</strong><span class="manual-hint">Клик — выбор · Shift — группа · рамка — несколько · сдвиг только по времени</span>
          ${button('roles','Настроить роли')}${button('auto','Предложить разметку','id="manual-auto"')}
        </div>
        <div class="manual-tools">${button('copy','Копировать')}${button('paste','Вставить в курсор')}${button('delete','Удалить')}${button('add','Добавить слово')}
          <label>Курсор вставки, с <input id="manual-cursor" inputmode="decimal" aria-label="Курсор вставки, секунды"></label>${button('cursor-reset','За проигрывателем')}
          <output id="manual-selection-count"></output>
        </div>
        <div id="manual-ruler-window" class="manual-ruler-window" aria-label="Линейка времени. Клик устанавливает курсор вставки."><div id="manual-ruler" class="manual-ruler"></div></div>
        <div id="manual-lanes-scroll" class="manual-lanes-scroll" tabindex="0" aria-label="Редактор времени и ролей. Стрелки сдвигают выбранную группу; Delete удаляет."><div id="manual-lanes" class="manual-lanes"></div></div>
        <section id="manual-inspector" class="manual-inspector" aria-label="Свойства выделенных плашек"></section>
        <details class="manual-details"><summary>Все плашки и наложения <span id="manual-list-count"></span></summary><div class="manual-list-tools"><label>Найти текст <input id="manual-search" type="search"></label>${button('select-all','Выделить все найденные')}</div><div id="manual-annotation-list" class="manual-annotation-list"></div></details>
      </div>
      <div id="manual-current" class="manual-current" aria-label="Текущие исполнения" hidden></div>
      <dialog id="manual-dialog" class="manual-dialog"><div class="dialog-heading"><h2 id="manual-dialog-title"></h2>${button('close-dialog','Закрыть')}</div><div id="manual-dialog-content"></div><p id="manual-dialog-error" class="manual-status error" role="alert" hidden></p></dialog>`;
    q('.timeline-header').after(section);
    section.addEventListener('click',event=>{const action=event.target.closest('[data-manual]')?.dataset.manual;if(action)dispatch(action,event);});
    section.addEventListener('submit',event=>{event.preventDefault();submit(event.target);});
    const confirmCursor=event=>{const value=parseTime(event.target.value);if(value===null||value<0||value>project().duration){say('Курсор должен находиться внутри записи.',true);return;}insertion=value;renderLanes();revealPosition(value);host.draw();};
    q('#manual-cursor').addEventListener('change',confirmCursor);
    q('#manual-cursor').addEventListener('focusout',confirmCursor);
    q('#manual-cursor').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();confirmCursor(event);}});
    q('#manual-search').addEventListener('input',renderList);
    q('#manual-lanes-scroll').addEventListener('scroll',()=>{syncRuler();if(drag)return;host.scroll(q('#manual-lanes-scroll').scrollLeft);host.draw();});
    q('#manual-ruler-window').addEventListener('click',event=>{const x=event.clientX-event.currentTarget.getBoundingClientRect().left;if(x<74)return;insertion=Math.max(0,Math.min(project().duration,(x+q('#manual-lanes-scroll').scrollLeft-74)/host.pps()));renderLanes();host.draw();});
    q('#manual-lanes').addEventListener('pointerdown',beginDrag);
    q('#manual-lanes').addEventListener('pointermove',moveDrag);
    q('#manual-lanes').addEventListener('pointerup',endDrag);
    q('#manual-lanes').addEventListener('pointercancel',cancelDrag);
    q('#manual-workspace').addEventListener('keydown',keys);
    q('#manual-inspector').addEventListener('change',fieldChange);
    q('#manual-inspector').addEventListener('focusout',fieldChange);
    q('#manual-inspector').addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.dataset.field){event.preventDefault();fieldChange(event);}});
    window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
    window.addEventListener('resize',()=>{if(history){renderLanes();invalidate();renderFilters();host.draw();}});
  }
  function parseTime(value){const normalized=value.trim().replace(',','.');return normalized&&/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)&&Number.isFinite(Number(normalized))?Number(normalized):null;}
  async function load(id) {
    const generation=++loading;jobId=id;history=null;editing=false;activated=false;selection.clear();clipboard=null;insertion=null;hidden=new Set();proposal=null;commonOffset=0;document.body.classList.remove('manual-editing');q('#manual-workspace').hidden=false;renderLoading();
    try {const data=await request(endpoint());if(generation!==loading)return;history=new M.History(data);activated=data.revision>0||data.source_analysis?.kind==='manual-auto';
      try{hidden=new Set(JSON.parse(localStorage.getItem(`vocalcreator:roles:${id}`)||'[]'));}catch(_){}
      q('#manual-current-link').href=`/?job=${encodeURIComponent(id)}`;q('#manual-cycles').href=`/static/manual-cycles.html?job=${encodeURIComponent(id)}`;q('#manual-conflict').hidden=true;invalidate();render();host.draw();
    }catch(error){if(generation!==loading)return;say(`Не удалось открыть слой разметки: ${error.message}`,true);q('#manual-save-state').textContent='Слой недоступен';if(error.detail?.saved_project){history=new M.History(error.detail.saved_project);q('#manual-conflict').hidden=false;}q('#manual-edit').disabled=true;}
  }
  function renderLoading(){q('#manual-save-state').textContent='Загрузка разметки…';q('#manual-edit').disabled=true;q('#manual-edit-area').hidden=true;q('#manual-role-filter').replaceChildren();q('#manual-current').hidden=true;}
  function startEdit(){if(!history)return;editing=true;activated=true;host.karaoke(false);document.body.classList.add('manual-editing');invalidate();render();q('#manual-lanes-scroll').focus();q('#manual-workspace').scrollIntoView({block:'start'});host.draw();revealPosition(host.position());host.draw();}
  async function save(done=false) {
    if(!history||saving)return false;
    saving=true;renderButtons();const snapshot=M.clone(project()),localVersion=JSON.stringify(project());
    try{const saved=await request(endpoint(),{method:'PUT',body:JSON.stringify(snapshot)});
      // A save can finish while a later edit is already in the browser.
      if(JSON.stringify(project())===localVersion)history.savedAs(saved);
      else {history.project.revision=saved.revision;history.saved=JSON.stringify(saved);for(const item of [...history.past,...history.future])item.revision=saved.revision;}
      say('Разметка сохранена. Это черновик, а не подтверждённый эталон.');q('#manual-conflict').hidden=true;
      if(done&&!dirty()){editing=false;document.body.classList.remove('manual-editing');invalidate();}
      return true;
    }catch(error){say(error.status===409?'В другой вкладке уже сохранена новая ревизия. Ваши правки остались здесь: экспортируйте их и откройте актуальную версию отдельно.':`Запись не удалась: ${error.message}. Правки остались в редакторе.`,true);q('#manual-conflict').hidden=false;return false;
    }finally{saving=false;render();host.draw();}
  }
  function renderButtons(){
    if(!history)return;
    q('#manual-edit').hidden=editing;q('#manual-edit').disabled=false;
    for(const name of ['undo','redo','save','done'])q(`#manual-${name}`).hidden=!editing;
    q('#manual-save').disabled=saving;q('#manual-done').disabled=saving;q('#manual-save').textContent=saving?'Сохраняю…':'Сохранить';
    q('#manual-undo').disabled=!history.past.length||saving;q('#manual-redo').disabled=!history.future.length||saving;
    q('#manual-save-state').textContent=dirty()?'Есть несохранённые правки':project().revision?`Сохранено · ревизия ${project().revision}`:project().source_analysis?.kind==='manual-auto'?'Автоматическое предложение · без правок':'Исходный разбор · без правок';
    q('#manual-edit-area').hidden=!editing;q('#manual-selection-count').textContent=`Выделено: ${selection.size}`;
    q('#manual-current').hidden=!active();
  }
  function render(){if(!history)return;renderButtons();renderFilters();if(editing){renderLanes();renderInspector();renderList();}renderPosition(host.position());}
  function renderFilters(){
    const root=q('#manual-role-filter');root.hidden=!active()||editing;if(root.hidden)return;const rows=commonWindow();
    root.innerHTML=roles().map(r=>`<div class="manual-filter-role" style="--role:${esc(r.color)}"><button type="button" data-manual="toggle-role" data-role="${esc(r.id||'')}" aria-pressed="${!hidden.has(roleId(r.id))}"><i></i>${esc(r.name)}</button><button type="button" data-manual="solo-role" data-role="${esc(r.id||'')}" aria-label="Только ${esc(r.name)}">Только эта</button></div>`).join('')+button('all-roles','Показать все')+`<span>${hidden.size?'Часть слов скрыта · звук и ноты без изменений':'Все роли видны'}</span><div class="manual-caption-tools">${button('common-up','Выше',`aria-label="Предыдущие строки плашек" ${rows.first===0?'disabled':''}`)}${button('common-down','Ниже',`aria-label="Следующие строки плашек" ${rows.end>=rows.total?'disabled':''}`)}<span>Строки наложений ${rows.first+1}–${rows.end} / ${rows.total}</span>${button('common-choose','Плашки у курсора')}</div>`;
  }
  function persistFilter(){try{localStorage.setItem(`vocalcreator:roles:${jobId}`,JSON.stringify([...hidden]));}catch(_){}invalidate();renderFilters();host.draw();}
  function renderLanes(){
    if(!editing||!history||drag)return;const root=q('#manual-lanes'),pps=host.pps(),layout=M.layout(project().annotations,{pps,grouped:true,roles:project().roles}),width=Math.max(q('#manual-lanes-scroll').clientWidth,project().duration*pps+74);
    root.style.width=`${width}px`;root.style.height=`${layout.height}px`;
    const ticks=[];for(let t=0;t<=project().duration;t+=pps>80?1:5)ticks.push(`<span style="left:${74+t*pps}px">${esc(host.format(t,false))}</span>`);
    const ruler=q('#manual-ruler');ruler.style.width=`${width}px`;ruler.innerHTML=ticks.join('')+`<i class="manual-insertion" style="left:${74+cursor()*pps}px"></i><i class="manual-playing" style="left:${74+host.position()*pps}px"></i>`;
    root.innerHTML=layout.rows.map(row=>`<div class="manual-lane-row" style="top:${row.top}px;height:${row.height}px"><span class="manual-lane-name" style="--role:${esc(role(row.role_id).color)}">${esc(role(row.role_id).name)}</span></div>`).join('')+layout.items.map(item=>{
      const a=item.annotation,r=role(a.role_id),selected=selection.has(a.annotation_id);return `<button type="button" class="manual-block ${selected?'selected':''}" data-annotation="${esc(a.annotation_id)}" aria-pressed="${selected}" style="--role:${esc(r.color)};left:${74+item.x}px;top:${item.y}px;width:${item.width}px" title="${esc(`${a.text} · ${r.name} · ${a.start.toFixed(3)}–${a.end.toFixed(3)} с${a.approximate?' · приблизительно':''}`)}"><span class="manual-handle start" data-edge="start"></span><span class="manual-block-label" style="min-width:${item.labelWidth}px">${a.approximate?'≈ ':''}${esc(a.text)}</span><span class="manual-handle end" data-edge="end"></span></button>`;
    }).join('')+`<div class="manual-insertion" style="left:${74+cursor()*pps}px"></div><div class="manual-playing" style="left:${74+host.position()*pps}px"></div><div id="manual-marquee" hidden></div>`;
    if(document.activeElement!==q('#manual-cursor'))q('#manual-cursor').value=cursor().toFixed(3);syncRuler();
  }
  function renderInspector(){
    const selected=project().annotations.filter(a=>selection.has(a.annotation_id)),root=q('#manual-inspector');
    if(!selected.length){root.innerHTML='<p>Выберите плашку на шкале или в списке. Перекрытия остаются отдельными исполнениями.</p>';return;}
    const one=selected.length===1,a=selected[0],sameRole=selected.every(item=>item.role_id===a.role_id),select=`<label>Роль<select id="manual-field-role" data-field="role_id" aria-label="Роль выделения">${sameRole?'':'<option value="__mixed__">Разные роли</option>'}${options(sameRole?a.role_id:null)}</select></label>`;
    root.innerHTML=one?`<label class="manual-text-field">Текст<input id="manual-field-text" data-field="text" value="${esc(a.text)}" aria-label="Текст плашки"></label>${select}<label>Начало, с<input id="manual-field-start" data-field="start" inputmode="decimal" value="${M.placed(a)?a.start.toFixed(3):''}" aria-label="Начало плашки, секунды"></label><label>Конец, с<input id="manual-field-end" data-field="end" inputmode="decimal" value="${M.placed(a)?a.end.toFixed(3):''}" aria-label="Конец плашки, секунды"></label><label>Длительность, с<input id="manual-field-duration" data-field="duration" inputmode="decimal" value="${M.placed(a)?(a.end-a.start).toFixed(3):''}" aria-label="Длительность плашки, секунды"></label>${button('apply-fields','Применить поля')}${button('listen','Слушать отсюда')}<p>${esc(a.unit==='part'?'Часть слова':'Слово')} · ${esc(a.provenance?.kind||'исходный разбор')}${a.approximate?' · ≈ приблизительное время':''} · секунды исходной записи${M.placed(a)?'':' · задайте начало и конец через «Разместить»'}</p>${!M.placed(a)?button('place','Разместить'):''}`:
      `<strong>${selected.length} плашек</strong>${select}<label>Сдвиг группы, с<input id="manual-group-delta" inputmode="decimal" value="0"></label>${button('move','Сдвинуть группу')}<p>Сохраняются длительности и расстояния. Группа ограничивается краем записи целиком.</p>`;
    if(!sameRole)q('#manual-field-role').value='__mixed__';
  }
  function fieldChange(event){const field=event.target.dataset.field;if(!field)return;let value=event.target.value;if(field==='role_id'){if(value==='__mixed__')return;value=value||null;}
    const selected=project().annotations.filter(a=>selection.has(a.annotation_id));
    if(selected.length===1){const previous=field==='duration'?selected[0].end-selected[0].start:selected[0][field];if((field==='role_id'?value:(['start','end','duration'].includes(field)?parseTime(value):value))===previous)return;}
    if(['start','end','duration'].includes(field)){value=parseTime(value);if(value===null){event.target.setAttribute('aria-invalid','true');say('Введите время в секундах. Прежнее корректное значение сохранено.',true);return;}}
    try{const next=M.edit(project(),[...selection],field,value);changed(next);say('');}catch(error){event.target.setAttribute('aria-invalid','true');say(error.message,true);}
  }
  function matched(){const term=(q('#manual-search')?.value||'').toLocaleLowerCase();return project().annotations.filter(a=>!term||a.text.toLocaleLowerCase().includes(term));}
  function renderList(){if(!history)return;const items=matched();q('#manual-list-count').textContent=`(${project().annotations.length})`;q('#manual-annotation-list').innerHTML=items.map(a=>`<button type="button" data-manual="select-list" data-id="${esc(a.annotation_id)}" class="${selection.has(a.annotation_id)?'selected':''}" aria-pressed="${selection.has(a.annotation_id)}"><i style="background:${esc(role(a.role_id).color)}"></i><strong>${esc(a.text)}</strong><span>${esc(role(a.role_id).name)}</span><time>${M.placed(a)?`${a.start.toFixed(3)}–${a.end.toFixed(3)}`:'Без времени'}</time><small>${a.unit==='part'?'часть':''}${a.approximate?' ≈':''}</small></button>`).join('')||'<p>Нет плашек. Добавьте слово из текста или своё.</p>';}
  function select(id,toggle=false){if(toggle){if(selection.has(id))selection.delete(id);else selection.add(id);}else selection=new Set([id]);render();host.draw();}
  function keys(event){
    if(!editing||event.target.closest('input,textarea,select,[contenteditable=true]'))return;
    if(!event.target.closest('#manual-lanes-scroll,#manual-annotation-list'))return;
    if(event.target.closest('#manual-dialog'))return;
    const command=event.metaKey||event.ctrlKey,key=event.key.toLowerCase();let action=null;
    if(command&&key==='c')action='copy';if(command&&key==='v')action='paste';if(command&&key==='z')action=event.shiftKey?'redo':'undo';if(command&&key==='y')action='redo';if(command&&key==='a')action='select-all';
    if(event.key==='Delete'||event.key==='Backspace')action='delete';
    if(action){event.preventDefault();event.stopPropagation();dispatch(action,event);return;}
    if(['ArrowLeft','ArrowRight'].includes(event.key)&&selection.size){event.preventDefault();event.stopPropagation();operate(()=>M.move(project(),[...selection],(event.key==='ArrowLeft'?-1:1)*(event.shiftKey?.1:.01)));}
    if(event.code==='Space'){event.preventDefault();event.stopPropagation();host.togglePlay();}
  }
  function beginDrag(event){
    if(event.button!==0||!editing)return;const root=q('#manual-lanes'),rect=root.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
    if(x<74)return;
    const item=event.target.closest('[data-annotation]'),toggle=event.shiftKey||event.metaKey||event.ctrlKey;
    if(item){const id=item.dataset.annotation;if(toggle){select(id,true);return;}if(!selection.has(id))selection=new Set([id]);const edge=event.target.dataset.edge;if(edge&&selection.size>1){say('Изменение края доступно у одной плашки. Снимите групповое выделение.',true);return;}drag={type:edge||'move',startX:event.clientX,startY:event.clientY,before:M.clone(project()),ids:[...selection],id,moved:false};}
    else {insertion=Math.max(0,Math.min(project().duration,(x-74)/host.pps()));drag={type:'marquee',startX:event.clientX,startY:event.clientY,x,y,beforeSelection:toggle?[...selection]:[],moved:false};if(!toggle)selection.clear();}
    root.setPointerCapture(event.pointerId);event.preventDefault();q('#manual-lanes-scroll').focus();renderInspector();renderButtons();
  }
  function moveDrag(event){
    if(!drag)return;const dx=event.clientX-drag.startX,dy=event.clientY-drag.startY;drag.moved=drag.moved||Math.abs(dx)>3||(drag.type==='marquee'&&Math.abs(dy)>3);
    if(drag.type==='marquee'){
      const left=Math.min(drag.x,drag.x+dx),top=Math.min(drag.y,drag.y+dy),right=Math.max(drag.x,drag.x+dx),bottom=Math.max(drag.y,drag.y+dy),box=q('#manual-marquee');box.hidden=false;Object.assign(box.style,{left:`${left}px`,top:`${top}px`,width:`${right-left}px`,height:`${bottom-top}px`});
      const ids=M.layout(project().annotations,{pps:host.pps(),grouped:true,roles:project().roles}).items.filter(i=>i.x+74<right&&i.x+74+i.width>left&&i.y<bottom&&i.y+25>top).map(i=>i.annotation.annotation_id);selection=new Set([...drag.beforeSelection,...ids]);
    }else {
      try {const delta=dx/host.pps();drag.preview=drag.type==='move'?M.move(drag.before,drag.ids,delta):M.edit(drag.before,[drag.id],drag.type,drag.before.annotations.find(a=>a.annotation_id===drag.id)[drag.type]+delta);
        for(const a of drag.preview.annotations.filter(a=>drag.ids.includes(a.annotation_id))){const node=q(`[data-annotation="${a.annotation_id}"]`);node.style.left=`${74+a.start*host.pps()}px`;node.style.width=`${Math.max(18,(a.end-a.start)*host.pps())}px`;}
      }catch(_){} // Illegal edge previews retain the last complete valid interval.
    }
    for(const node of q('#manual-lanes').querySelectorAll('[data-annotation]'))node.classList.toggle('selected',selection.has(node.dataset.annotation));
  }
  function endDrag(){if(!drag)return;const finished=drag;drag=null;if(finished.preview&&finished.moved){changed(finished.preview);say('');}else{render();host.draw();}}
  function cancelDrag(){drag=null;render();host.draw();}
  function graphLayout(){const pps=host.pps(),key=`${pps}|${project()?.revision}|${[...hidden]}|${editing}`;if(!commonLayout||layoutKey!==key){commonLayout=M.layout(visible(),{pps});layoutKey=key;}return commonLayout;}
  function commonRows(){return window.innerHeight<=650?2:3;}
  function commonWindow(){const layout=graphLayout(),rows=commonRows();if(layout!==captionLayout||rows!==captionRows||commonOffset!==captionRange?.first){captionRange=M.captionWindow(layout,commonOffset,rows);captionLayout=layout;captionRows=rows;commonOffset=captionRange.first;}return captionRange;}
  function graphTop(){if(!active())return 110;const rows=commonWindow();return Math.max(110,52+(rows.end-rows.first)*34+16);}
  function draw(ctx,{width,scrollLeft,position}){
    if(!active())return;const rows=commonWindow(),pps=host.pps();
    ctx.save();ctx.beginPath();ctx.rect(74,48,width-74,graphTop()-48);ctx.clip();
    for(const item of rows.items){const a=item.annotation,x=74+item.x-scrollLeft,y=48+item.y-rows.first*34,r=role(a.role_id);if(x+item.labelWidth<74||x>width||y+25<48||y>=graphTop())continue;
      ctx.fillStyle=r.color;ctx.globalAlpha=.2;ctx.fillRect(x,y,item.width,25);ctx.globalAlpha=1;ctx.strokeStyle=r.color;ctx.strokeRect(x+.5,y+.5,item.width-1,24);
      if(a.start<=position&&position<a.end||selection.has(a.annotation_id)){ctx.lineWidth=2;ctx.strokeStyle='#f4f6fb';ctx.strokeRect(x-1,y-1,item.width+2,27);ctx.lineWidth=1;}
      const text=`${a.approximate?'≈ ':''}${a.text}`,labelX=Math.max(74,x)+4;
      host.drawText(ctx,text,labelX,y+17,'500 12px -apple-system,sans-serif',r.color);
    }
    if(insertion!==null){const x=74+insertion*pps-scrollLeft;ctx.strokeStyle='#e8c888';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x,48);ctx.lineTo(x,graphTop());ctx.stroke();ctx.setLineDash([]);}
    ctx.restore();
  }
  function hit(x,y,scrollLeft){if(!active())return false;const rows=commonWindow(),matches=rows.items.filter(i=>x>=Math.max(74,74+i.x-scrollLeft)&&x<=74+i.x-scrollLeft+i.labelWidth&&y>=48+i.y-rows.first*34&&y<=73+i.y-rows.first*34&&y<graphTop());if(!matches.length)return false;const a=matches[0].annotation;selection=new Set([a.annotation_id]);host.seek(a.start);renderPosition(a.start);if(editing)render();return true;}
  function renderPosition(position){
    if(!active())return;const {current,next}=M.currentAndNext(visible(),position,3*host.rate()),key=current.map(a=>a.annotation_id).join('|')+';'+next.map(a=>a.annotation_id).join('|')+';'+[...hidden]+';'+editing;
    if(key!==positionKey){positionKey=key;const renderWords=items=>items.map(a=>`<span class="manual-live-word" style="--role:${esc(role(a.role_id).color)}"><small>${esc(role(a.role_id).name)}</small><strong>${a.approximate?'≈ ':''}${esc(a.text)}</strong></span>`).join('');
      q('#manual-current').innerHTML=`<div><span>Сейчас</span>${current.length?renderWords(current):`<span class="manual-empty">${!visible().length?'Все роли скрыты или плашек нет':'Пауза в тексте'}</span>`}</div><div><span>Далее</span>${next.length?renderWords(next):'<span class="manual-empty">Нет следующих слов</span>'}</div>`;
    }
      // Every performance text field reads this same filtered overlay.
      q('#inspector-word').textContent=current.map(a=>`${role(a.role_id).name}: ${a.text}`).join(' · ')||(!visible().length?'Все роли скрыты':'Нет слова');q('#inspector-word').title=q('#inspector-word').textContent;
      q('#inspector-word-status').textContent=current.some(a=>a.approximate)?'≈':'';q('#inspector-word-status').title='Разметка слов · общая мелодия';
      q('#word-previous').textContent='';q('#word-next').textContent=next.map(a=>a.text).join(' · ');q('#word-after-next').textContent='';
      q('#inspector-word-detail').textContent=current.map(a=>`${role(a.role_id).name}: ${a.text} · ${a.start.toFixed(3)}–${a.end.toFixed(3)} с · ${a.annotation_id} · исполнение ${a.occurrence_id}`).join('\n')||'В этой позиции нет видимых слов.';q('#inspector-region-detail').textContent='Слова показаны из отдельного слоя разметки. Ноты и высота голоса относятся к общей мелодии.';
    for(const playing of document.querySelectorAll('.manual-playing'))playing.style.left=`${74+position*host.pps()}px`;
    if(insertion===null&&editing){for(const marker of document.querySelectorAll('.manual-insertion'))marker.style.left=`${74+position*host.pps()}px`;if(document.activeElement!==q('#manual-cursor'))q('#manual-cursor').value=position.toFixed(3);}
  }
  function syncRuler(){q('#manual-ruler').style.transform=`translateX(${-q('#manual-lanes-scroll').scrollLeft}px)`;}
  function revealPosition(time){const scroll=q('#manual-lanes-scroll');host.scroll(M.scrollForTime(time,host.pps(),scroll.clientWidth,project().duration));scroll.scrollLeft=host.scrollLeft();syncRuler();}
  function sync(){if(editing&&!drag){const scroll=q('#manual-lanes-scroll');if(Math.abs(scroll.scrollLeft-host.scrollLeft())>.5)scroll.scrollLeft=host.scrollLeft();syncRuler();}}
  function modal(title,body){q('#manual-dialog-title').textContent=title;q('#manual-dialog-content').innerHTML=body;q('#manual-dialog-error').hidden=true;if(!q('#manual-dialog').open)q('#manual-dialog').showModal();}
  function modalError(error){q('#manual-dialog-error').textContent=error.message||String(error);q('#manual-dialog-error').hidden=false;}
  function addDialog(placing=false){const a=placing?project().annotations.find(a=>selection.has(a.annotation_id)):null,start=a&&M.placed(a)?a.start:Math.min(cursor(),Math.max(0,project().duration-.5));
    const tokens=[...new Set((project().canonical_text||'').split(/\s+/).filter(Boolean))];
    modal(placing?'Разместить плашку':'Добавить слово',`<form data-form="${placing?'place':'add'}"><label>Текст<input name="text" list="manual-source-words" value="${esc(a?.text||'')}" required autofocus></label><datalist id="manual-source-words">${tokens.map(t=>`<option value="${esc(t)}"></option>`).join('')}</datalist><div class="manual-form-row"><label>Начало, с<input name="start" value="${start.toFixed(3)}" inputmode="decimal" required></label><label>Конец, с<input name="end" value="${Math.min(project().duration,start+.5).toFixed(3)}" inputmode="decimal" required></label><label>Роль<select name="role">${options(a?.role_id)}</select></label></div><p>Можно выбрать слово исходного TXT или написать свой текст. Время — от начала записи.</p><button class="manual-primary" type="submit">${placing?'Разместить':'Добавить'}</button></form>`);
  }
  function rolesDialog(){modal('Роли и цвета',`<p>Назначайте роль выбранным плашкам через список. Цвет обозначает текстовую разметку.</p><div class="manual-role-editor">${project().roles.map(r=>`<form data-form="role" data-id="${esc(r.id)}"><input name="color" type="color" value="${esc(r.color)}" aria-label="Цвет роли ${esc(r.name)}"><input name="name" value="${esc(r.name)}" required aria-label="Имя роли ${esc(r.name)}"><button type="submit">Применить</button>${button('delete-role','Удалить',`data-id="${esc(r.id)}"`)}</form>`).join('')}</div><form data-form="new-role" class="manual-form-row"><label>Новая роль<input name="name" required placeholder="Например, второй голос"></label><label>Цвет<input name="color" type="color" value="${palette[project().roles.length%palette.length]}"></label><button type="submit">Создать роль</button></form><p>Удаление роли переносит её плашки в «Без роли». Разметка сохраняется.</p>`);}
  async function referencesDialog(){
    if(!editing)startEdit();
    modal('Проверка и эталоны',`<p>Проверка охватывает все роли, включая скрытые при тренировке. Слушайте запись обычными кнопками проигрывателя; закройте это окно, чтобы просмотреть шкалу.</p><div class="manual-form-row">${button('review','Проверить фрагмент')}${button('review-all','Проверить всю песню')}${button('snapshot','Сохранить эталон')}${button('import-reference','Импорт эталона')}</div><p id="manual-review-summary"></p><div id="manual-review-list"></div><h3>Неизменяемые версии</h3><div id="manual-reference-list">Загружаю версии…</div><input id="manual-reference-file" type="file" accept=".json,application/json" hidden>`);
    q('#manual-review-summary').textContent=project().reviews.length?`${project().reviews.length} проверенных интервалов в рабочей версии. Правки снимают проверку затронутых участков.`:'Проверенных интервалов пока нет. Сохранение черновика не подтверждает правильность разметки.';
    q('#manual-review-list').innerHTML=project().reviews.map((r,index)=>`<div>${r.start.toFixed(3)}–${r.end.toFixed(3)} с · все роли · неопределённостей: ${r.uncertainties?.length||0} ${button('remove-review','Снять проверку',`data-index="${index}"`)}</div>`).join('');
    q('#manual-reference-file').addEventListener('change',importReference);
    try{const data=await request(endpoint('/references'));refs=data.references||[];if(!q('#manual-reference-list'))return;q('#manual-reference-list').innerHTML=refs.map(r=>`<div class="manual-reference-row"><span><strong>${esc(r.label||`Версия ${r.version}`)}</strong><small>${esc(r.language?.toUpperCase())} · ревизия ${r.project_revision} · ${esc(new Date(r.created_at).toLocaleString('ru-RU'))}</small></span>${button('view-reference','Открыть',`data-id="${esc(r.reference_id)}"`)}${button('export-reference','Экспорт',`data-id="${esc(r.reference_id)}"`)}</div>`).join('')||'<p>Версий пока нет. Сохраните эталон после прослушивания и проверки.</p>';}catch(error){modalError(error);}
  }
  function reviewDialog(all=false){const start=all?0:cursor(),end=all?project().duration:Math.min(project().duration,start+5);
    modal('Проверить все исполнения в интервале',`<form data-form="review"><div class="manual-form-row"><label>Начало, с<input name="start" inputmode="decimal" value="${start.toFixed(3)}" required></label><label>Конец, с<input name="end" inputmode="decimal" value="${end.toFixed(3)}" required></label></div><label class="manual-check"><input type="checkbox" name="listened" required> Я прослушал этот интервал и проверил все роли, включая тихий фон и отсутствие слов в паузах.</label><fieldset><legend>Сомнения (не отмечайте, если всё проверено)</legend><div class="manual-checks">${['presence:Наличие исполнения','text:Текст','start:Начало','end:Конец','role:Роль'].map(pair=>{const [key,label]=pair.split(':');return `<label><input type="checkbox" name="uncertain" value="${key}">${label}</label>`;}).join('')}</div></fieldset><label>Комментарий к сомнениям<textarea name="note" rows="2" placeholder="Что осталось неопределённым"></textarea></label><p>Отмеченные сомнения исключают соответствующие признаки из точной оценки. Конец слова за границей интервала не подтверждается.</p><button type="submit" class="manual-primary">Зафиксировать проверку</button></form>`);
  }
  function snapshotDialog(){modal('Сохранить версию эталона',`<form data-form="snapshot"><label>Название версии<input name="label" required placeholder="Например, проверенный первый припев"></label><label>Язык<select name="language"><option value="en" ${project().language==='en'?'selected':''}>English</option><option value="ru" ${project().language==='ru'?'selected':''}>Русский</option></select></label><label>Особенности и трудные места<textarea name="cases" rows="3" placeholder="По одному описанию на строку"></textarea></label><p>Сохранится неизменяемая копия разметки и охвата проверки. Модели и расчёты не запускаются.</p><button type="submit" class="manual-primary">Сохранить эталон</button></form>`);}
  async function importReference(event){const file=event.target.files[0];if(!file)return;try{const reference=JSON.parse(await file.text());if(dirty()){modalError(Error('Сначала сохраните или экспортируйте текущие правки. Импорт восстанавливает рабочую разметку из эталона.'));return;}const restored=await request(endpoint('/import'),{method:'POST',body:JSON.stringify({revision:project().revision,reference})});history=new M.History(restored);selection.clear();activated=true;invalidate();render();host.draw();say('Эталон импортирован в рабочую версию. Исходные данные проверены.');referencesDialog();}catch(error){modalError(Error(`Импорт не выполнен, ваши данные сохранены: ${error.message}`));}}
  async function submit(form){try{const data=new FormData(form),type=form.dataset.form;
    if(type==='add'||type==='place'){const start=parseTime(data.get('start')),end=parseTime(data.get('end'));if(start===null||end===null)throw Error('Введите начало и конец в секундах.');const values={text:data.get('text'),start,end,role_id:data.get('role')||null};
      if(type==='add'){const result=M.add(project(),values);changed(result.project,result.ids);}else{const next=M.clone(project()),a=next.annotations.find(a=>selection.has(a.annotation_id));Object.assign(a,values);M.valid(a,next.duration);changed(M.invalidate(project(),next));}q('#manual-dialog').close();
    }
    if(type==='role'){changed(M.role(project(),'update',{id:form.dataset.id,name:data.get('name'),color:data.get('color')}));rolesDialog();}
    if(type==='new-role'){changed(M.role(project(),'add',{name:data.get('name'),color:data.get('color')}));rolesDialog();}
    if(type==='review'){const start=parseTime(data.get('start')),end=parseTime(data.get('end'));if(start===null||end===null||start<0||end<=start||end>project().duration)throw Error('Укажите непустой интервал внутри записи.');if(!data.get('listened'))throw Error('Проверка требует прослушивания всех ролей.');const next=M.clone(project()),features=data.getAll('uncertain');next.reviews.push({review_id:M.id('review'),start,end,all_roles:true,uncertainties:features.length?[{annotation_id:null,features,start,end,note:data.get('note')||'Неопределённость отмечена пользователем'}]:[]});changed(next);referencesDialog();}
    if(type==='snapshot'){if(!project().reviews.length)throw Error('Сначала проверьте хотя бы один интервал. Можно проверить и паузу без слов.');if(dirty()||!project().revision){if(!await save())throw Error('Версия не создана: сначала сохраните рабочий проект.');}await request(endpoint('/references'),{method:'POST',body:JSON.stringify({revision:project().revision,label:data.get('label'),language:data.get('language'),difficult_cases:String(data.get('cases')||'').split('\n').filter(Boolean)})});say('Создана новая неизменяемая версия эталона.');referencesDialog();}
    }catch(error){modalError(error);}}
  async function automatic(){
    if(dirty()&&!await save())return;
    q('#manual-auto').disabled=true;say('Анализирую аудио для нового результата: времена, повторы и роли…');
    try{const result=await request(endpoint('/auto'),{method:'POST',body:'{}'});const automatic=result.automatic;
      await host.open(result.job_id);startEdit();
      modal('Автоматическое предложение',`<p>Открыт отдельный результат: ${automatic?.annotations?.length||0} плашек, ${automatic?.roles?.length||0} ролей. Предыдущий ручной проект и его эталоны сохранены в библиотеке.</p><p>Приблизительные границы и роли требуют прослушивания. Исправьте предложение и сохраните рабочую разметку.</p><pre class="manual-diagnostics">${esc(JSON.stringify(automatic?.diagnostics||{},null,2))}</pre>`);
      say('Новое предложение готово. Исходный ручной проект сохранён отдельно.');
    }catch(error){say(`Предложение не удалось: ${error.message}. Ручной редактор доступен.`,true);}finally{q('#manual-auto').disabled=false;}
  }
  async function dispatch(action,event){if(!history)return;const source=event.target.closest('[data-manual]');
    if(action==='edit')startEdit();
    if(action==='save'||action==='done')await save(action==='done');
    if(action==='undo'||action==='redo'){if(history[action]()){invalidate();render();host.draw();say('');}}
    if(action==='copy'){try{clipboard=M.copy(project(),[...selection]);say(`Скопировано ${clipboard.annotations.length}. Укажите курсор и вставьте группу.`);}catch(error){say(error.message,true);}}
    if(action==='paste')operate(()=>M.paste(project(),clipboard,cursor()));
    if(action==='delete')operate(()=>{const next=M.remove(project(),[...selection]);selection.clear();return next;});
    if(action==='cursor-reset'){insertion=null;renderLanes();revealPosition(host.position());host.draw();}
    if(action==='select-all'){selection=new Set(matched().map(a=>a.annotation_id));render();host.draw();}
    if(action==='select-list'){const a=project().annotations.find(a=>a.annotation_id===source.dataset.id);select(a.annotation_id,event.shiftKey||event.ctrlKey||event.metaKey);if(M.placed(a)){host.seek(a.start);host.scroll(Math.max(0,a.start*host.pps()-150));renderLanes();sync();host.draw();}}
    if(action==='move'){const value=Number(q('#manual-group-delta').value.replace(',','.'));if(!Number.isFinite(value))say('Введите сдвиг группы в секундах.',true);else operate(()=>M.move(project(),[...selection],value));}
    if(action==='apply-fields'){
      const a=project().annotations.find(a=>selection.has(a.annotation_id)),next=M.clone(project()),edited=next.annotations.find(item=>item.annotation_id===a.annotation_id);
      try{edited.text=q('#manual-field-text').value;edited.role_id=q('#manual-field-role').value||null;const start=parseTime(q('#manual-field-start').value),end=parseTime(q('#manual-field-end').value),duration=parseTime(q('#manual-field-duration').value);if(start===null||end===null||duration===null)throw Error('Введите корректные времена.');edited.start=start;edited.end=end;if(duration!==M.rounded(a.end-a.start))edited.end=M.rounded(start+duration);M.valid(edited,next.duration);changed(M.invalidate(project(),next));say('');}catch(error){say(error.message,true);}
    }
    if(action==='listen'){const a=project().annotations.find(a=>selection.has(a.annotation_id));if(M.placed(a)){host.seek(a.start);host.play();}}
    if(action==='add'||action==='place')addDialog(action==='place');
    if(action==='roles')rolesDialog();
    if(action==='delete-role'){changed(M.role(project(),'remove',source.dataset.id));rolesDialog();}
    if(action==='toggle-role'){const id=roleId(source.dataset.role||null);hidden.has(id)?hidden.delete(id):hidden.add(id);persistFilter();}
    if(action==='solo-role'){const selected=roleId(source.dataset.role||null);hidden=new Set(roles().map(r=>roleId(r.id)).filter(id=>id!==selected));persistFilter();}
    if(action==='all-roles'){hidden.clear();commonOffset=0;persistFilter();}
    if(action==='common-up'||action==='common-down'){commonOffset=commonWindow().first+(action==='common-up'?-1:1);renderFilters();host.draw();}
    if(action==='common-choose'){const t=host.position(),items=visible().filter(a=>M.placed(a)&&a.start<t+3&&a.end>t);modal('Плашки у курсора',`<p>Все видимые исполнения в ${host.format(t)} и следующие три секунды.</p><div class="manual-common-list">${items.map(a=>button('choose-common',`${esc(a.text)} · ${esc(role(a.role_id).name)} · ${a.start.toFixed(3)}–${a.end.toFixed(3)} с`,`data-id="${esc(a.annotation_id)}"`)).join('')||'<p>В этом участке нет видимых плашек.</p>'}</div>`);}
    if(action==='choose-common'){const a=project().annotations.find(a=>a.annotation_id===source.dataset.id);selection=new Set([a.annotation_id]);const item=graphLayout().items.find(i=>i.annotation.annotation_id===a.annotation_id);commonOffset=Math.max(0,item.lane-1);q('#manual-dialog').close();host.seek(a.start);renderFilters();host.draw();}
    if(action==='export')exportJSON(project(),`manual-lyrics-${jobId}-draft.json`);
    if(action==='close-dialog')q('#manual-dialog').close();
    if(action==='references')referencesDialog();
    if(action==='review'||action==='review-all')reviewDialog(action==='review-all');
    if(action==='remove-review'){const next=M.clone(project());next.reviews.splice(Number(source.dataset.index),1);changed(next);referencesDialog();}
    if(action==='snapshot')snapshotDialog();
    if(action==='import-reference')q('#manual-reference-file').click();
    if(action==='view-reference'||action==='export-reference'){
      try{const ref=await request(endpoint(`/references/${source.dataset.id}`));if(action==='export-reference')exportJSON(ref,`${ref.reference_id}.json`);
        else modal(`Эталон: ${ref.label||ref.reference_id}`,`<p>Неизменяемая версия ${ref.version} · ${ref.project.annotations.length} плашек · ${ref.project.reviews.length} проверок</p><p>Эта версия не меняется вместе с редактором. Чтобы восстановить её, экспортируйте и импортируйте JSON.</p><pre class="manual-diagnostics">${esc(JSON.stringify({coverage:ref.coverage,reviews:ref.project.reviews,annotations:ref.project.annotations,roles:ref.project.roles},null,2))}</pre>${button('export-reference','Экспорт этой версии',`data-id="${esc(ref.reference_id)}"`)}`);
      }catch(error){modalError(error);}}
    if(action==='auto')automatic();
    if(action==='apply-auto'&&proposal){const next=M.clone(project());next.annotations=M.clone(proposal.annotations||[]);next.roles=M.clone(proposal.roles||[]);next.occurrences=M.clone(proposal.occurrences||[]);next.reviews=[];changed(M.reconcile(next),[]);q('#manual-dialog').close();say('Предложение применено к черновику. Сохраните после проверки; отмена вернёт предыдущую разметку.');}
  }
  window.ManualEditor={init,load,active,draw,graphTop,hit,renderPosition,sync,dirty,project,editing:()=>editing,refresh:()=>{invalidate();if(editing)renderLanes();},canLeave:()=>{if(saving){say('Дождитесь окончания сохранения.');return false;}return !dirty()||window.confirm('Есть несохранённые правки разметки. Покинуть песню и потерять эти правки?');}};
})();
