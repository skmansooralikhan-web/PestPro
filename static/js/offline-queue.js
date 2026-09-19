// PCT CRM — offline job card queue.
//
// When a technician submits a job card with no connection, the submission
// is saved to IndexedDB instead of being lost. Whenever the browser comes
// back online (or the "Sync Now" button is pressed), everything queued is
// replayed against the real server endpoint in submission order. The
// server-side view (blueprints/jobcards.py: start()) doesn't know or care
// whether a POST arrived immediately or after being queued — it's the same
// endpoint either way, so inventory deduction and visit completion happen
// correctly whenever the sync actually lands.

const DB_NAME = 'pct_crm_offline';
const DB_VERSION = 1;
const STORE = 'pendingJobCards';

function openQueueDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'localId' });
        store.createIndex('visitId', 'visitId', { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// Queue one job card submission. `fields` is a plain object of form field
// name -> string value (photos/signature already base64-encoded strings,
// same shape the server-side view already expects from a normal submit).
async function queueJobCard(visitId, fields) {
  const db = await openQueueDB();
  const localId = 'offline-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  const record = { localId, visitId, fields, queuedAt: new Date().toISOString() };
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add(record);
    tx.oncomplete = () => resolve(record);
    tx.onerror = () => reject(tx.error);
  });
}

async function getPendingJobCards() {
  const db = await openQueueDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function getPendingVisitIds() {
  const items = await getPendingJobCards();
  return new Set(items.map((i) => i.visitId));
}

async function removePendingJobCard(localId) {
  const db = await openQueueDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(localId);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// Replays every queued submission against the real server endpoint, in the
// order they were queued (so if a technician somehow queued two updates for
// the same visit, the second — most recent — one wins, matching what would
// have happened had both gone out live). Returns {synced, failed, authExpired}.
async function syncPendingJobCards() {
  const items = await getPendingJobCards();
  items.sort((a, b) => a.queuedAt.localeCompare(b.queuedAt));
  let synced = 0, failed = 0, authExpired = false;

  for (const item of items) {
    const body = new URLSearchParams();
    Object.entries(item.fields).forEach(([k, v]) => body.append(k, v ?? ''));
    body.set('csrf_token', getCsrfToken());

    try {
      const resp = await fetch('/jobcards/start/' + item.visitId, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
        redirect: 'follow',
      });
      if (resp.status === 401 || resp.status === 403 ||
          (resp.redirected && resp.url.includes('/auth/login'))) {
        authExpired = true;
        failed++;
        continue;
      }
      if (resp.ok) {
        await removePendingJobCard(item.localId);
        synced++;
      } else {
        failed++;
      }
    } catch (e) {
      // Still offline, or the request otherwise failed — leave it queued
      // and stop trying the rest until the next sync attempt.
      failed += (items.length - synced - failed);
      break;
    }
  }
  return { synced, failed, authExpired };
}

// Node-only export hook for automated testing (no-op in real browsers,
// where `module` is never defined).
if (typeof module !== 'undefined') {
  module.exports = {
    queueJobCard, getPendingJobCards, getPendingVisitIds,
    removePendingJobCard, syncPendingJobCards,
  };
}
