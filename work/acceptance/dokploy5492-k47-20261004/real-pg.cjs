'use strict';
// KESTREL-47: one retained-source PostgreSQL acceptance proof, not an app test suite.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const ts = require('typescript');
const postgres = require('postgres');
const orm = require('drizzle-orm');
const core = require('drizzle-orm/pg-core');
const { drizzle } = require('drizzle-orm/postgres-js');
const { nanoid } = require('nanoid');
const { TRPCError } = require('@trpc/server');
const target = path.resolve(process.env.DOKPLOY_ROOT || 'dokploy');
const packet = path.resolve(process.env.PACKET_ROOT || 'packet');
const output = path.resolve(process.env.PROOF_OUTPUT || 'evidence');
fs.mkdirSync(output, { recursive: true });
const url = new URL(process.env.DATABASE_URL || '');
assert(['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), 'Only a loopback disposable PostgreSQL service is permitted');
assert.equal(url.pathname, '/dokploy5492_acceptance', 'Refusing a non-proof database');
const blob = bytes => crypto.createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
const readPinned = (file, expected) => {
  const bytes = fs.readFileSync(file);
  assert.equal(blob(bytes), expected, `Source guard failed: ${file}`);
  return bytes.toString('utf8');
};
const account = readPinned(path.join(target, 'packages/server/src/db/schema/account.ts'), '946223f39b502ab7dd6fa81be1d412eecda7dffc');
const before = readPinned(path.join(packet, 'move.before.ts'), '03b1736e3e3596c72ba3235fde73c27a8025d9a3');
const after = readPinned(path.join(packet, 'move.after.ts'), '46ac7ceeba936fe33d653fcbbda349ebdea80d2e');
readPinned(path.join(packet, 'move-rowlock.patch'), '1b5f240adffb4af20f88403cddffeb4c3670073f');
const original = readPinned(path.join(output, 'organization.before.ts'), '2cf8009b62e87eafb5233dd45c3e42524575b2d4');
const patched = fs.readFileSync(path.join(target, 'apps/dokploy/server/api/routers/organization.ts'), 'utf8');
assert(original.includes(before), 'Before callback is not an exact full-file excerpt');
assert(patched.includes(after), 'After callback is not the applied full-file patch');
const compile = source => {
  const result = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }, reportDiagnostics: true });
  const errors = (result.diagnostics || []).filter(x => x.category === ts.DiagnosticCategory.Error);
  assert.equal(errors.length, 0, ts.formatDiagnosticsWithColorAndContext(errors, { getCurrentDirectory: () => '.', getCanonicalFileName: x => x, getNewLine: () => '\n' }));
  return result.outputText;
};
// Use the exact four production table declarations. Only the unrelated parent user
// table is reduced to its referenced primary key; no account/authorization proof.
const names = ['organization', 'team', 'teamMember', 'member'];
const ast = ts.createSourceFile('account.ts', account, ts.ScriptTarget.Latest, true);
const statements = names.map(name => {
  const found = ast.statements.filter(s => ts.isVariableStatement(s) && s.declarationList.declarations.some(d => ts.isIdentifier(d.name) && d.name.text === name));
  assert.equal(found.length, 1, `Expected one production declaration for ${name}`);
  return found[0].getText(ast);
});
const user = core.pgTable('user', { id: core.text('id').primaryKey() });
const schema = { exports: {}, ...core, ...orm, nanoid, user, Date };
vm.runInNewContext(compile(statements.join('\n')), schema, { filename: 'production-account-tables.cjs', timeout: 1000 });
const tables = { user, ...schema.exports };
const q = name => '"' + name.replaceAll('"', '""') + '"';
const dialect = new core.PgDialect();
function defaultSql(value) {
  if (value instanceof orm.SQL) {
    const compiled = dialect.sqlToQuery(value);
    assert.equal(compiled.params.length, 0, 'Unexpected parameterized schema default');
    return compiled.sql;
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return "'" + value.replaceAll("'", "''") + "'";
  throw new Error('Unsupported production schema default');
}
// DDL is derived from those Drizzle declarations, including actual constraints.
const ddl = Object.values(tables).map(table => {
  const cfg = core.getTableConfig(table);
  const columns = cfg.columns.map(c => [q(c.name), c.getSQLType(), c.notNull ? 'NOT NULL' : '', c.primary ? 'PRIMARY KEY' : '', c.isUnique ? 'UNIQUE' : '', c.default === undefined ? '' : 'DEFAULT ' + defaultSql(c.default)].filter(Boolean).join(' '));
  for (const fk of cfg.foreignKeys) {
    const r = fk.reference();
    columns.push(`FOREIGN KEY (${r.columns.map(c => q(c.name)).join(',')}) REFERENCES ${q(orm.getTableName(r.foreignTable))} (${r.foreignColumns.map(c => q(c.name)).join(',')}) ON DELETE ${fk.onDelete || 'no action'} ON UPDATE ${fk.onUpdate || 'no action'}`);
  }
  return `CREATE TABLE ${q(cfg.name)} (${columns.join(',\n')});`;
}).join('\n');
fs.writeFileSync(path.join(output, 'schema.sql'), ddl);
function callback(excerpt, db, audits) {
  const file = ts.createSourceFile('route.ts', `const routes = ({\n${excerpt}\n});`, ts.ScriptTarget.Latest, true);
  let arrow;
  const walk = node => {
    if (ts.isPropertyAssignment(node) && node.name.getText(file) === 'moveMemberToTeam') {
      assert(ts.isCallExpression(node.initializer));
      assert.equal(node.initializer.expression.name.text, 'mutation');
      [arrow] = node.initializer.arguments;
    }
    ts.forEachChild(node, walk);
  };
  walk(file);
  assert(arrow && ts.isArrowFunction(arrow), 'Actual mutation callback not found');
  return vm.runInNewContext(compile(`(${arrow.getText(file)})`), { db, ...tables, ...orm, TRPCError, nanoid, Date, audit: async (_ctx, event) => { audits.push(event); } }, { timeout: 1000 });
}
const namespace = `proof_k47_${process.pid}`;
const admin = postgres(url.href, { max: 1, onnotice: () => {} });
let control, worker;
const report = { source: '06523165940dfd61d48fdb7977a39bcafa95f03b', packet: 'f5d7b088a038311f912b45e880a5a427646096c0', originalBlob: blob(Buffer.from(original)), patchedBlob: blob(Buffer.from(patched)), node: process.version, typescript: ts.version, cases: [] };
const emit = value => { console.log(JSON.stringify(value)); };
const ctx = { session: { activeOrganizationId: 'org' } };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let db;
async function seed() {
  await control.unsafe('TRUNCATE team_member, member, team, organization, "user" CASCADE');
  await db.insert(user).values(['owner', 'u', 'keeper', 'full'].map(id => ({ id })));
  await db.insert(tables.organization).values({ id: 'org', name: 'synthetic', ownerId: 'owner', createdAt: new Date() });
  await db.insert(tables.team).values([
    { id: 'A', name: 'A', organizationId: 'org', memberCount: 2, maxMembers: 2 },
    { id: 'B', name: 'B', organizationId: 'org', memberCount: 0, maxMembers: 1 },
    { id: 'C', name: 'C', organizationId: 'org', memberCount: 0, maxMembers: 1 },
    { id: 'Q', name: 'Q', organizationId: 'org', memberCount: 1, maxMembers: 1 },
  ]);
  await db.insert(tables.member).values([['m', 'u', 'A'], ['k', 'keeper', 'A'], ['f', 'full', 'Q']].map(([id, userId, teamId]) => ({ id, userId, teamId, organizationId: 'org', role: 'member', createdAt: new Date() })));
  await db.insert(tables.teamMember).values([['tm-u', 'u', 'A'], ['tm-keeper', 'keeper', 'A'], ['tm-full', 'full', 'Q']].map(([id, userId, teamId]) => ({ id, userId, teamId, membershipKey: `${teamId}:${userId}`, createdAt: new Date() })));
}
async function snapshot() {
  const members = await control.unsafe('SELECT id, user_id, team_id FROM member ORDER BY id');
  const memberships = await control.unsafe('SELECT id, user_id, team_id FROM team_member ORDER BY user_id, team_id');
  const counts = await control.unsafe('SELECT t.id, t.member_count AS stored, count(tm.id)::int AS actual FROM team t LEFT JOIN team_member tm ON tm.team_id=t.id GROUP BY t.id ORDER BY t.id');
  return JSON.parse(JSON.stringify({ members, memberships, counts }));
}
async function waitForTwoLocks() {
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    const rows = await control.unsafe("SELECT pid, wait_event_type, wait_event, query FROM pg_stat_activity WHERE datname=current_database() AND application_name='k47-route' AND wait_event_type='Lock'");
    if (rows.length === 2) return JSON.parse(JSON.stringify(rows));
    await sleep(25);
  }
  throw new Error('Could not observe both real transactions waiting on PostgreSQL locks');
}
async function race(label, excerpt, fixed) {
  await seed();
  const audits = [];
  const move = callback(excerpt, db, audits);
  let pending, blocked;
  const started = performance.now();
  // A third real transaction pins the initial membership row. Both callbacks
  // execute concurrently; no ORM methods, transaction results, or rows are mocked.
  try {
    await control.begin(async lock => {
      await lock.unsafe("SELECT id FROM team_member WHERE id='tm-u' FOR UPDATE");
      pending = Promise.allSettled(['B', 'C'].map(teamId => move({ ctx, input: { memberId: 'm', teamId } })));
      blocked = await waitForTwoLocks();
    });
  } catch (error) {
    if (pending) await pending;
    throw error;
  }
  const settled = await pending;
  assert(settled.every(r => r.status === 'fulfilled' && r.value === true), JSON.stringify(settled));
  const state = await snapshot();
  const memberships = state.memberships.filter(x => x.user_id === 'u').map(x => x.team_id);
  const pointer = state.members.find(x => x.id === 'm').team_id;
  if (fixed) {
    assert.deepEqual(memberships, [pointer]);
    assert(state.counts.every(x => x.stored === x.actual));
    assert.equal(state.counts.find(x => x.id === 'A').stored, 1);
    assert(blocked.some(x => /for update/i.test(x.query) && /member/.test(x.query)), 'No member lock wait observed');
  } else {
    assert.deepEqual(memberships, ['B', 'C']);
    assert.equal(state.counts.find(x => x.id === 'A').stored, 0);
    assert.equal(state.counts.find(x => x.id === 'A').actual, 1);
  }
  assert.equal(audits.length, 2);
  const result = { name: label, pass: true, elapsedMs: performance.now() - started, blocked, pointer, state };
  report.cases.push(result); emit(result);
}
async function main() {
  report.postgresql = (await admin.unsafe('SELECT version() AS version'))[0].version;
  await admin.unsafe(`CREATE SCHEMA ${q(namespace)}`);
  const options = { search_path: namespace, statement_timeout: 12000, lock_timeout: 8000 };
  control = postgres(url.href, { max: 2, connection: { ...options, application_name: 'k47-control' }, onnotice: () => {} });
  worker = postgres(url.href, { max: 2, connection: { ...options, application_name: 'k47-route' }, onnotice: () => {} });
  db = drizzle(worker, { schema: tables });
  report.isolation = (await control.unsafe('SHOW transaction_isolation'))[0].transaction_isolation;
  assert.equal(report.isolation, 'read committed');
  await control.unsafe(ddl);
  await race('before-real-concurrency-reproduces-corruption', before, false);
  await race('after-real-concurrency-preserves-membership-and-counts', after, true);
  await seed();
  const initial = await snapshot();
  const audits = [];
  const move = callback(after, db, audits);
  await assert.rejects(move({ ctx, input: { memberId: 'm', teamId: 'Q' } }), error => error.code === 'BAD_REQUEST' && error.message === 'Team member limit reached');
  assert.deepEqual(await snapshot(), initial);
  assert.equal(audits.length, 0);
  report.cases.push({ name: 'full-destination-rolls-back-all-membership-and-counter-writes', pass: true });
  emit(report.cases.at(-1));
  await seed();
  const capacity = await Promise.allSettled(['m', 'k'].map(memberId => move({ ctx, input: { memberId, teamId: 'B' } })));
  assert.equal(capacity.filter(x => x.status === 'fulfilled').length, 1);
  assert.equal(capacity.filter(x => x.status === 'rejected' && x.reason.code === 'BAD_REQUEST').length, 1);
  const final = await snapshot();
  assert(final.counts.every(x => x.stored === x.actual));
  assert.equal(final.counts.find(x => x.id === 'B').stored, 1);
  report.cases.push({ name: 'two-members-one-free-slot-accepts-one-and-rolls-back-the-other', pass: true, state: final });
  emit(report.cases.at(-1));
  report.pass = true;
}
main().catch(error => {
  report.pass = false;
  report.error = { name: error.name, message: error.message, stack: error.stack };
  console.error(error); process.exitCode = 1;
}).finally(async () => {
  try {
    if (worker) await worker.end({ timeout: 2 });
    if (control) await control.end({ timeout: 2 });
    await admin.unsafe(`DROP SCHEMA IF EXISTS ${q(namespace)} CASCADE`);
    await admin.end({ timeout: 2 });
  } catch (error) { report.cleanupError = error.message; process.exitCode = 1; }
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  emit({ pass: report.pass === true && !report.cleanupError, cases: report.cases.length, postgresql: report.postgresql, isolation: report.isolation, source: report.source, patchedBlob: report.patchedBlob });
});
