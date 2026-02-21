/* ═══════════════════════════════════════════════════════
   Myra Language Teacher – Frontend App
═══════════════════════════════════════════════════════ */

// ── State ─────────────────────────────────────────────
const state = {
  currentWord: null,       // { english, translation, emoji, romanized, language, category }
  config: {},
  score: 0,
  wordsAttempted: 0,
  attempts: 0,             // current word attempt count
  maxAttempts: 3,
  isRecording: false,
  mediaRecorder: null,
  audioChunks: [],
  recTimerInterval: null,
  ttsAudio: null,          // current playing Audio object
  pendingTimeoutIds: [],   // timeouts to clear when Stop is pressed
  stopRequested: false,    // true when Stop pressed; skips processAudio after recording
};

// ── DOM refs ──────────────────────────────────────────
const $ = id => document.getElementById(id);

const els = {
  dinoWrapper:  $('dino-wrapper'),
  dinoSvg:      $('dino-svg'),
  dinoMouth:      $('dino-mouth'),
  dinoTeeth:      $('dino-teeth'),
  dinoMouthInner: $('dino-mouth-inner'),
  bubble:       $('speech-bubble'),
  bubbleText:   $('bubble-text'),
  langBadge:    $('lang-badge'),
  wordCard:     $('word-card'),
  wordEmoji:    $('word-emoji'),
  wordEnglish:  $('word-english'),
  wordTranslation: $('word-translation'),
  wordRomanized: $('word-romanized'),
  feedbackBanner: $('feedback-banner'),
  feedbackText:  $('feedback-text'),
  dots:         [$('dot-1'), $('dot-2'), $('dot-3')],
  btnPlay:      $('btn-play'),
  btnRecord:    $('btn-record'),
  btnSkip:      $('btn-skip'),
  btnStop:      $('btn-stop'),
  recIndicator: $('recording-indicator'),
  recTimer:     $('rec-timer'),
  scoreDisplay: $('score-display'),
  wordsDisplay: $('words-display'),
  confetti:     $('confetti-container'),
  childTitle:   $('child-name-title'),
};

// ── Dino speech messages ──────────────────────────────
const MESSAGES = {
  idle:    ["Hi! Let's learn words! 🌟", "Ready to learn? 🦕", "Let's go! 🎉", "You can do it! 💪"],
  prompt:  ["Can you say this? 🎤", "Now YOU try! 🌟", "Say it with me! 😊", "Your turn! 🎤"],
  correct: ["Amazing! ⭐", "Yay!! 🎉", "Super! 🌟", "Brilliant! 🦕", "You're a star! ⭐"],
  wrong:   ["Try again! 💪", "So close! 🤗", "Almost! Give it another go! 😊", "Keep trying! 🌟"],
  skip:    ["Next word! Let's go! 🚀", "New word coming! 🌟", "Here we go again! 🦕"],
  listen:  ["I'm listening… 👂", "Speak up! 🎤", "Go ahead! 🌟"],
  stop:    ["Stopped. Ready when you are! 🌟", "Paused! Take your time. 🦕", "Whenever you're ready! 😊"],
};

function randomMsg(key) {
  const arr = MESSAGES[key];
  return arr[Math.floor(Math.random() * arr.length)];
}

// ── Init ──────────────────────────────────────────────
async function init() {
  state.config = await fetchConfig();
  state.maxAttempts = state.config.max_attempts ?? 3;

  if (state.config.child_name) {
    els.childTitle.textContent = `🦕 ${state.config.child_name} Learns!`;
  }

  resetDots();
  await loadNextWord();
}

const CONFIG_KEY = 'myra_config';

async function fetchConfig() {
  const defaults = { languages: ['telugu', 'assamese'], categories: ['animals', 'colors', 'body_parts', 'numbers', 'food', 'common_objects'], child_name: 'Myra', show_romanized: true, similarity_threshold: 50, max_attempts: 3 };
  try {
    const resp = await fetch('/api/config');
    const serverDefaults = await resp.json();
    const stored = sessionStorage.getItem(CONFIG_KEY);
    if (stored) {
      return { ...serverDefaults, ...JSON.parse(stored) };
    }
    return serverDefaults;
  } catch {
    const stored = sessionStorage.getItem(CONFIG_KEY);
    if (stored) return { ...defaults, ...JSON.parse(stored) };
    return defaults;
  }
}

