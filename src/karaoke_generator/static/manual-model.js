/* The editable text layer never receives or mutates notes or audio buffers. */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ManualLyricsModel = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function() {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const rounded = value => Math.round(value * 1000) / 1000;
  const shifted = (value, delta) => Math.round((value + delta) * 1e6) / 1e6;
  let serial = 0;
  function id(prefix) { return `${prefix}-${globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${++serial}`}`; }
  function placed(a) { return Number.isFinite(a.start) && Number.isFinite(a.end) && a.end > a.start; }
  function valid(a, duration) {
    if (!a.text?.trim()) throw Error('Введите текст плашки.');
    if (!placed(a) || a.start < 0 || a.end > duration) throw Error(`Нужны границы 0 ≤ начало < конец ≤ ${duration.toFixed(3)} с. Прежний интервал сохранён.`);
  }
  function reconcile(project) {
    const old = new Map((project.occurrences || []).map(o => [o.occurrence_id, o]));
    const groups = new Map();
    for (const a of project.annotations) {
      if (!groups.has(a.occurrence_id)) groups.set(a.occurrence_id, []);
      groups.get(a.occurrence_id).push(a);
    }
    project.occurrences = [...groups].map(([occurrence_id, annotations]) => {
      const previous = old.get(occurrence_id);
      return { ...previous, occurrence_id, annotation_ids: annotations.map(a => a.annotation_id),
        complete: previous ? previous.complete !== false && (previous.annotation_ids || []).every(i => annotations.some(a => a.annotation_id === i)) : annotations.every(a => a.unit !== 'part'),
        source_word_id: previous?.source_word_id ?? annotations[0].source_word_id ?? null };
    });
    return project;
  }
  function semantic(a) { return a && [a.text, a.start, a.end, a.role_id, a.occurrence_id, a.unit].join('|'); }
  function invalidate(before, after) {
    const old = new Map(before.annotations.map(a => [a.annotation_id, a]));
    const next = new Map(after.annotations.map(a => [a.annotation_id, a]));
    const changed = [...new Set([...old.keys(), ...next.keys()])].flatMap(key => semantic(old.get(key)) === semantic(next.get(key)) ? [] : [old.get(key), next.get(key)].filter(Boolean));
    if (changed.length) after.reviews = (after.reviews || []).filter(r => !changed.some(a => placed(a) && a.start < r.end && a.end > r.start));
    return reconcile(after);
  }
  function edit(project, ids, field, value) {
    const next = clone(project), selected = new Set(ids);
    if (!selected.size) throw Error('Сначала выделите плашку.');
    if (selected.size !== 1 && field !== 'role_id') throw Error('Текст и границы редактируются у одной плашки.');
    if (field === 'role_id' && value !== null && !next.roles.some(r => r.id === value)) throw Error('Роль не найдена.');
    for (const a of next.annotations.filter(a => selected.has(a.annotation_id))) {
      if (field === 'duration') a.end = rounded(a.start + value);
      else if (['start', 'end'].includes(field)) a[field] = rounded(value);
      else a[field] = value;
      if (!a.text?.trim()) throw Error('Введите текст плашки.');
      if (placed(a) || ['start', 'end', 'duration'].includes(field)) valid(a, next.duration);
      a.provenance = { ...a.provenance, edited: true };
      if (['start', 'end', 'duration'].includes(field)) a.approximate = false;
    }
    return invalidate(project, next);
  }
  function move(project, ids, delta) {
    const selected = new Set(ids), group = project.annotations.filter(a => selected.has(a.annotation_id));
    if (!group.length) return project;
    if (group.some(a => !placed(a))) throw Error('Сначала задайте границы плашек без времени.');
    delta = rounded(delta);
    delta = Math.max(-Math.min(...group.map(a => a.start)), Math.min(project.duration - Math.max(...group.map(a => a.end)), delta));
    if (delta === 0) return project;
    const next = clone(project);
    for (const a of next.annotations) if (selected.has(a.annotation_id)) { a.start = shifted(a.start, delta); a.end = shifted(a.end, delta); a.provenance = {...a.provenance, edited:true}; }
    return invalidate(project, next);
  }
  function copy(project, ids) {
    const group = project.annotations.filter(a => ids.includes(a.annotation_id));
    if (!group.length) throw Error('Сначала выделите плашки для копирования.');
    if (group.some(a => !placed(a))) throw Error('Копировать во времени можно плашки с заданными границами.');
    return clone({ project_id:project.project_id, annotations:group, occurrences:project.occurrences.filter(o => group.some(a => a.occurrence_id === o.occurrence_id)), start:Math.min(...group.map(a => a.start)) });
  }
  function paste(project, clipboard, cursor) {
    if (!clipboard?.annotations?.length) throw Error('Сначала скопируйте плашки.');
    if (clipboard.project_id !== project.project_id) throw Error('Вставка доступна внутри одной песни.');
    if (!Number.isFinite(cursor) || cursor < 0) throw Error('Укажите корректное время курсора вставки.');
    const delta = cursor - clipboard.start;
    if (clipboard.annotations.some(a => shifted(a.end, delta) > project.duration)) throw Error('Вся группа не помещается до конца записи. Установите курсор раньше: ничего не вставлено.');
    const next = clone(project), occurrences = new Map(clipboard.occurrences.map(o => [o.occurrence_id, o])), newOccurrences = new Map(), added = [];
    for (const a of clipboard.annotations) {
      if (!newOccurrences.has(a.occurrence_id)) newOccurrences.set(a.occurrence_id, id('occ'));
      added.push({...clone(a), annotation_id:id('ann'), occurrence_id:newOccurrences.get(a.occurrence_id), start:shifted(a.start,delta), end:shifted(a.end,delta),
        provenance:{kind:'copy',copied_from_annotation_id:a.annotation_id,copied_from_occurrence_id:a.occurrence_id,origin:a.provenance}});
    }
    for (const [oldId, newId] of newOccurrences) {
      const original = occurrences.get(oldId), copiedIds = new Set(clipboard.annotations.filter(a => a.occurrence_id === oldId).map(a => a.annotation_id));
      next.occurrences.push({occurrence_id:newId, annotation_ids:added.filter(a => a.occurrence_id === newId).map(a => a.annotation_id), source_word_id:original?.source_word_id ?? null,
        complete:original?.complete !== false && (original?.annotation_ids || []).every(i => copiedIds.has(i))});
    }
    next.annotations.push(...added);
    return {project:invalidate(project,next),ids:added.map(a => a.annotation_id)};
  }
  function add(project, values) {
    const next = clone(project), a = {annotation_id:id('ann'),occurrence_id:id('occ'),unit:'word',role_id:null,source_word_id:null,source_part_id:null,approximate:false,provenance:{kind:'manual'},...values};
    a.start = rounded(a.start); a.end = rounded(a.end); valid(a, project.duration);
    next.annotations.push(a); return {project:invalidate(project,next),ids:[a.annotation_id]};
  }
  function remove(project, ids) { const next=clone(project);next.annotations=next.annotations.filter(a=>!ids.includes(a.annotation_id));return invalidate(project,next); }
  function role(project, action, value) {
    const next = clone(project);
    if (action === 'add') { if(!value.name?.trim())throw Error('Введите имя роли.');next.roles.push({...value,id:id('role')}); }
    if (action === 'update') { const item=next.roles.find(r=>r.id===value.id);if(!item)throw Error('Роль не найдена.');if(value.name!==undefined&&!value.name.trim())throw Error('Введите имя роли.');Object.assign(item,value); }
    if (action === 'remove') { next.roles=next.roles.filter(r=>r.id!==value);for(const a of next.annotations)if(a.role_id===value)a.role_id=null; }
    return invalidate(project,next);
  }
  // Timeline positions are absolute source seconds. Lanes are layout only.
  function layout(annotations, {pps=60, grouped=false, roles=[]}={}) {
    const ordered = annotations.filter(placed).slice().sort((a,b)=>a.start-b.start || a.end-b.end || a.annotation_id.localeCompare(b.annotation_id));
    const groups = grouped ? [null,...roles.map(r=>r.id)] : [undefined], rows=[], items=[];let top=0;
    for(const roleId of groups) {
      const ends=[];
      for(const a of ordered.filter(a=>roleId===undefined || a.role_id===roleId)) {
        const x=a.start*pps, width=Math.max(18,(a.end-a.start)*pps), labelWidth=Math.max(width,Math.min(300,Array.from(a.text).length*7+35));
        let lane=ends.findIndex(end=>end<=x);if(lane<0)lane=ends.length;
        ends[lane]=x+labelWidth+8;items.push({annotation:a,x,width,labelWidth,y:top+lane*34+8,lane});
      }
      const height=Math.max(50,ends.length*34+16);rows.push({role_id:roleId,top,height});top+=height;
    }
    return {items,rows,height:Math.max(50,top)};
  }
  function currentAndNext(annotations, position, horizon=3) {
    const timed=annotations.filter(placed).slice().sort((a,b)=>a.start-b.start||a.end-b.end);
    const current=timed.filter(a=>a.start<=position&&position<a.end),nextStart=timed.find(a=>a.start>position)?.start;
    return {current,next:nextStart===undefined?[]:timed.filter(a=>a.start>position&&a.start<=nextStart+horizon)};
  }
  function captionWindow(layout, offset, rows) {
    const total=Math.max(1,...layout.items.map(item=>item.lane+1));
    const first=Math.max(0,Math.min(Math.max(0,total-rows),Math.trunc(offset)));
    const end=Math.min(total,first+rows);
    return {first,end,total,items:layout.items.filter(item=>item.lane>=first&&item.lane<end)};
  }
  function scrollForTime(time, pps, viewportWidth, duration) {
    const visibleWidth=Math.max(0,viewportWidth-74);
    return Math.max(0,Math.min(Math.max(0,duration*pps-visibleWidth),time*pps-visibleWidth/2));
  }
  class History {
    constructor(project) {this.project=clone(project);this.past=[];this.future=[];this.saved=JSON.stringify(project);}
    commit(next) {if(JSON.stringify(next)===JSON.stringify(this.project))return false;this.past.push(clone(this.project));this.future=[];this.project=clone(next);return true;}
    undo() {if(!this.past.length)return false;this.future.push(this.project);this.project=this.past.pop();return true;}
    redo() {if(!this.future.length)return false;this.past.push(this.project);this.project=this.future.pop();return true;}
    savedAs(project) {const revision=project.revision;this.project=clone(project);this.saved=JSON.stringify(project);for(const p of [...this.past,...this.future])p.revision=revision;}
    get dirty() {return JSON.stringify(this.project)!==this.saved;}
  }
  return {clone,id,placed,rounded,valid,reconcile,invalidate,edit,move,copy,paste,add,remove,role,layout,currentAndNext,captionWindow,scrollForTime,History};
});
