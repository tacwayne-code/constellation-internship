import test from 'node:test';
import assert from 'node:assert/strict';
import {transferSources,transferTargets,transferMatches,taskPermission} from '../web/src/wms/transfer.js';

const location=(id,depth=1,more={})=>({id,depth,verified:true,enabled:true,reserved:null,access_error:'',...more});
const box=(barcode,location,more={})=>({barcode,location,status:'IN_STOCK',reserved:null,...more});
test('whole-box choices omit closed, reserved and inaccessible stock without losing empty bins',()=>{
 const state={locations:[location('L-01-01-1'),location('L-02-01-1'),location('L-03-01-2',2,{access_error:'前排占用'})],stock:[box('MATERIAL','L-01-01-1'),box('EMPTY','L-02-01-1',{container_type:'EMPTY_BIN'}),box('BLOCKED','L-03-01-2'),box('SHIPPED','L-01-01-1',{status:'SHIPPED'})],tasks:[]};
 assert.deepEqual(transferSources(state).map(item=>item.barcode),['MATERIAL','EMPTY']);
 state.tasks.push({id:'move',kind:'MOVE',barcode:'MATERIAL',location:'L-01-01-1',target_location:'L-02-01-1',status:'PLC_DONE'});
 assert.deepEqual(transferSources(state),[]);
 assert.equal(taskPermission({kind:'MOVE'}),'inventory');
 assert.equal(taskPermission({kind:'INBOUND',document_kind:'OUTBOUND'}),'outbound');
});
test('targets permit same-bay placement after retrieval while guarding other front occupancy and both task ends',()=>{
 const material=box('SOURCE','L-01-01-1');
 const state={locations:[location('L-01-01-1'),location('L-01-01-2',2),location('R-01-01-1'),location('R-01-01-2',2),location('R-02-01-1')],stock:[material,box('OTHER','R-01-01-1')],tasks:[]};
 assert.deepEqual(transferTargets(state,material).map(item=>item.id),['L-01-01-2','R-02-01-1']);
 state.tasks.push({id:'other',kind:'MOVE',barcode:'OTHER',location:'R-01-01-1',target_location:'R-02-01-1',status:'QUEUED'});
 assert.deepEqual(transferTargets(state,material).map(item=>item.id),['L-01-01-2']);
 assert.equal(transferMatches('MOTOR-01 电机 M2026 1kW','电机 m2026'),true);
 assert.equal(transferMatches('MOTOR-01 电机 M2026 1kW','电机 m2025'),false);
});
