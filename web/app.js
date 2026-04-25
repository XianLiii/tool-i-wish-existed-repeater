// Repeater — single-file app logic.

const audio = document.getElementById('audio');
const reader = document.getElementById('reader');
const bookTitle = document.getElementById('bookTitle');
const btnPlay = document.getElementById('btnPlay');
const btnPrev = document.getElementById('btnPrev');
const btnNext = document.getElementById('btnNext');
const repeatCountInput = document.getElementById('repeatCount');
const unitPicker = document.getElementById('unitPicker');
const unitTrigger = document.getElementById('unitTrigger');
const unitPopover = document.getElementById('unitPopover');
const unitLabelEl = document.getElementById('unitLabel');
const unitIconEl = document.getElementById('unitIcon');

const UNIT_META = {
  sentence: { label: 'Sentence', svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="11" width="16" height="2" rx="1"/></svg>' },
  paragraph: { label: 'Paragraph', svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="6" width="16" height="1.8" rx="0.9"/><rect x="4" y="11.1" width="16" height="1.8" rx="0.9"/><rect x="4" y="16.2" width="10" height="1.8" rx="0.9"/></svg>' },
  chapter: { label: 'Chapter', svg: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v15H5.5A1.5 1.5 0 0 1 4 17.5V5.5Z"/><path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13v15h5.5a1.5 1.5 0 0 0 1.5-1.5V5.5Z"/></svg>' },
};

function applyUnitUI() {
  const meta = UNIT_META[state.unit] || UNIT_META.sentence;
  unitLabelEl.textContent = meta.label;
  unitIconEl.innerHTML = meta.svg;
  for (const btn of unitPopover.querySelectorAll('button')) {
    btn.classList.toggle('current', btn.dataset.value === state.unit);
  }
}

function setUnitType(val) {
  if (!(val in UNIT_META) || val === state.unit) { applyUnitUI(); return; }
  state.unit = val;
  applyUnitUI();
  // Re-anchor the unitRef on the current sentence under the new grouping
  if (state.unitRef) {
    let sentIdx;
    if (state.unitRef.type === 'sentence') sentIdx = state.unitRef.idx;
    else if (state.unitRef.type === 'paragraph') sentIdx = state.paragraphs[state.unitRef.idx].sentRange[0];
    else sentIdx = state.chapters[state.unitRef.idx].sentRange[0];
    setUnit(state.unit, unitIdxFromSentence(state.unit, sentIdx));
  }
}

unitTrigger.addEventListener('click', (e) => {
  e.stopPropagation();
  unitPopover.hidden = !unitPopover.hidden;
});
unitPopover.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-value]');
  if (!btn) return;
  setUnitType(btn.dataset.value);
  unitPopover.hidden = true;
});
document.addEventListener('click', (e) => {
  if (!unitPicker.contains(e.target)) unitPopover.hidden = true;
});

const repeatCounter = document.getElementById('repeatCounter');
const nowLabel = document.getElementById('nowLabel');
const nowPrimary = document.getElementById('nowPrimary');
const nowSecondary = document.getElementById('nowSecondary');

// Sets the bottom-bar position label. Two parts so layout-B mobile can stack
// "Sentence 1" (primary, more prominent) above "Ch 1" (secondary, muted).
// On desktop both render inline with a " · " separator (CSS-controlled).
function setNowLabel(primary, secondary) {
  nowPrimary.textContent = primary || '';
  nowSecondary.textContent = secondary || '';
  nowLabel.classList.toggle('no-secondary', !secondary);
}
const curTime = document.getElementById('curTime');
const totalTime = document.getElementById('totalTime');
const seek = document.getElementById('seek');
const tocToggle = document.getElementById('tocToggle');
const toc = document.getElementById('toc');
const tocList = document.getElementById('tocList');

const state = {
  library: [],       // [{id, title, author, manifest}]
  bookId: null,
  book: null,
  sentences: [],     // flat [{id, text, start, end, chIdx, paIdx, seIdx, node}]
  paragraphs: [],    // flat [{start, end, sentRange:[i,j]}]
  chapters: [],      // [{start, end, sentRange:[i,j], paraRange:[i,j]}]
  unit: 'sentence',
  unitRef: null,
  repeatRemaining: -1,
  repeatDone: 0,
  tolerance: 0.08,
  focusMode: true,
  lastFocusIdx: -1,
  inLoopPause: false,    // true while waiting LOOP_PAUSE_MS between repetitions
};

// "Breathing" gap inserted at every loop boundary so the listener perceives
// a clean restart instead of an abrupt cut. Audio file is untouched —
// we just pause the player for `getPauseMs()` before jumping back to start.
// User configurable inline in the bottom bar (0–60s).
const PAUSE_KEY = 'repeater.pauseSeconds';
const PAUSE_DEFAULT = 0.5;
const PAUSE_MAX = 60;
function getPauseMs() {
  const v = parseFloat(document.getElementById('pauseSeconds')?.value);
  if (isNaN(v) || v < 0) return PAUSE_DEFAULT * 1000;
  return Math.min(PAUSE_MAX, v) * 1000;
}
let pendingLoopRef = null;
let pendingLoopTimer = null;
function cancelPendingLoop() {
  pendingLoopRef = null;
  if (pendingLoopTimer) { clearTimeout(pendingLoopTimer); pendingLoopTimer = null; }
  state.inLoopPause = false;
}

const LAST_BOOK_KEY = 'repeater.lastBook';
const PROGRESS_URL = 'data/progress.json';
let progressByBook = {}; // {<bookId>: {t, unit, updated}}

// ---- Utility ----

function fmt(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function getRepeatCount() {
  const raw = repeatCountInput.value.trim();
  if (raw === '' || raw === '∞' || raw === 'inf') return -1;
  const n = parseInt(raw, 10);
  return isNaN(n) || n < 1 ? -1 : n;
}

// ---- Load & render ----

async function loadLibrary() {
  try {
    const r = await fetch('library.json?t=' + Date.now());
    if (r.ok) {
      const data = await r.json();
      state.library = data.books || [];
    }
  } catch { state.library = []; }
  // Fallback for legacy single-book setup
  if (!state.library.length) {
    state.library = [{ id: 'book', title: 'Book', manifest: 'manifest.json' }];
  }
}

async function loadProgress() {
  try {
    const r = await fetch(PROGRESS_URL + '?t=' + Date.now());
    if (r.ok) progressByBook = (await r.json()) || {};
  } catch {}
}

async function saveProgress() {
  try {
    await fetch(PROGRESS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(progressByBook),
    });
  } catch {}
}

function pickInitialBookId() {
  const saved = localStorage.getItem(LAST_BOOK_KEY);
  if (saved && state.library.find(b => b.id === saved)) return saved;
  return state.library[0]?.id;
}

async function loadBook(bookId) {
  const entry = state.library.find(b => b.id === bookId);
  if (!entry) return;
  state.bookId = bookId;
  localStorage.setItem(LAST_BOOK_KEY, bookId);

  const res = await fetch(entry.manifest + '?t=' + Date.now());
  const data = await res.json();
  state.book = data;
  renderTitle();

  // Reset unit state between books
  state.unitRef = null;
  state.repeatDone = 0;
  state.lastFocusIdx = -1;

  audio.src = new URL(data.audio, new URL(entry.manifest, location.href)).href;

  buildIndexes(data);
  renderBook(data);
  renderTOC(data);
  renderLibraryMenu();
  markSentencesWithNotes();
  // Refresh side panels so vocab/notes counts + lists reflect the new book
  // (both are stored per-bookId, so the visible slice changes when we switch).
  renderVocab();
  renderNotes();

  audio.addEventListener('loadedmetadata', () => {
    totalTime.textContent = fmt(audio.duration);
    seek.max = audio.duration;
    // Restore progress. Also anchor state.unitRef to the sentence covering
    // the restored time, so clicking ▶ resumes from here instead of jumping
    // back to the first sentence (which happens when unitRef is null).
    const p = progressByBook[bookId];
    if (p && p.t > 0 && p.t < audio.duration) {
      if (p.unit) setUnitType(p.unit);
      // Anchor unitRef on the sentence covering p.t so the player knows
      // which sentence to loop when ▶ is pressed. Pass seek:false so
      // setUnit doesn't move the playhead to that sentence's start —
      // we want to resume mid-sentence at the exact saved time.
      const sentIdx = state.sentences.findIndex(
        s => s.start <= p.t && p.t < s.end
      );
      if (sentIdx >= 0) {
        setUnit(state.unit, unitIdxFromSentence(state.unit, sentIdx), { seek: false });
      }
      audio.currentTime = p.t;
    }
  }, { once: true });
}

