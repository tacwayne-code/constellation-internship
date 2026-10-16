import test from 'node:test';
import assert from 'node:assert/strict';
import {dateRange,queryParameters,queryStatus} from '../web/src/wms/documentQuery.js';

process.env.TZ='Asia/Shanghai';

test('date shortcuts include today and cross month and leap-day boundaries',()=>{
 assert.deepEqual(dateRange(1,new Date(2026,9,16)),{from:'2026-10-16',to:'2026-10-16'});
 assert.deepEqual(dateRange(7,new Date(2026,0,3)),{from:'2025-12-28',to:'2026-01-03'});
 assert.deepEqual(dateRange(3,new Date(2024,2,1)),{from:'2024-02-28',to:'2024-03-01'});
 assert.deepEqual(dateRange(0),{from:'',to:''});
});
test('date filters use local midnight through exclusive next midnight and encode keywords',()=>{
 const params=new URLSearchParams(queryParameters('INBOUND',{from:'2026-10-16',to:'2026-10-16',keyword:'轴承 & 20mm',status:'COMPLETED'},2,10));
 assert.equal(params.get('created_from'),'2026-10-15T16:00:00.000Z');
 assert.equal(params.get('created_before'),'2026-10-16T16:00:00.000Z');
 assert.equal(params.get('keyword'),'轴承 & 20mm');assert.equal(params.get('page'),'3');
 assert.equal(params.get('status'),'COMPLETED');
});
test('invalid date ranges cannot silently query a different day',()=>{
 for(const range of [{from:'2026-10-17',to:'2026-10-16'},{from:'2026-02-30',to:''},{from:'garbage',to:''}])assert.throws(()=>queryParameters('TASK',{...range,keyword:'',status:''},0,10));
 const all=new URLSearchParams(queryParameters('TASK',{from:'',to:'',keyword:'',status:''},0,5));
 assert.equal(all.has('created_from'),false);assert.equal(all.has('created_before'),false);
 assert.equal(queryStatus({record_type:'TASK',status:'QUEUED'}),'待下发');
 assert.equal(queryStatus({record_type:'COUNT',status:'WAIT_COUNT'}),'待盘点核对');
});
