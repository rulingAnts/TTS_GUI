'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
let voiceData = null;
let piperVoiceData = null;
let unavailableVoices = new Set();
let progressInterval = null;
let piperDlInterval = null;
let isGenerating = false;
let isPreviewing = false;
let hasV3 = false;
let lastOutputPath = null;
let currentEngine = 'kokoro';
let currentMode = 'voice';      // 'voice' | 'podcast'
let xttsData = { ready: false, speakers: [], languages: [] };
let xttsVoiceMode = 'builtin';  // 'builtin' | 'sample'
let xttsSamplePath = '';
let parsedScript = null;        // result from api.parse_script()
let speakerCardEls = {};        // { speakerName: { voiceEl, speedEl } }

// ─────────────────────────────────────────────────────────────────────────────
// DOM references
// ─────────────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);

// Voice slot descriptors: [slot-prefix, lang-id, accent-id, voice-id, gender-radio-name]
const VOICE_SLOTS = [
  { prefix: 'v1', lang: 'v1-language', accent: 'v1-accent', voice: 'v1-voice', radio: 'v1-gender' },
  { prefix: 'v2', lang: 'v2-language', accent: 'v2-accent', voice: 'v2-voice', radio: 'v2-gender' },
  { prefix: 'v3', lang: 'v3-language', accent: 'v3-accent', voice: 'v3-voice', radio: 'v3-gender' },
];

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────
function appInit() {
  initControls();
  initVoiceBlending();
  initPostProcessing();
  initEngineTabs();
  initXttsControls();
  initModeSwitcher();
  loadVoiceData();
  loadDefaultOutputDir();
}

// pywebview injects its API asynchronously — wait for it before calling anything
window.addEventListener('pywebviewready', appInit);
// Fallback in case the event already fired before this script ran
if (window.pywebview && window.pywebview.api) appInit();

// Called by Python backend after window loads if espeak-ng is missing
window.showStartupError = function (msg) {
  $('modal-body').textContent = msg;
  $('startup-modal').classList.remove('hidden');
};

// ─────────────────────────────────────────────────────────────────────────────
// Voice data loading
// ─────────────────────────────────────────────────────────────────────────────
async function loadVoiceData() {
  try {
    setStatus('Loading voice data…', 'running');
    const result = await window.pywebview.api.get_voice_data();
    voiceData = result.voices;
    unavailableVoices = new Set(result.unavailable || []);
    piperVoiceData = result.piper_voices || {};

    VOICE_SLOTS.forEach(slot => populateLanguages(slot));
    cascadeAccent(VOICE_SLOTS[0]);
    populatePiperVoices();
    checkPiperStatus();
    checkXttsStatus();
    setStatus('Ready', 'idle');
  } catch (err) {
    setStatus('Failed to load voice data: ' + err, 'error');
  }
}

async function loadDefaultOutputDir() {
  try {
    const dir = await window.pywebview.api.get_default_output_dir();
    if (dir) $('output-dir').value = dir;
  } catch (_) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// Engine tabs
// ─────────────────────────────────────────────────────────────────────────────
function initEngineTabs() {
  $$('.engine-tab').forEach(btn => {
    btn.addEventListener('click', () => switchEngine(btn.dataset.engine));
  });
}

function switchEngine(engine) {
  currentEngine = engine;

  $$('.engine-tab').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.engine === engine)
  );

  const isKokoro = engine === 'kokoro';
  const isXtts   = engine === 'xtts';
  const isPiper  = engine === 'piper';

  $('kokoro-fields').classList.toggle('hidden', !isKokoro);
  $('xtts-fields').classList.toggle('hidden',   !isXtts);
  $('piper-fields').classList.toggle('hidden',   !isPiper);

  // Voice blending is Kokoro-only
  $('kokoro-blend-section').classList.toggle('hidden', !isKokoro);
  if (!isKokoro && $('blend-enable').checked) {
    $('blend-enable').checked = false;
    $('blend-panel').classList.add('hidden');
  }

  if (isPiper) checkPiperStatus();
  if (isXtts)  checkXttsStatus();
}

// ─────────────────────────────────────────────────────────────────────────────
// Piper voice population
// ─────────────────────────────────────────────────────────────────────────────
function populatePiperVoices() {
  if (!piperVoiceData) return;

  const langSel = $('piper-language');
  langSel.innerHTML = '';
  Object.keys(piperVoiceData).forEach(lang => {
    const opt = document.createElement('option');
    opt.value = lang;
    opt.textContent = lang;
    langSel.appendChild(opt);
  });

  langSel.addEventListener('change', updatePiperVoiceDropdown);
  updatePiperVoiceDropdown();
}

