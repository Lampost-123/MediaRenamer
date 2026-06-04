/* ── State ─────────────────────────────────────────────────── */
const state = {
  files: [],           // raw scan results
  folders: [],         // folder rename proposals
  matches: {},         // fileId -> chosen candidate
  proposed: {},        // fileId -> proposed filename string
  proposedFolders: {}, // fileId -> proposed target folder path
  statuses: {},        // fileId -> status string
  selectedId: null,
  candidates: {},
  settings: {},
  canUndo: false,
  scanRoot: "",        // the folder the user scanned
};

/* ── API helpers ───────────────────────────────────────────── */
const api = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const txt = await r.text();
      let msg = txt;
      try { msg = JSON.parse(txt).detail || txt; } catch {}
      throw new Error(msg);
    }
    return r.json();
  },
};

/* ── DOM refs ──────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const el = {
  folderPath:    $('folder-path'),
  btnBrowse:     $('btn-browse'),
  btnScan:       $('btn-scan'),
  btnRename:     $('btn-rename'),
  btnUndo:       $('btn-undo'),
  fileTableWrap: $('file-table-wrap'),
  fileTable:     $('file-table'),
  tbody:         $('file-tbody'),
  emptyState:    $('empty-state'),
  countBadge:    $('count-badge'),
  selectAll:     $('select-all'),
  statusText:    $('status-text'),
  progressWrap:  $('progress-wrap'),
  progressBar:   $('progress-bar'),
  apiKeyInput:   $('api-key-input'),
  btnSaveSettings: $('btn-save-settings'),
  tvTemplate:    $('tv-template'),
  movieTemplate: $('movie-template'),
  apiKeyBanner:  $('api-key-banner'),
  detailPanel:   $('detail-panel'),
  detailEmpty:   $('detail-empty'),
  detailContent: $('detail-content'),
  modalOverlay:  $('modal-overlay'),
  modalTitle:    $('modal-title'),
  modalSubtitle: $('modal-subtitle'),
  modalBody:     $('modal-body'),
  modalCancel:   $('modal-cancel'),
  modalConfirm:  $('modal-confirm'),
};

/* ── Init ──────────────────────────────────────────────────── */
let _initDone = false;
let _initResolve;
const initReady = new Promise(r => { _initResolve = r; });

async function init() {
  try {
    // Retry up to 5 times in case the server isn't ready yet on first page load
    let lastErr;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        state.settings = await api.get('/api/settings');
        lastErr = null;
        break;
      } catch (e) {
        lastErr = e;
        await new Promise(r => setTimeout(r, 400));
      }
    }
    if (lastErr) { setStatus('Could not connect to server: ' + lastErr.message, 'error'); return; }

    el.apiKeyInput.value = state.settings.tmdb_api_key || '';
    el.tvTemplate.value = state.settings.tv_template || '';
    el.movieTemplate.value = state.settings.movie_template || '';
    if (state.settings.last_folder) el.folderPath.value = state.settings.last_folder;
    document.getElementById('chk-nfo').checked = !!state.settings.generate_nfo;
    checkApiKeyBanner();

    const undoStatus = await api.get('/api/undo/status');
    state.canUndo = undoStatus.can_undo;
    updateUndoBtn();

    // Resume watching folders that were active before restart
    (state.settings.watched_folders || []).forEach(f => startWatching(f));
  } catch (e) {
    setStatus('Failed to load settings: ' + e.message, 'error');
  } finally {
    // Always resolve so scan() never hangs waiting for init
    _initDone = true;
    _initResolve();
  }
}

function checkApiKeyBanner() {
  const hasKey = !!(el.apiKeyInput.value.trim());
  el.apiKeyBanner.classList.toggle('visible', !hasKey);
}

/* ── Status bar ────────────────────────────────────────────── */
function setStatus(msg, type = 'info') {
  el.statusText.textContent = msg;
  el.statusText.style.color = type === 'error' ? 'var(--red)' : type === 'success' ? 'var(--green)' : 'var(--text-dim)';
}

function setProgress(pct, visible = true) {
  el.progressWrap.classList.toggle('visible', visible);
  el.progressBar.style.width = pct + '%';
}

/* ── Folder browsing — opens a native OS dialog via the Python backend ──── */
el.btnBrowse.addEventListener('click', async () => {
  const orig = el.btnBrowse.textContent;
  el.btnBrowse.disabled = true;
  el.btnBrowse.textContent = '⏳';
  setStatus('Opening folder browser…', 'info');
  try {
    const data = await api.get('/api/browse');
    if (data.path) {
      el.folderPath.value = data.path;
      setStatus('Folder selected — click Scan to continue', 'info');
    } else {
      setStatus('No folder selected', 'info');
    }
  } catch (e) {
    setStatus('Folder browser failed: ' + e.message, 'error');
  } finally {
    el.btnBrowse.disabled = false;
    el.btnBrowse.textContent = orig;
  }
});

/* ── Scan ──────────────────────────────────────────────────── */
el.btnScan.addEventListener('click', scan);
el.folderPath.addEventListener('keydown', e => { if (e.key === 'Enter') scan(); });

async function scan() {
  const folder = el.folderPath.value.trim();
  if (!folder) { setStatus('Enter a folder path first', 'error'); return; }
  setStatus('Scanning…');
  setProgress(0, true);
  el.btnScan.disabled = true;
  if (!_initDone) { setStatus('Loading settings…'); await initReady; }
  try {
    const data = await api.post('/api/scan', { path: folder, recursive: true });
    state.files = data.files;
    state.folders = data.folders || [];
    state.matches = {};
    state.proposed = {};
    state.proposedFolders = {};
    state.statuses = {};
    state.candidates = {};
    state.selectedId = null;
    state.scanRoot = folder;
    data.files.forEach(f => { state.statuses[f.id] = 'pending'; });
    state.folders.forEach(f => { state.statuses[f.id] = 'folder'; state.proposed[f.id] = f.proposed_name; });
    renderTable();
    updateRenameBtn();
    if (data.count === 0) {
      setStatus('No media files found — check the path is correct and the folder contains .mkv/.mp4/.avi etc.', 'error');
    } else {
      const folderMsg = state.folders.length ? ` + ${state.folders.length} folder${state.folders.length !== 1 ? 's' : ''} to rename` : '';
      setStatus(`Found ${data.count} media file${data.count !== 1 ? 's' : ''}${folderMsg}`, 'info');
    }
    setProgress(100);
    setTimeout(() => setProgress(0, false), 800);
    if (data.count > 0) startBulkLookup();
  } catch (e) {
    setStatus('Scan failed: ' + e.message, 'error');
    setProgress(0, false);
  } finally {
    el.btnScan.disabled = false;
  }
}