function renderTitle() {
  bookTitle.innerHTML = `${state.book?.title || 'Loading…'}<span class="title-caret">▾</span>`;
}

async function load() {
  applyUnitUI();
  await Promise.all([loadLibrary(), loadProgress(), loadNotes(), loadStats()]);
  const id = pickInitialBookId();
  if (id) await loadBook(id);
}

function buildIndexes(book) {
  state.sentences = [];
  state.paragraphs = [];
  state.chapters = [];
  for (let ci = 0; ci < book.chapters.length; ci++) {
    const ch = book.chapters[ci];
    const chStartSent = state.sentences.length;
    const chStartPara = state.paragraphs.length;
    for (let pi = 0; pi < ch.paragraphs.length; pi++) {
      const pa = ch.paragraphs[pi];
      const paStart = state.sentences.length;
      const globalParaIdx = state.paragraphs.length;
      for (let si = 0; si < pa.sentences.length; si++) {
        const se = pa.sentences[si];
        state.sentences.push({
          globalIdx: state.sentences.length,
          chIdx: ci, paIdx: pi, seIdx: si,
          globalParaIdx,
          text: se.text,
          start: se.start,
          end: se.end,
          matchRatio: se.match_ratio || 0,
          node: null,
        });
      }
      state.paragraphs.push({
        chIdx: ci, paIdx: pi,
        start: pa.start,
        end: pa.end,
        sentRange: [paStart, state.sentences.length],
      });
    }
    state.chapters.push({
      chIdx: ci,
      title: ch.title,
      start: ch.start,
      end: ch.end,
      sentRange: [chStartSent, state.sentences.length],
      paraRange: [chStartPara, state.paragraphs.length],
    });
  }

  // Whisper's word-level timings in continuous speech set word[i].end == word[i+1].start,
  // so raw sent[i].end often equals sent[i+1].start — playback bleeds into the next word.
  // Tighten each sentence's end: leave a small silent gap before the next sentence begins.
  const BOUNDARY_GAP = 0.12;
  const MIN_LEN = 0.2;
  for (let i = 0; i < state.sentences.length - 1; i++) {
    const cur = state.sentences[i];
    const nxt = state.sentences[i + 1];
    if (cur.start == null || cur.end == null || nxt.start == null) continue;
    const target = nxt.start - BOUNDARY_GAP;
    if (cur.end > target) {
      cur.end = Math.max(cur.start + MIN_LEN, target);
    }
  }
  // Re-roll paragraph and chapter ends from trimmed sentence ends
  for (const p of state.paragraphs) {
    const last = state.sentences[p.sentRange[1] - 1];
    if (last && last.end != null) p.end = last.end;
  }
  for (const c of state.chapters) {
    const last = state.sentences[c.sentRange[1] - 1];
    if (last && last.end != null) c.end = last.end;
  }
}

// Sentences whose first token is one of these structural markers get
// rendered on their own line via CSS (.sentence.line-break). Covers both
// well-formatted Whisper output (A. B. C. D.) and raw word-level output
// (lowercase "a they're leaving", "number one" without caps/punctuation).
const STRUCTURAL_LINE_START = new RegExp(
  "^(?:" +
    // Option letters with period: "A. He's..." / "a. he's..."
    "[A-Da-d]\\.\\s+\\S" +
    // Option letters as bare char followed by Capital word (merged): "A He's..."
    "|[A-D]\\s+[A-Z]" +
    // Option letters lowercase + pronoun/contraction (word-level text)
    "|[a-d]\\s+(?:he|she|it|they|we|you|I|a|an|the|some|many|most|both|" +
      "either|there|this|that|these|those|\\w+'(?:s|re|ve|ll|d|m))\\b" +
    // Section headers
    "|Number\\s+(?:\\d+|one|two|three|four|five|six|seven|eight|nine|ten|" +
      "eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|" +
      "nineteen|twenty)\\b" +
    "|Questions\\s+\\d+\\s+through" +
    "|Part\\s+\\d+" +
    "|Directions\\b" +
    "|Go on to" +
    "|Look at the picture" +
    "|Now,?\\s*(?:part|listen)\\b" +
    "|This is the end" +
  ")", "i");

function renderBook(book) {
  reader.innerHTML = '';
  for (let ci = 0; ci < book.chapters.length; ci++) {
    const ch = book.chapters[ci];
    const chDiv = document.createElement('section');
    chDiv.className = 'chapter';
    chDiv.id = `ch-${ci}`;
    const h = document.createElement('h2');
    h.textContent = ch.title;
    chDiv.appendChild(h);

    for (let pi = 0; pi < ch.paragraphs.length; pi++) {
      const pa = ch.paragraphs[pi];
      const pDiv = document.createElement('p');
      pDiv.className = 'paragraph';
      pDiv.dataset.chIdx = ci;
      pDiv.dataset.paIdx = pi;

      for (let si = 0; si < pa.sentences.length; si++) {
        const se = pa.sentences[si];
        const span = document.createElement('span');
        span.className = 'sentence';
        if (!(se.start >= 0) || (se.match_ratio || 0) < 0.1) {
          span.classList.add('no-timing');
        }
        // Structural sentences (TOEIC option letters, section headers, etc.)
        // get their own visual line so the question reads like a test sheet
        // instead of a wall of text. Harmless on prose — regular narrative
        // sentences never start with "A." / "B." / "Number N" / etc.
        if (STRUCTURAL_LINE_START.test(se.text)) {
          span.classList.add('line-break');
        }
        span.textContent = (si === 0 ? '' : ' ') + se.text;
        span.dataset.globalIdx = findGlobalIdx(ci, pi, si);
        span.addEventListener('click', () => onSentenceClick(+span.dataset.globalIdx));
        pDiv.appendChild(span);
        state.sentences[+span.dataset.globalIdx].node = span;
      }
      chDiv.appendChild(pDiv);
    }
    reader.appendChild(chDiv);
  }
}

function findGlobalIdx(ci, pi, si) {
  // Linear lookup OK — called only during render.
  for (let i = 0; i < state.sentences.length; i++) {
    const s = state.sentences[i];
    if (s.chIdx === ci && s.paIdx === pi && s.seIdx === si) return i;
  }
  return -1;
}

function renderTOC(book) {
  tocList.innerHTML = '';
  for (let ci = 0; ci < book.chapters.length; ci++) {
    const ch = book.chapters[ci];
    const li = document.createElement('li');
    const count = state.chapters[ci].sentRange[1] - state.chapters[ci].sentRange[0];
    li.innerHTML = `<span class="name">${ch.title}</span><span class="count">${count}</span>`;
    li.addEventListener('click', () => {
      document.getElementById(`ch-${ci}`).scrollIntoView({ behavior: 'smooth', block: 'start' });
      // On mobile, close the panel so the reader is visible.
      // On desktop, keep it open — the TOC is a side panel that
      // coexists with the reader, no reason to dismiss it.
      if (mobileMQ.matches) toc.hidden = true;
    });
    tocList.appendChild(li);
  }
}

// ---- Unit navigation ----

function makeUnitRef(type, idx) {
  if (type === 'sentence') {
    const s = state.sentences[idx];
    if (!s) return null;
    return { type, idx, start: s.start, end: s.end, primary: `Sentence ${idx + 1}`, secondary: `Ch ${s.chIdx + 1}` };
  }
  if (type === 'paragraph') {
    const p = state.paragraphs[idx];
    if (!p) return null;
    return { type, idx, start: p.start, end: p.end, primary: `Paragraph ${idx + 1}`, secondary: `Ch ${p.chIdx + 1}` };
  }
  if (type === 'chapter') {
    const c = state.chapters[idx];
    if (!c) return null;
    return { type, idx, start: c.start, end: c.end, primary: c.title, secondary: '' };
  }
  return null;
}

