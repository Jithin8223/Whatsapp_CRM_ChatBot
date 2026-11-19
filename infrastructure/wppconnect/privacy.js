// Launch the unchanged pinned server through its supported initServer interface.
// Never emit upstream error objects: Axios errors can contain webhook credentials.
const fs = require('fs');
const path = require('path');
const root = '/usr/src/wpp-server';
const config = require(`${root}/dist/config`).default;
config.log = { level: 'warn', logger: ['console'] };
// The library default (60 s from the first QR) closed sessions before people could
// open WhatsApp > Linked devices and scan, forcing repeated pairing attempts.
const autoClose = Number(process.env.WPP_AUTO_CLOSE_MS);
Object.assign(config.createOptions, {
  logQR: false, disableWelcome: true, updatesLog: false,
  autoClose: Number.isFinite(autoClose) && autoClose >= 60000 ? autoClose : 180000,
});
Object.assign(config.webhook, { autoDownload: false, readMessage: false, onPresenceChanged: false });
const { initServer, logger } = require(`${root}/dist/index`);
const CreateSessionUtil = require(`${root}/dist/util/createSessionUtil`).default;

// ---- Chromium profile safety -------------------------------------------------------
// Each session already uses its own profile (<userDataDir>/<session>). Two failure
// modes remained: (1) Singleton* lock files survive a container restart and, because
// PIDs restart from 1, can name a live unrelated process, so Chromium refuses the
// profile ("in use by another process"); (2) after Auto Close the old browser exits
// asynchronously while a new start immediately launches a second Chromium on the
// same profile. Both are handled here without touching session credentials.
const profileRoot = path.resolve(config.customUserDataDir || `${root}/userDataDir`);
const LOCKS = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const note = message => process.stdout.write(`WPPConnect profile guard: ${message}\n`);

function browserUsing(dir) {
  const flag = `--user-data-dir=${dir}`;
  for (const pid of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(pid)) continue;
    try {
      const args = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0');
      if (!args.includes(flag)) continue;
      const state = fs.readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ').pop()[0];
      if (state !== 'Z' && state !== 'X') return true; // zombies hold no profile
    } catch { /* process exited while scanning */ }
  }
  return false;
}

function clearStaleLocks(dir) {
  let removed = 0;
  for (const name of LOCKS) {
    const file = path.join(dir, name);
    try { fs.lstatSync(file); fs.rmSync(file, { force: true }); removed++; } catch { /* absent */ }
  }
  return removed;
}

// At boot no browser from this container exists yet, so every lock is stale.
try {
  let cleared = 0;
  for (const entry of fs.readdirSync(profileRoot, { withFileTypes: true })) {
    const dir = path.join(profileRoot, entry.name);
    if (entry.isDirectory() && !browserUsing(dir)) cleared += clearStaleLocks(dir);
  }
  if (cleared) note(`removed ${cleared} stale lock file(s) at startup.`);
} catch { /* first run: no profiles yet */ }

// Launch one browser at a time: startAllSession otherwise starts every stored session
// at once, and concurrent Chromium launches were failing ("Failed to launch browser").
let launchQueue = Promise.resolve();
const LAUNCH_SLOT_MS = 20000;
const PROFILE_RELEASE_MS = 30000;
const initializeSession = CreateSessionUtil.prototype.initializeSession;
CreateSessionUtil.prototype.initializeSession = async function (req, clients, session, res) {
  const current = clients[session];
  if (!/^[A-Za-z0-9_-]+$/.test(session) || (current && current.status != null && current.status !== 'CLOSED')) {
    // Invalid name or already running: the original method rejects/no-ops.
    return initializeSession.call(this, req, clients, session, res);
  }
  let release;
  const slot = new Promise(resolve => { release = resolve; });
  const previous = launchQueue;
  launchQueue = previous.then(() => slot);
  await previous;
  const timer = setTimeout(release, LAUNCH_SLOT_MS);
  const done = () => { clearTimeout(timer); release(); };
  try {
    const live = clients[session];
    if (live && live.status != null && live.status !== 'CLOSED') { done(); return; } // started while queued
    const dir = path.resolve(profileRoot, session);
    const deadline = Date.now() + PROFILE_RELEASE_MS;
    while (browserUsing(dir) && Date.now() < deadline) await sleep(500);
    if (browserUsing(dir)) {
      note('previous browser still owns the profile; start skipped, retry shortly.');
      done();
      return;
    }
    if (clearStaleLocks(dir)) note('cleared stale profile lock before launch.');
    const attempt = initializeSession.call(this, req, clients, session, res);
    attempt.then(done, done);
    return await attempt;
  } catch (error) {
    done();
    throw error;
  }
};
const { SenderLayer } = require(`${root}/node_modules/@wppconnect-team/wppconnect/dist/api/layers/sender.layer`);
const { HostLayer } = require(`${root}/node_modules/@wppconnect-team/wppconnect/dist/api/layers/host.layer`);

// WA-JS 4.7.2 calls the throwing getter even on WhatsApp Web builds
// which only expose the nullable getter. Use the signed-in account's LID.
function ensureLidSupport(page) {
  return page.evaluate(() => {
    const prefs = WPP.whatsapp.UserPrefs;
    if (!prefs || typeof prefs.getMeLidUserOrThrow === 'function') return false;
    if (typeof prefs.getMaybeMeLidUser === 'function' && prefs.getMaybeMeLidUser()?.isLid()) {
      const getMeLid = () => {
        const lid = prefs.getMaybeMeLidUser();
        if (!lid) throw new Error('Signed-in account LID is unavailable.');
        return lid;
      };
      Object.defineProperty(prefs, 'getMeLidUserOrThrow', {
        configurable: true,
        value: getMeLid,
      });
      return true;
    }
    return false;
  });
}

const getHostDevice = HostLayer.prototype.getHostDevice;
HostLayer.prototype.getHostDevice = async function (...args) {
  const host = await getHostDevice.apply(this, args);
  try {
    if (await ensureLidSupport(this.page)) {
      process.stdout.write('WPPConnect installed the signed-in LID getter compatibility fix.\n');
    }
  } catch {
    // Device identity remains available when a browser is navigating.
  }
  return host;
};

const sendText = SenderLayer.prototype.sendText;
SenderLayer.prototype.sendText = async function (...args) {
  await ensureLidSupport(this.page);
  return sendText.apply(this, args);
};
const winston = require(`${root}/node_modules/winston`);
logger.clear();
logger.add(new winston.transports.Console({
  level: 'warn',
  format: winston.format.printf(info => {
    // Retain a diagnostic category while stripping URLs, identifiers and long tokens.
    const raw = String(info.message || info.name || 'upstream event');
    const safe = raw
      .replace(/https?:\/\/\S+/gi, '[url]')
      .replace(/Bearer\s+\S+/gi, 'Bearer [redacted]')
      .replace(/[A-Za-z0-9_-]{24,}/g, '[redacted]')
      .replace(/\d/g, '#')
      .replace(/[^\x20-\x7E]/g, '')
      .slice(0, 240);
    return `WPPConnect ${info.level}: ${safe || 'upstream event'} (private details suppressed)`;
  }),
}));
require(`${root}/node_modules/@wppconnect-team/wppconnect`).defaultLogger.silent = true;
for (const method of ['log', 'info', 'debug', 'warn', 'error']) {
  console[method] = () => {};
}
process.on('uncaughtException', () => { process.stderr.write('WPPConnect fatal error; private details suppressed.\n'); process.exit(1); });
process.on('unhandledRejection', () => { process.stderr.write('WPPConnect unhandled failure; private details suppressed.\n'); process.exit(1); });
initServer(config);
