import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {EventEmitter} from 'node:events';
import {recordDiagnostic,observeChild} from './diagnostics.mjs';

test('diagnostics keep technical facts and discard private strings',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'jp-diagnostic-'));t.after(()=>fs.rmSync(dir,{recursive:true}));const file=path.join(dir,'events.jsonl');
 const secret='PRIVATE-CONTENT-SHOULD-NEVER-APPEAR';
 recordDiagnostic('uncaught_exception',{error:{name:'TypeError',message:secret,stack:`TypeError: ${secret}\n    at private (file:///legacy/scripts/jp-evolution-local-api.mjs:554:9)\n    at ${secret}`},token:secret},file);
 recordDiagnostic(secret,{error:{name:secret}},file);
 const raw=fs.readFileSync(file,'utf8'),row=JSON.parse(raw.trim());
 assert.ok(!raw.includes(secret));assert.equal(row.errorName,'TypeError');assert.deepEqual(row.frames,[{file:'jp-evolution-local-api.mjs',line:554,column:9}]);assert.ok(row.memory.heapLimit>0);
 assert.equal(fs.statSync(file).mode&0o777,0o600);
});
test('fatal stderr is reduced to a code and child exit remains observable',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'jp-fatal-'));t.after(()=>fs.rmSync(dir,{recursive:true}));const file=path.join(dir,'events.jsonl');
 const child=new EventEmitter();child.stderr=new EventEmitter();observeChild(child,file);
 child.stderr.emit('data',Buffer.from('private text\nFATAL ERR'));child.stderr.emit('data',Buffer.from('OR: Reached heap limit Allocation failed - JavaScript heap out of memory\nsecret payload'));
 child.emit('exit',null,'SIGABRT');
 const raw=fs.readFileSync(file,'utf8'),rows=raw.trim().split('\n').map(JSON.parse);
 assert.equal(rows[0].fatal,'heap_exhausted');assert.equal(rows[1].signal,'SIGABRT');assert.ok(!raw.includes('private text'));assert.ok(!raw.includes('secret payload'));
});
test('exception observer records failure without swallowing or printing secrets',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'jp-exit-'));t.after(()=>fs.rmSync(dir,{recursive:true}));const file=path.join(dir,'events.jsonl');
 const script=`import {installProcessDiagnostics} from ${JSON.stringify(new URL('./diagnostics.mjs',import.meta.url).href)};installProcessDiagnostics(${JSON.stringify(file)});throw Error('fixture private');`;
 const child=spawn(process.execPath,['--input-type=module','-e',script],{stdio:'ignore'});
 const code=await new Promise(resolve=>child.once('exit',resolve));assert.notEqual(code,0);
 const raw=fs.readFileSync(file,'utf8');assert.ok(raw.includes('uncaught_exception'));assert.ok(raw.includes('process_exit'));assert.ok(!raw.includes('fixture private'));
});