function unitIdxFromSentence(type, sentIdx) {
  const s = state.sentences[sentIdx];
  if (!s) return 0;
  if (type === 'sentence') return sentIdx;
  if (type === 'paragraph') {
    for (let i = 0; i < state.paragraphs.length; i++) {
      const p = state.paragraphs[i];
      if (sentIdx >= p.sentRange[0] && sentIdx < p.sentRange[1]) return i;
    }
  }
  if (type === 'chapter') return s.chIdx;
  return 0;
}

function setUnit(type, idx, { seek = true } = {}) {
  const ref = makeUnitRef(type, idx);
  if (!ref) return;
  state.unitRef = ref;
  state.repeatDone = 0;
  state.repeatRemaining = getRepeatCount();
  setNowLabel(ref.primary, ref.secondary);
  updateCounterUI();
  highlightUnit(ref);
  if (seek && ref.start >= 0) {
    audio.currentTime = ref.start;
  }
}

function highlightUnit(ref) {
  for (const s of state.sentences) {
    s.node && s.node.classList.remove('selected');
  }
  if (!ref) return;
  let range = null;
  if (ref.type === 'sentence') range = [ref.idx, ref.idx + 1];
  else if (ref.type === 'paragraph') range = state.paragraphs[ref.idx].sentRange;
  else if (ref.type === 'chapter') range = state.chapters[ref.idx].sentRange;
  if (range) {
    for (let i = range[0]; i < range[1]; i++) {
      state.sentences[i].node && state.sentences[i].node.classList.add('selected');
    }
  }
}

function updateCounterUI() {
  if (!state.unitRef) { repeatCounter.textContent = ''; return; }
  if (state.repeatRemaining < 0) {
    repeatCounter.textContent = `${state.repeatDone + 1}`;
  } else {
    repeatCounter.textContent = `${state.repeatDone + 1} / ${state.repeatDone + state.repeatRemaining}`;
  }
}

function advanceUnit() {
  if (!state.unitRef) return;
  const type = state.unit;
  let nextIdx = state.unitRef.idx + 1;
  const max = type === 'sentence' ? state.sentences.length :
              type === 'paragraph' ? state.paragraphs.length :
              state.chapters.length;
  // Skip unaligned units
  while (nextIdx < max) {
    const ref = makeUnitRef(type, nextIdx);
    if (ref && ref.start >= 0 && ref.end > ref.start) {
      setUnit(type, nextIdx);
      const el = state.sentences[
        type === 'sentence' ? nextIdx :
        type === 'paragraph' ? state.paragraphs[nextIdx].sentRange[0] :
        state.chapters[nextIdx].sentRange[0]
      ];
      if (el && el.node) el.node.scrollIntoView({ behavior: 'smooth', block: 'center' });
      audio.play();
      return;
    }
    nextIdx++;
  }
  audio.pause();
}

// ---- Playback loop ----

function onTimeUpdate() {
  const t = audio.currentTime;
  curTime.textContent = fmt(t);
  seek.value = t;

  // Playing-sentence highlight + focus fade + auto-scroll
  updateFocus(t);

  const ref = state.unitRef;
  if (!ref || ref.end == null) return;
  if (audio.paused) return;

  if (t >= ref.end - state.tolerance) {
    state.repeatDone += 1;
    if (state.repeatRemaining < 0 || state.repeatDone < state.repeatRemaining) {
      // Brief pause at the loop boundary, then jump back to start.
      // The pause/play handlers check state.inLoopPause and skip visual
      // updates, so focus mode + the play button don't flicker.
      cancelPendingLoop();
      state.inLoopPause = true;
      audio.pause();
      pendingLoopRef = ref;
      pendingLoopTimer = setTimeout(() => {
        pendingLoopTimer = null;
        if (pendingLoopRef !== ref) { state.inLoopPause = false; return; }
        pendingLoopRef = null;
        audio.currentTime = ref.start;
        const p = audio.play();
        const clear = () => { state.inLoopPause = false; };
        if (p && typeof p.then === 'function') p.finally(clear); else clear();
      }, getPauseMs());
      updateCounterUI();
      recordRepeat();
    } else {
      advanceUnit();
    }
  }
}

function findSentenceAt(t) {
  // Binary-ish: find last sentence whose start <= t.
  let last = -1;
  for (let i = 0; i < state.sentences.length; i++) {
    const s = state.sentences[i];
    if (s.start == null) continue;
    if (s.start <= t + 0.05) last = i;
    else break;
  }
  // If t is beyond last sentence's end, we still return last.
  return last;
}

function currentUnitRange(sentIdx) {
  if (sentIdx < 0) return null;
  if (state.unit === 'sentence') return [sentIdx, sentIdx + 1];
  if (state.unit === 'paragraph') {
    for (const p of state.paragraphs) {
      if (sentIdx >= p.sentRange[0] && sentIdx < p.sentRange[1]) return p.sentRange;
    }
  }
  if (state.unit === 'chapter') {
    for (const c of state.chapters) {
      if (sentIdx >= c.sentRange[0] && sentIdx < c.sentRange[1]) return c.sentRange;
    }
  }
  return [sentIdx, sentIdx + 1];
}

function updateFocus(t) {
  const curIdx = findSentenceAt(t);
  if (curIdx < 0) return;
  const curSent = state.sentences[curIdx];
  const range = currentUnitRange(curIdx);  // range of the currently repeating unit

  for (let i = 0; i < state.sentences.length; i++) {
    const s = state.sentences[i];
    if (!s.node) continue;

    s.node.classList.toggle('playing', i === curIdx);

    let dist;
    if (range && i >= range[0] && i < range[1]) dist = 0;
    else if (range && i < range[0]) dist = Math.min(3, range[0] - i);
    else if (range) dist = Math.min(3, i - range[1] + 1);
    else dist = 3;
    s.node.dataset.dist = String(dist);
  }

  if (curIdx !== state.lastFocusIdx) {
    state.lastFocusIdx = curIdx;
    scrollToCenter(curSent.node);
    // Keep the now-label in sync with the actual playing sentence so the
    // user sees the index update as audio progresses.
    if (state.unit === 'sentence') {
      setNowLabel(`Sentence ${curIdx + 1}`, `Ch ${curSent.chIdx + 1}`);
    }
    // On touch devices, keep the floating + glued to the current sentence
    // so the user can always tap to add a note without having to hover.
    if (isTouchDevice && curSent.node) showHoverButton(curSent.node);
  }
}

function scrollToCenter(el) {
  const rect = el.getBoundingClientRect();
  const readerRect = reader.getBoundingClientRect();
  const target = reader.scrollTop + rect.top - readerRect.top - (readerRect.height / 2) + (rect.height / 2);
  reader.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
}

// ---- Event handlers ----

// Touch devices have no :hover, so the floating + can't follow the mouse.
// We instead pin it to whatever sentence the user just acted on.
const isTouchDevice = window.matchMedia && window.matchMedia('(hover: none)').matches;

function onSentenceClick(globalIdx) {
  cancelPendingLoop();
  const unitIdx = unitIdxFromSentence(state.unit, globalIdx);
  setUnit(state.unit, unitIdx);
  audio.play();
  if (isTouchDevice) {
    const node = state.sentences[globalIdx]?.node;
    if (node) showHoverButton(node);
  }
}

btnPlay.addEventListener('click', () => {
  cancelPendingLoop();
  if (audio.paused) {
    if (!state.unitRef) {
      // pick first aligned sentence
      const firstAligned = state.sentences.findIndex(s => s.start >= 0);
      if (firstAligned >= 0) setUnit(state.unit, unitIdxFromSentence(state.unit, firstAligned));
    }
    audio.play();
  } else {
    audio.pause();
  }
});
let lastPlayedSentIdx = -1;

function clearPauseMarker() {
  if (lastPlayedSentIdx >= 0) {
    const prev = state.sentences[lastPlayedSentIdx];
    if (prev?.node) prev.node.classList.remove('last-played');
    lastPlayedSentIdx = -1;
  }
}

function setPauseMarker() {
  const idx = findSentenceAt(audio.currentTime);
  if (idx < 0) return;
  if (lastPlayedSentIdx === idx) return;
  clearPauseMarker();
  const s = state.sentences[idx];
  if (s?.node) {
    s.node.classList.add('last-played');
    lastPlayedSentIdx = idx;
  }
}