// ── Word loading ──────────────────────────────────────
async function loadNextWord() {
  state.attempts = 0;
  resetDots();
  hideFeedback();
  els.wordCard.classList.remove('correct-flash', 'wrong-flash');

  try {
    const params = new URLSearchParams();
    (state.config.languages || []).forEach(l => params.append('languages', l));
    (state.config.categories || []).forEach(c => params.append('categories', c));
    const resp = await fetch(`/api/word?${params}`);
    if (!resp.ok) {
      const err = await resp.json();
      setBubble(err.detail || 'Error loading word. Check settings!');
      return;
    }
    state.currentWord = await resp.json();
  } catch (e) {
    setBubble('Cannot connect to server. Is it running?');
    return;
  }

  displayWord(state.currentWord);
  setBubble(randomMsg('idle'));
  animateDino('idle');
}

function displayWord(word) {
  const showRoman = state.config.show_romanized ?? true;

  // Re-trigger card animation
  els.wordCard.style.animation = 'none';
  void els.wordCard.offsetWidth;
  els.wordCard.style.animation = '';

  els.wordEmoji.textContent     = word.emoji || '🌟';
  els.wordEnglish.textContent   = word.english.toUpperCase();
  els.wordTranslation.textContent = word.translation;
  els.wordRomanized.textContent = (showRoman && word.romanized) ? `(${word.romanized})` : '';

  const langLabel = word.language === 'telugu' ? 'Telugu 🌟' : 'Assamese 🌿';
  els.langBadge.textContent = langLabel;
}

// ── TTS: play pronunciation ───────────────────────────
async function playWord() {
  if (!state.currentWord) return;
  state.stopRequested = false;
  stopExistingAudio();

  const { translation, language } = state.currentWord;
  const url = `/api/tts?text=${encodeURIComponent(translation)}&language=${language}`;

  setBubble("Listen carefully! 👂");
  animateDino('talk');

  try {
    const audio = new Audio(url);
    state.ttsAudio = audio;

    audio.addEventListener('ended', () => {
      animateDino('idle');
      setBubble(randomMsg('prompt'));
    });

    await audio.play();
  } catch (e) {
    console.error('TTS playback error:', e);
    animateDino('idle');
  }
}

function stopExistingAudio() {
  if (state.ttsAudio) {
    state.ttsAudio.pause();
    state.ttsAudio.currentTime = 0;
    state.ttsAudio = null;
  }
}

// ── Prompt + record (plays "Name, repeat after me! <word>" then listens) ──
async function playPromptThenRecord() {
  if (state.isRecording) return;
  if (!state.currentWord) return;
  state.stopRequested = false;

  stopExistingAudio();

  const childName = state.config.child_name || 'Myra';
  const { translation, language } = state.currentWord;

  setBubble(`${childName}, repeat after me! 🎤`);
  animateDino('ask');

  try {
    // 1. Play "<Name>, repeat after me!" in English
    const promptText = `${childName}, repeat after me!`;
    await playAudioUrl(`/api/tts?text=${encodeURIComponent(promptText)}&language=english`);

    // 2. Short pause between prompt and word
    await sleep(350);

    // 3. Play the target-language word
    await playAudioUrl(`/api/tts?text=${encodeURIComponent(translation)}&language=${language}`);

    // 4. Short gap, then start recording
    await sleep(500);
    animateDino('idle');
    setBubble(randomMsg('listen'));
    startRecording();
  } catch (e) {
    console.error('Prompt playback error:', e);
    animateDino('idle');
    // Still try to record even if TTS failed
    startRecording();
  }
}

