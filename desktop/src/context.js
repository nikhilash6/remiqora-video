'use strict';
const fs = require('node:fs');
const { defaultDataRoot, layout } = require('./paths');
const { setStatus, withNeeds } = require('./bootstrap/sets');

/** The context every call works with: the folders and what is picked. */
function makeContext(cfg, { manifest, resources, platform }) {
  const L = layout(cfg.dataRoot || defaultDataRoot(), cfg.modelsDir);
  const find = (cfg.find || []).filter((d) => fs.existsSync(d));
  // what is on disk already counts as picked: a later run only adds (a set is never removed by the installer)
  const status = setStatus(manifest, [L.models, ...find]);
  const picked = cfg.sets || manifest.profiles.minimum;
  const sets = withNeeds(manifest, [...picked, ...Object.keys(status).filter((id) => status[id].complete && cfg.sets)]);
  return {
    L, manifest, resources, platform, selection: { sets, find },
    skip: (process.env.REMIQORA_VIDEO_SKIP_COMPONENTS || '').split(',').map((s) => s.trim()).filter(Boolean),
    lang: cfg.lang || 'ru',
    // the MiniMax H3 licence tick: without it the downloader refuses H3's sets
    h3Accepted: !!cfg.h3Accepted,
  };
}

module.exports = { makeContext };
