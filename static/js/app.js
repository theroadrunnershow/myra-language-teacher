/* ═══════════════════════════════════════════════════════
   Myra Language Teacher – Frontend App
   Roo UX v2: voice layer, Web Audio SFX, animations, streaks
═══════════════════════════════════════════════════════ */

// ── State ─────────────────────────────────────────────
const state = {
  currentWord: null,       // { english, translation, emoji, romanized, language, category }
  config: {},
  score: 0,
  wordsAttempted: 0,
  attempts: 0,             // current word attempt count
  maxAttempts: 3,
  streak: 0,               // consecutive correct answers
  isRecording: false,
  mediaRecorder: null,
  audioChunks: [],
  recTimerInterval: null,
  ttsAudio: null,          // current playing word-pronunciation Audio object
  voiceAudio: null,        // current playing Roo voice-line Audio object
  pendingTimeoutIds: [],   // timeouts to clear when Stop is pressed
  stopRequested: false,    // true when Stop pressed; skips processAudio after recording
  blinkTimerId: null,      // handle for the random blink scheduler
};

// ── DOM refs ──────────────────────────────────────────
const $ = id => document.getElementById(id);

const els = {
  dinoWrapper:    $('dino-wrapper'),
  dinoSvg:        $('dino-svg'),
  dinoMouth:      $('dino-mouth'),
  dinoTeeth:      $('dino-teeth'),
  dinoMouthInner: $('dino-mouth-inner'),
  dinoEyelid:     $('dino-eyelid'),
  bubble:         $('speech-bubble'),
  bubbleText:     $('bubble-text'),
  langBadge:      $('lang-badge'),
  wordCard:       $('word-card'),
  wordEmoji:      $('word-emoji'),
  wordEnglish:    $('word-english'),
  wordTranslation: $('word-translation'),
  wordRomanized:  $('word-romanized'),
  feedbackBanner: $('feedback-banner'),
  feedbackText:   $('feedback-text'),
  dots:           [$('dot-1'), $('dot-2'), $('dot-3')],
  btnPlay:        $('btn-play'),
  btnRecord:      $('btn-record'),
  btnSkip:        $('btn-skip'),
  btnStop:        $('btn-stop'),
  recIndicator:   $('recording-indicator'),
  recTimer:       $('rec-timer'),
  scoreDisplay:   $('score-display'),
  wordsDisplay:   $('words-display'),
  confetti:       $('confetti-container'),
  childTitle:     $('child-name-title'),
  streakDisplay:  $('streak-display'),
  streakCount:    $('streak-count'),
};

// ── Dino speech messages ──────────────────────────────
// 8+ lines per state. Emojis stripped before TTS so gTTS doesn't choke.
const MESSAGES = {
  idle: [
    "Hi! Let's learn words! 🌟",
    "Ready to learn? 🦕",
    "Let's go! 🎉",
    "You can do it! 💪",
    "What an exciting word! 🦕",
    "I love learning with you! 💕",
    "Let's find out together! 🎯",
    "Ooooh! Look at this one! 🌟",
  ],
  prompt: [
    "Can you say this? 🎤",
    "Now YOU try! 🌟",
    "Say it with me! 😊",
    "Your turn! 🎤",
    "I believe in you! 💪",
    "You've got this! 🌟",
    "Ready? Let's go! 🚀",
    "Give it your best! 🦕",
  ],
  correct: [
    "Amazing! ⭐",
    "Yay!! 🎉",
    "Super! 🌟",
    "Brilliant! 🦕",
    "You're a star! ⭐",
    "ROOOAR-mazing! 🦕",
    "Incredible! 🌟",
    "You did it!! 🎉",
  ],
  // Escalated celebrations based on which attempt succeeded
  correct1: [
    "ROOOAR-mazing! You did it! First try!",
    "WHOOOOA! First try! You are incredible!",
    "Yes! First try! I knew you could!",
    "Wow! Perfect! You are a superstar!",
  ],
  correct2: [
    "Yes! You kept trying and you got it! That is my Myra!",
    "You did not give up! Amazing!",
    "Second try! That is the spirit!",
    "You persisted and won! Brilliant!",
  ],
  correct3: [
    "You did not give up! That is the bravest thing ever!",
    "Third time is the charm! You are incredible!",
    "You never quit! That makes me SO happy!",
    "Persistence wins! You are amazing!",
  ],
  streak3: [
    "Three in a ROWWW! You are on fire!",
    "Three words! Roo is so proud of you!",
    "Hat trick! Three correct in a row!",
  ],
  streak5: [
    "FIVE WORDS! You are unstoppable!",
    "Five in a row! Someone get this kid a trophy!",
    "Five words! Roo might actually explode from happy!",
  ],
  wrong: [
    "Try again! 💪",
    "So close! 🤗",
    "Almost! Give it another go! 😊",
    "Keep trying! 🌟",
    "Oopsie! Close though! Let's try one more time!",
    "Hmmm! I believe in you so much. One more?",
    "You have almost got it! Let's try again!",
    "So close! You will get it this time!",
  ],
  outOfAttempts: [
    "Awww! It's a tricky one! You will get it next time!",
    "That one is a toughie! But you will remember next time!",
    "Great effort! You will get it next time, I promise!",
  ],
  skip: [
    "Next word! Let's go! 🚀",
    "New word coming! 🌟",
    "Here we go again! 🦕",
    "Ooh next one! Here it comes! Zoom!",
    "On to the next adventure! 🚀",
  ],
  listen: [
    "I'm listening! 👂",
    "Speak up! 🎤",
    "Go ahead! 🌟",
    "I am all ears! Literally! Big ears!",
    "Ready when you are! 🎤",
    "Let me hear your voice! 🌟",
  ],
  stop: [
    "Stopped. Ready when you are! 🌟",
    "Paused! Take your time. 🦕",
    "Whenever you're ready! 😊",
    "No rush! I will wait! 🦕",
    "Take a breath! I am here! 🌟",
  ],
};

