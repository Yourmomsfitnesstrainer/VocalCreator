const M=require('../src/karaoke_generator/static/syllable-model.js');
const notes=[{id:'n1',start:0,end:.4,midi:60},{id:'n2',start:.5,end:1,midi:64},{id:'n3',start:1.2,end:1.8,midi:67},{id:'long',start:2,end:4,midi:60}];
function fixture(){return M.normalize({schema_version:1,job_id:'test-job',revision:0,base_analysis_key:'test-base',source:{duration:6,language:'ru',notes_sha256:'test-notes'},canonical_text:'ма молоко',provenance:{test:true},occurrences:[{occurrence_id:'o1',source_word_id:'w1',source_text:'ма',source_ranges:[{start:0,end:2}],unit_ids:['u1']},{occurrence_id:'o2',source_word_id:'w2',source_text:'молоко',source_ranges:[{start:3,end:9}],unit_ids:['u2']}],units:[{unit_id:'u1',occurrence_id:'o1',kind:'syllable',text:'ма',order:0,source_ranges:[{start:0,end:2}],start:0,end:1.8,intervals:[],segmentation_status:'suggested',timing_status:'suggested',origin:'automatic',reason_codes:[]},{unit_id:'u2',occurrence_id:'o2',kind:'word_fallback',text:'молоко',order:0,source_ranges:[{start:3,end:9}],start:2,end:4,intervals:[],segmentation_status:'unavailable',timing_status:'approximate',origin:'automatic',reason_codes:['character_evidence_unavailable']}],note_links:[{link_id:'l1',unit_id:'u1',source_note_id:'n1',start:0,end:.4,continuation:false,origin:'automatic'},{link_id:'l2',unit_id:'u1',source_note_id:'n2',start:.5,end:1,continuation:true,origin:'automatic'},{link_id:'l3',unit_id:'u1',source_note_id:'n3',start:1.2,end:1.8,continuation:true,origin:'automatic'},{link_id:'l4',unit_id:'u2',source_note_id:'long',start:2,end:4,continuation:false,origin:'automatic'}],unresolved:[]});}
const mappings=[{part_index:0,source_note_id:'long',start:2,end:2.4},{part_index:1,source_note_id:'long',start:2.4,end:3.1},{part_index:2,source_note_id:'long',start:3.1,end:4}];
function snapshots(){const f=fixture(),split=M.split(f,'u2',['мо','ло','ко'],mappings,notes);return {
  original:f,
  rename:M.rename(f,'u1','мя'),
  deletion:M.removeLink(f,'l1'),
  reassignment:M.reassign(f,'l1',{text:'да'},notes).document,
  middleReassignment:M.reassign(f,'l2',{text:'ми'},notes).document,
  splitRegion:M.splitRegion(f,'l4',2.8,'ло',notes).document,
  split:split.document,
  overrideSplit:M.split(f,'u2',['ma','la','ko'],mappings,notes).document,
  merge:M.merge(split.document,split.unit_ids).document,
  unpitched:M.setUnpitched(f,'u1',[{start:.42,end:.48}]),
  unplaced:M.removeLink(M.removeLink(M.removeLink(f,'l1'),'l2'),'l3'),
  authored:M.add(f,{text:'эй',start:5,end:5.4},notes).document,
  noTime:M.add(f,{text:'неизвестно'},notes).document,
  movedTime:M.setLinkTime(f,'l1',.1,.3,notes),
  reattached:M.linkTo(M.removeLink(f,'l1'),'u1',{source_note_id:'n1',start:0,end:.4},notes),
};}
module.exports={fixture,notes,mappings,snapshots};
if(require.main===module)process.stdout.write(JSON.stringify({notes,snapshots:snapshots()}));
