'use strict';
// Deterministic collaborator-contract check of the actual route callback.
// No server, network, PostgreSQL, or live organization data is used.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
let ts;
try { ts = require('typescript'); }
catch { ts = require(require.resolve('typescript', { paths: [process.cwd()] })); }

const col = (table, name) => Object.freeze({ kind: 'column', table, name });
const table = (name, columns) => Object.assign({ $name: name }, Object.fromEntries(columns.map(c => [c, col(name, c)])));
const member = table('member', ['id', 'userId', 'organizationId', 'teamId']);
const team = table('team', ['id', 'organizationId', 'memberCount', 'maxMembers']);
const teamMember = table('teamMember', ['id', 'userId', 'teamId', 'membershipKey']);
const value = (v, row) => v?.kind === 'column' ? row[v.table]?.[v.name] : v;
const eq = (a, b) => row => value(a, row) === value(b, row);
const and = (...predicates) => row => predicates.every(predicate => predicate(row));
function sql(strings, ...values) {
  const form = strings.join('$');
  if (form === '$ < $') return row => value(values[0], row) < value(values[1], row);
  if (form === '$ + 1') return row => value(values[0], row) + 1;
  if (form === 'GREATEST($ - 1, 0)') return row => Math.max(value(values[0], row) - 1, 0);
  throw new Error('Unimplemented SQL expression in harness: ' + form);
}
const project = (fields, env) => Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, value(v, env)]));
class TRPCError extends Error { constructor({code, message}) { super(message); this.code = code; } }
class Model {
  constructor(readBarrier = 0) {
    this.state = {
      member: [
        { id: 'm-alice', userId: 'alice', organizationId: 'org', teamId: 'A' },
        { id: 'm-charlie', userId: 'charlie', organizationId: 'org', teamId: 'A' },
        { id: 'm-outsider', userId: 'outsider', organizationId: 'other', teamId: null },
      ],
      team: [
        { id: 'A', organizationId: 'org', memberCount: 2, maxMembers: 2 },
        { id: 'B', organizationId: 'org', memberCount: 0, maxMembers: 2 },
        { id: 'C', organizationId: 'org', memberCount: 0, maxMembers: 2 },
        { id: 'D', organizationId: 'other', memberCount: 0, maxMembers: 2 },
      ],
      teamMember: [
        { id: 'a-alice', teamId: 'A', userId: 'alice', membershipKey: 'A:alice' },
        { id: 'a-charlie', teamId: 'A', userId: 'charlie', membershipKey: 'A:charlie' },
      ],
    };
    this.trace = []; this.audit = []; this.id = 0; this.txId = 0;
    this.tail = Promise.resolve(); this.reads = 0; this.readBarrier = readBarrier;
    this.barrier = new Promise(resolve => { this.releaseReads = resolve; });
    this.db = this.session(null);
    this.db.transaction = callback => this.transaction(callback);
  }
  session(tx) {
    const query = name => ({findFirst: async ({where}) => {
      this.trace.push({tx, operation: 'findFirst', table: name});
      return structuredClone(this.state[name].find(row => where({[name]: row})));
    }});
    return {
      query: {member: query('member'), team: query('team')},
      select: fields => new Query(this, tx, 'select', fields),
      delete: table => new Query(this, tx, 'delete').from(table),
      update: table => new Query(this, tx, 'update').from(table),
      insert: table => ({values: async input => {
        assert.notEqual(tx, null, 'writes must stay inside transaction');
        if (table === teamMember && this.state.teamMember.some(row => row.membershipKey === input.membershipKey)) {
          throw new Error('membershipKey unique constraint');
        }
        this.state[table.$name].push(structuredClone(input));
        this.trace.push({tx, operation: 'insert', table: table.$name});
      }}),
    };
  }
  async transaction(callback) {
    // A legal interleaving: both old membership reads complete BEFORE two
    // transactions run serially. Serialization alone cannot repair stale input.
    const preceding = this.tail;
    let release;
    this.tail = new Promise(resolve => { release = resolve; });
    await preceding;
    const tx = ++this.txId;
    const snapshot = structuredClone(this.state);
    this.trace.push({tx, operation: 'begin'});
    try {
      const result = await callback(this.session(tx));
      this.trace.push({tx, operation: 'commit'});
      return result;
    } catch (error) {
      this.state = snapshot;
      this.trace.push({tx, operation: 'rollback'});
      throw error;
    } finally { release(); }
  }
  async outsideMembershipBarrier() {
    if (!this.readBarrier) return;
    if (++this.reads === this.readBarrier) this.releaseReads();
    await this.barrier;
  }
  snapshot() {
    return {
      memberships: this.state.teamMember.filter(row => row.userId === 'alice').map(row => row.teamId).sort(),
      memberTeam: this.state.member.find(row => row.userId === 'alice')?.teamId,
      counters: Object.fromEntries(this.state.team.map(row => [row.id, row.memberCount])),
    };
  }
}
class Query {
  constructor(model, tx, operation, fields) { Object.assign(this, {model, tx, operation, fields}); }
  from(table) { this.table = table; return this; }
  innerJoin(table, predicate) { this.join = {table, predicate}; return this; }
  where(predicate) { this.predicate = predicate; return this; }
  set(changes) { this.changes = changes; return this; }
  returning(fields) { this.returnedFields = fields; return this; }
  for(lock) { assert.equal(lock, 'update'); assert.notEqual(this.tx, null); this.lock = lock; return this; }
  then(resolve, reject) { this.promise ??= this.execute(); return this.promise.then(resolve, reject); }
  async execute() {
    const name = this.table.$name;
    const {model, tx} = this;
    let rows = model.state[name].map(row => ({[name]: row}));
    if (this.join) rows = rows.flatMap(env => model.state[this.join.table.$name]
      .map(row => ({...env, [this.join.table.$name]: row})).filter(this.join.predicate));
    rows = rows.filter(this.predicate || (() => true));
    model.trace.push({tx, operation: this.operation, table: name, lock: this.lock || null, count: rows.length});
    if (this.operation === 'select') {
      const result = rows.map(row => project(this.fields, row));
      if (name === 'teamMember' && tx === null) await model.outsideMembershipBarrier();
      return structuredClone(result);
    }
    assert.notEqual(tx, null, 'writes must stay inside transaction');
    if (this.operation === 'delete') {
      const removed = new Set(rows.map(row => row[name]));
      model.state[name] = model.state[name].filter(row => !removed.has(row));
    } else if (this.operation === 'update') {
      for (const row of rows) {
        const changes = Object.fromEntries(Object.entries(this.changes).map(([key, v]) => [key, typeof v === 'function' ? v(row) : v]));
        Object.assign(row[name], changes);
      }
    } else throw new Error('Unimplemented query operation: ' + this.operation);
    return this.returnedFields ? rows.map(row => project(this.returnedFields, row)) : [];
  }
}
function compile(sourcePath) {
  const source = fs.readFileSync(sourcePath, 'utf8');
  const wrapped = `const router = {\n${source}\n};\nrouter.moveMemberToTeam;`;
  const compiled = ts.transpileModule(wrapped, {
    reportDiagnostics: true,
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  });
  const errors = (compiled.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
  assert.equal(errors.length, 0, errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')).join('\n'));
  return model => {
    const scalar = { min() { return this; }, nullable() { return this; } };
    return vm.runInNewContext(compiled.outputText, {
      withPermission: (resource, permission) => {
        assert.equal(resource, 'team'); assert.equal(permission, 'update');
        return { input() { return this; }, mutation(fn) { return fn; } };
      },
      z: {object: x => x, string: () => scalar},
      db: model.db, member, team, teamMember, and, eq, sql, TRPCError,
      nanoid: () => 'generated-' + (++model.id),
      audit: async (_ctx, event) => { model.audit.push(structuredClone(event)); },
    }, {timeout: 1000, filename: path.basename(sourcePath)});
  };
}
const call = (handler, teamId, memberId = 'm-alice') => handler({ctx: {session: {activeOrganizationId: 'org'}}, input: {teamId, memberId}});
function consistent(model) {
  for (const row of model.state.team) {
    assert.equal(row.memberCount, model.state.teamMember.filter(m => m.teamId === row.id).length, `counter for team ${row.id}`);
    assert.ok(row.memberCount <= row.maxMembers, `quota for team ${row.id}`);
  }
  for (const m of model.state.member.filter(m => m.organizationId === 'org')) {
    const ids = model.state.teamMember.filter(tm => tm.userId === m.userId).map(tm => tm.teamId).sort();
    assert.deepEqual(ids, m.teamId === null ? [] : [m.teamId], `move consistency for ${m.userId}`);
  }
}
function lockedBeforeReads(model) {
  const reads = model.trace.filter(x => x.operation === 'select' && x.table === 'teamMember');
  assert.ok(reads.length > 0);
  for (const read of reads) {
    assert.notEqual(read.tx, null, 'membership read outside transaction');
    const before = model.trace.slice(0, model.trace.indexOf(read));
    assert.ok(before.some(x => x.tx === read.tx && x.table === 'member' && x.operation === 'select' && x.lock === 'update' && x.count === 1), 'member row was not locked before reading membership');
  }
}
async function main() {
  const before = compile(process.argv[2] || path.join(__dirname, 'move.before.ts'));
  const after = compile(process.argv[3] || path.join(__dirname, 'move.after.ts'));
  const baseline = new Model(2), old = before(baseline);
  await Promise.all([call(old, 'B'), call(old, 'C')]);
  assert.deepEqual(baseline.snapshot(), {memberships: ['B', 'C'], memberTeam: 'C', counters: {A: 0, B: 1, C: 1, D: 0}});
  assert.throws(() => consistent(baseline), /counter for team A/);
  console.log('BASELINE REPRODUCED', JSON.stringify(baseline.snapshot()));
  const tests = [];
  async function test(name, fn) { await fn(); tests.push(name); console.log('PASS', name); }
  await test('concurrent moves see committed memberships after member lock', async () => {
    const model = new Model(2), handler = after(model);
    await Promise.all([call(handler, 'B'), call(handler, 'C')]);
    consistent(model); lockedBeforeReads(model);
    assert.deepEqual(model.snapshot(), {memberships: ['C'], memberTeam: 'C', counters: {A: 1, B: 0, C: 1, D: 0}});
    assert.equal(model.audit.length, 2);
    console.log('REPAIRED RESULT', JSON.stringify(model.snapshot()));
  });
  await test('full destination rolls back removals and counters', async () => {
    const model = new Model(); model.state.team.find(t => t.id === 'B').maxMembers = 0;
    const start = structuredClone(model.state);
    await assert.rejects(call(after(model), 'B'), {code: 'BAD_REQUEST'});
    assert.deepEqual(model.state, start); assert.equal(model.audit.length, 0);
  });
  await test('foreign member cannot be moved', async () => {
    const model = new Model(), start = structuredClone(model.state);
    await assert.rejects(call(after(model), 'B', 'm-outsider'), {code: 'NOT_FOUND'});
    assert.deepEqual(model.state, start); assert.equal(model.audit.length, 0);
  });
  await test('foreign destination cannot receive a member', async () => {
    const model = new Model(), start = structuredClone(model.state);
    await assert.rejects(call(after(model), 'D'), {code: 'NOT_FOUND'});
    assert.deepEqual(model.state, start); assert.equal(model.audit.length, 0);
  });
  await test('unassignment keeps unrelated members and counters intact', async () => {
    const model = new Model(); await call(after(model), null);
    consistent(model); lockedBeforeReads(model);
    assert.deepEqual(model.snapshot().memberships, []); assert.equal(model.audit[0].resourceId, 'alice');
    assert.equal(model.audit[0].metadata.teamId, null);
  });
  await test('moving to the same full team does not consume extra capacity', async () => {
    const model = new Model(); await call(after(model), 'A');
    consistent(model); lockedBeforeReads(model);
    assert.equal(model.state.team.find(t => t.id === 'A').memberCount, 2);
  });
  await test('existing destination quota still rejects a second member', async () => {
    const model = new Model(); model.state.team.find(t => t.id === 'B').maxMembers = 1;
    const handler = after(model);
    const results = await Promise.allSettled([call(handler, 'B'), call(handler, 'B', 'm-charlie')]);
    assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
    assert.equal(results.filter(r => r.status === 'rejected' && r.reason.code === 'BAD_REQUEST').length, 1);
    consistent(model); lockedBeforeReads(model);
  });
  console.log(JSON.stringify({baseline: 'reproduced', candidateChecks: tests.length, node: process.version, typescript: ts.version, scope: 'actual route callback; deterministic in-memory DB collaborators; not PostgreSQL or full-app validation'}));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
