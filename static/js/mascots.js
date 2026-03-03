/* ═══════════════════════════════════════════════════════
   Myra Language Teacher – Animal Mascots
   Each entry has:
     svg        – inner HTML for #dino-svg (replaces dino)
     mouthFrames – [[path_d, ry, cy], …] 4 frames for animateMouth()
     mouthClosed – the closed-mouth path_d string
   Required IDs in every SVG: dino-mouth, dino-mouth-inner,
     dino-teeth, dino-eyelid  (re-hydrated by applyMascot).
   dino: null  sentinel → keep original inline HTML, no swap.
═══════════════════════════════════════════════════════ */

const MASCOTS = {

  dino: null,   // keep the original inline dino SVG

  // ── Cat ────────────────────────────────────────────────────────────────────
  // Orange tabby, round face, pointy ears, whiskers, curled tail
  cat: {
    mouthClosed: 'M 232,268 Q 250,278 268,268',
    mouthFrames: [
      ['M 232,268 Q 250,272 268,268',  0,    0],
      ['M 232,267 Q 250,265 268,267',  6,  276],
      ['M 232,267 Q 250,263 268,267', 10,  280],
      ['M 232,268 Q 250,270 268,268',  4,  274],
    ],
    svg: `
<!-- Cat body -->
<ellipse cx="250" cy="340" rx="95" ry="80" fill="#E8941A" />
<!-- Cat belly -->
<ellipse cx="250" cy="355" rx="60" ry="52" fill="#F5C842" />
<!-- Cat head -->
<circle cx="250" cy="220" r="100" fill="#E8941A" />
<!-- Left ear -->
<polygon points="155,148 178,88 210,152" fill="#E8941A" />
<polygon points="163,145 180,102 204,148" fill="#FFB6C1" />
<!-- Right ear -->
<polygon points="345,148 322,88 290,152" fill="#E8941A" />
<polygon points="337,145 320,102 296,148" fill="#FFB6C1" />
<!-- Face white patch -->
<ellipse cx="250" cy="235" rx="62" ry="55" fill="#F5C842" opacity="0.5" />
<!-- Left eye -->
<ellipse cx="215" cy="205" rx="18" ry="20" fill="#2ECC71" />
<ellipse cx="215" cy="205" rx="6"  ry="18" fill="#1a1a1a" />
<circle  cx="218" cy="199" r="4"   fill="white" />
<!-- Right eye -->
<ellipse cx="285" cy="205" rx="18" ry="20" fill="#2ECC71" />
<ellipse cx="285" cy="205" rx="6"  ry="18" fill="#1a1a1a" />
<circle  cx="288" cy="199" r="4"   fill="white" />
<!-- Eyelid for blink (left) -->
<ellipse id="dino-eyelid" cx="215" cy="205" rx="18" ry="0" fill="#E8941A" />
<!-- Nose -->
<ellipse cx="250" cy="242" rx="9" ry="6" fill="#FF69B4" />
<!-- Mouth -->
<path id="dino-mouth" d="M 232,268 Q 250,278 268,268" fill="none" stroke="#5a2a00" stroke-width="3" stroke-linecap="round" />
<path d="M 250,248 L 250,262" fill="none" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<!-- Mouth inner (hidden by default) -->
<ellipse id="dino-mouth-inner" cx="250" cy="275" rx="14" ry="0" fill="#c0392b" style="display:none" />
<!-- Teeth -->
<g id="dino-teeth" style="display:none">
  <rect x="238" y="268" width="10" height="9" rx="2" fill="white" />
  <rect x="252" y="268" width="10" height="9" rx="2" fill="white" />
</g>
<!-- Whiskers left -->
<line x1="165" y1="248" x2="225" y2="255" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<line x1="165" y1="260" x2="225" y2="260" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<line x1="165" y1="272" x2="225" y2="265" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<!-- Whiskers right -->
<line x1="335" y1="248" x2="275" y2="255" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<line x1="335" y1="260" x2="275" y2="260" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<line x1="335" y1="272" x2="275" y2="265" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<!-- Left arm -->
<ellipse cx="162" cy="330" rx="22" ry="50" fill="#E8941A" transform="rotate(-20,162,330)" />
<!-- Right arm -->
<ellipse cx="338" cy="330" rx="22" ry="50" fill="#E8941A" transform="rotate(20,338,330)" />
<!-- Left paw -->
<ellipse cx="150" cy="374" rx="24" ry="14" fill="#E8941A" />
<!-- Right paw -->
<ellipse cx="350" cy="374" rx="24" ry="14" fill="#E8941A" />
<!-- Left leg -->
<ellipse cx="200" cy="415" rx="24" ry="18" fill="#E8941A" />
<!-- Right leg -->
<ellipse cx="300" cy="415" rx="24" ry="18" fill="#E8941A" />
<!-- Tail (curled) -->
<path d="M 340,380 Q 400,360 410,310 Q 420,260 380,250" fill="none" stroke="#E8941A" stroke-width="22" stroke-linecap="round" />
<path d="M 340,380 Q 400,360 410,310 Q 420,260 380,250" fill="none" stroke="#F5C842" stroke-width="10" stroke-linecap="round" opacity="0.5" />
<!-- Cheek blush -->
<ellipse cx="186" cy="245" rx="16" ry="10" fill="#FF69B4" opacity="0.35" />
<ellipse cx="314" cy="245" rx="16" ry="10" fill="#FF69B4" opacity="0.35" />
`
  },

  // ── Dog ────────────────────────────────────────────────────────────────────
  // Golden retriever, floppy ears, big nose, droopy smile
  dog: {
    mouthClosed: 'M 228,270 Q 250,286 272,270',
    mouthFrames: [
      ['M 228,270 Q 250,278 272,270',  0,    0],
      ['M 228,269 Q 250,267 272,269',  7,  278],
      ['M 228,269 Q 250,265 272,269', 12,  282],
      ['M 228,270 Q 250,274 272,270',  5,  276],
    ],
    svg: `
<!-- Dog body -->
<ellipse cx="250" cy="345" rx="100" ry="82" fill="#C8860A" />
<!-- Dog belly -->
<ellipse cx="250" cy="360" rx="64" ry="54" fill="#F0C060" />
<!-- Dog head -->
<circle cx="250" cy="215" r="105" fill="#C8860A" />
<!-- Muzzle -->
<ellipse cx="250" cy="250" rx="55" ry="42" fill="#F0C060" />
<!-- Left floppy ear -->
<ellipse cx="158" cy="225" rx="32" ry="70" fill="#A06808" transform="rotate(-12,158,225)" />
<!-- Right floppy ear -->
<ellipse cx="342" cy="225" rx="32" ry="70" fill="#A06808" transform="rotate(12,342,225)" />
<!-- Left eye -->
<circle cx="212" cy="195" r="20" fill="#1a1a1a" />
<circle cx="219" cy="188" r="7"  fill="white" />
<!-- Right eye -->
<circle cx="288" cy="195" r="20" fill="#1a1a1a" />
<circle cx="295" cy="188" r="7"  fill="white" />
<!-- Eyelid for blink -->
<ellipse id="dino-eyelid" cx="250" cy="195" rx="50" ry="0" fill="#C8860A" />
<!-- Nose -->
<ellipse cx="250" cy="238" rx="18" ry="13" fill="#1a1a1a" />
<ellipse cx="244" cy="233" rx="5"  ry="3"  fill="white" opacity="0.5" />
<!-- Mouth -->
<path id="dino-mouth" d="M 228,270 Q 250,286 272,270" fill="none" stroke="#5a2a00" stroke-width="3.5" stroke-linecap="round" />
<path d="M 250,251 L 250,268" fill="none" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<!-- Mouth inner -->
<ellipse id="dino-mouth-inner" cx="250" cy="278" rx="16" ry="0" fill="#c0392b" style="display:none" />
<!-- Teeth -->
<g id="dino-teeth" style="display:none">
  <rect x="237" y="269" width="12" height="10" rx="3" fill="white" />
  <rect x="252" y="269" width="12" height="10" rx="3" fill="white" />
</g>
<!-- Left arm -->
<ellipse cx="158" cy="328" rx="24" ry="54" fill="#C8860A" transform="rotate(-15,158,328)" />
<!-- Right arm -->
<ellipse cx="342" cy="328" rx="24" ry="54" fill="#C8860A" transform="rotate(15,342,328)" />
<!-- Left paw -->
<ellipse cx="145" cy="376" rx="26" ry="15" fill="#C8860A" />
<!-- Right paw -->
<ellipse cx="355" cy="376" rx="26" ry="15" fill="#C8860A" />
<!-- Left leg -->
<ellipse cx="200" cy="418" rx="25" ry="18" fill="#C8860A" />
<!-- Right leg -->
<ellipse cx="300" cy="418" rx="25" ry="18" fill="#C8860A" />
<!-- Tail (wagging) -->
<path d="M 345,360 Q 400,320 415,270 Q 425,235 400,220" fill="none" stroke="#C8860A" stroke-width="24" stroke-linecap="round" />
<!-- Cheek blush -->
<ellipse cx="182" cy="252" rx="18" ry="11" fill="#FF69B4" opacity="0.3" />
<ellipse cx="318" cy="252" rx="18" ry="11" fill="#FF69B4" opacity="0.3" />
<!-- Eyebrow dots (friendly look) -->
<ellipse cx="212" cy="174" rx="14" ry="6" fill="#A06808" />
<ellipse cx="288" cy="174" rx="14" ry="6" fill="#A06808" />
`
  },

  // ── Panda ──────────────────────────────────────────────────────────────────
  // Black/white, round black ear patches, big eye patches, no tail
  panda: {
    mouthClosed: 'M 228,268 Q 250,280 272,268',
    mouthFrames: [
      ['M 228,268 Q 250,274 272,268',  0,    0],
      ['M 228,267 Q 250,265 272,267',  7,  276],
      ['M 228,267 Q 250,263 272,267', 11,  280],
      ['M 228,268 Q 250,271 272,268',  4,  274],
    ],
    svg: `
<!-- Panda body -->
<ellipse cx="250" cy="345" rx="98" ry="82" fill="white" stroke="#ddd" stroke-width="1" />
<!-- Panda belly -->
<ellipse cx="250" cy="360" rx="62" ry="52" fill="#f5f5f5" />
<!-- Panda ears (black patches on top of head) -->
<circle cx="175" cy="145" r="38" fill="#1a1a1a" />
<circle cx="325" cy="145" r="38" fill="#1a1a1a" />
<!-- Panda head -->
<circle cx="250" cy="215" r="105" fill="white" />
<!-- Black eye patches -->
<ellipse cx="210" cy="200" rx="35" ry="30" fill="#1a1a1a" transform="rotate(-12,210,200)" />
<ellipse cx="290" cy="200" rx="35" ry="30" fill="#1a1a1a" transform="rotate(12,290,200)" />
<!-- Eyes (white then pupil) -->
<circle cx="210" cy="198" r="16" fill="white" />
<circle cx="290" cy="198" r="16" fill="white" />
<circle cx="213" cy="196" r="10" fill="#1a1a1a" />
<circle cx="293" cy="196" r="10" fill="#1a1a1a" />
<circle cx="217" cy="192" r="4"  fill="white" />
<circle cx="297" cy="192" r="4"  fill="white" />
<!-- Eyelid for blink -->
<ellipse id="dino-eyelid" cx="250" cy="198" rx="55" ry="0" fill="white" />
<!-- Muzzle -->
<ellipse cx="250" cy="248" rx="50" ry="36" fill="#f5f5f5" stroke="#ddd" stroke-width="1" />
<!-- Nose -->
<ellipse cx="250" cy="238" rx="14" ry="9" fill="#1a1a1a" />
<ellipse cx="245" cy="234" rx="4"  ry="2.5" fill="white" opacity="0.5" />
<!-- Mouth -->
<path id="dino-mouth" d="M 228,268 Q 250,280 272,268" fill="none" stroke="#1a1a1a" stroke-width="3" stroke-linecap="round" />
<path d="M 250,247 L 250,266" fill="none" stroke="#1a1a1a" stroke-width="2.5" stroke-linecap="round" />
<!-- Mouth inner -->
<ellipse id="dino-mouth-inner" cx="250" cy="276" rx="15" ry="0" fill="#c0392b" style="display:none" />
<!-- Teeth (empty group - pandas have no visible front teeth in cartoon) -->
<g id="dino-teeth" style="display:none">
  <rect x="240" y="268" width="10" height="9" rx="2" fill="white" />
  <rect x="252" y="268" width="10" height="9" rx="2" fill="white" />
</g>
<!-- Black arms -->
<ellipse cx="158" cy="328" rx="28" ry="56" fill="#1a1a1a" transform="rotate(-12,158,328)" />
<ellipse cx="342" cy="328" rx="28" ry="56" fill="#1a1a1a" transform="rotate(12,342,328)" />
<!-- White paws -->
<ellipse cx="145" cy="378" rx="28" ry="16" fill="white" stroke="#ddd" stroke-width="1" />
<ellipse cx="355" cy="378" rx="28" ry="16" fill="white" stroke="#ddd" stroke-width="1" />
<!-- Black legs -->
<ellipse cx="198" cy="415" rx="28" ry="20" fill="#1a1a1a" />
<ellipse cx="302" cy="415" rx="28" ry="20" fill="#1a1a1a" />
<!-- Cheek blush -->
<ellipse cx="182" cy="248" rx="18" ry="11" fill="#FF69B4" opacity="0.28" />
<ellipse cx="318" cy="248" rx="18" ry="11" fill="#FF69B4" opacity="0.28" />
`
  },

  // ── Fox ────────────────────────────────────────────────────────────────────
  // Burnt orange, cream muzzle, pointy ears, bushy tail
  fox: {
    mouthClosed: 'M 230,264 Q 250,276 270,264',
    mouthFrames: [
      ['M 230,264 Q 250,270 270,264',  0,    0],
      ['M 230,263 Q 250,261 270,263',  6,  272],
      ['M 230,263 Q 250,259 270,263', 10,  276],
      ['M 230,264 Q 250,267 270,264',  4,  270],
    ],
    svg: `
<!-- Fox body -->
<ellipse cx="250" cy="342" rx="96" ry="80" fill="#D4570A" />
<!-- Fox belly (cream) -->
<ellipse cx="250" cy="358" rx="60" ry="52" fill="#F5DEB3" />
<!-- Fox head -->
<circle cx="250" cy="218" r="102" fill="#D4570A" />
<!-- Left ear (pointy) -->
<polygon points="158,152 178,80 214,155" fill="#D4570A" />
<polygon points="164,150 180,96 208,152" fill="#1a1a1a" />
<polygon points="168,148 182,104 206,150" fill="#FFB6C1" />
<!-- Right ear (pointy) -->
<polygon points="342,152 322,80 286,155" fill="#D4570A" />
<polygon points="336,150 320,96 292,152" fill="#1a1a1a" />
<polygon points="332,148 318,104 294,150" fill="#FFB6C1" />
<!-- Muzzle (cream) -->
<ellipse cx="250" cy="252" rx="58" ry="44" fill="#F5DEB3" />
<!-- Left eye -->
<ellipse cx="210" cy="198" rx="20" ry="19" fill="#1a1a1a" />
<circle  cx="217" cy="192" r="6"   fill="white" />
<!-- Right eye -->
<ellipse cx="290" cy="198" rx="20" ry="19" fill="#1a1a1a" />
<circle  cx="297" cy="192" r="6"   fill="white" />
<!-- Eyelid for blink -->
<ellipse id="dino-eyelid" cx="250" cy="198" rx="52" ry="0" fill="#D4570A" />
<!-- Nose -->
<ellipse cx="250" cy="238" rx="12" ry="8" fill="#1a1a1a" />
<!-- Mouth -->
<path id="dino-mouth" d="M 230,264 Q 250,276 270,264" fill="none" stroke="#5a2a00" stroke-width="3" stroke-linecap="round" />
<path d="M 250,246 L 250,262" fill="none" stroke="#5a2a00" stroke-width="2.5" stroke-linecap="round" />
<!-- Mouth inner -->
<ellipse id="dino-mouth-inner" cx="250" cy="272" rx="14" ry="0" fill="#c0392b" style="display:none" />
<!-- Teeth -->
<g id="dino-teeth" style="display:none">
  <rect x="239" y="264" width="10" height="8" rx="2" fill="white" />
  <rect x="252" y="264" width="10" height="8" rx="2" fill="white" />
</g>
<!-- Left arm -->
<ellipse cx="160" cy="326" rx="24" ry="52" fill="#D4570A" transform="rotate(-16,160,326)" />
<!-- Right arm -->
<ellipse cx="340" cy="326" rx="24" ry="52" fill="#D4570A" transform="rotate(16,340,326)" />
<!-- Left paw -->
<ellipse cx="148" cy="372" rx="25" ry="14" fill="#D4570A" />
<!-- Right paw -->
<ellipse cx="352" cy="372" rx="25" ry="14" fill="#D4570A" />
<!-- Left leg -->
<ellipse cx="200" cy="414" rx="24" ry="18" fill="#D4570A" />
<!-- Right leg -->
<ellipse cx="300" cy="414" rx="24" ry="18" fill="#D4570A" />
<!-- Bushy tail -->
<ellipse cx="385" cy="350" rx="45" ry="70" fill="#D4570A" transform="rotate(-25,385,350)" />
<ellipse cx="392" cy="360" rx="20" ry="35" fill="#F5DEB3" transform="rotate(-25,392,360)" />
<!-- Cheek blush -->
<ellipse cx="184" cy="248" rx="17" ry="10" fill="#FF69B4" opacity="0.3" />
<ellipse cx="316" cy="248" rx="17" ry="10" fill="#FF69B4" opacity="0.3" />
`
  },

  // ── Rabbit ─────────────────────────────────────────────────────────────────
  // Pale pink-white, very tall ears, pink pupil, buck teeth, fluffy tail
  rabbit: {
    mouthClosed: 'M 232,268 Q 250,278 268,268',
    mouthFrames: [
      ['M 232,268 Q 250,273 268,268',  0,    0],
      ['M 232,267 Q 250,265 268,267',  6,  276],
      ['M 232,267 Q 250,263 268,267', 10,  280],
      ['M 232,268 Q 250,271 268,268',  4,  274],
    ],
    svg: `
<!-- Rabbit body -->
<ellipse cx="250" cy="348" rx="94" ry="80" fill="#F8EEF0" />
<!-- Rabbit belly -->
<ellipse cx="250" cy="362" rx="58" ry="50" fill="white" />
<!-- Rabbit ears (very tall) -->
<ellipse cx="205" cy="110" rx="28" ry="88" fill="#F8EEF0" />
<ellipse cx="205" cy="110" rx="17" ry="75" fill="#FFB6C1" />
<ellipse cx="295" cy="110" rx="28" ry="88" fill="#F8EEF0" />
<ellipse cx="295" cy="110" rx="17" ry="75" fill="#FFB6C1" />
<!-- Rabbit head -->
<circle cx="250" cy="218" r="100" fill="#F8EEF0" />
<!-- Left eye -->
<circle cx="214" cy="202" r="18" fill="#FF8FAB" />
<circle cx="214" cy="202" r="10" fill="#1a1a1a" />
<circle cx="219" cy="196" r="4"  fill="white" />
<!-- Right eye -->
<circle cx="286" cy="202" r="18" fill="#FF8FAB" />
<circle cx="286" cy="202" r="10" fill="#1a1a1a" />
<circle cx="291" cy="196" r="4"  fill="white" />
<!-- Eyelid for blink -->
<ellipse id="dino-eyelid" cx="250" cy="202" rx="50" ry="0" fill="#F8EEF0" />
<!-- Muzzle -->
<ellipse cx="250" cy="248" rx="46" ry="34" fill="white" />
<!-- Nose (small pink) -->
<ellipse cx="250" cy="238" rx="10" ry="7" fill="#FF69B4" />
<!-- Mouth (W-shape for rabbit) -->
<path id="dino-mouth" d="M 232,268 Q 250,278 268,268" fill="none" stroke="#c0608a" stroke-width="3" stroke-linecap="round" />
<path d="M 250,245 L 250,265" fill="none" stroke="#c0608a" stroke-width="2.5" stroke-linecap="round" />
<!-- Mouth inner -->
<ellipse id="dino-mouth-inner" cx="250" cy="275" rx="13" ry="0" fill="#c0392b" style="display:none" />
<!-- Buck teeth -->
<g id="dino-teeth">
  <rect x="238" y="265" width="10" height="13" rx="3" fill="white" stroke="#ddd" stroke-width="1" />
  <rect x="251" y="265" width="10" height="13" rx="3" fill="white" stroke="#ddd" stroke-width="1" />
</g>
<!-- Left arm -->
<ellipse cx="162" cy="330" rx="22" ry="50" fill="#F8EEF0" transform="rotate(-18,162,330)" />
<!-- Right arm -->
<ellipse cx="338" cy="330" rx="22" ry="50" fill="#F8EEF0" transform="rotate(18,338,330)" />
<!-- Left paw -->
<ellipse cx="150" cy="374" rx="24" ry="14" fill="#F8EEF0" />
<!-- Right paw -->
<ellipse cx="350" cy="374" rx="24" ry="14" fill="#F8EEF0" />
<!-- Left leg (big rabbit feet) -->
<ellipse cx="196" cy="420" rx="32" ry="18" fill="#F8EEF0" />
<!-- Right leg -->
<ellipse cx="304" cy="420" rx="32" ry="18" fill="#F8EEF0" />
<!-- Fluffy tail (back) -->
<circle cx="350" cy="370" r="28" fill="white" />
<circle cx="350" cy="370" r="20" fill="#F8EEF0" />
<!-- Cheek blush -->
<ellipse cx="184" cy="242" rx="18" ry="11" fill="#FF69B4" opacity="0.32" />
<ellipse cx="316" cy="242" rx="18" ry="11" fill="#FF69B4" opacity="0.32" />
`
  },

};