audio.addEventListener('play', () => {
  if (state.inLoopPause) return;     // mid-loop, keep visuals stable
  btnPlay.classList.add('playing');
  if (state.focusMode) reader.classList.add('focus-mode');
  clearPauseMarker();
});
audio.addEventListener('pause', () => {
  if (state.inLoopPause) return;     // mid-loop, keep visuals stable
  btnPlay.classList.remove('playing');
  reader.classList.remove('focus-mode');
  setPauseMarker();
});

btnNext.addEventListener('click', () => { cancelPendingLoop(); advanceUnit(); });
btnPrev.addEventListener('click', () => {
  cancelPendingLoop();
  if (!state.unitRef) return;
  const type = state.unit;
  let idx = state.unitRef.idx - 1;
  const max = type === 'sentence' ? state.sentences.length :
              type === 'paragraph' ? state.paragraphs.length :
              state.chapters.length;
  while (idx >= 0) {
    const ref = makeUnitRef(type, idx);
    if (ref && ref.start >= 0) { setUnit(type, idx); audio.play(); return; }
    idx--;
  }
});


repeatCountInput.addEventListener('change', () => {
  state.repeatRemaining = getRepeatCount();
  state.repeatDone = 0;
  updateCounterUI();
});
// If the user blanks the field, restore "∞" so the default is always visible.
repeatCountInput.addEventListener('blur', () => {
  if (repeatCountInput.value.trim() === '') {
    repeatCountInput.value = '∞';
    state.repeatRemaining = -1;
    state.repeatDone = 0;
    updateCounterUI();
  }
});

seek.addEventListener('input', () => {
  cancelPendingLoop();
  const t = +seek.value;
  audio.currentTime = t;
  // Re-anchor the repeat unit to whichever sentence covers this new time
  // so pressing ▶ resumes from here (instead of being bounced back to
  // the previously-set unit's start by the loop logic).
  const sentIdx = state.sentences.findIndex(s => s.start <= t && t < s.end);
  if (sentIdx >= 0) {
    setUnit(state.unit, unitIdxFromSentence(state.unit, sentIdx), { seek: false });
  }
});

tocToggle.addEventListener('click', () => {
  toc.hidden = !toc.hidden;
  if (!toc.hidden) {
    titleMenu.hidden = true;
    if (mobileMQ.matches) hideRightPanels(null);
  }
  syncToggleActive();
});

// ---- Library / title menu ----

const titleMenu = document.getElementById('titleMenu');
const libraryList = document.getElementById('libraryList');
const addBookBtn = document.getElementById('addBookBtn');
const addBookModal = document.getElementById('addBookModal');
const addBookClose = document.getElementById('addBookClose');

function renderLibraryMenu() {
  libraryList.innerHTML = '';
  for (const b of state.library) {
    const p = progressByBook[b.id];
    let progressLabel = 'Not started';
    if (p && p.totalSent) {
      const cur = Math.min(p.sentIdx + 1, p.totalSent);
      progressLabel = `${cur} / ${p.totalSent}`;
    } else if (p && p.t > 10) {
      progressLabel = 'Played';
    }
    // Append repeat count if available
    const bookRepeats = bookTotalRepeats(b.id);
    if (bookRepeats > 0) progressLabel += ` · ${bookRepeats} repeats`;
    const li = document.createElement('li');
    li.dataset.id = b.id;
    if (b.id === state.bookId) li.classList.add('current');
    li.innerHTML = `
      <span class="book-title">${b.title || b.id}</span>
      ${b.author ? `<span class="book-author">${b.author}</span>` : ''}
      <span class="book-progress">${progressLabel}</span>
    `;
    li.addEventListener('click', async () => {
      titleMenu.hidden = true;
      if (b.id === state.bookId) return;
      await loadBook(b.id);
    });
    libraryList.appendChild(li);
  }
}

function bookTotalRepeats(bookId) {
  const s = statsByBook?.[bookId];
  if (!s || !s.chapters) return 0;
  return Object.values(s.chapters).reduce((a, c) => a + (c.repeats || 0), 0);
}

bookTitle.addEventListener('click', (e) => {
  e.stopPropagation();
  titleMenu.hidden = !titleMenu.hidden;
  if (!titleMenu.hidden) {
    const rect = bookTitle.getBoundingClientRect();
    titleMenu.style.left = rect.left + 'px';
    titleMenu.style.top = (rect.bottom + 6) + 'px';
    renderLibraryMenu();
  }
});

document.addEventListener('click', (e) => {
  if (!titleMenu.contains(e.target) && e.target !== bookTitle) titleMenu.hidden = true;
});

addBookBtn.addEventListener('click', () => {
  titleMenu.hidden = true;
  addBookModal.hidden = false;
});
addBookClose.addEventListener('click', () => { addBookModal.hidden = true; });
addBookModal.addEventListener('click', (e) => {
  if (e.target === addBookModal) addBookModal.hidden = true;
});

// ---- Progress tracking ----

let lastSavedT = 0;
function markProgress() {
  if (!state.bookId) return;
  const t = audio.currentTime;
  const sentIdx = findSentenceAt(t);
  progressByBook[state.bookId] = {
    t,
    sentIdx: sentIdx >= 0 ? sentIdx : 0,
    totalSent: state.sentences.length,
    unit: state.unit,
    updated: Date.now(),
    title: state.book?.title || '',
  };
  lastSavedT = t;
  saveProgress();
}
audio.addEventListener('pause', () => markProgress());
audio.addEventListener('timeupdate', () => {
  if (audio.paused) return;
  if (Math.abs(audio.currentTime - lastSavedT) > 15) markProgress();
});
window.addEventListener('beforeunload', () => markProgress());

// ---- Word lookup (double-click) + Vocab book ----

const vocabPanel = document.getElementById('vocabPanel');
const vocabList = document.getElementById('vocabList');
const vocabCount = document.getElementById('vocabCount');
const vocabToggle = document.getElementById('vocabToggle');
const wordPopup = document.getElementById('wordPopup');
const wordTerm = document.getElementById('wordTerm');
const wordPhon = document.getElementById('wordPhon');
const wordDef = document.getElementById('wordDef');
const wordAdd = document.getElementById('wordAdd');
const wordEnrich = document.getElementById('wordEnrich');
const wordCn = document.getElementById('wordCn');
const wordMnemonic = document.getElementById('wordMnemonic');

const VOCAB_URL = 'data/vocab.json';
const VOCAB_KEY = 'repeater.vocab.v1';
let vocabByBook = {};     // {bookId: [entries]}
let currentLookup = null; // {word, def}

function currentBookVocab() {
  if (!state.bookId) return [];
  if (!vocabByBook[state.bookId]) vocabByBook[state.bookId] = [];
  return vocabByBook[state.bookId];
}

async function loadVocab() {
  // 1. Try server-side file first
  try {
    const r = await fetch(VOCAB_URL + '?t=' + Date.now());
    if (r.ok) {
      const data = await r.json();
      if (Array.isArray(data)) {
        // Legacy flat format → migrate under current book id.
        // Falls back to "one-hundred-years" if bookId isn't set yet
        // (that's what all pre-migration vocab came from in practice).
        const bid = state.bookId || 'one-hundred-years';
        vocabByBook = data.length ? { [bid]: data } : {};
        await saveVocab();
        return;
      }
      if (data && typeof data === 'object') { vocabByBook = data; return; }
    }
  } catch {}
  // 2. Fall back to localStorage
  try {
    const stored = JSON.parse(localStorage.getItem(VOCAB_KEY) || '{}');
    if (Array.isArray(stored)) {
      const bid = state.bookId || 'one-hundred-years';
      vocabByBook = stored.length ? { [bid]: stored } : {};
    } else if (stored && typeof stored === 'object') {
      vocabByBook = stored;
    }
    saveVocab();
  } catch { vocabByBook = {}; }
}

async function saveVocab() {
  localStorage.setItem(VOCAB_KEY, JSON.stringify(vocabByBook));
  try {
    await fetch(VOCAB_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(vocabByBook),
    });
  } catch {}
}