// Track last 2 used indices per key to prevent immediate repeats
const _lastMsgIdx = {};

function randomMsg(key) {
  const arr = MESSAGES[key];
  if (!arr || arr.length === 0) return '';
  if (arr.length <= 2) return arr[Math.floor(Math.random() * arr.length)];
  const last = _lastMsgIdx[key] || [];
  let idx;
  do { idx = Math.floor(Math.random() * arr.length); } while (last.includes(idx));
  _lastMsgIdx[key] = [...last.slice(-1), idx];
  return arr[idx];
}

// Strip emoji characters so gTTS doesn't choke on them
function stripEmoji(text) {
  return text
    .replace(/[\u{1F000}-\u{1FFFF}]/gu, '')
    .replace(/[\u{2600}-\u{27FF}]/gu, '')
    .replace(/[\u{FE00}-\u{FEFF}]/gu, '')
    .replace(/[⭐✨💫🌟🦕🎉💪😊🎤👂🚀💕🎯🔥🤗]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// ── Web Audio API – synthesised SFX ───────────────────
// All sounds are generated programmatically (no audio files needed).
let _audioCtx = null;

function getAudioCtx() {
  if (!_audioCtx) {
    _audioCtx = new (window.AudioContext || window['webkitAudioContext'])();
  }
  if (_audioCtx.state === 'suspended') _audioCtx.resume().catch(() => {});
  return _audioCtx;
}

function playTone(freq, durationSec, type = 'sine', peakGain = 0.22) {
  try {
    const ctx = getAudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(peakGain, ctx.currentTime + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + durationSec);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + durationSec + 0.01);
  } catch (_) { /* AudioContext unavailable */ }
}

// D5 marimba pop – fires on every button press
function playButtonTap() {
  playTone(587, 0.08, 'sine', 0.10);
}

// G4 → B4 rising "doo-doot" – fires when a new word card appears
function playWordReveal() {
  playTone(392, 0.14, 'sine', 0.16);
  setTimeout(() => playTone(494, 0.18, 'sine', 0.16), 160);
}

// E4 → G4 → B4 staccato – fires while waiting for Whisper result
function playProcessingSound() {
  [330, 392, 494].forEach((freq, i) => {
    setTimeout(() => playTone(freq, 0.09, 'sine', 0.12), i * 150);
  });
}

// B3 → A3 descending tone – soft "aw shucks" for wrong answer
function playBeepSound() {
  try {
    const ctx = getAudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(247, ctx.currentTime);
    osc.frequency.linearRampToValueAtTime(220, ctx.currentTime + 0.42);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.52);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.56);
  } catch (_) { /* AudioContext unavailable */ }
}

// C4 → E4 → G4 → C5 ascending arpeggio – correct answer fanfare
function playYaaySound() {
  [262, 330, 392, 523].forEach((freq, i) => {
    setTimeout(() => playTone(freq, 0.2, 'sine', 0.20), i * 120);
  });
}

