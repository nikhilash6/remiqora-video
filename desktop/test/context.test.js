'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { makeContext } = require('../src/context');

const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'install', 'manifest.json'), 'utf8'));

test('the H3 licence tick reaches the context (so --accept-h3 is passed)', () => {
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'rqctx-'));
  const base = { dataRoot, sets: ['h3'] };
  const opts = { manifest, resources: {}, platform: 'win32' };
  assert.strictEqual(makeContext({ ...base, h3Accepted: true }, opts).h3Accepted, true);
  assert.strictEqual(makeContext(base, opts).h3Accepted, false);
});
