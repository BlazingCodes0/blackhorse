#!/usr/bin/env node
/**
 * BlackHorse sidecar — SignalBot bridge.
 *
 * Runs the REAL SignalBot (no reimplementation) and emits its compacted output
 * as JSON to a file. Paths are configurable via env so nothing is hardcoded:
 *   BH_SIGNALBOT_DIR  (default ~/qubitFlowBeta/qubitFlowBeta/SignalBot)
 *   BH_QUBITFLOW_DIR  (default ~/qubitFlowBeta/qubitFlowBeta, for the compactor)
 *
 * Usage:
 *   node signal.js --pair DOT/USD --strategy swing --exchange kraken --out /tmp/x.json
 *
 * Output: {"ok":true, "out":"<file>", "ms":123, "channels":[...], "pair":"..."}
 * on success; {"ok":false,"error":"..."} on failure. Never throws to stdout.
 */
const path = require('path');
const fs = require('fs');
const os = require('os');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] !== undefined ? process.argv[i + 1] : def;
}

const HOME = os.homedir();
const SIGNALBOT_DIR =
  process.env.BH_SIGNALBOT_DIR || path.join(HOME, 'qubitFlowBeta', 'qubitFlowBeta', 'SignalBot');
const QF_DIR =
  process.env.BH_QUBITFLOW_DIR || path.join(HOME, 'qubitFlowBeta', 'qubitFlowBeta');

const pair = arg('pair', 'DOT/USD');
let strategy = arg('strategy', 'swing');
const exchange = arg('exchange', 'kraken');
const out = arg('out', path.join(os.tmpdir(), `bh_signal_${Date.now()}.json`));
const started = Date.now();

function done(obj) {
  process.stdout.write(JSON.stringify(obj));
}

async function main() {
  if (!fs.existsSync(SIGNALBOT_DIR)) {
    return done({ ok: false, error: `SignalBot dir not found: ${SIGNALBOT_DIR}` });
  }
  const entry = path.join(SIGNALBOT_DIR, 'signalBots', 'range', 'sendRangeSignalBot.js');
  if (!fs.existsSync(entry)) {
    return done({ ok: false, error: `SignalBot entry not found: ${entry}` });
  }

  // Load SignalBot's own config and override pair/exchange (no hardcoding).
  const cfgPath = path.join(SIGNALBOT_DIR, 'botConfigs.json');
  let cfg = {};
  try { cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8')); } catch (_) {}
  cfg = { ...cfg, pair, exchange };
  const channelsArg = arg('channels', '');
  if (channelsArg) {
    const ivs = channelsArg.split(',').map(s => s.trim()).filter(Boolean);
    // Fill up to 3 slots; if < 3, repeat last to keep slots populated
    while (ivs.length < 3) ivs.push(ivs[ivs.length - 1] || '60m');
    cfg = { ...cfg, custom: { ripple: [ivs[0]], wave: [ivs[1]], tide: [ivs[2]] } };
    strategy = 'custom';
  }

  process.chdir(SIGNALBOT_DIR);
  const { runRangeSignalBot } = require(entry);
  const rawPath = path.join(os.tmpdir(), `bh_signal_raw_${process.pid}_${Date.now()}.json`);
  const raw = await runRangeSignalBot({ config: cfg, factor: 'range', style: strategy, outputPath: rawPath });

  // Compact using qubitFlow's own compactor when available; fall back minimally.
  let compact;
  try {
    const { compactSignalBotOutput } = require(
      path.join(QF_DIR, 'tradingBot', 'master', 'signalOutputCompactor')
    );
    compact = compactSignalBotOutput(raw);
  } catch (_e) {
    compact = raw;
  }

  // keep the MA cross series the compactor would otherwise drop
  if (raw && raw.signals) compact.signals = raw.signals;
  fs.writeFileSync(out, JSON.stringify(compact));
  try { fs.unlinkSync(rawPath); } catch (_) {}

  const channels = ['ripple', 'wave', 'tide'].filter((c) => compact[c]);
  done({
    ok: true,
    out,
    ms: Date.now() - started,
    pair,
    exchange,
    strategy,
    channels,
    bytes: Buffer.byteLength(JSON.stringify(compact)),
  });
}

main().catch((e) => done({ ok: false, error: (e && e.stack) || String(e) }));
