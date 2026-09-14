/* Text operations are pure: source notes, pitch and audio never enter history. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SyllableModel=api;})(globalThis,function(){
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  let serial=0;
  const id=prefix=>`${prefix}-${globalThis.crypto?.randomUUID?.()||`${Date.now().toString(36)}-${++serial}`}`;
  const ordered=(a,b)=>a.start-b.start||a.end-b.end||String(a.link_id||'').localeCompare(String(b.link_id||''));
  const duration=doc=>doc.source.duration??doc.duration;
  const findUnit=(doc,key)=>{const unit=doc.units.find(u=>u.unit_id===key);if(!unit)throw Error('Слог больше не найден. Выберите область заново.');return unit;};
  const sourceText=(doc,ranges)=>ranges.map(r=>Array.from(doc.canonical_text).slice(r.start,r.end).join('')).join('');
  const placed=unit=>unit.intervals?.length>0;
  const activeAt=(doc,time)=>doc.units.find(u=>u.intervals.some(i=>i.start<=time&&time<i.end));
  function manual(doc,unit){unit.origin='manual';unit.source_text=sourceText(doc,unit.source_ranges||[]);unit.manual_override=unit.text!==unit.source_text;unit.provenance={...unit.provenance,edited:true};}
  function compact(intervals){const result=[];for(const i of intervals.sort(ordered)){const last=result.at(-1);if(last&&last.support===i.support&&i.start<=last.end+1e-7)last.end=Math.max(last.end,i.end);else result.push({...i});}return result;}
  function normalize(doc){
    for(const unit of doc.units){
      const links=doc.note_links.filter(l=>l.unit_id===unit.unit_id).sort(ordered);
      links.forEach((link,index)=>{link.continuation=index>0;});
      unit.intervals=[...compact((unit.intervals||[]).filter(i=>i.support==='unpitched')),...compact(links.map(l=>({start:l.start,end:l.end,support:'note_link'})))].sort(ordered);
      if(!unit.intervals.length)unit.timing_status='unavailable';else if(unit.timing_status==='unavailable')unit.timing_status='approximate';
      unit.start=unit.intervals.length?unit.intervals[0].start:null;unit.end=unit.intervals.length?Math.max(...unit.intervals.map(i=>i.end)):null;
    }
    for(const occurrence of doc.occurrences)occurrence.unit_ids=doc.units.filter(u=>u.occurrence_id===occurrence.occurrence_id).sort((a,b)=>a.order-b.order).map((u,index)=>{u.order=index;return u.unit_id;});
    const ids=new Set(doc.units.map(u=>u.unit_id)),linkIds=new Set(doc.note_links.map(l=>l.link_id));doc.note_links.sort(ordered);
    doc.unresolved=(doc.unresolved||[]).filter(r=>(!r.unit_id||ids.has(r.unit_id))&&(!r.link_id||linkIds.has(r.link_id))&&!['unplaced','unpitched','no_note_links','no_note_link','timing_unavailable'].includes(r.code||r.reason_code));
    for(const unit of doc.units)if(!doc.note_links.some(l=>l.unit_id===unit.unit_id))doc.unresolved.push({unit_id:unit.unit_id,occurrence_id:unit.occurrence_id,reason_code:placed(unit)?'no_note_link':'timing_unavailable',text:unit.text});
    return doc;
  }
  function validate(doc,notes){
    if(doc.schema_version!==1)throw Error('Неизвестный формат слоговой партии. Исходный файл сохранён.');
    const units=new Map(doc.units.map(u=>[u.unit_id,u])),occurrences=new Map(doc.occurrences.map(o=>[o.occurrence_id,o])),links=new Set(),max=duration(doc);
    if(units.size!==doc.units.length||occurrences.size!==doc.occurrences.length)throw Error('Повторяются идентификаторы слогов или исполнений.');
    const validTime=i=>Number.isFinite(i.start)&&Number.isFinite(i.end)&&i.start>=0&&i.start<i.end&&i.end<=max+1e-6;
    const all=[];
    for(const unit of doc.units){
      if(!unit.text?.trim())throw Error('Введите текст слога.');
      if(!occurrences.has(unit.occurrence_id))throw Error('Не найдено исполнение слова.');
      let previousEnd=-Infinity;for(const interval of unit.intervals||[]){if(interval.start<previousEnd-1e-7)throw Error('Интервалы слога с нотой и без высоты перекрываются. Уточните границы.');previousEnd=interval.end;if(!validTime(interval))throw Error(`Нужны границы 0 ≤ начало < конец ≤ ${max.toFixed(3)} с. Прежние границы сохранены.`);all.push({...interval,unit_id:unit.unit_id});}
      if(!unit.intervals.length&&(unit.start!==null||unit.end!==null))throw Error('У неразмещённого слога не должно быть времени.');
    }
    all.sort(ordered);for(let a=0;a<all.length;a++)for(let b=a+1;b<all.length&&all[b].start<all[a].end-1e-7;b++)if(all[a].unit_id!==all[b].unit_id)throw Error('Интервалы разных слогов ведущего голоса перекрываются. Уточните границы: прежняя партия сохранена.');
    for(const link of doc.note_links){
      if(links.has(link.link_id))throw Error('Повторяется идентификатор связи.');links.add(link.link_id);
      const unit=units.get(link.unit_id);if(!unit||!validTime(link)||!unit.intervals.some(i=>i.start<=link.start+1e-7&&i.end>=link.end-1e-7))throw Error('Связь выходит за активный интервал слога.');
      if(notes){const note=notes.find(n=>n.id===link.source_note_id);if(!note||!(note.intervals||[note]).some(i=>link.start>=i.start-1e-7&&link.end<=i.end+1e-7))throw Error('Текстовая область должна находиться внутри исходной ноты.');}
    }
    for(const occurrence of doc.occurrences){let previous=null;for(const unit of doc.units.filter(u=>u.occurrence_id===occurrence.occurrence_id).sort((a,b)=>a.order-b.order)){if(previous&&placed(unit)&&unit.start<previous.start-1e-7)throw Error('Порядок слогов одного исполнения не совпадает со временем.');if(placed(unit))previous=unit;}}
    return doc;
  }
  const finish=(doc,notes)=>validate(normalize(doc),notes);
  function rename(doc,key,text){const next=clone(doc),unit=findUnit(next,key);unit.text=text.trim();manual(next,unit);return finish(next);}
  function makeUnit(doc,text,occurrenceId=null){
    let occurrence=doc.occurrences.find(o=>o.occurrence_id===occurrenceId);
    if(!occurrence){occurrence={occurrence_id:id('manual-occ'),source_word_id:null,source_text:'',source_ranges:[],unit_ids:[],origin:'manual'};doc.occurrences.push(occurrence);}
    const siblings=doc.units.filter(u=>u.occurrence_id===occurrence.occurrence_id);
    const unit={unit_id:id('manual-unit'),occurrence_id:occurrence.occurrence_id,kind:'syllable',text:text.trim(),order:siblings.length?Math.max(...siblings.map(u=>u.order))+1:0,source_ranges:[],start:null,end:null,intervals:[],segmentation_status:'approximate',timing_status:'approximate',origin:'manual',reason_codes:[],manual_override:true,source_text:'',provenance:{edited:true}};
    doc.units.push(unit);return unit;
  }
  function add(doc,{text,occurrence_id=null,source_note_id=null,start=null,end=null},notes){const next=clone(doc),unit=makeUnit(next,text,occurrence_id);if(start!==null||end!==null){if(source_note_id)next.note_links.push({link_id:id('manual-link'),unit_id:unit.unit_id,source_note_id,start,end,continuation:false,origin:'manual'});else unit.intervals=[{start,end,support:'unpitched'}];}return {document:finish(next,notes),unit_id:unit.unit_id};}
  function removeLink(doc,linkId){const next=clone(doc),link=next.note_links.find(l=>l.link_id===linkId);if(!link)throw Error('Связь больше не найдена.');next.note_links=next.note_links.filter(l=>l.link_id!==linkId);manual(next,findUnit(next,link.unit_id));return finish(next);}
  function reassign(doc,linkId,{unit_id=null,text=null,occurrence_id=null},notes){
    const next=clone(doc),link=next.note_links.find(l=>l.link_id===linkId);if(!link)throw Error('Сначала выберите связанную область.');
    const old=findUnit(next,link.unit_id),target=unit_id?findUnit(next,unit_id):makeUnit(next,text,occurrence_id||old.occurrence_id);
    if(!unit_id&&target.occurrence_id===old.occurrence_id){target.order=old.order-.25;if(link.start>Math.min(...next.note_links.filter(l=>l.unit_id===old.unit_id).map(l=>l.start)))target.order=old.order+.25;}
    link.unit_id=target.unit_id;link.origin='manual';manual(next,old);manual(next,target);return {document:finish(next,notes),unit_id:target.unit_id};
  }
  function linkTo(doc,key,{source_note_id,start,end},notes){const next=clone(doc),unit=findUnit(next,key);next.note_links.push({link_id:id('manual-link'),unit_id:key,source_note_id,start,end,origin:'manual',continuation:false});manual(next,unit);return finish(next,notes);}
  function splitRegion(doc,linkId,boundary,text,notes){
    const next=clone(doc),link=next.note_links.find(l=>l.link_id===linkId);if(!link||!Number.isFinite(boundary)||boundary<=link.start||boundary>=link.end)throw Error('Текстовая граница должна находиться внутри выбранной области.');
    const old=findUnit(next,link.unit_id),target=makeUnit(next,text,old.occurrence_id),end=link.end;target.order=old.order+.25;link.end=boundary;link.origin='manual';manual(next,old);
    next.note_links.push({link_id:id('manual-link'),unit_id:target.unit_id,source_note_id:link.source_note_id,start:boundary,end,origin:'manual',continuation:false});
    return {document:finish(next,notes),unit_id:target.unit_id};
  }
  function setLinkTime(doc,linkId,start,end,notes){const next=clone(doc),link=next.note_links.find(l=>l.link_id===linkId);if(!link)throw Error('Связь больше не найдена.');link.start=start;link.end=end;link.origin='manual';manual(next,findUnit(next,link.unit_id));return finish(next,notes);}
  function setUnpitched(doc,key,intervals){const next=clone(doc),unit=findUnit(next,key);unit.intervals=intervals.map(i=>({...i,support:'unpitched'}));manual(next,unit);return finish(next);}
  function split(doc,key,parts,mapping,notes){
    parts=parts.map(p=>p.trim());if(parts.length<2||parts.some(p=>!p))throw Error('Введите минимум два слога через |.');
    if(!Array.isArray(mapping)||!parts.every((_,index)=>mapping.some(m=>m.part_index===index)))throw Error('Укажите область или «Без времени» для каждого слога. Автоматического деления времени нет.');
    const next=clone(doc),old=findUnit(next,key),siblings=next.units.filter(u=>u.occurrence_id===old.occurrence_id).sort((a,b)=>a.order-b.order),position=siblings.indexOf(old);
    let sourceCursor=0;const sourceChars=(old.source_ranges||[]).flatMap(r=>Array.from({length:r.end-r.start},(_,i)=>r.start+i));
    const exact=parts.join('')===sourceText(next,old.source_ranges||[]);
    const units=parts.map((text,index)=>{let ranges=[];if(exact){const chars=sourceChars.slice(sourceCursor,sourceCursor+Array.from(text).length);sourceCursor+=Array.from(text).length;for(const p of chars){if(ranges.at(-1)?.end===p)ranges.at(-1).end++;else ranges.push({start:p,end:p+1});}}else if(index===0)ranges=clone(old.source_ranges||[]);
      const unit={...clone(old),unit_id:id('manual-unit'),kind:'syllable',segmentation_status:'approximate',text,order:position+index,source_ranges:ranges,intervals:[],start:null,end:null,reason_codes:[],provenance:{...old.provenance,split_from:old.unit_id}};manual(next,unit);return unit;});
    next.units=next.units.filter(u=>u.unit_id!==key);next.units.push(...units);next.note_links=next.note_links.filter(l=>l.unit_id!==key);
    for(const map of mapping){const unit=units[map.part_index];if(!unit)throw Error('Неизвестный слог в распределении.');if(map.unplaced)continue;if(map.source_note_id)next.note_links.push({link_id:id('manual-link'),unit_id:unit.unit_id,source_note_id:map.source_note_id,start:map.start,end:map.end,origin:'manual',continuation:false});else unit.intervals.push({start:map.start,end:map.end,support:'unpitched'});}
    [...siblings.slice(0,position),...units,...siblings.slice(position+1)].forEach((unit,index)=>{unit.order=index;});
    return {document:finish(next,notes),unit_id:units[0].unit_id,unit_ids:units.map(u=>u.unit_id)};
  }
  function merge(doc,keys){
    if(keys.length<2)throw Error('Выберите соседние слоги одного исполнения.');const next=clone(doc),selected=keys.map(k=>findUnit(next,k)).sort((a,b)=>a.order-b.order),occurrence=selected[0].occurrence_id,siblings=next.units.filter(u=>u.occurrence_id===occurrence).sort((a,b)=>a.order-b.order),at=siblings.indexOf(selected[0]);
    if(selected.some((u,i)=>u.occurrence_id!==occurrence||siblings[at+i]!==u))throw Error('Объединить можно только соседние слоги одного исполнения слова.');
    const unit={...clone(selected[0]),unit_id:id('manual-unit'),text:selected.map(u=>u.text).join(''),source_ranges:selected.flatMap(u=>u.source_ranges||[]),intervals:selected.flatMap(u=>u.intervals.filter(i=>i.support==='unpitched')),provenance:{merged_from:selected.map(u=>u.unit_id)}};manual(next,unit);
    next.units=next.units.filter(u=>!keys.includes(u.unit_id));next.units.push(unit);for(const link of next.note_links)if(keys.includes(link.unit_id)){link.unit_id=unit.unit_id;link.origin='manual';}return {document:finish(next),unit_id:unit.unit_id};
  }
  class History{
    constructor(doc){this.document=clone(doc);this.past=[];this.future=[];this.saved=JSON.stringify(doc);this.version=0;}
    get dirty(){return JSON.stringify(this.document)!==this.saved;}
    commit(doc){if(JSON.stringify(doc)===JSON.stringify(this.document))return;this.past.push(clone(this.document));this.document=clone(doc);this.future=[];this.version++;}
    undo(){if(!this.past.length)return;this.future.push(this.document);this.document=this.past.pop();this.version++;}
    redo(){if(!this.future.length)return;this.past.push(this.document);this.document=this.future.pop();this.version++;}
    savedAs(doc,snapshot){this.saved=JSON.stringify(doc);if(!snapshot||JSON.stringify(this.document)===JSON.stringify(snapshot))this.document=clone(doc);else this.document.revision=doc.revision;for(const d of [...this.past,...this.future])d.revision=doc.revision;}
  }
  return {clone,id,duration,sourceText,placed,activeAt,normalize,validate,rename,add,removeLink,reassign,linkTo,splitRegion,setLinkTime,setUnpitched,split,merge,History};
});
