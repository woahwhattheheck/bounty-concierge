import { createHash } from 'node:crypto';
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

// Deliberately tiny synthetic corpus. This is a publication-format prototype,
// not the production archive implementation for grydlock-testkit #66.
const FIXTURES = {
  destinations: {
    destinations: [
      {
        id: 'GPROTOCLEAN',
        type: 'account',
        label: 'clean',
        notes: 'prototype clean row',
        risk_pattern: 'none',
        fixture_status: 'synthetic-only',
      },
      {
        id: 'GPROTOMAL',
        type: 'account',
        label: 'malicious',
        notes: 'prototype malicious row',
        risk_pattern: 'sweep',
        fixture_status: 'synthetic-only',
      },
    ],
  },
  scores: { GPROTOCLEAN: 4, GPROTOMAL: 92 },
};

const SOURCE = {
  repository: 'Gryd-lock/grydlock-testkit',
  commit: '7064404d6e7c44df1f980d532d23901651d79426',
};

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function json(value) {
  return JSON.stringify(value, null, 2) + '\n';
}

function write(path, value) {
  writeFileSync(path, value, 'utf8');
}

function manifestFor(root, files, datasetVersion) {
  return {
    schema: 'grydlock-fixture-prototype/v1',
    dataset_version: datasetVersion,
    source: SOURCE,
    files: files.map((name) => {
      const bytes = readFileSync(join(root, name));
      return {
        path: name,
        bytes: bytes.length,
        sha256: sha256(bytes),
      };
    }),
  };
}

function verifyManifest(root, manifest, expectedVersion) {
  if (manifest.schema !== 'grydlock-fixture-prototype/v1') {
    throw new Error(`UNSUPPORTED_MANIFEST_SCHEMA:${manifest.schema}`);
  }

  if (expectedVersion && manifest.dataset_version !== expectedVersion) {
    throw new Error(
      `UNSUPPORTED_DATASET_VERSION:${manifest.dataset_version}:expected=${expectedVersion}:migrate=select-compatible-release`,
    );
  }

  for (const file of manifest.files) {
    const bytes = readFileSync(join(root, file.path));
    const digest = sha256(bytes);
    if (bytes.length !== file.bytes || digest !== file.sha256) {
      throw new Error(`ARTIFACT_INTEGRITY_MISMATCH:${file.path}`);
    }
  }

  return true;
}

function sizeTree(root) {
  let bytes = 0;
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else bytes += statSync(path).size;
    }
  };
  walk(root);
  return bytes;
}

function buildReleasePrototype(root) {
  mkdirSync(join(root, 'data'), { recursive: true });
  write(join(root, 'data/destinations.json'), json(FIXTURES.destinations));
  write(join(root, 'data/scores.json'), json(FIXTURES.scores));

  const manifest = manifestFor(
    root,
    ['data/destinations.json', 'data/scores.json'],
    '0.1.0',
  );
  write(join(root, 'manifest.json'), json(manifest));
  return manifest;
}

function buildPackagePrototype(root) {
  mkdirSync(join(root, 'data'), { recursive: true });
  mkdirSync(join(root, 'generated'), { recursive: true });

  write(join(root, 'data/destinations.json'), json(FIXTURES.destinations));
  write(join(root, 'data/scores.json'), json(FIXTURES.scores));
  write(
    join(root, 'generated/destinations.mjs'),
    `export default ${JSON.stringify(FIXTURES.destinations)};\n`,
  );
  write(
    join(root, 'generated/scores.mjs'),
    `export default ${JSON.stringify(FIXTURES.scores)};\n`,
  );
  write(
    join(root, 'index.mjs'),
    [
      "export { default as destinations } from './generated/destinations.mjs';",
      "export { default as scores } from './generated/scores.mjs';",
      '',
    ].join('\n'),
  );

  const manifest = manifestFor(
    root,
    [
      'data/destinations.json',
      'data/scores.json',
      'generated/destinations.mjs',
      'generated/scores.mjs',
    ],
    '0.1.0',
  );
  write(join(root, 'manifest.json'), json(manifest));

  write(
    join(root, 'package.json'),
    json({
      name: '@gryd-lock/testkit-data-prototype',
      version: '0.1.0',
      private: false,
      type: 'module',
      exports: {
        '.': './index.mjs',
        './manifest.json': './manifest.json',
        './data/destinations.json': './data/destinations.json',
        './data/scores.json': './data/scores.json',
      },
      files: ['index.mjs', 'data', 'generated', 'manifest.json'],
    }),
  );

  return manifest;
}