/* ── TMDB Bulk Lookup ──────────────────────────────────────── */
async function startBulkLookup() {
  // Read key from state.settings (populated by init) — fall back to input value
  const apiKey = state.settings.tmdb_api_key || el.apiKeyInput.value.trim();
  if (!apiKey) {
    state.files.forEach(f => { state.statuses[f.id] = 'no-match'; });
    renderTable();
    setStatus('Enter a TMDB API key in Settings to fetch metadata', 'info');
    return;
  }

  state.files.forEach(f => { state.statuses[f.id] = 'looking'; });
  renderTable();
  setStatus('Looking up metadata…');
  setProgress(10, true);

  try {
    const data = await api.post('/api/lookup/bulk', { files: state.files });
    data.results.forEach(result => {
      const id = result.id;
      const cands = result.candidates || [];
      state.candidates[id] = cands;
      if (cands.length > 0) {
        state.matches[id] = cands[0];
        state.statuses[id] = 'matched';
      } else {
        state.statuses[id] = 'no-match';
      }
    });
    await refreshAllProposed();

    // Fetch movie folder proposals (movies alone in their own folder → rename that folder)
    try {
      const matchesMap = {};
      state.files.forEach(f => { if (state.matches[f.id]) matchesMap[f.id] = state.matches[f.id]; });
      const mfp = await api.post('/api/movie-folder-proposals', { files: state.files, matches: matchesMap });
      if (mfp.proposals?.length) {
        // Merge with existing folder proposals, avoiding duplicates
        const existingIds = new Set(state.folders.map(f => f.id));
        mfp.proposals.forEach(p => {
          if (!existingIds.has(p.id)) {
            state.folders.push(p);
            state.statuses[p.id] = 'folder';
            state.proposed[p.id] = p.proposed_name;
          }
        });
      }
    } catch {}

    renderTable();
    updateRenameBtn();

    const matched = Object.values(state.statuses).filter(s => s === 'matched').length;
    const total = state.files.length;
    setStatus(`Matched ${matched}/${total} files`, matched === total ? 'success' : 'info');
    setProgress(100);
    setTimeout(() => setProgress(0, false), 800);
  } catch (e) {
    setStatus('Lookup failed: ' + e.message, 'error');
    setProgress(0, false);
    state.files.forEach(f => {
      if (state.statuses[f.id] === 'looking') state.statuses[f.id] = 'no-match';
    });
    renderTable();
  }
}

/* ── Propose names ─────────────────────────────────────────── */
async function refreshAllProposed() {
  const promises = state.files.map(f => refreshProposed(f));
  await Promise.allSettled(promises);
}

async function refreshProposed(fileEntry) {
  const match = state.matches[fileEntry.id] || null;
  try {
    const data = await api.post('/api/preview', { file_entry: fileEntry, match });
    state.proposed[fileEntry.id] = data.proposed_name;
    state.proposedFolders[fileEntry.id] = data.proposed_folder || fileEntry.folder;
  } catch {
    state.proposed[fileEntry.id] = fileEntry.filename;
    state.proposedFolders[fileEntry.id] = fileEntry.folder;
  }
}

/* ── Render Table ──────────────────────────────────────────── */
function renderTable() {
  const files = state.files;
  const folders = state.folders;
  if (files.length === 0 && folders.length === 0) {
    el.emptyState.classList.remove('hidden');
    el.fileTable.classList.add('hidden');
    el.countBadge.textContent = '0 files';
    return;
  }
  el.emptyState.classList.add('hidden');
  el.fileTable.classList.remove('hidden');
  const total = files.length + folders.length;
  el.countBadge.textContent = files.length + ' file' + (files.length !== 1 ? 's' : '') +
    (folders.length ? ` · ${folders.length} folder${folders.length !== 1 ? 's' : ''}` : '');

  el.tbody.innerHTML = '';
  // Folders first
  folders.forEach(f => {
    const row = buildFolderRow(f);
    el.tbody.appendChild(row);
  });
  files.forEach(f => {
    const row = buildRow(f);
    el.tbody.appendChild(row);
  });
  updateSelectAll();
}

function buildRow(f) {
  const tr = document.createElement('tr');
  tr.dataset.id = f.id;
  tr.classList.toggle('selected-row', state.selectedId === f.id);

  const status = state.statuses[f.id] || 'pending';
  const proposed = state.proposed[f.id] || '';
  const propFolder = state.proposedFolders[f.id] || f.folder;
  const folderChanged = propFolder && propFolder !== f.folder;
  // Show folder prefix if the file is moving to a different directory
  const folderPrefix = folderChanged ? `<span style="color:var(--text-muted);font-size:10px;">${escHtml(propFolder.split(/[\\/]/).pop())}/</span>` : '';
  const propFullTitle = folderChanged ? `${propFolder}\\${proposed}` : proposed;

  tr.innerHTML = `
    <td class="cell-check"><input type="checkbox" class="row-check" data-id="${escHtml(f.id)}" ${rowChecked(f.id) ? 'checked' : ''}></td>
    <td><span class="filename-orig" title="${escHtml(f.path)}">${escHtml(f.filename)}</span></td>
    <td class="col-arrow">›</td>
    <td><span class="filename-new ${!proposed ? 'no-match' : ''}" title="${escHtml(propFullTitle)}">${folderPrefix}${escHtml(proposed || '—')}</span></td>
    <td>${badgeHtml(status)}</td>
    <td><button class="btn-row-action" data-id="${escHtml(f.id)}" title="Edit match">✎</button></td>
  `;

  tr.querySelector('.row-check').addEventListener('change', e => {
    setRowChecked(f.id, e.target.checked);
    tr.classList.toggle('selected-row', e.target.checked && state.selectedId === f.id);
    updateRenameBtn();
    updateSelectAll();
  });

  tr.querySelector('.btn-row-action').addEventListener('click', () => openDetailFor(f));
  tr.addEventListener('click', e => {
    if (e.target.classList.contains('row-check') || e.target.classList.contains('btn-row-action')) return;
    openDetailFor(f);
    // highlight
    document.querySelectorAll('#file-tbody tr').forEach(r => r.classList.remove('selected-row'));
    tr.classList.add('selected-row');
    state.selectedId = f.id;
  });

  return tr;
}

