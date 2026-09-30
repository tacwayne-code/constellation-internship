// Exercise the page's non-motion diagnostic with a mocked HTTP boundary.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../web/physical.html'), 'utf8');
const start = html.indexOf('async function checkControlConnection()');
const end = html.indexOf("$('check-control').onclick", start);
assert.ok(start >= 0 && end > start);
const fn = html.slice(start, end);

async function run(key, status, detail, throws = false) {
  const elements = {controlkey: {value:key}, 'connection-result': {}, 'check-control': {disabled:false}};
  const calls=[];
  const context = {Date, JSON, controlAllowed:true, location:{origin:'http://192.168.1.45:8765'},
    $: id=>elements[id], fetch:async (url,options)=>{
      calls.push({url,options});
      if(throws) throw Error('Network unavailable');
      return {status,json:async()=>({detail})};
    }};
  vm.createContext(context);
  vm.runInContext(fn,context);
  await context.checkControlConnection();
  assert.equal(elements['check-control'].disabled,false);
  for(const {url,options} of calls){
    const body=JSON.parse(options.body);
    assert.equal(url,'/api/physical/command');
    assert.equal(body.site_ready,false);
    assert.equal(body.task_id,0);
    assert.equal(body.action,'task');
    assert.equal(options.headers['X-Control-Key'],'test-key');
  }
  return {text:elements['connection-result'].textContent,calls};
}

(async()=>{
  assert.equal((await run('   ')).calls.length,0);
  assert.match((await run(' test-key ',409,'请确认现场控制权和动作区域已核对')).text,/控制连接通过/);
  assert.match((await run('test-key',403,'请输入后台主机上的控制密钥')).text,/HTTP 403/);
  assert.match((await run('test-key',422,'参数错误')).text,/未通过/);
  assert.match((await run('test-key',200,'unexpected')).text,/未通过/);
  assert.match((await run('test-key',0,'',true)).text,/Network unavailable/);
  console.log('6 browser-request diagnostic cases passed; every request blocks PLC writes.');
})().catch(e=>{console.error(e);process.exitCode=1});
