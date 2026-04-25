'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
let voiceData = null;
let unavailableVoices = new Set();
let progressInterval = null;
let isGenerating = false;
let isPreviewing = false;
let hasV3 = false;
let lastOutputPath = null;

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
document.addEventListener('DOMContentLoaded', () => {
  initControls();
  initVoiceBlending();
  initPostProcessing();
  loadVoiceData();
  loadDefaultOutputDir();
});

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

    VOICE_SLOTS.forEach(slot => populateLanguages(slot));
    // Trigger initial cascade for v1 so dropdowns aren't empty
    cascadeAccent(VOICE_SLOTS[0]);
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
  $('btn-play-last').addEventListener('click', handlePlayLast);
  $('btn-open-folder').addEventListener('click', () => {
    const dir = $('output-dir').value || '';
    window.pywebview.api.open_folder(dir);
  });

  // Voice 1 cascade
  wireVoiceCascade(VOICE_SLOTS[0]);
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
  return {
    text:              $('text-input').value,
    voices:            getVoiceSpecs(),
    lang_code:         getLangCode(),
    speed:             parseFloat($('speed-slider').value),
    split_pattern:     getSplitPattern(),
    output_format:     $('output-format').value,
    output_filename:   $('output-filename').value.trim(),
    output_dir:        $('output-dir').value.trim(),
    post_processing:   getPostProcessingOptions(),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Preview
// ─────────────────────────────────────────────────────────────────────────────
async function handlePreview() {
  if (isGenerating || isPreviewing) return;

  const text = $('text-input').value.trim();
  if (!text) { setStatus('No text to preview', 'error'); return; }

  const specs = getVoiceSpecs();
  if (!specs.length) { setStatus('Select a voice first', 'error'); return; }

  isPreviewing = true;
  $('btn-preview').disabled = true;
  setStatus('Generating preview…', 'running');

  try {
    const result = await window.pywebview.api.preview({
      text,
      voices:    specs,
      lang_code: getLangCode(),
      speed:     parseFloat($('speed-slider').value),
    });

    if (result.success) {
      setStatus('Preview complete', 'success');
    } else {
      setStatus('Preview error: ' + result.error, 'error');
    }
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
  if (isGenerating) {
    await window.pywebview.api.cancel();
    setStatus('Cancelling…', 'running');
    return;
  }

  const text = $('text-input').value.trim();
  if (!text) { setStatus('No text to generate', 'error'); return; }

  const specs = getVoiceSpecs();
  if (!specs.length) { setStatus('Select a voice first', 'error'); return; }

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
    $('progress-label').textContent =
      `Generating chunk ${p.current_chunk} of ${p.total_chunks}…`;
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

function setGeneratingUI(on) {
  const btn = $('btn-generate');
  btn.textContent = on ? '✕ Cancel' : '⬡ Generate';
  btn.classList.toggle('cancelling', on);
  $('btn-preview').disabled = on;
}