// Higher-pitched arpeggio for streak milestones
function playStreakSound(streakCount) {
  const notes = streakCount >= 5
    ? [392, 494, 587, 784]
    : [330, 415, 494, 659];
  notes.forEach((freq, i) => {
    setTimeout(() => playTone(freq, 0.18, 'sine', 0.26), i * 100);
  });
}

// ── Roo's voice lines (English TTS) ───────────────────
// Fetches /api/dino-voice with the given text, plays it with mouth animation.
// Silently no-ops during recording to avoid Whisper contamination.
async function playDinoVoice(text) {
  if (state.isRecording) return;

  const clean = stripEmoji(text);
  if (!clean.trim()) return;

  // Stop any in-progress voice line
  if (state.voiceAudio) {
    state.voiceAudio.pause();
    state.voiceAudio = null;
  }

  try {
    const url = `/api/dino-voice?text=${encodeURIComponent(clean)}`;
    const audio = new Audio(url);
    state.voiceAudio = audio;

    animateMouth(true);
    els.dinoTeeth.style.display = 'block';

    const done = () => {
      state.voiceAudio = null;
      if (!state.isRecording) {
        animateMouth(false);
        const svg = els.dinoSvg;
        const stillSpeaking = svg.classList.contains('dino-talk') || svg.classList.contains('dino-ask');
        if (!stillSpeaking) els.dinoTeeth.style.display = 'none';
      }
    };

    audio.addEventListener('ended', done);
    audio.addEventListener('error', done);
    await audio.play();
  } catch (e) {
    console.warn('Dino voice failed:', e);
    state.voiceAudio = null;
    animateMouth(false);
  }
}

// ── Blink timer ────────────────────────────────────────
// Schedules random eye blinks every 3–7 s during idle.
function blinkDino() {
  const eyelid = els.dinoEyelid;
  if (!eyelid || state.isRecording) return;
  eyelid.setAttribute('ry', '22');
  setTimeout(() => eyelid.setAttribute('ry', '0'), 150);
}

function startBlinkTimer() {
  if (state.blinkTimerId) clearTimeout(state.blinkTimerId);
  const delay = 3000 + Math.random() * 4000;
  state.blinkTimerId = setTimeout(() => {
    blinkDino();
    startBlinkTimer();
  }, delay);
}

function stopBlinkTimer() {
  if (state.blinkTimerId) {
    clearTimeout(state.blinkTimerId);
    state.blinkTimerId = null;
  }
}

// ── Init ──────────────────────────────────────────────
const CONFIG_KEY = 'myra_config';

async function init() {
  const stored = localStorage.getItem(CONFIG_KEY);
  if (!stored || !JSON.parse(stored).setup_complete) {
    window.location.href = '/settings';
    return;
  }

  state.config = await fetchConfig();
  state.maxAttempts = state.config.max_attempts ?? 3;

  if (state.config.child_name) {
    els.childTitle.textContent = `🦕 ${state.config.child_name} Learns!`;
    document.title = `${state.config.child_name} Learns Languages 🦕`;
  }

  resetDots();
  startBlinkTimer();
  await loadNextWord();
}