const work = mkdtempSync(join(tmpdir(), 'gryd71-'));
const releaseRoot = join(work, 'release');
const packageRoot = join(work, 'package');
mkdirSync(releaseRoot);
mkdirSync(packageRoot);

// Prototype A: content-addressed release directory.
const releaseManifest = buildReleasePrototype(releaseRoot);
verifyManifest(releaseRoot, releaseManifest, '0.1.0');
const releaseBytes = sizeTree(releaseRoot);

const tampered = join(work, 'tampered');
cpSync(releaseRoot, tampered, { recursive: true });
write(
  join(tampered, 'data/scores.json'),
  json({ ...FIXTURES.scores, GPROTOMAL: 1 }),
);

let tamperFailure = '';
try {
  verifyManifest(
    tampered,
    JSON.parse(readFileSync(join(tampered, 'manifest.json'), 'utf8')),
    '0.1.0',
  );
} catch (error) {
  tamperFailure = error.message;
}
if (!tamperFailure.startsWith('ARTIFACT_INTEGRITY_MISMATCH:')) {
  throw new Error('tamper test did not fail closed');
}

let unsupportedFailure = '';
try {
  verifyManifest(releaseRoot, releaseManifest, '9.9.9');
} catch (error) {
  unsupportedFailure = error.message;
}
if (!unsupportedFailure.startsWith('UNSUPPORTED_DATASET_VERSION:')) {
  throw new Error('version test did not fail closed');
}

// Rollback demonstration: the archived original remains independently verifiable.
verifyManifest(releaseRoot, releaseManifest, '0.1.0');

// Prototype B: installable package-shaped projection.
const packageManifest = buildPackagePrototype(packageRoot);
verifyManifest(packageRoot, packageManifest, '0.1.0');
const packageUnpackedBytes = sizeTree(packageRoot);

const packJson = JSON.parse(
  execFileSync('npm', ['pack', '--json', '--ignore-scripts'], {
    cwd: packageRoot,
    encoding: 'utf8',
  }),
);
const pack = packJson[0];
const tarPath = join(packageRoot, pack.filename);

const installRoot = join(work, 'consumer');
mkdirSync(installRoot);
write(join(installRoot, 'package.json'), json({ private: true, type: 'module' }));
execFileSync(
  'npm',
  ['install', '--ignore-scripts', '--no-audit', '--no-fund', tarPath],
  { cwd: installRoot, stdio: 'pipe' },
);

const installedRoot = join(
  installRoot,
  'node_modules/@gryd-lock/testkit-data-prototype',
);
const mod = await import(pathToFileURL(join(installedRoot, 'index.mjs')).href);
if (mod.destinations.destinations.length !== 2 || mod.scores.GPROTOMAL !== 92) {
  throw new Error('installed package consumer mismatch');
}

const installedManifest = JSON.parse(
  readFileSync(join(installedRoot, 'manifest.json'), 'utf8'),
);
verifyManifest(installedRoot, installedManifest, '0.1.0');

const result = {
  source_pin: SOURCE,
  release_prototype: {
    verification: 'PASS',
    rollback_reverify: 'PASS',
    tamper_failure: tamperFailure,
    unsupported_version_failure: unsupportedFailure,
    directory_bytes: releaseBytes,
    artifact_count: releaseManifest.files.length,
  },
  package_prototype: {
    npm_pack: 'PASS',
    local_install: 'PASS',
    consumer_import: 'PASS',
    installed_manifest_verify: 'PASS',
    tarball_filename: pack.filename,
    tarball_bytes: pack.size,
    unpacked_bytes_reported_by_npm: pack.unpackedSize,
    local_source_tree_bytes_before_tarball: packageUnpackedBytes,
    npm_tarball_integrity: pack.integrity,
    npm_tarball_shasum: pack.shasum,
    artifact_count_in_manifest: packageManifest.files.length,
  },
  interpretation: {
    content_addressed_release:
      'strong immutable-byte verification and trivial rollback; consumer install ergonomics are manual unless wrapped',
    package:
      'best install/import ergonomics and registry-style tarball integrity; generated browser modules duplicate raw fixture bytes and add publication machinery',
  },
};

console.log(JSON.stringify(result, null, 2));
rmSync(work, { recursive: true, force: true });