function buildFolderRow(f) {
  const tr = document.createElement('tr');
  tr.dataset.id = f.id;
  tr.style.background = 'rgba(148,226,213,0.04)';
  tr.innerHTML = `
    <td class="cell-check"><input type="checkbox" class="row-check" data-id="${escHtml(f.id)}" ${rowChecked(f.id) ? 'checked' : ''}></td>
    <td><span class="filename-orig" title="${escHtml(f.path)}">📁 ${escHtml(f.filename)}</span></td>
    <td class="col-arrow">›</td>
    <td><span class="filename-new" style="color:var(--teal)" title="${escHtml(f.proposed_name)}">📁 ${escHtml(f.proposed_name)}</span></td>
    <td><span class="badge" style="background:rgba(148,226,213,0.12);color:var(--teal)">📁 Folder</span></td>
    <td></td>
  `;
  tr.querySelector('.row-check').addEventListener('change', e => {
    setRowChecked(f.id, e.target.checked);
    updateRenameBtn();
    updateSelectAll();
  });
  return tr;
}

/* per-row checked state stored in a Set */
const excludedIds = new Set();
function rowChecked(id) { return !excludedIds.has(id); }
function setRowChecked(id, checked) {
  if (checked) excludedIds.delete(id);
  else excludedIds.add(id);
}

function updateSelectAll() {
  const allItems = [...state.files, ...state.folders];
  const all = allItems.length;
  const checked = allItems.filter(f => rowChecked(f.id)).length;
  el.selectAll.checked = all > 0 && checked === all;
  el.selectAll.indeterminate = checked > 0 && checked < all;
}

el.selectAll.addEventListener('change', () => {
  [...state.files, ...state.folders].forEach(f => setRowChecked(f.id, el.selectAll.checked));
  renderTable();
  updateRenameBtn();
});

function badgeHtml(status) {
  const map = {
    pending:  ['badge-pending',  '·',  'Pending'],
    looking:  ['badge-looking',  '<span class="spinner">⟳</span>', 'Looking up…'],
    matched:  ['badge-matched',  '✓',  'Matched'],
    'no-match': ['badge-no-match', '?', 'No match'],
    manual:   ['badge-manual',   '✎',  'Manual'],
    done:     ['badge-done',     '✔',  'Done'],
    error:    ['badge-error',    '✕',  'Error'],
  };
  const [cls, icon, label] = map[status] || map.pending;
  return `<span class="badge ${cls}">${icon} ${label}</span>`;
}

function updateRowStatus(id, status, proposed) {
  const tr = el.tbody.querySelector(`tr[data-id="${CSS.escape(id)}"]`);
  if (!tr) return;
  if (proposed !== undefined) {
    state.proposed[id] = proposed;
    const span = tr.querySelector('.filename-new');
    if (span) { span.textContent = proposed; span.title = proposed; span.classList.toggle('no-match', !proposed); }
  }
  state.statuses[id] = status;
  const badgeCell = tr.querySelector('.badge');
  if (badgeCell) badgeCell.outerHTML = badgeHtml(status);
  tr.classList.toggle('row-done', status === 'done');
}

/* ── Detail / Side panel ────────────────────────────────────── */
function openDetailFor(f) {
  state.selectedId = f.id;
  const cands = state.candidates[f.id] || [];
  const match = state.matches[f.id];
  const proposed = state.proposed[f.id] || '';

  el.detailEmpty.classList.add('hidden');
  el.detailContent.classList.remove('hidden');

  const poster = match?.poster || '';
  const overview = match?.overview || f.title_guess || '';
  const title = match?.title || f.title_guess || f.filename;
  const year = match?.year || f.year || '';
  const mtype = match?.type || f.type || '';

  el.detailContent.innerHTML = `
    <div style="display:flex;gap:12px;margin-bottom:10px;">
      ${poster
        ? `<img class="detail-poster" src="${escHtml(poster)}" alt="" onerror="this.style.display='none'">`
        : `<div class="detail-no-poster">🎬</div>`}
      <div>
        <div class="detail-title">${escHtml(title)}</div>
        <div class="detail-meta">${escHtml([mtype === 'tv' ? 'TV Show' : mtype === 'movie' ? 'Movie' : 'Unknown', year].filter(Boolean).join(' · '))}</div>
        <div class="detail-overview">${escHtml(overview)}</div>
      </div>
    </div>
    <div class="detail-meta" style="margin-bottom:6px;">
      <strong style="color:var(--text-dim)">Proposed:</strong>
      <span class="mono text-accent" style="font-size:11px;">${escHtml(proposed || '—')}</span>
    </div>
    ${cands.length > 1 ? `
    <div class="candidates-label">Other matches</div>
    ${cands.slice(1).map((c, i) => `
      <div class="candidate-item" data-idx="${i+1}">
        <div class="cand-title">${escHtml(c.title)}</div>
        <div class="cand-meta">${escHtml([c.type === 'tv' ? 'TV' : 'Movie', c.year].filter(Boolean).join(' · '))}</div>
      </div>`).join('')}
    ` : ''}
    <div class="detail-manual-search">
      <label>Search manually</label>
      <div class="manual-search-row">
        <input type="text" id="manual-query" placeholder="Search TMDB…" value="${escHtml(f.title_guess || '')}">
        <button class="btn btn-primary" id="btn-manual-search" style="padding:5px 10px;font-size:12px;">Search</button>
      </div>
      <div id="manual-results" style="margin-top:8px;"></div>
    </div>
  `;

  // candidate click (other matches)
  el.detailContent.querySelectorAll('.candidate-item').forEach(item => {
    item.addEventListener('click', async () => {
      const idx = parseInt(item.dataset.idx);
      const cand = cands[idx];
      await pickCandidate(f, cand);
      openDetailFor(f);
    });
  });

  // manual search
  const manualInput = $('manual-query');
  $('btn-manual-search').addEventListener('click', () => doManualSearch(f, manualInput.value));
  manualInput.addEventListener('keydown', e => { if (e.key === 'Enter') doManualSearch(f, manualInput.value); });
}

