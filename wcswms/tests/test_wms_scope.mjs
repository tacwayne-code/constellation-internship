import test from 'node:test';
import assert from 'node:assert/strict';
import {api,apiPath,setDeviceScope,setSession,clearSession} from '../web/src/wms/api.js';

const storage={};
Object.defineProperty(storage,'removeItem',{value:key=>delete storage[key]});
globalThis.sessionStorage=storage;
globalThis.window={dispatchEvent(){}};

test('only warehouse operations are routed to the selected device',()=>{
 setDeviceScope('abc');
 assert.equal(apiPath('/api/tasks'),'/api/scopes/abc/api/tasks');
 assert.equal(apiPath('/api/inventory.csv'),'/api/scopes/abc/api/inventory.csv');
 for(const path of ['/api/sites','/api/devices','/api/users','/api/auth/logout'])assert.equal(apiPath(path),path);
 setDeviceScope('');
 assert.throws(()=>apiPath('/api/state'),/请先选择/);
 assert.equal(apiPath('/api/warehouses'),'/api/warehouses');
 setDeviceScope('primary');assert.equal(apiPath('/api/state'),'/api/state');
});

test('late responses from a previous device cannot trigger follow-up operations',async()=>{
 setDeviceScope('one');let resolve,requested;
 globalThis.fetch=(url)=>{requested=url;return new Promise(r=>resolve=r);};
 const pending=api('/api/tasks');
 assert.equal(requested,'/api/scopes/one/api/tasks');
 setDeviceScope('two');resolve({ok:true,json:async()=>({id:'old-task'})});
 await assert.rejects(pending,/已切换/);
 assert.equal(apiPath('/api/tasks'),'/api/scopes/two/api/tasks');
});

test('switching devices clears old drafts while retaining login verification',async()=>{
 setSession({csrf_token:'synthetic-token'});storage['wms-inbound-draft']='old-box';
 setDeviceScope('three');assert.equal(storage['wms-inbound-draft'],undefined);
 let options;globalThis.fetch=async(url,value)=>{options=value;return {ok:true,json:async()=>({ok:true})};};
 await api('/api/tasks',{kind:'INBOUND'});
 assert.equal(options.headers['X-CSRF-Token'],'synthetic-token');
 clearSession();await api('/api/tasks',{kind:'INBOUND'});
 assert.equal(options.headers['X-CSRF-Token'],'');
});