function renderVocab() {
  const vocab = currentBookVocab();
  vocabCount.textContent = vocab.length ? `${vocab.length}` : '';
  vocabList.innerHTML = '';
  if (!vocab.length) {
    vocabList.innerHTML = '<li class="vocab-empty">Double-click a word to save it here.</li>';
    return;
  }
  for (let i = vocab.length - 1; i >= 0; i--) {
    const v = vocab[i];
    const li = document.createElement('li');
    const phon = v.phonetic ? `<span class="vocab-phonetic">${v.phonetic}</span>` : '';
    const cnLine = v.cn ? `<span class="vocab-cn">${v.cn}</span>` : '';
    const mnLine = v.mnemonic ? `<span class="vocab-mn">${v.mnemonic}</span>` : '';
    li.innerHTML = `<span class="vocab-word">${v.word}</span><button class="vocab-del" data-i="${i}">Remove</button>${phon}${cnLine}<span class="vocab-gloss">${v.gloss || ''}</span>${mnLine}`;
    vocabList.appendChild(li);
  }
}
loadVocab().then(renderVocab);

vocabList.addEventListener('click', (e) => {
  const b = e.target.closest('.vocab-del');
  if (b) {
    currentBookVocab().splice(+b.dataset.i, 1);
    saveVocab();
    renderVocab();
  }
});

// On desktop: TOC (left) is independent of right-side panels (Vocab/Notes/Stats).
// On mobile: any open panel covers the screen, so opening one closes the rest.
const mobileMQ = window.matchMedia('(max-width: 640px)');

function hideRightPanels(except) {
  if (except !== 'vocab') vocabPanel.hidden = true;
  if (except !== 'notes' && typeof notesPanel !== 'undefined' && notesPanel) notesPanel.hidden = true;
  if (except !== 'stats' && typeof statsPanel !== 'undefined' && statsPanel) statsPanel.hidden = true;
}

function hideAllPanels(except) {
  if (except !== 'toc') toc.hidden = true;
  hideRightPanels(except);
}

function syncToggleActive() {
  const tocToggleEl = document.getElementById('tocToggle');
  if (tocToggleEl) tocToggleEl.classList.toggle('active', !toc.hidden);
  vocabToggle.classList.toggle('active', !vocabPanel.hidden);
  if (typeof noteToggle !== 'undefined' && noteToggle) {
    noteToggle.classList.toggle('active', !notesPanel.hidden);
  }
  if (typeof statsToggle !== 'undefined' && statsToggle) {
    statsToggle.classList.toggle('active', !statsPanel.hidden);
  }
}

vocabToggle.addEventListener('click', () => {
  vocabPanel.hidden = !vocabPanel.hidden;
  if (!vocabPanel.hidden) {
    if (mobileMQ.matches) hideAllPanels('vocab'); else hideRightPanels('vocab');
    renderVocab();
  }
  syncToggleActive();
});

document.getElementById('vocabExport').addEventListener('click', () => {
  const scope = pickExportScope('生词', vocabByBook, currentBookVocab().length);
  if (!scope) return;
  const md = exportVocabMD(scope);
  if (!md) return;
  const date = new Date().toISOString().slice(0, 10);
  const fileName = scope === 'all'
    ? `vocab-all-${date}.md`
    : `vocab-${(state.book?.title || 'book').replace(/[^a-zA-Z0-9-]+/g, '-')}-${date}.md`;
  downloadFile(fileName, md);
});

// Ask whether to export just the current book's data or all books' data.
// Returns 'current' | 'all' | null (user cancelled or nothing to export).
function pickExportScope(noun, byBookMap, currentCount) {
  const otherBooks = Object.entries(byBookMap)
    .filter(([bid, arr]) => bid !== state.bookId && arr?.length);
  const totalOthers = otherBooks.reduce((n, [, arr]) => n + arr.length, 0);
  if (!currentCount && !totalOthers) return null;
  if (!totalOthers) return 'current';
  if (!currentCount) return 'all';
  // Both exist — ask. `confirm` returns true for OK (current) and false for Cancel (all).
  const ok = confirm(
    `导出${noun}：\n\n` +
    `【确定】 仅导出本书${noun}（${currentCount} 条）\n` +
    `【取消】 导出所有书的${noun}（共 ${currentCount + totalOthers} 条）`
  );
  return ok ? 'current' : 'all';
}