async function pickCandidate(f, cand) {
  state.matches[f.id] = cand;
  state.statuses[f.id] = 'manual';
  await refreshProposed(f);
  updateRowStatus(f.id, 'manual', state.proposed[f.id]);
  updateRenameBtn();
}

async function doManualSearch(f, query) {
  if (!query.trim()) return;
  const resultsDiv = $('manual-results');
  if (!resultsDiv) return;
  resultsDiv.innerHTML = '<span style="color:var(--text-dim);font-size:12px;">Searching…</span>';

  try {
    const apiKey = el.apiKeyInput.value.trim();
    if (!apiKey) { resultsDiv.innerHTML = '<span style="color:var(--red);font-size:12px;">API key required</span>'; return; }

    const data = await api.post('/api/search', {
      file_id: f.id,
      query: query.trim(),
      media_type: f.type || 'unknown',
      year: f.year || null,
      season: f.season || null,
      episode: f.episode || null,
    });

    const cands = data.candidates || [];
    state.candidates[f.id] = cands;

    if (cands.length === 0) {
      resultsDiv.innerHTML = '<span style="color:var(--text-muted);font-size:12px;">No results found</span>';
      return;
    }

    resultsDiv.innerHTML = cands.map((c, i) => `
      <div class="candidate-item" data-cand-idx="${i}" style="margin-bottom:6px;">
        <div class="cand-title">${escHtml(c.title)}</div>
        <div class="cand-meta">${escHtml([c.type === 'tv' ? 'TV' : 'Movie', c.year].filter(Boolean).join(' · '))}</div>
      </div>`).join('');

    resultsDiv.querySelectorAll('.candidate-item').forEach(item => {
      item.addEventListener('click', async () => {
        const idx = parseInt(item.dataset.candIdx);
        await pickCandidate(f, cands[idx]);
        openDetailFor(f);
      });
    });
  } catch (e) {
    resultsDiv.innerHTML = `<span style="color:var(--red);font-size:12px;">Error: ${escHtml(e.message)}</span>`;
  }
}

/* ── Settings save ─────────────────────────────────────────── */
el.btnSaveSettings.addEventListener('click', saveSettings);

