import fs from 'node:fs';
import v8 from 'node:v8';

const defaultFile='/state/core-diagnostics.jsonl';
const names=new Set(['Error','TypeError','RangeError','SyntaxError','ReferenceError','AssertionError','AbortError']);
const codes=new Set(['dispatch_held','dispatch_route_denied','unbound_native_attempt','non_scalar_payload','individual_scope_denied','manual_session_expired','agenda_session_invalid','EACCES','ENOMEM','ENOSPC','ECONNREFUSED','EPIPE','EEXIST','ERR_UNHANDLED_ERROR']);
const events=new Set(['process_start','uncaught_exception','child_exit','child_fatal','process_exit']);
export function recordDiagnostic(event,fields={},file=defaultFile){
  if(!events.has(event))return;
  const row={at:new Date().toISOString(),event,pid:process.pid};
  if(Number.isInteger(fields.exitCode)||fields.exitCode===null)row.exitCode=fields.exitCode;
  if(['SIGTERM','SIGKILL','SIGABRT','SIGSEGV',null].includes(fields.signal))row.signal=fields.signal;
  if(['heap_exhausted','native_fatal'].includes(fields.fatal))row.fatal=fields.fatal;
  if(names.has(fields.error?.name))row.errorName=fields.error.name;
  const code=fields.error?.code||fields.error?.message;
  if(codes.has(code))row.errorCode=code;
  if(fields.error?.stack){
    row.frames=String(fields.error.stack).split('\n').slice(1,9).flatMap(line=>{
      const m=/(?:file:\/\/)?\/(?:legacy\/scripts|ops-app)\/([a-zA-Z0-9_.-]+\.mjs):(\d+):(\d+)/.exec(line);
      return m?[{file:m[1],line:Number(m[2]),column:Number(m[3])}]:[];
    });
  }
  const memory=process.memoryUsage();
  row.memory={rss:memory.rss,heapUsed:memory.heapUsed,heapTotal:memory.heapTotal,external:memory.external,heapLimit:v8.getHeapStatistics().heap_size_limit};
  try{fs.appendFileSync(file,JSON.stringify(row)+'\n',{mode:0o600});}catch{}
}

export function installProcessDiagnostics(file=defaultFile){
  recordDiagnostic('process_start',{},file);
  // Observe the exception without changing Node's normal fatal behavior.
  process.on('uncaughtExceptionMonitor',error=>recordDiagnostic('uncaught_exception',{error},file));
  process.once('exit',exitCode=>recordDiagnostic('process_exit',{exitCode},file));
}

export function observeChild(child,file=defaultFile){
  let tail='',recorded=false;
  child.stderr?.on('data',bytes=>{
    const sample=tail+bytes.toString('utf8');
    if(!recorded&&/FATAL ERROR|heap out of memory/.test(sample)){
      recordDiagnostic('child_fatal',{fatal:/heap out of memory|heap limit|Mark-compacts/.test(sample)?'heap_exhausted':'native_fatal'},file);
      recorded=true;
    }
    // Discard all unstructured output; retain only enough bytes to detect a split signature.
    tail=sample.slice(-160);
  });
  child.once('exit',(exitCode,signal)=>recordDiagnostic('child_exit',{exitCode,signal},file));
}
