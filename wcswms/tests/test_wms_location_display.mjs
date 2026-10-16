import test from 'node:test';
import assert from 'node:assert/strict';
import {api,setDeviceScope,setLocationLayouts,storedLocation,locationLabel,displayLocationText} from '../web/src/wms/api.js';

const storage={};
Object.defineProperty(storage,'removeItem',{value:key=>delete storage[key]});
globalThis.sessionStorage=storage;

test('single-depth locations omit the depth suffix without changing displayed coordinates',()=>{
 setLocationLayouts({rack_layouts:[{side:1,depths:1},{side:2,depths:1}]});
 assert.equal(storedLocation('L-03-06-1'),'L-06-03');
 assert.equal(locationLabel({id:'R-15-06-1',side:2,level:6,column:15,depth:1}),'R-06-15 · 右仓 6层 15列');
 assert.equal(displayLocationText('库位 L-03-06-1 已预留'),'库位 L-06-03 已预留');
 assert.equal(storedLocation('STATION-1'),'交接点 1');
 assert.equal(storedLocation(null),'—');
});

test('mixed rack structures retain front and rear depth labels only on the double-depth side',()=>{
 setLocationLayouts({rack_layouts:[{side:1,depths:1},{side:2,depths:2}]});
 assert.equal(storedLocation('L-03-06-1'),'L-06-03');
 assert.equal(storedLocation('R-03-06-1'),'R-06-03 · 单伸');
 assert.equal(storedLocation('R-03-06-2'),'R-06-03 · 双伸');
 assert.equal(locationLabel({id:'R-03-06-2',side:2,level:6,column:3,depth:2}),'R-06-03 · 右仓 6层 3列 · 双伸');
 assert.equal(displayLocationText('R-03-06-1 到 R-03-06-2'),'R-06-03（单伸） 到 R-06-03（双伸）');
});

test('legacy layouts infer each side independently and explicit configuration takes precedence',()=>{
 const locations=[{side:1,depth:1},{side:1,depth:2,enabled:false},{side:2,depth:2}];
 setLocationLayouts({locations});
 assert.equal(storedLocation('L-01-01-1'),'L-01-01');
 assert.equal(storedLocation('R-01-01-1'),'R-01-01 · 单伸');
 setLocationLayouts({locations,rack_layouts:[{side:2,depths:1}]});
 assert.equal(storedLocation('R-01-01-1'),'R-01-01');
});

test('accepted device snapshots update formatting and switching rejects late layout responses',async()=>{
 setDeviceScope('first');
 globalThis.fetch=async()=>({ok:true,json:async()=>({scope:{device_id:'first'},rack_layouts:[{side:1,depths:2}]})});
 await api('/api/state');
 assert.equal(storedLocation('L-01-01-1'),'L-01-01 · 单伸');
 let resolve;
 globalThis.fetch=()=>new Promise(done=>{resolve=done;});
 const pending=api('/api/state');
 setDeviceScope('second');
 assert.equal(storedLocation('L-01-01-1'),'L-01-01');
 resolve({ok:true,json:async()=>({scope:{device_id:'first'},rack_layouts:[{side:1,depths:2}]})});
 await assert.rejects(pending,/已切换/);
 assert.equal(storedLocation('L-01-01-1'),'L-01-01');
 setDeviceScope('primary');
});