function updatePiperVoiceDropdown() {
  if (!piperVoiceData) return;
  const lang = $('piper-language').value;
  const voiceSel = $('piper-voice-select');
  voiceSel.innerHTML = '';

  const voices = piperVoiceData[lang]?.voices || [];
  voices.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.id;
    opt.textContent = `${v.name} (${v.gender})`;
    voiceSel.appendChild(opt);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Piper model status + download
// ─────────────────────────────────────────────────────────────────────────────
async function checkPiperStatus() {
  try {
    const s = await window.pywebview.api.get_piper_status();
    setPiperStatusUI(s.ready);
  } catch (_) {}
}

function setPiperStatusUI(ready) {
  const txt = $('piper-status-text');
  const btn = $('btn-download-piper');
  if (ready) {
    txt.textContent = '✓ Model ready';
    txt.className = 'piper-status-ready';
    btn.classList.add('hidden');
  } else {
    txt.textContent = '⚠ Model not downloaded';
    txt.className = 'piper-status-missing';
    btn.classList.remove('hidden');
  }
}

async function handlePiperDownload() {
  const btn = $('btn-download-piper');
  btn.disabled = true;
  setStatus('Starting Piper download…', 'running');
  showProgress(true);

  try {
    const resp = await window.pywebview.api.start_piper_download();
    if (!resp.started) {
      setStatus('Download failed to start: ' + (resp.error || ''), 'error');
      showProgress(false);
      btn.disabled = false;
      return;
    }
  } catch (err) {
    setStatus('Download error: ' + err, 'error');
    showProgress(false);
    btn.disabled = false;
    return;
  }

  if (piperDlInterval) clearInterval(piperDlInterval);
  piperDlInterval = setInterval(async () => {
    try {
      const p = await window.pywebview.api.get_piper_download_progress();
      const pct = Math.round((p.progress || 0) * 100);
      $('progress-fill').style.width = pct + '%';
      $('progress-label').textContent = p.status || 'Downloading…';

      if (!p.downloading) {
        clearInterval(piperDlInterval);
        piperDlInterval = null;
        showProgress(false);
        btn.disabled = false;
        if (p.done) {
          setStatus('Piper model downloaded', 'success');
          setPiperStatusUI(true);
        } else {
          setStatus('Download failed: ' + (p.error || 'unknown'), 'error');
        }
      }
    } catch (_) {}
  }, 400);
}

// ─────────────────────────────────────────────────────────────────────────────
// XTTS v2 status + load
// ─────────────────────────────────────────────────────────────────────────────
async function checkXttsStatus() {
  try {
    const s = await window.pywebview.api.get_xtts_status();
    xttsData = s;
    applyXttsStatusUI(s);
  } catch (_) {}
}

function applyXttsStatusUI(s) {
  const txt = $('xtts-status-text');
  const btn = $('btn-load-xtts');
  const controls = $('xtts-controls');

  if (s.ready) {
    txt.textContent = '✓ XTTS v2 ready';
    txt.className = 'piper-status-ready';
    btn.classList.add('hidden');
    controls.classList.remove('hidden');
    populateXttsLanguages(s.languages);
    populateXttsSpeakers(s.speakers);
  } else if (s.loading) {
    txt.textContent = '⏳ Loading model…';
    txt.className = 'piper-status-checking';
    btn.classList.add('hidden');
    controls.classList.add('hidden');
    setTimeout(checkXttsStatus, 2000);
  } else if (s.error) {
    txt.textContent = '✗ Error: ' + s.error;
    txt.className = 'piper-status-missing';
    btn.textContent = '↺ Retry';
    btn.classList.remove('hidden');
    controls.classList.add('hidden');
  } else if (s.model_on_disk) {
    txt.textContent = 'Model on disk — click to load';
    txt.className = 'piper-status-missing';
    btn.textContent = '▶ Load XTTS v2';
    btn.classList.remove('hidden');
    controls.classList.add('hidden');
  } else {
    txt.textContent = '⚠ Not downloaded (~1.8 GB)';
    txt.className = 'piper-status-missing';
    btn.textContent = '⬇ Download & Load';
    btn.classList.remove('hidden');
    controls.classList.add('hidden');
  }
}

function populateXttsLanguages(languages) {
  const sel = $('xtts-language');
  sel.innerHTML = '';
  (languages || []).forEach(({ name, code }) => {
    const opt = document.createElement('option');
    opt.value = code;
    opt.textContent = name;
    if (code === 'en') opt.selected = true;
    sel.appendChild(opt);
  });
  // Also populate podcast speaker card XTTS language dropdowns
  $$('.spk-xtts-lang').forEach(el => {
    el.innerHTML = sel.innerHTML;
  });
}

function populateXttsSpeakers(speakers) {
  const sel = $('xtts-speaker');
  sel.innerHTML = '';
  (speakers || []).forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
  // Also populate podcast speaker card XTTS speaker dropdowns
  $$('.spk-xtts-speaker').forEach(el => {
    el.innerHTML = sel.innerHTML;
  });
}

async function handleLoadXtts() {
  $('btn-load-xtts').disabled = true;
  setStatus('Loading XTTS v2 (this may take a while on first run)…', 'running');
  try {
    await window.pywebview.api.start_xtts_load();
    setTimeout(checkXttsStatus, 1000);
  } catch (err) {
    setStatus('XTTS load failed: ' + err, 'error');
    $('btn-load-xtts').disabled = false;
  }
}

function initXttsControls() {
  $('btn-load-xtts').addEventListener('click', handleLoadXtts);

  // Voice mode radio toggle
  $$('input[name="xtts-voice-mode"]').forEach(r => {
    r.addEventListener('change', e => {
      xttsVoiceMode = e.target.value;
      $('xtts-builtin-panel').classList.toggle('hidden', xttsVoiceMode !== 'builtin');
      $('xtts-sample-panel').classList.toggle('hidden',  xttsVoiceMode !== 'sample');
    });
  });

  // Browse voice sample
  $('btn-browse-voice-sample').addEventListener('click', async () => {
    const path = await window.pywebview.api.browse_voice_sample();
    if (path) { xttsSamplePath = path; $('xtts-sample-path').value = path; }
  });

  // Test buttons
  $('btn-test-xtts').addEventListener('click', () => {
    testXttsVoice($('xtts-language').value, '', $('xtts-speaker').value);
  });
  $('btn-test-xtts-sample').addEventListener('click', () => {
    testXttsVoice($('xtts-language').value, xttsSamplePath, '');
  });
}

async function testXttsVoice(language, speakerWav, speaker) {
  if (isPreviewing || isGenerating) return;
  isPreviewing = true;
  setAllTestButtons(true);
  setStatus(`Testing XTTS voice…`, 'running');
  try {
    const r = await window.pywebview.api.test_xtts_voice(language, speakerWav || '', speaker || '');
    setStatus(r.success ? 'Ready' : 'Test error: ' + r.error, r.success ? 'idle' : 'error');
  } catch (err) {
    setStatus('Test failed: ' + err, 'error');
  } finally {
    isPreviewing = false;
    setAllTestButtons(false);
  }
}

function getXttsParams() {
  return {
    engine:           'xtts',
    xtts_language:    $('xtts-language').value,
    xtts_speaker:     xttsVoiceMode === 'builtin' ? $('xtts-speaker').value : '',
    xtts_speaker_wav: xttsVoiceMode === 'sample'  ? xttsSamplePath : '',
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Voice dropdown cascade
// ─────────────────────────────────────────────────────────────────────────────
function populateLanguages(slot) {
  if (!voiceData) return;
  const sel = $(slot.lang);
  const prev = sel.value;
  sel.innerHTML = '';
  Object.keys(voiceData).forEach(lang => {
    const opt = document.createElement('option');
    opt.value = lang;
    opt.textContent = lang;
    sel.appendChild(opt);
  });
  if (prev && voiceData[prev]) sel.value = prev;
  cascadeAccent(slot);
}

function cascadeAccent(slot) {
  if (!voiceData) return;
  const lang = $(slot.lang).value;
  const accentSel = $(slot.accent);
  const prev = accentSel.value;
  accentSel.innerHTML = '';

  if (!lang || !voiceData[lang]) { cascadeVoice(slot); return; }

  const accents = Object.keys(voiceData[lang].accents);
  accents.forEach(acc => {
    const opt = document.createElement('option');
    opt.value = acc;
    opt.textContent = acc;
    accentSel.appendChild(opt);
  });
  if (prev && accents.includes(prev)) accentSel.value = prev;
  cascadeVoice(slot);
}

function cascadeVoice(slot) {
  if (!voiceData) return;
  const lang = $(slot.lang).value;
  const accent = $(slot.accent).value;
  const voiceSel = $(slot.voice);
  const gender = getGender(slot.radio);
  const prevVoice = voiceSel.value;

  voiceSel.innerHTML = '<option value="">— select voice —</option>';

  if (!lang || !accent || !voiceData[lang]?.accents[accent]) return;

  const accentData = voiceData[lang].accents[accent];
  const groups = [];

  if (gender !== 'male')   groups.push({ label: 'Female', list: accentData.female || [] });
  if (gender !== 'female') groups.push({ label: 'Male',   list: accentData.male   || [] });

  groups.forEach(({ label, list }) => {
    if (!list.length) return;

    if (gender === 'all') {
      const hdr = document.createElement('option');
      hdr.disabled = true;
      hdr.textContent = `── ${label} ──`;
      voiceSel.appendChild(hdr);
    }

    list.forEach(id => {
      const opt = document.createElement('option');
      opt.value = id;
      const unavail = unavailableVoices.has(id);
      opt.textContent = unavail ? `${id} (unavailable)` : id;
      opt.disabled = unavail;
      if (unavail) opt.classList.add('unavailable');
      voiceSel.appendChild(opt);
    });
  });

  // Restore previous selection if still valid
  if (prevVoice) {
    const match = [...voiceSel.options].find(o => o.value === prevVoice && !o.disabled);
    if (match) voiceSel.value = prevVoice;
  }

  updateBlendSpec();
}

function getGender(radioName) {
  const checked = document.querySelector(`input[name="${radioName}"]:checked`);
  return checked ? checked.value : 'all';
}

// ─────────────────────────────────────────────────────────────────────────────
// Control wiring
// ─────────────────────────────────────────────────────────────────────────────
function initControls() {
  // Text area
  $('text-input').addEventListener('input', updateCharCount);

  // Load .txt file
  $('btn-load-file').addEventListener('click', async () => {
    const path = await window.pywebview.api.browse_file();
    if (!path) return;
    const content = await window.pywebview.api.load_file(path);
    $('text-input').value = content;
    updateCharCount();
  });

  $('btn-clear').addEventListener('click', () => {
    $('text-input').value = '';
    updateCharCount();
  });

  // Speed slider
  $('speed-slider').addEventListener('input', e => {
    $('speed-value').textContent = parseFloat(e.target.value).toFixed(2) + '×';
  });

  // Output dir browse
  $('btn-browse-dir').addEventListener('click', async () => {
    const dir = await window.pywebview.api.browse_folder();
    if (dir) $('output-dir').value = dir;
  });

  // Action buttons
  $('btn-preview').addEventListener('click', handlePreview);
  $('btn-generate').addEventListener('click', handleGenerateOrCancel);
  $('btn-download-piper').addEventListener('click', handlePiperDownload);
  $('btn-play-last').addEventListener('click', handlePlayLast);
  $('btn-open-folder').addEventListener('click', () => {
    const dir = $('output-dir').value || '';
    window.pywebview.api.open_folder(dir);
  });

  // Voice 1 cascade
  wireVoiceCascade(VOICE_SLOTS[0]);

  // Single-voice test button
  $('btn-test-v1').addEventListener('click', () => testVoice($('v1-voice').value));
}

function wireVoiceCascade(slot) {
  $(slot.lang).addEventListener('change', () => cascadeAccent(slot));
  $(slot.accent).addEventListener('change', () => cascadeVoice(slot));
  document.querySelectorAll(`input[name="${slot.radio}"]`)
    .forEach(r => r.addEventListener('change', () => cascadeVoice(slot)));
  $(slot.voice).addEventListener('change', updateBlendSpec);
}

// ─────────────────────────────────────────────────────────────────────────────
// Voice blending
// ─────────────────────────────────────────────────────────────────────────────
function initVoiceBlending() {
  $('blend-enable').addEventListener('change', e => {
    const on = e.target.checked;
    $('blend-panel').classList.toggle('hidden', !on);
    if (on) {
      populateLanguages(VOICE_SLOTS[1]);
      wireVoiceCascade(VOICE_SLOTS[1]);
    }
    updateBlendSpec();
  });

  $('blend-ratio-12').addEventListener('input', () => {
    updateBlendRatio12Display();
    updateBlendSpec();
  });

  $('btn-add-v3').addEventListener('click', () => {
    hasV3 = true;
    $('blend-v3-block').classList.remove('hidden');
    $('btn-add-v3').classList.add('hidden');
    populateLanguages(VOICE_SLOTS[2]);
    wireVoiceCascade(VOICE_SLOTS[2]);
    updateBlendSpec();
  });

  $('btn-remove-v3').addEventListener('click', () => {
    hasV3 = false;
    $('blend-v3-block').classList.add('hidden');
    $('btn-add-v3').classList.remove('hidden');
    updateBlendSpec();
  });

  $('blend-weight-3').addEventListener('input', () => {
    updateV3WeightDisplay();
    updateBlendSpec();
  });

  updateBlendRatio12Display();
}

function updateBlendRatio12Display() {
  const val = parseInt($('blend-ratio-12').value);
  const v1pct = val;
  const v2pct = 100 - val;
  $('blend-ratio-12-display').textContent = `${v1pct}% / ${v2pct}%`;

  // Update dual-color track
  const slider = $('blend-ratio-12');
  slider.style.background = `linear-gradient(to right,
    var(--accent) 0% ${v1pct}%,
    var(--accent-v2) ${v1pct}% 100%)`;
}

function updateV3WeightDisplay() {
  const val = parseInt($('blend-weight-3').value);
  $('blend-weight-3-display').textContent = val + '%';

  const slider = $('blend-weight-3');
  slider.style.background = `linear-gradient(to right,
    var(--text-muted) 0% ${val}%,
    var(--accent-v3) ${val}% 100%)`;
}

function updateBlendSpec() {
  if (!$('blend-enable').checked) {
    $('blend-spec-display').textContent = '—';
    return;
  }

  const specs = getVoiceSpecs();
  if (!specs.length) { $('blend-spec-display').textContent = '—'; return; }

  const total = specs.reduce((s, v) => s + v.weight, 0);
  const str = specs
    .map(v => `${v.voice_id}:${Math.round((v.weight / total) * 100)}`)
    .join(',');
  $('blend-spec-display').textContent = str;
}

// ─────────────────────────────────────────────────────────────────────────────
// Post-processing wiring
// ─────────────────────────────────────────────────────────────────────────────
function initPostProcessing() {
  linkCheckboxToSubOptions('proc-trim',     'trim-options');
  linkCheckboxToSubOptions('proc-fade-in',  'fade-in-options');
  linkCheckboxToSubOptions('proc-fade-out', 'fade-out-options');

  linkSliderToDisplay('trim-db',      'trim-db-value',      v => `${v < 0 ? '−' : ''}${Math.abs(v)} dB`);
  linkSliderToDisplay('fade-in-ms',   'fade-in-ms-value',   v => `${v} ms`);
  linkSliderToDisplay('fade-out-ms',  'fade-out-ms-value',  v => `${v} ms`);
}

function linkCheckboxToSubOptions(checkId, optionsId) {
  $(checkId).addEventListener('change', e => {
    $(optionsId).classList.toggle('hidden', !e.target.checked);
  });
}

function linkSliderToDisplay(sliderId, displayId, fmt) {
  $(sliderId).addEventListener('input', e => {
    $(displayId).textContent = fmt(e.target.value);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Character count
// ─────────────────────────────────────────────────────────────────────────────
function updateCharCount() {
  const len = $('text-input').value.length;
  $('char-count').textContent = `${len.toLocaleString()} characters`;
  // Soft warning at ~1600 chars (≈400 tokens)
  $('char-warning').classList.toggle('hidden', len < 1600);
}

// ─────────────────────────────────────────────────────────────────────────────
// Param collection
// ─────────────────────────────────────────────────────────────────────────────
function getVoiceSpecs() {
  const specs = [];

  const v1 = $('v1-voice').value;
  if (!v1) return specs;

  if (!$('blend-enable').checked) {
    specs.push({ voice_id: v1, weight: 100 });
    return specs;
  }

  const ratio12 = parseInt($('blend-ratio-12').value); // V1 %
  const v2 = $('v2-voice').value;

  if (!v2) {
    specs.push({ voice_id: v1, weight: 100 });
    return specs;
  }

  if (hasV3) {
    const v3 = $('v3-voice').value;
    const w3raw = parseInt($('blend-weight-3').value); // 0-100
    // V3 takes w3% of the total; remainder split by ratio12 between V1 & V2
    const w3 = w3raw;
    const remaining = 100 - w3;
    const w1 = Math.round(remaining * ratio12 / 100);
    const w2 = remaining - w1;
    if (w1 > 0) specs.push({ voice_id: v1, weight: w1 });
    if (w2 > 0) specs.push({ voice_id: v2, weight: w2 });
    if (v3 && w3 > 0) specs.push({ voice_id: v3, weight: w3 });
  } else {
    specs.push({ voice_id: v1, weight: ratio12 });
    specs.push({ voice_id: v2, weight: 100 - ratio12 });
  }

  return specs;
}

function getLangCode() {
  const lang = $('v1-language').value;
  if (!lang || !voiceData) return 'a';
  return voiceData[lang]?.lang_code || 'a';
}

function getSplitPattern() {
  const raw = $('split-pattern').value;
  // unescape \\n sequences stored as JS string
  return raw.replace(/\\n/g, '\n');
}

function getPostProcessingOptions() {
  const trimOn = $('proc-trim').checked;
  const fadeInOn = $('proc-fade-in').checked;
  const fadeOutOn = $('proc-fade-out').checked;
  return {
    normalize:    $('proc-normalize').checked,
    trim_silence: trimOn,
    trim_db:      trimOn ? parseInt($('trim-db').value) : -40,
    noise_gate:   $('proc-noise-gate').checked,
    fade_in_ms:   fadeInOn ? parseInt($('fade-in-ms').value) : 0,
    fade_out_ms:  fadeOutOn ? parseInt($('fade-out-ms').value) : 0,
  };
}

function buildParams() {
  // Shared params for both engines
  const shared = {
    engine:          currentEngine,
    text:            $('text-input').value,
    speed:           parseFloat($('speed-slider').value),
    split_pattern:   getSplitPattern(),
    output_format:   $('output-format').value,
    output_filename: $('output-filename').value.trim(),
    output_dir:      $('output-dir').value.trim(),
    post_processing: getPostProcessingOptions(),
  };

  if (currentEngine === 'piper') {
    return { ...shared, piper_voice: $('piper-voice-select').value };
  }
  if (currentEngine === 'xtts') {
    return { ...shared, ...getXttsParams() };
  }
  return { ...shared, voices: getVoiceSpecs(), lang_code: getLangCode() };
}

// ─────────────────────────────────────────────────────────────────────────────
// Preview
// ─────────────────────────────────────────────────────────────────────────────
async function handlePreview() {
  if (isGenerating || isPreviewing) return;

  const text = $('text-input').value.trim();
  if (!text) { setStatus('No text to preview', 'error'); return; }

  const speed = parseFloat($('speed-slider').value);
  let previewParams;

  if (currentEngine === 'piper') {
    const piperVoice = $('piper-voice-select').value;
    if (!piperVoice) { setStatus('Select a Piper voice first', 'error'); return; }
    previewParams = { engine: 'piper', text, piper_voice: piperVoice, speed };
  } else {
    const specs = getVoiceSpecs();
    if (!specs.length) { setStatus('Select a voice first', 'error'); return; }
    previewParams = { engine: 'kokoro', text, voices: specs, lang_code: getLangCode(), speed };
  }

  isPreviewing = true;
  $('btn-preview').disabled = true;
  setStatus('Generating preview…', 'running');

  try {
    const result = await window.pywebview.api.preview(previewParams);
    setStatus(result.success ? 'Preview complete' : 'Preview error: ' + result.error,
              result.success ? 'success' : 'error');
  } catch (err) {
    setStatus('Preview failed: ' + err, 'error');
  } finally {
    isPreviewing = false;
    $('btn-preview').disabled = false;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Generate / Cancel
// ─────────────────────────────────────────────────────────────────────────────
async function handleGenerateOrCancel() {
  if (currentMode === 'podcast') { handleGeneratePodcast(); return; }

  if (isGenerating) {
    await window.pywebview.api.cancel();
    setStatus('Cancelling…', 'running');
    return;
  }

  const text = $('text-input').value.trim();
  if (!text) { setStatus('No text to generate', 'error'); return; }

  if (currentEngine === 'piper') {
    if (!$('piper-voice-select').value) { setStatus('Select a Piper voice first', 'error'); return; }
  } else {
    const specs = getVoiceSpecs();
    if (!specs.length) { setStatus('Select a voice first', 'error'); return; }
  }

  const params = buildParams();

  try {
    const resp = await window.pywebview.api.generate(params);
    if (!resp.started) {
      setStatus('Could not start: ' + (resp.error || 'unknown error'), 'error');
      return;
    }
  } catch (err) {
    setStatus('Generate failed: ' + err, 'error');
    return;
  }

  isGenerating = true;
  setGeneratingUI(true);
  setStatus('Starting generation…', 'running');
  showProgress(true);
  startProgressPolling();
}

async function handleCancel() {
  await window.pywebview.api.cancel();
  setStatus('Cancelling…', 'running');
}

// ─────────────────────────────────────────────────────────────────────────────
// Progress polling
// ─────────────────────────────────────────────────────────────────────────────
function startProgressPolling() {
  if (progressInterval) clearInterval(progressInterval);
  progressInterval = setInterval(async () => {
    try {
      const p = await window.pywebview.api.get_progress();
      applyProgress(p);

      if (!p.running && p.result !== null && p.result !== undefined) {
        clearInterval(progressInterval);
        progressInterval = null;
        isGenerating = false;
        setGeneratingUI(false);
        showProgress(false);
        handleGenerationResult(p.result, p.last_output_path);
      }
    } catch (_) {}
  }, 400);
}

function applyProgress(p) {
  const pct = Math.round((p.progress || 0) * 100);
  $('progress-fill').style.width = pct + '%';

  if (p.total_chunks > 0) {
    const label = currentMode === 'podcast'
      ? `Line ${p.current_chunk} of ${p.total_chunks}…`
      : `Generating chunk ${p.current_chunk} of ${p.total_chunks}…`;
    $('progress-label').textContent = label;
  } else {
    $('progress-label').textContent = pct < 100 ? 'Generating…' : 'Finishing…';
  }
}

function handleGenerationResult(result, outputPath) {
  if (result.success) {
    lastOutputPath = result.output_path || outputPath;
    const dur = result.duration_seconds
      ? ` (${result.duration_seconds.toFixed(1)}s)`
      : '';
    setStatus(`Saved: ${lastOutputPath}${dur}`, 'success');
    $('btn-play-last').disabled = false;
  } else {
    setStatus('Error: ' + result.error, 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Play last output
// ─────────────────────────────────────────────────────────────────────────────
async function handlePlayLast() {
  if (!lastOutputPath) return;
  try {
    setStatus('Playing…', 'running');
    const result = await window.pywebview.api.preview({
      text:      '.',           // dummy — play_last re-routes in backend
      voices:    getVoiceSpecs().length ? getVoiceSpecs() : [{ voice_id: 'af_heart', weight: 100 }],
      lang_code: getLangCode(),
      speed:     1.0,
      play_file: lastOutputPath,  // backend checks this key
    });
    setStatus(result.success ? 'Playback complete' : ('Playback error: ' + result.error),
              result.success ? 'success' : 'error');
  } catch (err) {
    setStatus('Playback failed: ' + err, 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// UI helpers
// ─────────────────────────────────────────────────────────────────────────────
function setStatus(msg, type) {
  const el = $('status-text');
  el.textContent = msg;
  el.className = 'status-' + (type || 'idle');
}

function showProgress(visible) {
  $('progress-area').classList.toggle('visible', visible);
  if (!visible) {
    $('progress-fill').style.width = '0%';
    $('progress-label').textContent = '';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Voice test (plays hardcoded sample sentence)
// ─────────────────────────────────────────────────────────────────────────────
async function testVoice(voiceId) {
  if (!voiceId) { setStatus('Select a voice first', 'error'); return; }
  if (isGenerating || isPreviewing) { setStatus('Busy — wait for current task to finish', 'error'); return; }

  isPreviewing = true;
  setAllTestButtons(true);
  setStatus(`Testing ${voiceId}…`, 'running');

  try {
    const result = await window.pywebview.api.test_voice(voiceId);
    setStatus(result.success ? 'Ready' : ('Test error: ' + result.error),
              result.success ? 'idle' : 'error');
  } catch (err) {
    setStatus('Test failed: ' + err, 'error');
  } finally {
    isPreviewing = false;
    setAllTestButtons(false);
  }
}

function setAllTestButtons(disabled) {
  $$('.btn-test-voice').forEach(btn => { btn.disabled = disabled; });
}

function setGeneratingUI(on) {
  const btn = $('btn-generate');
  const label = currentMode === 'podcast' ? '⬡ Generate Podcast' : '⬡ Generate';
  btn.textContent = on ? '✕ Cancel' : label;
  btn.classList.toggle('cancelling', on);
  $('btn-preview').disabled = on;
  setAllTestButtons(on);
}

// ─────────────────────────────────────────────────────────────────────────────
// Mode switcher (Single Voice ↔ Podcast)
// ─────────────────────────────────────────────────────────────────────────────
function initModeSwitcher() {
  $$('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => switchMode(btn.dataset.mode));
  });
  $('btn-parse-script').addEventListener('click', handleParseScript);
  $('between-speakers-ms').addEventListener('input', e => {
    $('between-speakers-value').textContent = e.target.value + ' ms';
  });
}

function switchMode(mode) {
  currentMode = mode;
  const isPodcast = mode === 'podcast';

  $$('.mode-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.mode === mode)
  );

  $('podcast-panel').classList.toggle('hidden', !isPodcast);
  $('single-voice-sections').classList.toggle('hidden', isPodcast);
  $('speed-row').classList.toggle('hidden', isPodcast);
  $('split-row').classList.toggle('hidden', isPodcast);
  $('btn-preview').classList.toggle('hidden', isPodcast);

  // Update generate button label
  const btn = $('btn-generate');
  if (!isGenerating) btn.textContent = isPodcast ? '⬡ Generate Podcast' : '⬡ Generate';
}

// ─────────────────────────────────────────────────────────────────────────────
// Script parsing
// ─────────────────────────────────────────────────────────────────────────────
async function handleParseScript() {
  const text = $('text-input').value.trim();
  if (!text) { setStatus('Paste or load a script first', 'error'); return; }

  $('btn-parse-script').disabled = true;
  setStatus('Parsing script…', 'running');

  try {
    const result = await window.pywebview.api.parse_script(text);
    if (!result.success) {
      setStatus('Parse error: ' + result.error, 'error');
      return;
    }
    parsedScript = result;
    const statsBar = $('podcast-stats-bar');
    statsBar.textContent =
      `${result.speakers.length} speakers · ${result.line_count} lines`;
    statsBar.classList.remove('hidden');
    renderSpeakerCards(result.speakers, result.speaker_line_counts);
    $('podcast-timing-section').style.display = '';
    setStatus(
      `Parsed: ${result.speakers.length} speakers, ${result.line_count} lines`,
      'success'
    );
  } catch (err) {
    setStatus('Parse failed: ' + err, 'error');
  } finally {
    $('btn-parse-script').disabled = false;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Speaker cards
// ─────────────────────────────────────────────────────────────────────────────

// Cycle through these for default voice assignments
const _DEFAULT_VOICES = [
  'af_heart', 'am_echo', 'bf_emma', 'bm_george',
  'af_bella', 'am_michael', 'af_river', 'am_fenrir',
];

function getAllVoicesFlat() {
  if (!voiceData) return [];
  const out = [];
  for (const [lang, langData] of Object.entries(voiceData)) {
    for (const accentData of Object.values(langData.accents)) {
      for (const id of [...(accentData.female || []), ...(accentData.male || [])]) {
        if (!unavailableVoices.has(id))
          out.push({ id, lang, langCode: langData.lang_code });
      }
    }
  }
  return out;
}

function renderSpeakerCards(speakers, lineCounts) {
  const container = $('speaker-cards');
  container.innerHTML = '';
  speakerCardEls = {};

  const allVoices = getAllVoicesFlat();

  speakers.forEach((speaker, idx) => {
    const defaultVoice = _DEFAULT_VOICES[idx % _DEFAULT_VOICES.length];
    const count = lineCounts?.[speaker] ?? 0;
    const card = buildSpeakerCard(speaker, defaultVoice, count, allVoices);
    container.appendChild(card);
  });
}

function buildSpeakerCard(speaker, defaultVoice, lineCount, allVoices) {
  const card = document.createElement('div');
  card.className = 'speaker-card';

  // Group voices by language for the <optgroup> approach
  const byLang = {};
  for (const v of allVoices) {
    if (!byLang[v.lang]) byLang[v.lang] = [];
    byLang[v.lang].push(v);
  }

  const optionsHtml = Object.entries(byLang).map(([lang, voices]) => {
    const opts = voices.map(v =>
      `<option value="${v.id}" data-lang="${v.langCode}"${v.id === defaultVoice ? ' selected' : ''}>${v.id}</option>`
    ).join('');
    return `<optgroup label="${lang}">${opts}</optgroup>`;
  }).join('');

  // Build XTTS language options from loaded data
  const xttsLangOpts = xttsData.languages.map(({ name, code }) =>
    `<option value="${code}"${code === 'en' ? ' selected' : ''}>${name}</option>`
  ).join('');
  const xttsSpeakerOpts = xttsData.speakers.map(s =>
    `<option value="${s}">${s}</option>`
  ).join('');

  card.innerHTML = `
    <div class="speaker-card-header">
      <span class="speaker-name">${speaker}</span>
      <span class="speaker-line-count">${lineCount} line${lineCount !== 1 ? 's' : ''}</span>
    </div>

    <div class="speaker-engine-toggle">
      <button class="engine-mini-btn active" data-engine="kokoro">Kokoro</button>
      <button class="engine-mini-btn"        data-engine="xtts">XTTS v2</button>
    </div>

    <!-- Kokoro controls -->
    <div class="spk-kokoro-controls">
      <div class="form-row">
        <label>Voice</label>
        <select class="select spk-voice">${optionsHtml}</select>
        <button class="btn btn-ghost btn-small btn-test-voice spk-test-btn"
                title="Test this voice">▶</button>
      </div>
      <div class="form-row">
        <label>Speed</label>
        <div class="slider-with-value">
          <input type="range" class="slider spk-speed" min="0.5" max="2.0" step="0.05" value="1.0">
          <span class="slider-value spk-speed-val">1.00×</span>
        </div>
      </div>
    </div>

    <!-- XTTS controls -->
    <div class="spk-xtts-controls hidden">
      <div class="form-row">
        <label>Language</label>
        <select class="select spk-xtts-lang">${xttsLangOpts || '<option value="en">English</option>'}</select>
      </div>
      <div class="form-row">
        <label>Speaker</label>
        <select class="select spk-xtts-speaker">${xttsSpeakerOpts || '<option value="">Load model first</option>'}</select>
        <button class="btn btn-ghost btn-small btn-test-voice spk-test-xtts-btn"
                title="Test XTTS voice">▶</button>
      </div>
      <div class="form-row">
        <label>Or sample</label>
        <div class="path-input-group">
          <input type="text" class="text-input spk-xtts-wav" placeholder="Optional .wav file" readonly>
          <button class="btn btn-secondary btn-small spk-browse-wav">Browse</button>
        </div>
      </div>
    </div>
  `;

  // Speed slider
  const speedEl  = card.querySelector('.spk-speed');
  const speedVal = card.querySelector('.spk-speed-val');
  speedEl.addEventListener('input', () => {
    speedVal.textContent = parseFloat(speedEl.value).toFixed(2) + '×';
  });

  // Kokoro test button
  card.querySelector('.spk-test-btn').addEventListener('click', () => {
    testVoice(card.querySelector('.spk-voice').value);
  });

  // XTTS test button
  card.querySelector('.spk-test-xtts-btn').addEventListener('click', () => {
    const lang = card.querySelector('.spk-xtts-lang').value;
    const spk  = card.querySelector('.spk-xtts-speaker').value;
    const wav  = card.querySelector('.spk-xtts-wav').value;
    testXttsVoice(lang, wav, spk);
  });

  // XTTS browse voice sample
  card.querySelector('.spk-browse-wav').addEventListener('click', async () => {
    const path = await window.pywebview.api.browse_voice_sample();
    if (path) card.querySelector('.spk-xtts-wav').value = path;
  });

  // Engine mini-toggle
  card.querySelectorAll('.engine-mini-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      card.querySelectorAll('.engine-mini-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const isXtts = btn.dataset.engine === 'xtts';
      card.querySelector('.spk-kokoro-controls').classList.toggle('hidden',  isXtts);
      card.querySelector('.spk-xtts-controls').classList.toggle('hidden', !isXtts);
    });
  });

  speakerCardEls[speaker] = {
    card,
    voiceEl: card.querySelector('.spk-voice'),
    speedEl,
  };

  return card;
}

function collectSpeakerVoices() {
  const result = {};
  for (const [speaker, els] of Object.entries(speakerCardEls)) {
    const card = els.card;
    const activeEngineBtn = card.querySelector('.engine-mini-btn.active');
    const engine = activeEngineBtn ? activeEngineBtn.dataset.engine : 'kokoro';

    if (engine === 'xtts') {
      result[speaker] = {
        engine:          'xtts',
        xtts_language:   card.querySelector('.spk-xtts-lang').value,
        xtts_speaker:    card.querySelector('.spk-xtts-speaker').value,
        xtts_speaker_wav: card.querySelector('.spk-xtts-wav').value || '',
        speed:           parseFloat(els.speedEl.value),
      };
    } else {
      const voiceId = els.voiceEl.value;
      const selected = els.voiceEl.selectedOptions[0];
      result[speaker] = {
        engine:   'kokoro',
        voice_id: voiceId,
        lang_code: selected?.dataset.lang || 'a',
        speed:    parseFloat(els.speedEl.value),
      };
    }
  }
  return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// Podcast generation
// ─────────────────────────────────────────────────────────────────────────────
async function handleGeneratePodcast() {
  if (isGenerating) {
    await window.pywebview.api.cancel();
    setStatus('Cancelling…', 'running');
    return;
  }

  const text = $('text-input').value.trim();
  if (!text) { setStatus('No script text', 'error'); return; }
  if (!parsedScript) { setStatus('Click "Parse Script" first', 'error'); return; }
  if (Object.keys(speakerCardEls).length === 0) {
    setStatus('No speakers assigned', 'error'); return;
  }

  const params = {
    text,
    speaker_voices:      collectSpeakerVoices(),
    output_format:       $('output-format').value,
    output_filename:     $('output-filename').value.trim(),
    output_dir:          $('output-dir').value.trim(),
    post_processing:     getPostProcessingOptions(),
    between_speakers_ms: parseInt($('between-speakers-ms').value),
  };

  try {
    const resp = await window.pywebview.api.generate_podcast(params);
    if (!resp.started) {
      setStatus('Could not start: ' + (resp.error || ''), 'error');
      return;
    }
  } catch (err) {
    setStatus('Failed to start: ' + err, 'error');
    return;
  }

  isGenerating = true;
  setGeneratingUI(true);
  setStatus('Generating podcast…', 'running');
  showProgress(true);
  startProgressPolling();
}
