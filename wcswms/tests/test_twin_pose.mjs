import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {physicalPreviewPose,validPhysicalCalibration} from '../web/src/twin-pose.js';
const ref={travel_x:3,lift_y:6};
test('physical preview uses millimetres converted on server, never simulation coordinates',()=>{
 assert.deepEqual(physicalPreviewPose({device:{x:900,y:900,z:900},twin:{relative_m:{x:.25,y:.1,z:-.04}}},ref),
  {x:3.25,y:6.1,dx:.25,dy:.1,forkMid:-.02,forkTip:-.04});
});

const calibration=JSON.parse(readFileSync(new URL('../assets/structure/physical-calibration.json',import.meta.url),'utf8'));
const map=JSON.parse(readFileSync(new URL('../assets/structure/twin-map.json',import.meta.url),'utf8'));
const machineReference=map.motion_reference_m;
const feedback=(x=871,y=-28,z=0)=>({axes:[{axis:'x',value:x},{axis:'y',value:y},{axis:'z',value:z}]});
test('survey point aligns first column from reversed end and first layer',()=>{
 assert(validPhysicalCalibration(calibration));
 const pose=physicalPreviewPose(feedback(),machineReference,calibration);
 assert.equal(pose.x,Math.max(...map.candidate_axes_m.x));
 assert.equal(pose.y,Math.min(...map.candidate_axes_m.y));
 assert.equal(machineReference.travel_x+pose.dx,pose.x);
 assert.equal(machineReference.lift_y+pose.dy,pose.y);
});
test('increasing PLC travel moves toward CAD negative X without inverting lift',()=>{
 const before=physicalPreviewPose(feedback(),machineReference,calibration);
 const after=physicalPreviewPose(feedback(971,72,0),machineReference,calibration);
 assert(Math.abs(after.x-before.x+.1)<1e-10);
 assert(Math.abs(after.y-before.y-.1)<1e-10);
});
test('PLC fork zero cancels both original CAD extensions and centres the forks',()=>{
 const pose=physicalPreviewPose(feedback(),machineReference,calibration);
 assert.equal(machineReference.fork_mid_extension+pose.forkMid,0);
 assert.equal(machineReference.fork_tip_extension+pose.forkTip,0);
 const extended=physicalPreviewPose(feedback(871,-28,100),machineReference,calibration);
 assert(Math.abs(machineReference.fork_mid_extension+extended.forkMid-.05)<1e-10);
 assert(Math.abs(machineReference.fork_tip_extension+extended.forkTip-.1)<1e-10);
});
test('server startup baseline never overrides the saved survey point',()=>{
 const state=feedback(2000,500,0);
 const before=physicalPreviewPose({...state,twin:{relative_m:{x:9,y:8,z:7}}},machineReference,calibration);
 const after=physicalPreviewPose({...state,twin:{relative_m:{x:0,y:0,z:0}}},machineReference,calibration);
 assert.deepEqual(after,before);
});
test('invalid calibration or missing axis feedback cannot fall through to old relative mapping',()=>{
 const state={...feedback(),twin:{relative_m:{x:99,y:99,z:99}}};
 assert.equal(physicalPreviewPose(state,machineReference,{...calibration,axis_sign:{x:0,y:1,z:1}}),null);
 assert.equal(physicalPreviewPose({...state,axes:[]},machineReference,calibration),null);
});
test('missing and invalid real feedback never become fake zero coordinates',()=>{
 for(const state of [null,{device:{x:900}},{twin:{relative_m:{x:NaN,y:1,z:1}}},{twin:{relative_m:{x:null,y:1,z:1}}}])
  assert.equal(physicalPreviewPose(state,ref),null);
});