// Fetch definition from the free dictionary API.
async function fetchDefinition(word) {
  try {
    const r = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`);
    if (!r.ok) return null;
    const data = await r.json();
    if (!Array.isArray(data) || !data.length) return null;
    return data;
  } catch { return null; }
}

// Enrich via local /api/enrich (Chinese + mnemonic from Claude)
async function fetchEnrichment(word, definition, context) {
  try {
    const r = await fetch('/api/enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ word, definition, context }),
    });
    if (!r.ok) return null;
    const data = await r.json();
    if (data.error) return null;
    return data; // {cn, mnemonic}
  } catch { return null; }
}

function firstGloss(def) {
  if (!def) return '';
  const entry = def[0];
  const meaning = entry.meanings?.[0];
  const d = meaning?.definitions?.[0]?.definition;
  return d || '';
}

function renderDef(def) {
  if (!def) {
    wordDef.innerHTML = '<span class="err">No definition found.</span>';
    wordPhon.textContent = '';
    return;
  }
  const entry = def[0];
  wordPhon.textContent = entry.phonetic || (entry.phonetics || []).map(p => p.text).filter(Boolean)[0] || '';
  let html = '';
  for (const m of (entry.meanings || []).slice(0, 3)) {
    html += `<div class="pos">${m.partOfSpeech || ''}</div><ol>`;
    for (const d of (m.definitions || []).slice(0, 3)) {
      html += `<li>${d.definition}</li>`;
    }
    html += '</ol>';
  }
  wordDef.innerHTML = html || '<span class="err">No definition found.</span>';
}

function positionPopup(rect) {
  const pad = 8;
  const pw = 320;
  const ph = 280;
  let left = rect.left + rect.width / 2 - pw / 2;
  let top = rect.bottom + pad;
  if (top + ph > window.innerHeight - 180) top = rect.top - ph - pad;
  left = Math.max(12, Math.min(window.innerWidth - pw - 12, left));
  wordPopup.style.left = left + 'px';
  wordPopup.style.top = top + 'px';
  wordPopup.style.width = pw + 'px';
}

function hidePopup() {
  wordPopup.hidden = true;
  currentLookup = null;
}

reader.addEventListener('dblclick', async (e) => {
  const sel = window.getSelection();
  const raw = sel.toString().trim();
  const word = raw.toLowerCase().replace(/^[^a-z']+|[^a-z']+$/gi, '');
  if (!word || !/^[a-z][a-z']*$/.test(word)) return;

  // Capture sentence context (for export)
  const sentEl = e.target.closest('.sentence');
  const context = sentEl ? sentEl.textContent.trim() : '';

  const rect = sel.getRangeAt(0).getBoundingClientRect();
  wordTerm.textContent = word;
  wordPhon.textContent = '';
  wordDef.textContent = 'Loading…';
  wordEnrich.hidden = true;
  wordCn.textContent = '';
  wordMnemonic.textContent = '';
  wordAdd.classList.remove('saved');
  wordAdd.textContent = '+ Add to vocab';
  wordAdd.disabled = false;
  positionPopup(rect);
  wordPopup.hidden = false;

  const def = await fetchDefinition(word);
  if (wordTerm.textContent !== word) return; // user double-clicked another word
  const phonetic = def?.[0]?.phonetic || (def?.[0]?.phonetics || []).map(p => p.text).filter(Boolean)[0] || '';
  currentLookup = { word, def, phonetic, context };
  renderDef(def);

  // If already saved in THIS book, mark + prefill enrichment from storage
  const existing = currentBookVocab().find(v => v.word === word);
  if (existing) {
    wordAdd.classList.add('saved');
    wordAdd.textContent = '✓ Saved';
    wordAdd.disabled = true;
    if (existing.cn || existing.mnemonic) {
      wordEnrich.hidden = false;
      wordCn.textContent = existing.cn || '';
      wordMnemonic.textContent = existing.mnemonic || '';
    }
  }

  // Kick off Claude enrichment in parallel
  wordEnrich.hidden = false;
  wordMnemonic.classList.add('loading');
  wordMnemonic.textContent = '加载中文与助记…';
  const firstDef = def?.[0]?.meanings?.[0]?.definitions?.[0]?.definition || '';
  const enrich = await fetchEnrichment(word, firstDef, context);
  if (wordTerm.textContent !== word) return;
  wordMnemonic.classList.remove('loading');
  if (enrich) {
    wordCn.textContent = enrich.cn || '';
    wordMnemonic.textContent = enrich.mnemonic || '';
    if (currentLookup && currentLookup.word === word) {
      currentLookup.cn = enrich.cn;
      currentLookup.mnemonic = enrich.mnemonic;
    }
  } else if (!existing?.cn) {
    wordEnrich.hidden = true;
  }
});

wordAdd.addEventListener('click', () => {
  if (!currentLookup) return;
  const { word, def, phonetic, context, cn, mnemonic } = currentLookup;
  const vocab = currentBookVocab();
  if (vocab.find(v => v.word === word)) return;
  vocab.push({
    word,
    phonetic: phonetic || '',
    gloss: firstGloss(def),
    definitions: flattenDefs(def),
    context: context || '',
    cn: cn || '',
    mnemonic: mnemonic || '',
    bookTitle: state.book?.title || '',
    addedAt: Date.now(),
  });
  saveVocab();
  renderVocab();
  wordAdd.classList.add('saved');
  wordAdd.textContent = '✓ Saved';
  wordAdd.disabled = true;
});

function flattenDefs(def) {
  if (!def || !def[0]) return [];
  const out = [];
  for (const m of (def[0].meanings || []).slice(0, 3)) {
    for (const d of (m.definitions || []).slice(0, 3)) {
      out.push({ pos: m.partOfSpeech || '', text: d.definition });
    }
  }
  return out;
}

function formatVocabEntry(v) {
  const lines = [];
  lines.push(`\n## ${v.word}`);
  if (v.phonetic) lines.push(`- **Phonetic**: ${v.phonetic}`);
  if (v.definitions?.length) {
    lines.push(`- **Definitions**:`);
    for (const d of v.definitions) {
      lines.push(`  - *${d.pos || ''}* — ${d.text}`);
    }
  } else if (v.gloss) {
    lines.push(`- **Definition**: ${v.gloss}`);
  }
  if (v.context) lines.push(`- **Context**: "${v.context}"`);
  if (v.cn) lines.push(`- **中文**: ${v.cn}`);
  if (v.mnemonic) lines.push(`- **Memory**: ${v.mnemonic}`);
  if (v.addedAt) {
    const d = new Date(v.addedAt);
    lines.push(`- **Saved**: ${d.toISOString().slice(0, 16).replace('T', ' ')}`);
  }
  return lines.join('\n');
}

function exportVocabMD(scope = 'current') {
  const date = new Date().toISOString().slice(0, 10);
  if (scope === 'all') {
    // Group by book, emit one H2 per book with its entries underneath
    const booksWithVocab = Object.entries(vocabByBook).filter(([, arr]) => arr?.length);
    if (!booksWithVocab.length) return '';
    const total = booksWithVocab.reduce((n, [, arr]) => n + arr.length, 0);
    const bookTitleFor = (bid) => {
      const entry = (state.library || []).find(b => b.id === bid);
      return entry?.title || bid;
    };
    let out = `# Vocabulary — All Books — ${date}\n\n${total} words across ${booksWithVocab.length} books\n`;
    for (const [bid, arr] of booksWithVocab) {
      out += `\n\n# ${bookTitleFor(bid)} (${arr.length})\n`;
      out += arr.map(formatVocabEntry).join('\n');
    }
    return out;
  }
  const vocab = currentBookVocab();
  if (!vocab.length) return '';
  const title = state.book?.title || 'Book';
  const header = `# Vocabulary — ${title} — ${date}\n\n${vocab.length} words\n`;
  return header + vocab.map(formatVocabEntry).join('\n');
}

function downloadFile(name, content, mime = 'text/markdown;charset=utf-8') {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

document.addEventListener('click', (e) => {
  if (wordPopup.hidden) return;
  if (wordPopup.contains(e.target)) return;
  if (e.detail === 2) return; // dblclick triggered click too
  hidePopup();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hidePopup(); });

// Font size control
const fontSizes = [15, 17, 19, 22, 26, 30];
let fontIdx = 2; // default 19
function applyFont() {
  document.documentElement.style.setProperty('--reader-font-size', fontSizes[fontIdx] + 'px');
  localStorage.setItem('repeater.fontIdx', String(fontIdx));
}
const savedFont = parseInt(localStorage.getItem('repeater.fontIdx') || '', 10);
if (!isNaN(savedFont) && savedFont >= 0 && savedFont < fontSizes.length) fontIdx = savedFont;
applyFont();
document.getElementById('fontInc').addEventListener('click', () => { if (fontIdx < fontSizes.length - 1) { fontIdx++; applyFont(); } });
document.getElementById('fontDec').addEventListener('click', () => { if (fontIdx > 0) { fontIdx--; applyFont(); } });

// ---- Notes ----

const notesPanel = document.getElementById('notesPanel');
const notesList = document.getElementById('notesList');
const notesCount = document.getElementById('notesCount');
const noteToggle = document.getElementById('noteToggle');
const noteAddHover = document.getElementById('noteAddHover');
const noteEditor = document.getElementById('noteEditor');
const noteEditorSent = document.getElementById('noteEditorSent');
const noteEditorText = document.getElementById('noteEditorText');
const noteEditorSave = document.getElementById('noteEditorSave');
const noteEditorDel = document.getElementById('noteEditorDel');
const noteEditorClose = document.getElementById('noteEditorClose');
const notesExport = document.getElementById('notesExport');

const NOTES_URL = 'data/notes.json';
let notesByBook = {};           // {bookId: [{sentIdx, text, context, chIdx, paIdx, seIdx, createdAt, updatedAt}]}
let hoveredSentEl = null;       // current span being hovered
let editingNote = null;         // {sentIdx, existing?} while editor is open

function currentBookNotes() {
  if (!state.bookId) return [];
  if (!notesByBook[state.bookId]) notesByBook[state.bookId] = [];
  return notesByBook[state.bookId];
}

async function loadNotes() {
  try {
    const r = await fetch(NOTES_URL + '?t=' + Date.now());
    if (r.ok) {
      const data = await r.json();
      if (data && typeof data === 'object') notesByBook = data;
    }
  } catch {}
}
async function saveNotes() {
  try {
    await fetch(NOTES_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(notesByBook),
    });
  } catch {}
}

function findNote(sentIdx) {
  return currentBookNotes().find(n => n.sentIdx === sentIdx);
}

function markSentencesWithNotes() {
  for (const s of state.sentences) s.node && s.node.classList.remove('has-note');
  for (const n of currentBookNotes()) {
    const s = state.sentences[n.sentIdx];
    if (s && s.node) s.node.classList.add('has-note');
  }
}

function positionHoverButton(sentEl) {
  const overall = sentEl.getBoundingClientRect();
  if (!overall || overall.height === 0) return;
  const top = overall.top + overall.height / 2 - 12;
  // Sit just inside the reader's right padding so the + feels attached to the
  // text instead of floating in the margin.
  const readerRect = reader.getBoundingClientRect();
  const padR = parseFloat(getComputedStyle(reader).paddingRight);
  const textRight = readerRect.right - padR;
  const left = Math.min(window.innerWidth - 30, textRight - 4);
  noteAddHover.style.top = top + 'px';
  noteAddHover.style.left = left + 'px';
}

function showHoverButton(sentEl) {
  hoveredSentEl = sentEl;
  const sentIdx = +sentEl.dataset.globalIdx;
  noteAddHover.classList.toggle('has-note', !!findNote(sentIdx));
  noteAddHover.dataset.sentIdx = String(sentIdx);
  positionHoverButton(sentEl);
  noteAddHover.hidden = false;
  requestAnimationFrame(() => noteAddHover.classList.add('visible'));
}

function hideHoverButton() {
  hoveredSentEl = null;
  noteAddHover.classList.remove('visible');
  setTimeout(() => { if (!hoveredSentEl) noteAddHover.hidden = true; }, 120);
}

reader.addEventListener('mouseover', (e) => {
  const sent = e.target.closest('.sentence');
  if (!sent) return;
  if (sent === hoveredSentEl) return;
  showHoverButton(sent);
});
reader.addEventListener('mouseleave', () => {
  // The + only hides when the mouse leaves the reader. Inside the reader the
  // cursor can drift through margins on the way to the +, so we don't hide on
  // sentence-leave events.
  setTimeout(() => {
    if (!noteAddHover.matches(':hover')) hideHoverButton();
  }, 80);
});
window.addEventListener('scroll', () => { if (hoveredSentEl) positionHoverButton(hoveredSentEl); }, true);

noteAddHover.addEventListener('click', (e) => {
  e.stopPropagation();
  const sentIdx = +noteAddHover.dataset.sentIdx;
  openNoteEditor(sentIdx);
});

function openNoteEditor(sentIdx) {
  const s = state.sentences[sentIdx];
  if (!s) return;
  const existing = findNote(sentIdx);
  editingNote = { sentIdx, existing: !!existing };
  noteEditorSent.textContent = s.text;
  noteEditorText.value = existing ? existing.text : '';
  noteEditorDel.hidden = !existing;

  const editorW = 360;
  const editorH = 240;
  const readerRect = reader.getBoundingClientRect();
  const padR = parseFloat(getComputedStyle(reader).paddingRight);
  const textRight = readerRect.right - padR;
  const sentRect = s.node.getBoundingClientRect();

  let left, top;
  // Prefer: floating in the reader's right margin so it doesn't cover any text.
  if (textRight + 16 + editorW <= window.innerWidth - 12) {
    left = textRight + 16;
  } else {
    // Fall back: center horizontally on screen.
    left = Math.max(12, (window.innerWidth - editorW) / 2);
  }
  top = Math.max(60, Math.min(window.innerHeight - editorH - 24, sentRect.top));

  noteEditor.style.left = left + 'px';
  noteEditor.style.top = top + 'px';
  noteEditor.hidden = false;
  setTimeout(() => noteEditorText.focus(), 0);
}

function closeNoteEditor() {
  noteEditor.hidden = true;
  editingNote = null;
}

noteEditorClose.addEventListener('click', closeNoteEditor);
noteEditor.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', (e) => {
  if (!noteEditor.hidden && !noteEditor.contains(e.target) && e.target !== noteAddHover && !noteAddHover.contains(e.target)) {
    closeNoteEditor();
  }
});
document.addEventListener('keydown', (e) => {
  if (!noteEditor.hidden && e.key === 'Escape') closeNoteEditor();
  if (!noteEditor.hidden && (e.metaKey || e.ctrlKey) && e.key === 'Enter') noteEditorSave.click();
});