async function fetchConfig() {
  const defaults = { languages: ['telugu'], categories: ['animals', 'colors', 'body_parts', 'numbers', 'food', 'common_objects'], child_name: '', show_romanized: true, similarity_threshold: 50, max_attempts: 3 };
  try {
    const resp = await fetch('/api/config');
    const serverDefaults = await resp.json();
    const stored = localStorage.getItem(CONFIG_KEY);
    if (stored) {
      return { ...serverDefaults, ...JSON.parse(stored) };
    }
    return serverDefaults;
  } catch {
    const stored = localStorage.getItem(CONFIG_KEY);
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
  playWordReveal();
  setBubble(randomMsg('idle'));
  animateDino('idle');
}

function displayWord(word) {
  const showRoman = state.config.show_romanized ?? true;

  // Re-trigger card slide-in animation
  els.wordCard.style.animation = 'none';
  void els.wordCard.offsetWidth;
  els.wordCard.style.animation = '';

  els.wordEmoji.textContent      = word.emoji || '🌟';
  els.wordEnglish.textContent    = word.english.toUpperCase();
  els.wordTranslation.textContent = word.translation;
  els.wordRomanized.textContent  = (showRoman && word.romanized) ? `(${word.romanized})` : '';

  const langLabel = word.language === 'telugu' ? 'Telugu 🌟' : 'Assamese 🌿';
  els.langBadge.textContent = langLabel;
}

// ── TTS: play pronunciation ───────────────────────────
async function playWord() {
  if (!state.currentWord) return;
  state.stopRequested = false;
  stopExistingAudio();

  const { translation, language } = state.currentWord;
  const url = `/api/tts?text=${encodeURIComponent(translation)}&language=${language}&slow=true`;

  setBubble("Listen carefully! 👂");
  animateDino('talk');

  try {
    const audio = new Audio(url);
    state.ttsAudio = audio;

    audio.addEventListener('ended', () => {
      animateDino('idle');
      const promptMsg = randomMsg('prompt');
      setBubble(promptMsg);
      // Roo voices the prompt (stripped of emoji)
      playDinoVoice(promptMsg);
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
  if (state.voiceAudio) {
    state.voiceAudio.pause();
    state.voiceAudio = null;
  }
  animateMouth(false);
}

// ── Prompt + record ────────────────────────────────────
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

    // 2. Short gap then the target-language word (slow for clarity)
    await sleep(350);
    await playAudioUrl(`/api/tts?text=${encodeURIComponent(translation)}&language=${language}&slow=true`);

    // 3. Gap then start recording
    await sleep(500);
    animateDino('idle');
    setBubble(randomMsg('listen'));
    startRecording();
  } catch (e) {
    console.error('Prompt playback error:', e);
    animateDino('idle');
    startRecording();
  }
}

// ── Helpers ───────────────────────────────────────────
function playAudioUrl(url, playbackRate = 1.0) {
  return new Promise((resolve, reject) => {
    const audio = new Audio(url);
    state.ttsAudio = audio;
    audio.playbackRate = playbackRate;
    audio.addEventListener('ended', resolve);
    audio.addEventListener('error', reject);
    audio.play().catch(reject);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Schedule a transition (next word, retry). IDs stored so Stop can cancel them.
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
  stopBlinkTimer(); // Don't blink while listening

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setBubble("I can't hear you! 😢 Please allow microphone access.");
    alert('Microphone access denied. Please allow it in your browser settings.');
    startBlinkTimer();
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
    startBlinkTimer(); // Resume blinking
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
  animateDino('idle');
  playProcessingSound();
  showDebug('…thinking…', '', '');

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
    updateStreak(true);

    // Pick escalated celebration based on which attempt succeeded
    const celebKey = state.attempts === 1 ? 'correct1'
                   : state.attempts === 2 ? 'correct2'
                   : 'correct3';
    const celebMsg = randomMsg(celebKey);

    showFeedback(
      `🎉 ${randomMsg('correct')} You said: "${result.transcribed}"`,
      'correct'
    );
    animateDino('celebrate');
    els.wordCard.classList.add('correct-flash');
    launchConfetti();
    launchSparkles();
    showStarPop();
    playYaaySound();
    setBubble(celebMsg);
    // Voice line after the arpeggio has started
    scheduleTransition(() => playDinoVoice(celebMsg), 350);
    scheduleTransition(nextWord, 2800);

  } else if (state.attempts >= state.maxAttempts) {
    // ❌ Out of attempts — reveal answer and move on
    state.wordsAttempted += 1;
    updateScore();
    updateStreak(false);

    const heard = result.transcribed ? `I heard: "${result.transcribed}". ` : '';
    showFeedback(
      `${heard}The word is: ${state.currentWord.translation} (${state.currentWord.english})`,
      'wrong'
    );
    animateDino('shake');
    els.wordCard.classList.add('wrong-flash');
    const outMsg = randomMsg('outOfAttempts');
    setBubble("Good try! Let's move on. 🌟");
    playBeepSound();
    scheduleTransition(() => playDinoVoice(outMsg), 250);
    scheduleTransition(nextWord, 3200);

  } else {
    // ❌ Wrong but retries remaining
    const heard = result.transcribed ? `I heard "${result.transcribed}".` : '';
    showFeedback(
      `${heard} ${randomMsg('wrong')} (${state.maxAttempts - state.attempts} tries left)`,
      'wrong'
    );
    // 1st wrong → curious head tilt; 2nd+ → shake
    animateDino(state.attempts === 1 ? 'tilt' : 'shake');
    els.wordCard.classList.add('wrong-flash');
    const wrongMsg = randomMsg('wrong');
    setBubble(wrongMsg);
    playBeepSound();
    scheduleTransition(() => playDinoVoice(wrongMsg), 250);

    scheduleTransition(() => {
      els.wordCard.classList.remove('wrong-flash');
      playPromptThenRecord();
    }, 1900);
  }
}

// ── Streak management ─────────────────────────────────
function updateStreak(correct) {
  if (!correct) {
    state.streak = 0;
    if (els.streakDisplay) els.streakDisplay.hidden = true;
    return;
  }

  state.streak += 1;

  if (state.streak >= 3) {
    if (els.streakDisplay && els.streakCount) {
      els.streakCount.textContent = state.streak;
      els.streakDisplay.hidden = false;
      els.streakDisplay.classList.remove('streak-pop');
      void els.streakDisplay.offsetWidth;
      els.streakDisplay.classList.add('streak-pop');
    }

    // Milestone sounds + Roo voice at 3, 5, and every 5 thereafter
    if (state.streak === 3 || state.streak === 5 || (state.streak > 5 && state.streak % 5 === 0)) {
      scheduleTransition(() => {
        playStreakSound(state.streak);
        const streakMsg = randomMsg(state.streak >= 5 ? 'streak5' : 'streak3');
        scheduleTransition(() => playDinoVoice(streakMsg), 500);
      }, 900);
    }
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
  els.dots.forEach(d => { d.className = 'dot'; });
  const max = state.maxAttempts || 3;
  els.dots.forEach((d, i) => { d.style.display = i < max ? '' : 'none'; });
}

function updateDots(index, correct) {
  if (index < els.dots.length) {
    els.dots[index].classList.add(correct ? 'right' : 'used');
  }
}

// ── Sparkle particles ──────────────────────────────────
function launchSparkles() {
  const wrapper = els.dinoWrapper;
  if (!wrapper) return;

  const icons = ['⭐', '✨', '💫', '🌟'];
  for (let i = 0; i < 8; i++) {
    const sp = document.createElement('div');
    sp.className = 'sparkle';
    sp.textContent = icons[Math.floor(Math.random() * icons.length)];
    // Spread around dino
    const angle = (i / 8) * 360;
    const x = 48 + Math.cos((angle * Math.PI) / 180) * 28;
    const y = 48 + Math.sin((angle * Math.PI) / 180) * 28;
    sp.style.left = `${x}%`;
    sp.style.top  = `${y}%`;
    sp.style.animationDelay    = `${Math.random() * 0.25}s`;
    sp.style.animationDuration = `${0.55 + Math.random() * 0.25}s`;
    wrapper.appendChild(sp);
    setTimeout(() => sp.remove(), 1100);
  }
}

// ── Star pop (+1 ⭐) ──────────────────────────────────
function showStarPop() {
  const scoreEl = els.scoreDisplay;
  if (!scoreEl) return;
  const rect = scoreEl.getBoundingClientRect();

  const el = document.createElement('div');
  el.className = 'star-pop';
  el.textContent = '+1 ⭐';
  el.style.left = `${rect.left}px`;
  el.style.top  = `${rect.top - 8}px`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1200);
}

// ── Dino animations ───────────────────────────────────
function animateDino(state_name) {
  const svg = els.dinoSvg;
  svg.classList.remove('dino-celebrate', 'dino-shake', 'dino-talk', 'dino-ask', 'dino-tilt');

  const teeth = els.dinoTeeth;

  if (state_name === 'celebrate') {
    svg.classList.add('dino-celebrate');
    teeth.style.display = 'block';
    // Keep teeth visible long enough for the voice line to finish
    setTimeout(() => {
      if (!state.voiceAudio) teeth.style.display = 'none';
    }, 2500);
  } else if (state_name === 'shake') {
    svg.classList.add('dino-shake');
  } else if (state_name === 'tilt') {
    svg.classList.add('dino-tilt');
    // Auto-remove after animation completes
    setTimeout(() => svg.classList.remove('dino-tilt'), 950);
  } else if (state_name === 'ask') {
    svg.classList.add('dino-ask');
    teeth.style.display = 'block';
    animateMouth(true);
  } else if (state_name === 'talk') {
    svg.classList.add('dino-talk');
    teeth.style.display = 'block';
    animateMouth(true);
  } else {
    // idle / default
    teeth.style.display = 'none';
    animateMouth(false);
  }
}

// Lip-sync frames: [upper-lip path d, inner-mouth ry, inner-mouth cy]
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

// ── Button tap sounds ─────────────────────────────────
function attachButtonSounds() {
  ['btn-play', 'btn-record', 'btn-skip', 'btn-stop'].forEach(id => {
    const btn = $(id);
    if (btn) btn.addEventListener('mousedown', playButtonTap);
  });
}

// ── Start ─────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  init();
  attachButtonSounds();
});