async function saveSettings() {
  const btn = el.btnSaveSettings;
  const tvVal    = el.tvTemplate.value.trim();
  const movieVal = el.movieTemplate.value.trim();

  // Don't wipe templates with blank strings — warn instead
  if (!tvVal) { setStatus('TV template cannot be empty — changes not saved', 'error'); return; }
  if (!movieVal) { setStatus('Movie template cannot be empty — changes not saved', 'error'); return; }

  btn.disabled = true;
  try {
    const saved = await api.post('/api/settings', {
      tmdb_api_key: el.apiKeyInput.value.trim(),
      tv_template:    tvVal,
      movie_template: movieVal,
      generate_nfo:   document.getElementById('chk-nfo').checked,
    });
    // Sync state so subsequent lookups use the new values
    state.settings = { ...state.settings, ...saved };
    checkApiKeyBanner();
    // Visual tick on the button
    const orig = btn.textContent;
    btn.textContent = '✓ Saved';
    btn.style.color = 'var(--green)';
    setTimeout(() => { btn.textContent = orig; btn.style.color = ''; }, 2000);
    setStatus('Settings saved', 'success');
    // Re-propose all names with new template
    if (state.files.length > 0) {
      await refreshAllProposed();
      renderTable();
    }
  } catch (e) {
    setStatus('Save failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

el.apiKeyInput.addEventListener('input', checkApiKeyBanner);

/* ── Rename ────────────────────────────────────────────────── */
function updateRenameBtn() {
  const toRename = getOperations();
  el.btnRename.disabled = toRename.length === 0;
  el.btnRename.textContent = toRename.length > 0 ? `▶ Rename ${toRename.length}` : '▶ Rename';
}

function getOperations() {
  const sep = '\\';

  // Folder renames first (Season 1→Season 01, movie folder renames)
  const folderOps = state.folders
    .filter(f => rowChecked(f.id) && state.statuses[f.id] !== 'done')
    .map(f => ({
      src: f.path,
      dst: f.folder + sep + f.proposed_name,
      op_type: 'folder',
      id: f.id,
    }));

  // Build remapping: old folder → new folder (after folder renames)
  const folderMap = {};
  folderOps.forEach(op => { folderMap[op.src] = op.dst; });

  const fileOps = state.files
    .filter(f => rowChecked(f.id) && state.proposed[f.id] && state.statuses[f.id] !== 'done')
    .filter(f => {
      const targetFolder = state.proposedFolders[f.id] || f.folder;
      const renamedFolder = folderMap[targetFolder] || targetFolder;
      const dst = renamedFolder + sep + state.proposed[f.id];
      return dst !== f.path; // skip if nothing changes
    })
    .map(f => {
      // Use proposed folder; update it if a folder rename covers it
      let targetFolder = state.proposedFolders[f.id] || f.folder;
      // If the proposed folder is inside a folder being renamed, update it
      for (const [oldF, newF] of Object.entries(folderMap)) {
        if (targetFolder === oldF || targetFolder.startsWith(oldF + sep)) {
          targetFolder = newF + targetFolder.slice(oldF.length);
          break;
        }
      }
      return {
        src: f.path,
        dst: targetFolder + sep + state.proposed[f.id],
        op_type: 'file',
        id: f.id,
      };
    });

  // Subtitle renames: pair each subtitle with its video's proposed destination
  const subtitleOps = [];
  state.files.forEach(f => {
    if (!state.proposed[f.id] || state.statuses[f.id] === 'done') return;
    if (!rowChecked(f.id)) return;
    const proposedName = state.proposed[f.id];
    const stem = proposedName.replace(/\.[^.]+$/, ''); // strip ext
    const targetFolder = (() => {
      let tf = state.proposedFolders[f.id] || f.folder;
      for (const [oldF, newF] of Object.entries(folderMap)) {
        if (tf === oldF || tf.startsWith(oldF + sep)) { tf = newF + tf.slice(oldF.length); break; }
      }
      return tf;
    })();
    (f.subtitles || []).forEach(sub => {
      const langSuffix = sub.lang ? `.${sub.lang}` : '';
      const newSubName = `${stem}${langSuffix}${sub.ext}`;
      const dstSub = targetFolder + sep + newSubName;
      if (dstSub !== sub.path) {
        subtitleOps.push({ src: sub.path, dst: dstSub, op_type: 'subtitle', id: sub.path });
      }
    });
  });

  // Duplicate detection: flag if two ops share the same dst
  const dstSet = new Set();
  const allOps = [...folderOps, ...fileOps, ...subtitleOps];
  const conflicts = new Set();
  allOps.forEach(op => {
    const d = op.dst.toLowerCase();
    if (dstSet.has(d)) conflicts.add(d);
    dstSet.add(d);
  });
  if (conflicts.size) {
    allOps.forEach(op => { if (conflicts.has(op.dst.toLowerCase())) op.conflict = true; });
  }

  return allOps;
}

el.btnRename.addEventListener('click', () => {
  const ops = getOperations();
  if (ops.length === 0) return;

  // Warn about conflicts
  const conflictOps = ops.filter(o => o.conflict);
  if (conflictOps.length) {
    setStatus(`⚠ ${conflictOps.length} naming conflict${conflictOps.length !== 1 ? 's' : ''} detected — resolve before renaming`, 'error');
    return;
  }

  const folderCount   = ops.filter(o => o.op_type === 'folder').length;
  const fileCount     = ops.filter(o => o.op_type === 'file').length;
  const subtitleCount = ops.filter(o => o.op_type === 'subtitle').length;
  const parts = [];
  if (folderCount)   parts.push(`${folderCount} folder${folderCount !== 1 ? 's' : ''}`);
  if (fileCount)     parts.push(`${fileCount} file${fileCount !== 1 ? 's' : ''}`);
  if (subtitleCount) parts.push(`${subtitleCount} subtitle${subtitleCount !== 1 ? 's' : ''}`);
  showConfirmModal(
    'Rename',
    `Rename ${parts.join(', ')}? Folders first, then files and subtitles. Use Undo to reverse.`,
    () => executeRename(ops)
  );
});

async function executeRename(operations) {
  el.btnRename.disabled = true;
  setProgress(0, true);
  setStatus('Renaming…');

  try {
    const r = await fetch('/api/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operations }),
    });

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const evt = JSON.parse(line.slice(6));
        handleRenameEvent(evt, operations.length);
      }
    }
  } catch (e) {
    setStatus('Rename failed: ' + e.message, 'error');
    el.btnRename.disabled = false;
    setProgress(0, false);
  }
}

function handleRenameEvent(evt, total) {
  if (evt.type === 'progress') {
    const pct = Math.round(((evt.index + 1) / total) * 100);
    setProgress(pct);
    const res = evt.result;
    // Check files first, then folders
    const fileEntry = state.files.find(f => f.path === res.src);
    const folderEntry = state.folders.find(f => f.path === res.src);
    const entry = fileEntry || folderEntry;
    if (entry) {
      updateRowStatus(entry.id, res.status === 'done' ? 'done' : 'error');
      // Update folder path in state so subsequent file rows show correctly
      if (folderEntry && res.status === 'done') {
        folderEntry.path = res.dst;
        // Update file entries that were inside this folder
        state.files.forEach(f => {
          if (f.folder === res.src) { f.folder = res.dst; f.path = res.dst + '\\' + f.filename; }
        });
      }
    }
  } else if (evt.type === 'done') {
    const doneCount = evt.results.filter(r => r.status === 'done').length;
    const errCount  = evt.results.filter(r => r.status === 'error').length;
    state.canUndo = evt.can_undo;
    updateUndoBtn();
    setStatus(
      `Renamed ${doneCount} item${doneCount !== 1 ? 's' : ''}${errCount > 0 ? `, ${errCount} error${errCount !== 1 ? 's' : ''}` : ''} — cleaning up empty folders…`,
      'info'
    );
    setProgress(100);
    setTimeout(() => setProgress(0, false), 1000);
    updateRenameBtn();
    // NFO generation — write sidecar files for successfully renamed videos
    if (document.getElementById('chk-nfo').checked) {
      const doneResults = evt.results.filter(r => r.status === 'done' && r.op_type !== 'folder' && r.op_type !== 'subtitle');
      doneResults.forEach(r => {
        const fileEntry = state.files.find(f => f.path === r.src);
        const match = fileEntry && state.matches[fileEntry.id];
        if (fileEntry && match) {
          api.post('/api/nfo/write', { file_entry: fileEntry, match, proposed_path: r.dst }).catch(() => {});
        }
      });
    }
    // Auto-clean empty folders left behind after reorganisation
    if (state.scanRoot) {
      api.post('/api/cleanup', { root: state.scanRoot })
        .then(d => {
          const msg = `Renamed ${doneCount} item${doneCount !== 1 ? 's' : ''}${errCount > 0 ? `, ${errCount} error${errCount !== 1 ? 's' : ''}` : ''}` +
            (d.count > 0 ? ` · removed ${d.count} empty folder${d.count !== 1 ? 's' : ''}` : '');
          setStatus(msg, errCount > 0 ? 'info' : 'success');
        })
        .catch(() => {
          setStatus(`Renamed ${doneCount} item${doneCount !== 1 ? 's' : ''}`, errCount > 0 ? 'info' : 'success');
        });
    }
  }
}

