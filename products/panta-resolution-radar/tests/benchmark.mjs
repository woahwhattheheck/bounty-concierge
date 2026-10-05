/** Pure local projection benchmark; not provider latency or a production load test. */
import {performance} from 'node:perf_hooks';
import {cpus} from 'node:os';
import {selectMarkets, summary} from '../public/model.mjs';
const now = 1791154800;
const rows = Array.from({length: 10000}, (_, i) => ({marketId: `fixture-${i}`,
  title: `Synthetic market ${i}`, category: ['science', 'world', 'sports'][i % 3],
  phase: i % 11 === 0 ? 'resolved' : i % 17 === 0 ? 'cancelled' : 'primary',
  resolutionTime: i % 19 === 0 ? null : now - 172800 + ((i * 7919) % 691200)}));
const samples = [];
for (let i = 0; i < 35; i++) {
  const start = performance.now();
  const selected = selectMarkets(rows, {}, now);
  summary(selected, now);
  const elapsed = performance.now() - start;
  if (selected.length !== rows.length) throw new Error('Benchmark lost rows.');
  if (i >= 5) samples.push(elapsed);
}
samples.sort((a,b) => a-b);
console.log(JSON.stringify({workload: 'filter + full schedule sort + summary on 10,000 synthetic rows',
  samples: samples.length, warmups: 5, medianMs: Number(samples[15].toFixed(3)),
  p95Ms: Number(samples[28].toFixed(3)), node: process.version, platform: process.platform,
  cpu: cpus()[0]?.model, upstreamRequests: 0, domRenderingMeasured: false,
  limitations: 'Single local process; no real API, network, DOM render or fleet throughput measurement.'}, null, 2));