noteEditorSave.addEventListener('click', () => {
  if (!editingNote) return;
  const text = noteEditorText.value.trim();
  const arr = currentBookNotes();
  const idx = arr.findIndex(n => n.sentIdx === editingNote.sentIdx);
  if (!text) {
    if (idx >= 0) { arr.splice(idx, 1); }
  } else {
    const s = state.sentences[editingNote.sentIdx];
    const payload = {
      sentIdx: editingNote.sentIdx,
      text,
      context: s?.text || '',
      chIdx: s?.chIdx, paIdx: s?.paIdx, seIdx: s?.seIdx,
      updatedAt: Date.now(),
    };
    if (idx >= 0) {
      arr[idx] = { ...arr[idx], ...payload };
    } else {
      arr.push({ ...payload, createdAt: Date.now() });
    }
  }
  saveNotes();
  markSentencesWithNotes();
  renderNotes();
  closeNoteEditor();
});
noteEditorDel.addEventListener('click', () => {
  if (!editingNote) return;
  const arr = currentBookNotes();
  const idx = arr.findIndex(n => n.sentIdx === editingNote.sentIdx);
  if (idx >= 0) arr.splice(idx, 1);
  saveNotes();
  markSentencesWithNotes();
  renderNotes();
  closeNoteEditor();
});

function renderNotes() {
  const arr = currentBookNotes().slice().sort((a, b) => a.sentIdx - b.sentIdx);
  notesCount.textContent = arr.length ? `${arr.length}` : '';
  notesList.innerHTML = '';
  if (!arr.length) {
    notesList.innerHTML = '<li class="notes-empty">Hover a sentence and click + to add a note.</li>';
    return;
  }
  for (const n of arr) {
    const li = document.createElement('li');
    const ctx = n.context || state.sentences[n.sentIdx]?.text || '';
    const escape = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    li.innerHTML = `<span class="note-sent">${escape(ctx)}</span><span class="note-text">${escape(n.text)}</span>`;
    li.addEventListener('click', () => {
      const s = state.sentences[n.sentIdx];
      if (s && s.node) {
        s.node.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => openNoteEditor(n.sentIdx), 350);
      }
    });
    notesList.appendChild(li);
  }
}

noteToggle.addEventListener('click', () => {
  notesPanel.hidden = !notesPanel.hidden;
  if (!notesPanel.hidden) {
    if (mobileMQ.matches) hideAllPanels('notes'); else hideRightPanels('notes');
    renderNotes();
  }
  syncToggleActive();
});

// Format a single notes array into a Markdown body. Chapter headers are
// only meaningful when the notes belong to the *currently loaded* book —
// for other books we don't have a chapter index handy, so we just list
// the notes in saved order under the book title.
function formatNotesForCurrentBook(arr) {
  let currentCh = -1;
  const bodies = [];
  for (const n of arr) {
    const s = state.sentences[n.sentIdx];
    if (s && s.chIdx !== currentCh) {
      currentCh = s.chIdx;
      const chTitle = state.book?.chapters?.[s.chIdx]?.title || `Chapter ${s.chIdx + 1}`;
      bodies.push(`\n## ${chTitle}\n`);
    }
    bodies.push(`> ${(n.context || '').trim()}\n\n${n.text.trim()}\n`);
  }
  return bodies.join('\n');
}

function formatNotesForOtherBook(arr) {
  // Sort by sentIdx as a rough proxy for reading order.
  const sorted = arr.slice().sort((a, b) => (a.sentIdx ?? 0) - (b.sentIdx ?? 0));
  return sorted.map(n => `> ${(n.context || '').trim()}\n\n${(n.text || '').trim()}\n`).join('\n');
}

function exportNotesMD(scope = 'current') {
  const date = new Date().toISOString().slice(0, 10);
  if (scope === 'all') {
    const booksWithNotes = Object.entries(notesByBook).filter(([, arr]) => arr?.length);
    if (!booksWithNotes.length) return '';
    const total = booksWithNotes.reduce((n, [, arr]) => n + arr.length, 0);
    const bookTitleFor = (bid) => {
      const entry = (state.library || []).find(b => b.id === bid);
      return entry?.title || bid;
    };
    let out = `# Notes — All Books — ${date}\n\n${total} notes across ${booksWithNotes.length} books\n`;
    for (const [bid, arr] of booksWithNotes) {
      out += `\n\n# ${bookTitleFor(bid)} (${arr.length})\n`;
      const sorted = arr.slice().sort((a, b) => (a.sentIdx ?? 0) - (b.sentIdx ?? 0));
      out += bid === state.bookId
        ? formatNotesForCurrentBook(sorted)
        : formatNotesForOtherBook(sorted);
    }
    return out;
  }
  const arr = currentBookNotes().slice().sort((a, b) => a.sentIdx - b.sentIdx);
  if (!arr.length) return '';
  const title = state.book?.title || 'Book';
  const header = `# Notes — ${title}\n\n${arr.length} notes · exported ${date}\n`;
  return header + formatNotesForCurrentBook(arr);
}

notesExport.addEventListener('click', () => {
  const scope = pickExportScope('笔记', notesByBook, currentBookNotes().length);
  if (!scope) return;
  const md = exportNotesMD(scope);
  if (!md) return;
  const date = new Date().toISOString().slice(0, 10);
  const fileName = scope === 'all'
    ? `notes-all-${date}.md`
    : `notes-${(state.book?.title || 'notes').replace(/[^a-zA-Z0-9-]+/g, '-')}-${date}.md`;
  downloadFile(fileName, md);
});

audio.addEventListener('timeupdate', onTimeUpdate);

// ---- Stats: per-chapter reading time + repeats ----

const statsToggle = document.getElementById('statsToggle');
const statsPanel = document.getElementById('statsPanel');
const statsList = document.getElementById('statsList');
const statsTotal = document.getElementById('statsTotal');