// ── Helpers ───────────────────────────────────────────
function playAudioUrl(url) {
  return new Promise((resolve, reject) => {
    const audio = new Audio(url);
    state.ttsAudio = audio;
    audio.addEventListener('ended', resolve);
    audio.addEventListener('error', reject);
    audio.play().catch(reject);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Play a short reaction sound ("yaaaaay" / "beeeep") with lip-sync
function playReaction(text) {
  const teeth = els.dinoTeeth;
  teeth.style.display = 'block';
  animateMouth(true);
  const done = () => { animateMouth(false); teeth.style.display = 'none'; };
  playAudioUrl(`/api/tts?text=${encodeURIComponent(text)}&language=english`)
    .then(done).catch(done);
}

// Schedule a transition (next word, retry, etc.). IDs stored so Stop can cancel them.
function scheduleTransition(fn, delayMs) {
  const id = setTimeout(() => {
    state.pendingTimeoutIds = state.pendingTimeoutIds.filter(x => x !== id);
    fn();
  }, delayMs);
  state.pendingTimeoutIds.push(id);
}

// ── Recording ─────────────────────────────────────────
async function startRecording() {
  if (state.isRecording) return;
  if (!state.currentWord) return;
  if (state.stopRequested) return;

  stopExistingAudio();

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setBubble("I can't hear you! 😢 Please allow microphone access.");
    alert('Microphone access denied. Please allow it in your browser settings.');
    return;
  }

  state.audioChunks = [];
  state.isRecording = true;

  const options = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? { mimeType: 'audio/webm;codecs=opus' }
    : {};

  state.mediaRecorder = new MediaRecorder(stream, options);

  state.mediaRecorder.addEventListener('dataavailable', e => {
    if (e.data.size > 0) state.audioChunks.push(e.data);
  });

  state.mediaRecorder.addEventListener('stop', async () => {
    stream.getTracks().forEach(t => t.stop());
    state.isRecording = false;
    setRecordingUI(false);
    if (state.stopRequested) {
      state.stopRequested = false;
      return;
    }
    await processAudio();
  });

  state.mediaRecorder.start();
  setRecordingUI(true);
  setBubble(randomMsg('listen'));

  // Auto-stop after 5 seconds
  const duration = 5;
  let remaining = duration;
  els.recTimer.textContent = remaining;

  state.recTimerInterval = setInterval(() => {
    remaining -= 1;
    els.recTimer.textContent = remaining;
    if (remaining <= 0) stopRecording();
  }, 1000);
}

function stopRecording() {
  if (!state.isRecording) return;
  clearInterval(state.recTimerInterval);
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    state.mediaRecorder.stop();
  }
}

function setRecordingUI(recording) {
  if (recording) {
    els.btnRecord.classList.add('recording');
    els.btnRecord.textContent = '⏹ Stop';
    els.btnRecord.onclick = stopRecording;
    els.recIndicator.classList.remove('hidden');
    els.btnPlay.disabled = true;
    els.btnSkip.disabled = true;
  } else {
    els.btnRecord.classList.remove('recording');
    els.btnRecord.textContent = '🎤 Say It!';
    els.btnRecord.onclick = playPromptThenRecord;
    els.recIndicator.classList.add('hidden');
    els.btnPlay.disabled = false;
    els.btnSkip.disabled = false;
  }
}

// ── Audio processing ──────────────────────────────────
async function processAudio() {
  if (!state.audioChunks.length) {
    setBubble("I didn't hear anything! Try again. 🎤");
    showDebug('', '', 'No audio recorded');
    return;
  }

  const mimeType = state.mediaRecorder?.mimeType || 'audio/webm';
  const audioBlob = new Blob(state.audioChunks, { type: mimeType });

  console.log(`Sending audio: mimeType=${mimeType}, size=${audioBlob.size} bytes`);

  setBubble("Hmm, let me think… 🤔");
  animateDino('think');
  showDebug('…thinking…', '', '');

  // Pick a file extension that matches the mime type for the filename hint
  const ext = mimeType.includes('mp4') ? 'mp4'
             : mimeType.includes('ogg') ? 'ogg'
             : mimeType.includes('wav') ? 'wav'
             : 'webm';

  const formData = new FormData();
  formData.append('audio', audioBlob, `recording.${ext}`);
  formData.append('language', state.currentWord.language);
  formData.append('expected_word', state.currentWord.translation);
  formData.append('romanized', state.currentWord.romanized || '');
  formData.append('audio_format', mimeType);
  formData.append('similarity_threshold', String(state.config.similarity_threshold ?? 50));

  try {
    const resp = await fetch('/api/recognize', { method: 'POST', body: formData });
    const result = await resp.json();
    console.log('Recognition result:', result);
    showDebug(result.transcribed, result.similarity, result.error || '');
    handleResult(result);
  } catch (err) {
    console.error('Recognition error:', err);
    setBubble("Oops! Something went wrong. Try again! 🙈");
    showDebug('', '', err.message);
    animateDino('idle');
  }
}

// ── Debug panel ───────────────────────────────────────
function showDebug(heard, similarity, error) {
  const panel = document.getElementById('debug-panel');
  if (!panel) return;

  const heardEl = document.getElementById('debug-heard');
  const simEl   = document.getElementById('debug-sim');
  const errEl   = document.getElementById('debug-error');

  heardEl.textContent = heard  ? `🎙️ Whisper heard: "${heard}"` : '';
  simEl.textContent   = (similarity !== '' && similarity !== undefined)
                        ? `📊 Match score: ${similarity}%` : '';
  errEl.textContent   = error  ? `⚠️ Error: ${error}` : '';

  panel.classList.remove('hidden');
}

// ── Result handling ───────────────────────────────────
function handleResult(result) {
  state.attempts += 1;
  updateDots(state.attempts - 1, result.is_correct);

  if (result.is_correct) {
    // ✅ Correct!
    state.score += 1;
    state.wordsAttempted += 1;
    updateScore();

    showFeedback(
      `🎉 ${randomMsg('correct')} You said: "${result.transcribed}"`,
      'correct'
    );
    animateDino('celebrate');
    els.wordCard.classList.add('correct-flash');
    launchConfetti();
    setBubble(randomMsg('correct'));
    playReaction('yaaaaay');

    // Move to next word after a short delay (cancelled if Stop pressed)
    scheduleTransition(nextWord, 2200);

  } else if (state.attempts >= state.maxAttempts) {
    // ❌ Out of attempts – auto-advance
    state.wordsAttempted += 1;
    updateScore();

    const heard = result.transcribed ? `I heard: "${result.transcribed}". ` : '';
    showFeedback(
      `${heard}The word is: ${state.currentWord.translation} (${state.currentWord.english})`,
      'wrong'
    );
    animateDino('shake');
    els.wordCard.classList.add('wrong-flash');
    setBubble("Good try! Let's move on. 🌟");
    playReaction('beeeep');

    scheduleTransition(nextWord, 3000);

  } else {
    // ❌ Wrong, but can try again
    const heard = result.transcribed ? `I heard "${result.transcribed}".` : '';
    showFeedback(
      `${heard} ${randomMsg('wrong')} (${state.maxAttempts - state.attempts} tries left)`,
      'wrong'
    );
    animateDino('shake');
    els.wordCard.classList.add('wrong-flash');
    setBubble(randomMsg('wrong'));
    playReaction('beeeep');

    // Say "Myra, repeat after me! <word>" again, then start recording (cancelled if Stop pressed)
    scheduleTransition(() => {
      els.wordCard.classList.remove('wrong-flash');
      playPromptThenRecord();
    }, 1500);
  }
}

// ── Stop flow ─────────────────────────────────────────
function stopFlow() {
  state.pendingTimeoutIds.forEach(id => clearTimeout(id));
  state.pendingTimeoutIds = [];

  stopExistingAudio();
  if (state.isRecording) {
    state.stopRequested = true;
    stopRecording();
  }
  if (state.isRecording) setRecordingUI(false);

  els.wordCard.classList.remove('correct-flash', 'wrong-flash');
  hideFeedback();
  animateDino('idle');
  setBubble(randomMsg('stop'));
}

// ── Navigation ────────────────────────────────────────
async function nextWord() {
  state.stopRequested = false;
  setBubble(randomMsg('skip'));
  await loadNextWord();
  // Auto-play pronunciation for the new word
  setTimeout(() => playWord(), 600);
}

// ── UI helpers ────────────────────────────────────────
function setBubble(text) {
  els.bubbleText.textContent = text;
}

function showFeedback(text, type) {
  els.feedbackBanner.className = `feedback-banner ${type}`;
  els.feedbackText.textContent = text;
}

function hideFeedback() {
  els.feedbackBanner.className = 'feedback-banner hidden';
  els.feedbackText.textContent = '';
}

function updateScore() {
  els.scoreDisplay.textContent = state.score;
  els.wordsDisplay.textContent = state.wordsAttempted;
}

function resetDots() {
  els.dots.forEach(d => {
    d.className = 'dot';
  });
  // Show only the configured max number of dots
  const max = state.maxAttempts || 3;
  els.dots.forEach((d, i) => {
    d.style.display = i < max ? '' : 'none';
  });
}

function updateDots(index, correct) {
  if (index < els.dots.length) {
    els.dots[index].classList.add(correct ? 'right' : 'used');
  }
}

// ── Dino animations ───────────────────────────────────
function animateDino(state_name) {
  const svg = els.dinoSvg;
  svg.classList.remove('dino-celebrate', 'dino-shake', 'dino-talk', 'dino-ask');

  const teeth = els.dinoTeeth;

  if (state_name === 'celebrate') {
    svg.classList.add('dino-celebrate');
    teeth.style.display = 'block';
    setTimeout(() => { teeth.style.display = 'none'; }, 1600);
  } else if (state_name === 'shake') {
    svg.classList.add('dino-shake');
  } else if (state_name === 'ask') {
    // Dino turns to face the toddler and asks the question
    svg.classList.add('dino-ask');
    teeth.style.display = 'block';
    animateMouth(true);
  } else if (state_name === 'talk') {
    svg.classList.add('dino-talk');
    teeth.style.display = 'block';
    // Talk animation: alternate mouth shape
    animateMouth(true);
  } else {
    teeth.style.display = 'none';
    animateMouth(false);
    // idle – default CSS handles it
  }
}

// Lip-sync frames: [upper-lip path d, inner-mouth ry, inner-mouth cy]
// Upper lip flattens as jaw drops; inner cavity grows to fill the gap.
const MOUTH_FRAMES = [
  ['M 330,162 Q 355,170 375,162',  0,    0],   // closed
  ['M 330,161 Q 355,164 375,161',  7,  170],   // slightly open
  ['M 330,161 Q 355,162 375,161', 12,  175],   // open
  ['M 330,162 Q 355,166 375,162',  5,  168],   // closing
];

let mouthInterval = null;
let _mouthFrameIdx = 0;

function animateMouth(open) {
  clearInterval(mouthInterval);
  const mouth = els.dinoMouth;
  const inner = els.dinoMouthInner;

  if (!open) {
    mouth.setAttribute('d', 'M 330,162 Q 355,178 375,162');
    if (inner) inner.style.display = 'none';
    return;
  }

  _mouthFrameIdx = 0;
  mouthInterval = setInterval(() => {
    const [d, ry, cy] = MOUTH_FRAMES[_mouthFrameIdx % MOUTH_FRAMES.length];
    _mouthFrameIdx++;
    mouth.setAttribute('d', d);
    if (inner) {
      if (ry > 0) {
        inner.style.display = '';
        inner.setAttribute('ry', ry);
        inner.setAttribute('cy', cy);
      } else {
        inner.style.display = 'none';
      }
    }
  }, 130);
}

// ── Confetti ──────────────────────────────────────────
const CONFETTI_COLORS = ['#FF69B4','#FFD700','#9B59B6','#2ECC71','#FF1493','#3498DB','#E74C3C','#F39C12'];

function launchConfetti() {
  const container = els.confetti;
  container.innerHTML = '';

  for (let i = 0; i < 60; i++) {
    const piece = document.createElement('div');
    piece.className = 'confetti-piece';
    piece.style.cssText = `
      left: ${Math.random() * 100}%;
      width: ${8 + Math.random() * 8}px;
      height: ${8 + Math.random() * 8}px;
      background: ${CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)]};
      border-radius: ${Math.random() > 0.5 ? '50%' : '3px'};
      animation-duration: ${1.5 + Math.random() * 2}s;
      animation-delay: ${Math.random() * 0.5}s;
    `;
    container.appendChild(piece);
  }

  setTimeout(() => { container.innerHTML = ''; }, 4000);
}

// ── Start ─────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', init);