/* ── Undo ──────────────────────────────────────────────────── */
function updateUndoBtn() {
  el.btnUndo.disabled = !state.canUndo;
}

el.btnUndo.addEventListener('click', () => {
  showConfirmModal(
    'Undo last rename',
    'Reverse the most recent batch of renames?',
    async () => {
      try {
        const data = await api.post('/api/undo', {});
        state.canUndo = data.can_undo;
        updateUndoBtn();
        setStatus(`Undone: ${data.reversed} item${data.reversed !== 1 ? 's' : ''} restored`, 'success');
        // Reset done statuses for files and folders
        state.files.forEach(f => { if (state.statuses[f.id] === 'done') state.statuses[f.id] = 'matched'; });
        state.folders.forEach(f => { if (state.statuses[f.id] === 'done') state.statuses[f.id] = 'folder'; });
        renderTable();
        updateRenameBtn();
      } catch (e) {
        setStatus('Undo failed: ' + e.message, 'error');
      }
    }
  );
});

/* ── Modal ─────────────────────────────────────────────────── */
let _modalConfirmFn = null;

function showConfirmModal(title, subtitle, onConfirm) {
  el.modalTitle.textContent = title;
  el.modalSubtitle.textContent = subtitle;
  el.modalOverlay.classList.add('visible');
  _modalConfirmFn = onConfirm;
}

el.modalCancel.addEventListener('click', () => el.modalOverlay.classList.remove('visible'));
el.modalConfirm.addEventListener('click', () => {
  el.modalOverlay.classList.remove('visible');
  if (_modalConfirmFn) _modalConfirmFn();
});
el.modalOverlay.addEventListener('click', e => { if (e.target === el.modalOverlay) el.modalOverlay.classList.remove('visible'); });

/* ── Collapsible sections ───────────────────────────────────── */
document.querySelectorAll('.side-section-header').forEach(header => {
  const body = header.nextElementSibling;
  const chevron = header.querySelector('.side-section-chevron');
  if (!chevron || !body) return;
  header.addEventListener('click', () => {
    const open = !body.classList.contains('hidden');
    body.classList.toggle('hidden', open);
    chevron.classList.toggle('open', !open);
  });
  chevron.classList.add('open');
});

/* ── Helpers ───────────────────────────────────────────────── */
function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── Tab switching ─────────────────────────────────────────── */
const tabFiles   = document.getElementById('files-tab');
const tabHistory = document.getElementById('history-tab');
const countBadgeWrap = document.getElementById('count-badge');
const selectAllWrap  = document.getElementById('select-all-wrap');

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    if (tab === 'files') {
      tabFiles.classList.remove('hidden');
      tabHistory.classList.add('hidden');
      countBadgeWrap.style.display = '';
      selectAllWrap.style.display = '';
    } else {
      tabFiles.classList.add('hidden');
      tabHistory.classList.remove('hidden');
      countBadgeWrap.style.display = 'none';
      selectAllWrap.style.display = 'none';
      loadHistory();
    }
  });
});

/* ── History ───────────────────────────────────────────────── */
async function loadHistory() {
  const pane = document.getElementById('history-pane');
  pane.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:20px;">Loading…</div>';
  try {
    const data = await api.get('/api/history');
    renderHistory(data.batches || []);
  } catch (e) {
    pane.innerHTML = `<div style="color:var(--red);font-size:12px;padding:20px;">Failed to load history: ${escHtml(e.message)}</div>`;
  }
}

const HISTORY_PAGE = 20;

function renderHistory(batches) {
  const pane = document.getElementById('history-pane');
  pane.innerHTML = '';
  if (batches.length === 0) {
    pane.innerHTML = `
      <div id="history-empty">
        <div class="empty-icon">🕓</div>
        <p>No rename history yet</p>
        <p style="font-size:12px;color:var(--text-muted);">Past renames will appear here with rollback options</p>
      </div>`;
    return;
  }
  appendHistoryPage(pane, batches, 0);
}

function appendHistoryPage(pane, batches, offset) {
  const page = batches.slice(offset, offset + HISTORY_PAGE);
  page.forEach(batch => pane.appendChild(buildHistoryCard(batch)));

  const remaining = batches.length - offset - HISTORY_PAGE;
  if (remaining > 0) {
    const btn = document.createElement('button');
    btn.className = 'btn';
    btn.style.cssText = 'width:100%;margin-top:4px;font-size:12px;';
    btn.textContent = `Load more (${remaining} remaining)`;
    btn.addEventListener('click', () => { btn.remove(); appendHistoryPage(pane, batches, offset + HISTORY_PAGE); });
    pane.appendChild(btn);
  }
}