const STATS_URL = 'data/stats.json';
let statsByBook = {}; // {bookId: {chapters: {chIdx: {playTimeMs, repeats}}}}
let lastTickT = -1;
let lastTickChIdx = -1;
let saveStatsTimer = null;

function getBookStats() {
  if (!state.bookId) return null;
  if (!statsByBook[state.bookId]) statsByBook[state.bookId] = { chapters: {} };
  return statsByBook[state.bookId];
}
function chStats(chIdx) {
  const s = getBookStats();
  if (!s) return null;
  if (!s.chapters[chIdx]) s.chapters[chIdx] = { playTimeMs: 0, repeats: 0 };
  return s.chapters[chIdx];
}
async function loadStats() {
  try {
    const r = await fetch(STATS_URL + '?t=' + Date.now());
    if (r.ok) {
      const data = await r.json();
      if (data && typeof data === 'object') statsByBook = data;
    }
  } catch {}
}
function scheduleSaveStats() {
  if (saveStatsTimer) return;
  saveStatsTimer = setTimeout(async () => {
    saveStatsTimer = null;
    try {
      await fetch(STATS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(statsByBook),
      });
    } catch {}
  }, 5000);
}

function tickPlayTime() {
  if (audio.paused) { lastTickT = -1; return; }
  const t = audio.currentTime;
  const idx = findSentenceAt(t);
  if (idx < 0) { lastTickT = t; return; }
  const chIdx = state.sentences[idx].chIdx;
  if (lastTickT >= 0 && chIdx === lastTickChIdx) {
    const dt = t - lastTickT;
    if (dt > 0 && dt < 1.5) {  // skip seeks / loops
      const cs = chStats(chIdx);
      if (cs) { cs.playTimeMs += dt * 1000; scheduleSaveStats(); }
    }
  }
  lastTickT = t;
  lastTickChIdx = chIdx;
}
function recordRepeat() {
  const idx = findSentenceAt(audio.currentTime);
  if (idx < 0) return;
  const chIdx = state.sentences[idx].chIdx;
  const cs = chStats(chIdx);
  if (cs) { cs.repeats += 1; scheduleSaveStats(); }
}
audio.addEventListener('timeupdate', tickPlayTime);
audio.addEventListener('pause', () => { if (!state.inLoopPause) lastTickT = -1; });
window.addEventListener('beforeunload', () => {
  // Flush any pending stats save before navigating away.
  if (saveStatsTimer) { clearTimeout(saveStatsTimer); saveStatsTimer = null; }
  try {
    fetch(STATS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(statsByBook),
      keepalive: true,
    });
  } catch {}
});

function fmtDuration(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function renderStats() {
  statsList.innerHTML = '';
  statsTotal.innerHTML = '';
  const s = getBookStats();
  if (!s || !state.book) return;
  let totalMs = 0, totalRep = 0;
  for (let ci = 0; ci < state.book.chapters.length; ci++) {
    const cs = s.chapters[ci] || { playTimeMs: 0, repeats: 0 };
    totalMs += cs.playTimeMs || 0;
    totalRep += cs.repeats || 0;
    const li = document.createElement('li');
    const title = state.book.chapters[ci].title || `Chapter ${ci + 1}`;
    li.innerHTML = `<span class="ch-name">${title}</span><span class="num">${cs.repeats || 0}</span><span class="num">${fmtDuration(cs.playTimeMs || 0)}</span>`;
    statsList.appendChild(li);
  }
  statsTotal.innerHTML = `<span class="label">Book total</span><span class="num">${totalRep}</span><span class="num">${fmtDuration(totalMs)}</span>`;
}

statsToggle.addEventListener('click', () => {
  statsPanel.hidden = !statsPanel.hidden;
  if (!statsPanel.hidden) {
    if (mobileMQ.matches) hideAllPanels('stats'); else hideRightPanels('stats');
    renderStats();
  }
  syncToggleActive();
});

// ---- Mobile chrome relayout: move TOC + topbar action buttons into the
// bottom-bar so the user can reach them with a thumb. The same DOM elements
// keep their original event listeners — we only relocate them. ----

function relayoutChrome() {
  const isMobile = mobileMQ.matches;
  const tocToggleEl = document.getElementById('tocToggle');
  const topbarLeft = document.querySelector('.topbar-left');
  const topbarActions = document.querySelector('.topbar-actions');
  const bottomBar = document.getElementById('bottomBar');
  const titleEl = document.getElementById('bookTitle');

  if (isMobile) {
    bottomBar.appendChild(tocToggleEl);
    while (topbarActions.firstChild) {
      bottomBar.appendChild(topbarActions.firstChild);
    }
    bottomBar.hidden = false;
  } else {
    // Restore: TOC toggle goes back to the topbar-left (before the title);
    // action buttons go back into .topbar-actions in their original order.
    if (tocToggleEl.parentElement !== topbarLeft) {
      topbarLeft.insertBefore(tocToggleEl, titleEl);
    }
    [...bottomBar.children].forEach((c) => topbarActions.appendChild(c));
    bottomBar.hidden = true;
  }
  syncToggleActive();
}

mobileMQ.addEventListener('change', relayoutChrome);
relayoutChrome();

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === ' ') { e.preventDefault(); btnPlay.click(); }
  else if (e.key === 'ArrowRight') btnNext.click();
  else if (e.key === 'ArrowLeft') btnPrev.click();
});

// ---- Inline pause + speed controls (replaces old advanced-settings popover) ----
// All controls are now visible in the bottom bar as a readable sentence:
//   Repeat — Sentence × ∞ at 1× · pause 0.5s
// Speed opens a small popover-up like the unit picker; pause is direct input.

const pauseSecondsInput = document.getElementById('pauseSeconds');
const speedPicker = document.getElementById('speedPicker');
const speedTrigger = document.getElementById('speedTrigger');
const speedPopover = document.getElementById('speedPopover');
const speedLabel = document.getElementById('speedLabel');

const SPEED_KEY = 'repeater.speed';

function fmtSpeed(v) {
  // 1 → "1×", 1.25 → "1.25×", 0.5 → "0.5×"
  return (v % 1 === 0 ? v.toFixed(0) : String(v)) + '×';
}

function setSpeed(v, persist = true) {
  const val = Math.max(0.5, Math.min(2, v));
  audio.playbackRate = val;
  if (speedLabel) speedLabel.textContent = fmtSpeed(val);
  for (const btn of speedPopover.querySelectorAll('button')) {
    btn.classList.toggle('current', parseFloat(btn.dataset.value) === val);
  }
  if (persist) localStorage.setItem(SPEED_KEY, String(val));
}

// Restore persisted pause + speed on load
const savedPause = parseFloat(localStorage.getItem(PAUSE_KEY));
if (!isNaN(savedPause) && savedPause >= 0) pauseSecondsInput.value = String(savedPause);
const savedSpeed = parseFloat(localStorage.getItem(SPEED_KEY));
if (!isNaN(savedSpeed)) setSpeed(savedSpeed, false);

pauseSecondsInput.addEventListener('change', () => {
  let v = parseFloat(pauseSecondsInput.value);
  if (isNaN(v) || v < 0) v = PAUSE_DEFAULT;
  if (v > PAUSE_MAX) v = PAUSE_MAX;
  pauseSecondsInput.value = String(v);
  localStorage.setItem(PAUSE_KEY, String(v));
});
pauseSecondsInput.addEventListener('blur', () => {
  if (pauseSecondsInput.value.trim() === '') {
    pauseSecondsInput.value = String(PAUSE_DEFAULT);
    localStorage.setItem(PAUSE_KEY, String(PAUSE_DEFAULT));
  }
});

// Speed popover open/close (mirrors unit picker pattern)
speedTrigger.addEventListener('click', (e) => {
  e.stopPropagation();
  speedPopover.hidden = !speedPopover.hidden;
});
speedPopover.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-value]');
  if (!btn) return;
  setSpeed(parseFloat(btn.dataset.value));
  speedPopover.hidden = true;
});
document.addEventListener('click', (e) => {
  if (!speedPicker.contains(e.target)) {
    speedPopover.hidden = true;
  }
});

load().catch(err => {
  reader.innerHTML = `<div class="loading">Failed to load: ${err.message}</div>`;
  console.error(err);
});
