import test from 'node:test';
import assert from 'node:assert/strict';
import {pageWindow} from '../web/src/wms/paging.js';

test('all 180 warehouse records remain reachable at every screen page size',()=>{
 const records=Array.from({length:180},(_,id)=>({id}));
 for(const size of [3,6,8,12]){
  const visited=[];
  for(let page=0;page<pageWindow(records,0,size).pages;page++)visited.push(...pageWindow(records,page,size).items);
  assert.deepEqual(visited,records);
 }
});
test('filtering and deletion clamp the current page to remaining records',()=>{
 assert.deepEqual(pageWindow(['one'],20,6).items,['one']);
 assert.equal(pageWindow(['one'],20,6).page,0);
 assert.deepEqual(pageWindow([],20,6).items,[]);
 assert.equal(pageWindow([],20,6).pages,1);
});
test('negative pages and small page sizes cannot drop the first record',()=>{
 assert.deepEqual(pageWindow(['a','b'],-2,0).items,['a']);
});