function buildHistoryCard(batch) {
  const isDone = batch.status === 'done';
  const dt = new Date(batch.timestamp);
  const timeStr = dt.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  const folderName = batch.folder ? batch.folder.split(/[\\/]/).pop() : '';
  const summary = `${batch.count} item${batch.count !== 1 ? 's' : ''}${folderName ? ' in ' + folderName : ''}`;

  const card = document.createElement('div');
  card.className = 'history-batch';
  card.dataset.id = batch.id;

  const statusBadge = isDone
    ? `<span class="badge badge-matched">✓ Done</span>`
    : `<span class="badge badge-undone">↩ Rolled back</span>`;

  const rollbackBtn = isDone
    ? `<button class="btn btn-danger btn-rollback" style="padding:3px 10px;font-size:11px;" data-id="${escHtml(batch.id)}">↩ Rollback</button>`
    : '';

  card.innerHTML = `
    <div class="history-batch-header">
      <span class="history-chevron">›</span>
      <span class="history-batch-time">${escHtml(timeStr)}</span>
      <span class="history-batch-summary">${escHtml(summary)}</span>
      <div class="history-batch-actions">
        ${statusBadge}
        ${rollbackBtn}
      </div>
    </div>
    <div class="history-ops hidden">
      ${batch.operations.map(op => {
        const srcName = op.src.split(/[\\/]/).pop();
        const dstName = op.dst.split(/[\\/]/).pop();
        const icon = op.op_type === 'folder' ? '📁' : '🎬';
        const rowCls = isDone ? '' : 'undone';
        return `<div class="history-op-row ${rowCls}">
          <span class="history-op-icon">${icon}</span>
          <span class="history-op-src" title="${escHtml(op.src)}">${escHtml(srcName)}</span>
          <span class="history-op-arrow">${isDone ? '→' : '←'}</span>
          <span class="history-op-dst" title="${escHtml(op.dst)}">${escHtml(dstName)}</span>
        </div>`;
      }).join('')}
    </div>
  `;

  // Toggle expand
  const header = card.querySelector('.history-batch-header');
  const ops    = card.querySelector('.history-ops');
  const chev   = card.querySelector('.history-chevron');
  header.addEventListener('click', e => {
    if (e.target.classList.contains('btn-rollback')) return;
    ops.classList.toggle('hidden');
    chev.classList.toggle('open');
  });

  // Rollback button
  const rbBtn = card.querySelector('.btn-rollback');
  if (rbBtn) {
    rbBtn.addEventListener('click', () => {
      showConfirmModal(
        'Roll back this batch?',
        `Reverse all ${batch.count} rename${batch.count !== 1 ? 's' : ''} from ${timeStr}. Files will be restored to their original names.`,
        async () => {
          rbBtn.disabled = true;
          rbBtn.textContent = '…';
          try {
            const result = await api.post('/api/undo', { batch_id: batch.id });
            state.canUndo = result.can_undo;
            updateUndoBtn();
            setStatus(`Rolled back ${result.reversed} item${result.reversed !== 1 ? 's' : ''}`, 'success');
            loadHistory(); // refresh
          } catch (err) {
            setStatus('Rollback failed: ' + err.message, 'error');
            rbBtn.disabled = false;
            rbBtn.textContent = '↩ Rollback';
          }
        }
      );
    });
  }

  return card;
}

/* ── Template token help ───────────────────────────────────── */
let _tokenHelp = null;

async function getTokenHelp() {
  if (_tokenHelp) return _tokenHelp;
  const data = await api.get('/api/templates/help');
  _tokenHelp = data.tokens;
  return _tokenHelp;
}

document.querySelectorAll('.btn-help').forEach(btn => {
  let panel = null;
  btn.addEventListener('click', async () => {
    // Toggle
    if (panel) { panel.remove(); panel = null; return; }
    const type = btn.dataset.help; // 'tv' or 'movie'
    const tokens = await getTokenHelp();
    const rows = (tokens[type] || []).map(([tok, desc, ex]) =>
      `<div class="token-row">
        <span class="token-name">${escHtml(tok)}</span>
        <span class="token-desc">${escHtml(desc)}</span>
        <span class="token-ex">${escHtml(ex)}</span>
       </div>`
    ).join('');
    panel = document.createElement('div');
    panel.id = 'token-help';
    panel.innerHTML = `
      <div style="font-weight:600;color:var(--text-dim);margin-bottom:6px;font-size:11px;">Available tokens</div>
      ${rows}
      <div style="margin-top:8px;color:var(--text-muted);font-size:10px;">Click a token name to insert it at the cursor.</div>
    `;
    // Click-to-insert
    panel.querySelectorAll('.token-name').forEach(span => {
      span.style.cursor = 'pointer';
      span.addEventListener('click', () => {
        const input = type === 'tv' ? el.tvTemplate : el.movieTemplate;
        const tok = span.textContent;
        const pos = input.selectionStart;
        input.value = input.value.slice(0, pos) + tok + input.value.slice(input.selectionEnd);
        input.selectionStart = input.selectionEnd = pos + tok.length;
        input.focus();
      });
    });
    btn.closest('.field').appendChild(panel);
  });
});

/* ── Keyboard shortcuts ────────────────────────────────────── */
document.addEventListener('keydown', e => {
  const tag = document.activeElement?.tagName;
  const typing = tag === 'INPUT' || tag === 'TEXTAREA';

  if (e.key === 'Escape') {
    document.getElementById('modal-overlay').classList.remove('visible');
    document.getElementById('shortcuts-panel').classList.add('hidden');
    return;
  }
  if (!e.ctrlKey) return;
  switch (e.key.toLowerCase()) {
    case 's':
      if (!typing) { e.preventDefault(); document.getElementById('btn-scan').click(); }
      break;
    case 'enter':
      e.preventDefault(); el.btnRename.click(); break;
    case 'z':
      e.preventDefault(); el.btnUndo.click(); break;
    case 'o':
      e.preventDefault(); document.getElementById('btn-browse').click(); break;
    case 'h':
      e.preventDefault(); document.querySelector('[data-tab="history"]').click(); break;
    case 'a':
      if (!typing) { e.preventDefault(); el.selectAll.click(); } break;
  }
});

// Shortcuts panel toggle
document.getElementById('btn-shortcuts').addEventListener('click', () => {
  document.getElementById('shortcuts-panel').classList.toggle('hidden');
});

/* ── Drag-and-drop ─────────────────────────────────────────── */
const dragOverlay = document.getElementById('drag-overlay');
let _dragDepth = 0;

document.addEventListener('dragenter', e => {
  e.preventDefault();
  _dragDepth++;
  dragOverlay.classList.add('visible');
});
document.addEventListener('dragleave', () => {
  _dragDepth--;
  if (_dragDepth <= 0) { _dragDepth = 0; dragOverlay.classList.remove('visible'); }
});
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => {
  e.preventDefault();
  _dragDepth = 0;
  dragOverlay.classList.remove('visible');

  // Try to get a real path from text data (works when dragging from Explorer address bar)
  const text = e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('text');
  const winPath = /^[A-Za-z]:[\\\/]/.test(text?.trim());
  if (winPath) {
    el.folderPath.value = text.trim();
    setStatus('Folder path set — click Scan to continue', 'info');
  } else {
    // Fall back to native OS picker
    setStatus('Opening folder browser…', 'info');
    document.getElementById('btn-browse').click();
  }
});

