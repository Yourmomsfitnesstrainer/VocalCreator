const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../src/karaoke_generator/static/syllable-model.js');
const {fixture,notes,mappings,snapshots}=require('./syllable_fixture.cjs');

test('S02 S18 a melisma has one identity, real gaps and current intervals, not envelope activity',()=>{
  const d=fixture();assert.equal(d.note_links.filter(l=>l.unit_id==='u1').length,3);
  assert.equal(M.activeAt(d,.45),undefined);assert.equal(M.activeAt(d,1.1),undefined);assert.equal(M.activeAt(d,.75).unit_id,'u1');
});
test('S09 changing text updates the shared unit while reassignment removes first-region activity and continuation',()=>{
  const original=fixture(),renamed=M.rename(original,'u1','мя');assert.equal(renamed.note_links.filter(l=>l.unit_id==='u1').length,3);assert.equal(renamed.units[0].manual_override,true);assert.equal(renamed.units[0].source_text,'ма');
  const reassigned=M.reassign(renamed,'l1',{text:'да'},notes).document;assert.equal(M.activeAt(reassigned,.2).text,'да');assert.equal(reassigned.units.find(u=>u.unit_id==='u1').start,.5);assert.equal(reassigned.note_links.find(l=>l.link_id==='l2').continuation,false);assert.equal(M.activeAt(original,.2).text,'ма');
});
test('S12 removing all links retains text unresolved, preserves independent support, and undo restores complete operation',()=>{
  const original=M.setUnpitched(fixture(),'u1',[{start:.42,end:.48}]),h=new M.History(original);h.commit(M.removeLink(h.document,'l1'));assert.equal(M.activeAt(h.document,.2),undefined);assert.equal(M.activeAt(h.document,.45).text,'ма');h.undo();assert.deepEqual(h.document,original);h.redo();
  h.commit(M.removeLink(M.removeLink(h.document,'l2'),'l3'));const u=h.document.units.find(u=>u.unit_id==='u1');assert.deepEqual(u.intervals,[{start:.42,end:.48,support:'unpitched'}]);assert.ok(h.document.unresolved.some(r=>r.unit_id==='u1'));const unplaced=M.setUnpitched(h.document,'u1',[]);assert.equal(unplaced.units[0].start,null);assert.equal(unplaced.units[0].timing_status,'unavailable');
});
test('S03 S10 split requires explicit complete mapping, allows multiple syllables in one untouched note',()=>{
  const original=fixture(),beforeNotes=JSON.stringify(notes);assert.throws(()=>M.split(original,'u2',['мо','ло','ко'],[],notes),/каждого слога/);assert.throws(()=>M.split(original,'u2',['мо','ло','ко'],mappings.slice(0,2),notes),/каждого слога/);
  const split=M.split(original,'u2',['мо','ло','ко'],mappings,notes);assert.deepEqual(split.document.units.filter(u=>u.occurrence_id==='o2').map(u=>[u.text,u.start,u.end]),[['мо',2,2.4],['ло',2.4,3.1],['ко',3.1,4]]);assert.equal(JSON.stringify(notes),beforeNotes);assert.equal(original.units[1].text,'молоко');assert.ok(split.unit_ids.every(id=>id!=='u2'));
});
test('S11 split rejects overlap atomically and notes with gapped acoustic intervals reject filling their rest',()=>{
  const original=fixture(),before=JSON.stringify(original),bad=mappings.map(m=>({...m}));bad[0].end=2.6;assert.throws(()=>M.split(original,'u2',['мо','ло','ко'],bad,notes),/перекрываются/);assert.equal(JSON.stringify(original),before);
  const gapNotes=notes.map(n=>n.id==='long'?{...n,intervals:[{start:2,end:2.5},{start:3,end:4}]}:n);assert.throws(()=>M.split(original,'u2',['мо','ло','ко'],mappings,gapNotes),/внутри исходной ноты/);
});
test('S11 merge is only adjacent same occurrence, changes IDs, preserves separated intervals',()=>{
  const d=fixture(),split=M.split(d,'u2',['мо','ло','ко'],mappings,notes);assert.throws(()=>M.merge(split.document,[split.unit_ids[0],split.unit_ids[2]]),/соседние/);assert.throws(()=>M.merge(split.document,['u1',split.unit_ids[0]]),/соседние/);const merged=M.merge(split.document,split.unit_ids).document;assert.equal(merged.units.find(u=>u.occurrence_id==='o2').text,'молоко');assert.deepEqual(merged.units.find(u=>u.occurrence_id==='o2').source_ranges,[{start:3,end:5},{start:5,end:7},{start:7,end:9}]);
  const s=M.split(d,'u1',['м','а'],[{part_index:0,source_note_id:'n1',start:0,end:.4},{part_index:1,source_note_id:'n2',start:.5,end:1},{part_index:1,source_note_id:'n3',start:1.2,end:1.8}],notes);assert.equal(M.merge(s.document,s.unit_ids).document.units.find(u=>u.occurrence_id==='o1').intervals.length,3);
});
test('S13 Unicode source ranges remain codepoints and free manual spelling records original text',()=>{
  const d=fixture();d.canonical_text='😀 ма молоко';for(const o of d.occurrences)for(const r of o.source_ranges){r.start+=2;r.end+=2;}for(const u of d.units)for(const r of u.source_ranges){r.start+=2;r.end+=2;}
  const changed=M.rename(d,'u2','milk');assert.equal(changed.units[1].source_text,'молоко');assert.equal(changed.units[1].manual_override,true);assert.equal(changed.canonical_text,'😀 ма молоко');
});
test('S17 unplaced entry and independent time need no fictitious note and reject conflicting leading units',()=>{
  const d=fixture(),added=M.add(d,{text:'эй'},notes);assert.equal(added.document.units.at(-1).start,null);assert.equal(added.document.units.at(-1).timing_status,'unavailable');const placed=M.setUnpitched(added.document,added.unit_id,[{start:5,end:5.5}]);assert.equal(placed.note_links.length,d.note_links.length);assert.equal(M.activeAt(placed,5.1).text,'эй');assert.throws(()=>M.setUnpitched(placed,added.unit_id,[{start:3,end:3.5}]),/перекрываются/);assert.throws(()=>M.setUnpitched(d,'u1',[{start:.1,end:.3}]),/перекрываются/);
});
test('S08 history is one transaction per input and save after later edit keeps newer text undoable',()=>{
  const h=new M.History(fixture());h.commit(M.rename(h.document,'u1','мя'));const snapshot=M.clone(h.document);h.commit(M.rename(h.document,'u1','да'));h.savedAs({...snapshot,revision:1},snapshot);assert.equal(h.document.units[0].text,'да');assert.equal(h.document.revision,1);assert.equal(h.dirty,true);h.undo();assert.equal(h.document.units[0].text,'мя');assert.equal(h.document.revision,1);assert.equal(h.dirty,false);
});
test('all server roundtrip fixtures normalize every linked interval and keep immutable source identity',()=>{
  const source=JSON.stringify(fixture().source);for(const [name,doc] of Object.entries(snapshots())){M.validate(doc,notes);assert.equal(JSON.stringify(doc.source),source,name);assert.equal(doc.canonical_text,'ма молоко',name);}
});
