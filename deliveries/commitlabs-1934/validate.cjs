const fs = require('node:fs'), fsp = require('node:fs/promises'), os = require('node:os'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict'), ts = require('typescript');
const root = __dirname;
function load(source, io, logs, cwd) {
  const module = {exports:{}};
  const result = ts.transpileModule(source,{reportDiagnostics:true,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}});
  assert.equal((result.diagnostics||[]).filter(x=>x.category===ts.DiagnosticCategory.Error).length,0);
  const localRequire = name => name==='fs/promises'?io:name==='./logger'?{logError:(...args)=>logs.push(args)}:require(name);
  vm.runInThisContext('(function(require,module,exports,process){'+result.outputText+'\n})')(localRequire,module,module.exports,{cwd:()=>cwd});
  return module.exports;
}
async function run(source) {
  const logs=[], temp=await fsp.mkdtemp(path.join(os.tmpdir(),'commitlabs1934-'));
  let injected;
  const io={...fsp, readFile:async (...args)=>{if(injected) throw injected; return fsp.readFile(...args);}};
  const api=load(source,io,logs,temp), cases=[];
  async function check(name, fn) { logs.length=0; try{await fn();cases.push({name,passed:true});}catch(err){cases.push({name,passed:false,error:err.message});} }
  try {
    await check('missing file creates independent empty data',async()=>{const first=await api.getMockData();assert.deepEqual(first,{commitments:[],attestations:[],listings:[]});first.commitments.push({id:'not-persisted'});assert.deepEqual(await api.getMockData(),{commitments:[],attestations:[],listings:[]});assert.equal(logs.length,0);});
    await fsp.writeFile(path.join(temp,'.mock-db.json'),'{"commitments":');
    await check('malformed JSON rejects',async()=>{await assert.rejects(api.getMockData(),SyntaxError);assert.deepEqual(logs,[[undefined,'Failed to read mock database']]);});
    injected=Object.assign(new Error('permission denied'),{code:'EACCES'});
    await check('permission error identity preserved',async()=>{await assert.rejects(api.getMockData(),err=>err===injected);assert.deepEqual(logs,[[undefined,'Failed to read mock database']]);});
    injected=undefined;
    await fsp.writeFile(path.join(temp,'.mock-db.json'),'{"commitments":[{"id":"retained"}]}');
    await check('valid data retained',async()=>{assert.deepEqual(await api.getMockData(),{commitments:[{id:'retained'}],attestations:[],listings:[]});assert.equal(logs.length,0);});
    return {passed:cases.filter(x=>x.passed).length,failed:cases.filter(x=>!x.passed).length,cases};
  } finally { await fsp.rm(temp,{recursive:true,force:true}); }
}
(async()=>{const candidate=fs.readFileSync(root+'/src/lib/backend/mockDb.ts','utf8'); const baseline=candidate.replace("import { logError } from './logger';\n",'').replace(/  } catch \(error: unknown\) \{[\s\S]*?    throw error;\n/,"  } catch {\n    return { ...EMPTY_MOCK_DATA };\n");const before=await run(baseline),after=await run(candidate);assert.equal(before.failed,3);assert.equal(after.failed,0);assert.equal(after.passed,4);console.log(JSON.stringify({before,after,node:process.version,typescript:ts.version,vitest_executed:false,method:'Actual production TypeScript with temporary real filesystem for missing/corrupt/valid data; permission error and logger doubled'},null,2));})().catch(err=>{console.error(err);process.exitCode=1});