/* ── Skip list ──────────────────────────────────────────────── */
async function loadSkipList() {
  try {
    const data = await api.get('/api/skiplist');
    renderSkipList(data.paths || []);
  } catch {}
}

function renderSkipList(paths) {
  const container = document.getElementById('skip-list-container');
  if (!container) return;
  if (paths.length === 0) {
    container.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:4px 0;">No files skipped yet. Right-click a row and choose Skip.</div>';
    return;
  }
  container.innerHTML = paths.map(p => `
    <div class="skip-item">
      <span title="${escHtml(p)}">${escHtml(p.split(/[\\/]/).pop())}</span>
      <button class="btn-skip-remove" data-path="${escHtml(p)}" title="Remove from skip list">✕</button>
    </div>`).join('');
  container.querySelectorAll('.btn-skip-remove').forEach(btn => {
    btn.addEventListener('click', async () => {
      await api.post('/api/skiplist/remove', { path: btn.dataset.path });
      loadSkipList();
    });
  });
}

// Add to skip list from row context menu (right-click)
document.getElementById('file-tbody').addEventListener('contextmenu', async e => {
  e.preventDefault();
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  const fileEntry = state.files.find(f => f.id === tr.dataset.id);
  if (!fileEntry) return;
  showConfirmModal(
    'Skip this file?',
    `Add "${fileEntry.filename}" to the skip list? It will be ignored in all future scans.`,
    async () => {
      await api.post('/api/skiplist/add', { path: fileEntry.path });
      // Remove from current view
      state.files = state.files.filter(f => f.id !== fileEntry.id);
      delete state.statuses[fileEntry.id];
      renderTable();
      updateRenameBtn();
      setStatus(`"${fileEntry.filename}" added to skip list`, 'info');
    }
  );
});

/* ── Missing episode detection ──────────────────────────────── */
async function checkMissingEpisodes(fileEntry) {
  const match = state.matches[fileEntry.id];
  if (!match || match.type !== 'tv') return null;
  const season = fileEntry.season;
  if (!season) return null;

  // Collect all episodes we own for this show+season
  const ownedEps = state.files
    .filter(f => {
      const m = state.matches[f.id];
      return m && m.tmdb_id === match.tmdb_id && f.season === season;
    })
    .map(f => f.episode);

  try {
    const data = await api.post('/api/missing-episodes', {
      tmdb_id: match.tmdb_id,
      season,
      owned_episodes: ownedEps,
    });
    return data;
  } catch {
    return null;
  }
}

/* ── Watched folder ─────────────────────────────────────────── */
let _watchPollTimer = null;
const _watchingFolders = new Set();

function updateWatchIndicator() {
  const ind = document.getElementById('watch-indicator');
  ind.classList.toggle('hidden', _watchingFolders.size === 0);
}

async function startWatching(folder) {
  if (_watchingFolders.has(folder)) return;
  try {
    await api.post('/api/watch/start', { folder });
    _watchingFolders.add(folder);
    updateWatchIndicator();
    if (!_watchPollTimer) _watchPollTimer = setInterval(pollWatcher, 10000);
  } catch {}
}

async function stopWatching(folder) {
  try {
    await api.post('/api/watch/stop', { folder });
    _watchingFolders.delete(folder);
    updateWatchIndicator();
    if (_watchingFolders.size === 0 && _watchPollTimer) {
      clearInterval(_watchPollTimer);
      _watchPollTimer = null;
    }
  } catch {}
}

async function pollWatcher() {
  try {
    const data = await api.get('/api/watch/poll');
    if (data.events?.length) {
      const totalNew = data.events.reduce((n, e) => n + e.files.length, 0);
      const folder = data.events[0].folder;
      showConfirmModal(
        `${totalNew} new file${totalNew !== 1 ? 's' : ''} detected`,
        `New media found in "${folder.split(/[\\/]/).pop()}". Scan now to add them to the preview?`,
        () => { el.folderPath.value = folder; scan(); }
      );
    }
  } catch {}
}

// Wire up toolbar watch button — toggle watching current folder
el.btnScan.addEventListener('contextmenu', e => {
  e.preventDefault();
  const folder = el.folderPath.value.trim();
  if (!folder) return;
  if (_watchingFolders.has(folder)) {
    stopWatching(folder);
    setStatus(`Stopped watching "${folder.split(/[\\/]/).pop()}"`, 'info');
  } else {
    startWatching(folder);
    setStatus(`Watching "${folder.split(/[\\/]/).pop()}" for new files (right-click Scan to stop)`, 'info');
  }
});

/* ── Detail panel: add missing episode check + skip list ──── */
const _origOpenDetailFor = openDetailFor;
openDetailFor = async function(f) {
  _origOpenDetailFor(f);
  // After standard detail renders, fetch missing episodes
  const missing = await checkMissingEpisodes(f);
  if (missing && missing.missing.length > 0) {
    const content = document.getElementById('detail-content');
    if (!content || content.classList.contains('hidden')) return;
    const section = document.createElement('div');
    section.style.cssText = 'margin-top:10px;';
    section.innerHTML = `
      <div class="candidates-label" style="color:var(--yellow);">⚠ Missing from Season ${f.season}: ${missing.missing.length}/${missing.all_episodes}</div>
      <div class="missing-ep-list">
        ${missing.missing.map(e => `<div class="missing-ep-row">E${String(e.episode_number).padStart(2,'0')} — ${escHtml(e.name)}</div>`).join('')}
      </div>`;
    content.appendChild(section);
  }
};

/* ── Boot ──────────────────────────────────────────────────── */
init();
loadSkipList();
