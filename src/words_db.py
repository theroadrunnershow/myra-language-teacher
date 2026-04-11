import random
import re

# Word database with English words + Telugu and Assamese translations
# tel_roman / asm_roman = approximate romanized pronunciation guide
WORD_DATABASE = {
    "animals": [
        {"english": "cat",      "telugu": "పిల్లి",      "assamese": "মেকুৰী",    "emoji": "🐱", "tel_roman": "pilli",      "asm_roman": "mekuri"},
        {"english": "dog",      "telugu": "కుక్క",      "assamese": "কুকুৰ",     "emoji": "🐶", "tel_roman": "kukka",      "asm_roman": "kukur"},
        {"english": "elephant", "telugu": "ఏనుగు",      "assamese": "হাতী",      "emoji": "🐘", "tel_roman": "enugu",      "asm_roman": "hati"},
        {"english": "lion",     "telugu": "సింహం",      "assamese": "সিংহ",      "emoji": "🦁", "tel_roman": "sinham",     "asm_roman": "singha"},
        {"english": "fish",     "telugu": "చేప",        "assamese": "মাছ",       "emoji": "🐟", "tel_roman": "chepa",      "asm_roman": "maas"},
        {"english": "bird",     "telugu": "పక్షి",      "assamese": "চৰাই",      "emoji": "🐦", "tel_roman": "pakshi",     "asm_roman": "shorai"},
        {"english": "cow",      "telugu": "ఆవు",        "assamese": "গৰু",       "emoji": "🐄", "tel_roman": "aavu",       "asm_roman": "guru"},
        {"english": "monkey",   "telugu": "కోతి",       "assamese": "বান্দৰ",    "emoji": "🐒", "tel_roman": "koti",       "asm_roman": "bandor"},
        {"english": "tiger",    "telugu": "పులి",       "assamese": "বাঘ",       "emoji": "🐯", "tel_roman": "puli",       "asm_roman": "bagh"},
        {"english": "rabbit",   "telugu": "కుందేలు",    "assamese": "শহাপহু",   "emoji": "🐰", "tel_roman": "kundelu",    "asm_roman": "shohapahu"},
        {"english": "duck",     "telugu": "బాతు",       "assamese": "হাঁহ",      "emoji": "🦆", "tel_roman": "baatu",      "asm_roman": "haah"},
        {"english": "frog",      "telugu": "కప్ప",           "assamese": "ভেকুলী",   "emoji": "🐸", "tel_roman": "kappa",           "asm_roman": "bhekuli"},
        {"english": "horse",     "telugu": "గుర్రం",         "assamese": "ঘোঁৰা",    "emoji": "🐴", "tel_roman": "gurram",          "asm_roman": "ghora"},
        {"english": "sheep",     "telugu": "గొర్రె",         "assamese": "ভেড়া",     "emoji": "🐑", "tel_roman": "gorre",           "asm_roman": "bhera"},
        {"english": "hen",       "telugu": "కోడి",           "assamese": "কুকুৰা",   "emoji": "🐔", "tel_roman": "kodi",            "asm_roman": "kukura"},
        {"english": "butterfly", "telugu": "సీతాకోకచిలుక",  "assamese": "পখিলা",    "emoji": "🦋", "tel_roman": "sitakokachiluka", "asm_roman": "pokhila"},
        {"english": "snake",     "telugu": "పాము",           "assamese": "সাপ",       "emoji": "🐍", "tel_roman": "paamu",           "asm_roman": "shaap"},
        {"english": "bear",      "telugu": "భల్లూకం",       "assamese": "ভালুক",    "emoji": "🐻", "tel_roman": "bhallukam",       "asm_roman": "bhaluk"},
    ],
    "colors": [
        {"english": "red",    "telugu": "ఎరుపు",   "assamese": "ৰঙা",        "emoji": "🔴", "tel_roman": "erupu",    "asm_roman": "ronga"},
        {"english": "blue",   "telugu": "నీలం",    "assamese": "নীলা",       "emoji": "🔵", "tel_roman": "neelam",   "asm_roman": "nila"},
        {"english": "green",  "telugu": "పచ్చ",    "assamese": "সেউজীয়া",   "emoji": "💚", "tel_roman": "pacha",    "asm_roman": "seujia"},
        {"english": "yellow", "telugu": "పసుపు",   "assamese": "হালধীয়া",   "emoji": "💛", "tel_roman": "pasupu",   "asm_roman": "haldhia"},
        {"english": "pink",   "telugu": "గులాబీ",  "assamese": "গোলাপী",     "emoji": "🌸", "tel_roman": "gulabi",   "asm_roman": "golapi"},
        {"english": "white",  "telugu": "తెలుపు",  "assamese": "বগা",        "emoji": "⬜", "tel_roman": "telupu",   "asm_roman": "boga"},
        {"english": "black",  "telugu": "నలుపు",   "assamese": "ক'লা",       "emoji": "⬛", "tel_roman": "nalupu",   "asm_roman": "kola"},
        {"english": "orange", "telugu": "నారింజ",  "assamese": "কমলা",       "emoji": "🟠", "tel_roman": "narinja",  "asm_roman": "komola"},
        {"english": "purple", "telugu": "ఊదా",     "assamese": "বেঙুনীয়া", "emoji": "💜", "tel_roman": "uuda",     "asm_roman": "bengonia"},
        {"english": "brown",  "telugu": "గోధుమ",   "assamese": "মটিয়া",     "emoji": "🟤", "tel_roman": "godhuma",  "asm_roman": "motia"},
    ],
    "body_parts": [
        {"english": "eye",    "telugu": "కన్ను",  "assamese": "চকু",  "emoji": "👁️", "tel_roman": "kannu",   "asm_roman": "shoku"},
        {"english": "nose",   "telugu": "ముక్కు", "assamese": "নাক",  "emoji": "👃", "tel_roman": "mukku",   "asm_roman": "naak"},
        {"english": "hand",   "telugu": "చేయి",   "assamese": "হাত",  "emoji": "✋", "tel_roman": "cheyi",   "asm_roman": "haat"},
        {"english": "leg",    "telugu": "కాలు",   "assamese": "ভৰি",  "emoji": "🦵", "tel_roman": "kaalu",   "asm_roman": "bhori"},
        {"english": "ear",    "telugu": "చెవి",   "assamese": "কাণ",  "emoji": "👂", "tel_roman": "chevi",   "asm_roman": "kaan"},
        {"english": "mouth",  "telugu": "నోరు",   "assamese": "মুখ",  "emoji": "👄", "tel_roman": "noru",    "asm_roman": "mukh"},
        {"english": "hair",   "telugu": "జుట్టు", "assamese": "চুলি", "emoji": "💇", "tel_roman": "juttu",   "asm_roman": "shuli"},
        {"english": "teeth",    "telugu": "పళ్ళు",  "assamese": "দাঁত",   "emoji": "🦷", "tel_roman": "pallu",   "asm_roman": "daat"},
        {"english": "head",     "telugu": "తల",     "assamese": "মূৰ",    "emoji": "🧠", "tel_roman": "tala",    "asm_roman": "mur"},
        {"english": "foot",     "telugu": "పాదం",   "assamese": "পা",     "emoji": "🦶", "tel_roman": "paadam",  "asm_roman": "paa"},
        {"english": "finger",   "telugu": "వేలు",   "assamese": "আঙুলি",  "emoji": "☝️", "tel_roman": "velu",    "asm_roman": "anguli"},
        {"english": "shoulder", "telugu": "భుజం",   "assamese": "কান্ধ",  "emoji": "💪", "tel_roman": "bhujam",  "asm_roman": "kaandh"},
    ],
    "numbers": [
        {"english": "one",   "telugu": "ఒకటి",  "assamese": "এক",   "emoji": "1️⃣", "tel_roman": "okati",  "asm_roman": "ek"},
        {"english": "two",   "telugu": "రెండు", "assamese": "দুই",  "emoji": "2️⃣", "tel_roman": "rendu",  "asm_roman": "dui"},
        {"english": "three", "telugu": "మూడు",  "assamese": "তিনি", "emoji": "3️⃣", "tel_roman": "mudu",   "asm_roman": "tini"},
        {"english": "four",  "telugu": "నాలుగు","assamese": "চাৰি", "emoji": "4️⃣", "tel_roman": "nalugu", "asm_roman": "shari"},
        {"english": "five",  "telugu": "అయిదు", "assamese": "পাঁচ", "emoji": "5️⃣", "tel_roman": "ayidu",  "asm_roman": "paansh"},
        {"english": "six",   "telugu": "ఆరు",   "assamese": "ছয়",  "emoji": "6️⃣", "tel_roman": "aaru",   "asm_roman": "shoy"},
        {"english": "seven", "telugu": "ఏడు",   "assamese": "সাত",  "emoji": "7️⃣", "tel_roman": "edu",    "asm_roman": "saat"},
        {"english": "eight", "telugu": "ఎనిమిది","assamese": "আঠ",  "emoji": "8️⃣", "tel_roman": "enimidi","asm_roman": "aath"},
        {"english": "nine",  "telugu": "తొమ్మిది","assamese": "ন",  "emoji": "9️⃣", "tel_roman": "tommidi","asm_roman": "no"},
        {"english": "ten",   "telugu": "పది",    "assamese": "দহ",   "emoji": "🔟", "tel_roman": "padi",   "asm_roman": "doh"},
    ],
    "food": [
        {"english": "apple",  "telugu": "ఆపిల్",      "assamese": "আপেল",   "emoji": "🍎", "tel_roman": "aapil",       "asm_roman": "aapel"},
        {"english": "banana", "telugu": "అరటిపండు",   "assamese": "কল",     "emoji": "🍌", "tel_roman": "aratipandu",  "asm_roman": "kol"},
        {"english": "milk",   "telugu": "పాలు",        "assamese": "গাখীৰ",  "emoji": "🥛", "tel_roman": "paalu",       "asm_roman": "gakheer"},
        {"english": "rice",   "telugu": "అన్నం",      "assamese": "ভাত",    "emoji": "🍚", "tel_roman": "annam",       "asm_roman": "bhat"},
        {"english": "water",  "telugu": "నీళ్ళు",     "assamese": "পানী",   "emoji": "💧", "tel_roman": "neellu",      "asm_roman": "paani"},
        {"english": "mango",  "telugu": "మామిడి",      "assamese": "আম",     "emoji": "🥭", "tel_roman": "maamidi",     "asm_roman": "aam"},
        {"english": "egg",    "telugu": "గుడ్డు",      "assamese": "কণী",   "emoji": "🥚", "tel_roman": "guddu",       "asm_roman": "koni"},
        {"english": "bread",   "telugu": "రొట్టె",      "assamese": "পাউৰুটি", "emoji": "🍞", "tel_roman": "rotte",       "asm_roman": "pauruti"},
        {"english": "grapes",  "telugu": "ద్రాక్ష",    "assamese": "আঙুৰ",    "emoji": "🍇", "tel_roman": "draaksha",    "asm_roman": "angur"},
        {"english": "potato",  "telugu": "బంగాళాదుంప","assamese": "আলু",      "emoji": "🥔", "tel_roman": "bangaladumpa","asm_roman": "aalu"},
        {"english": "tomato",  "telugu": "టమాటా",      "assamese": "টমেটো",   "emoji": "🍅", "tel_roman": "tamaata",     "asm_roman": "tometo"},
        {"english": "sweet",   "telugu": "మిఠాయి",     "assamese": "মিঠাই",   "emoji": "🍬", "tel_roman": "mithayi",     "asm_roman": "mithai"},
    ],
    "common_objects": [
        {"english": "ball",   "telugu": "బంతి",     "assamese": "বল",     "emoji": "⚽", "tel_roman": "banti",    "asm_roman": "bol"},
        {"english": "house",  "telugu": "ఇల్లు",    "assamese": "ঘৰ",    "emoji": "🏠", "tel_roman": "illu",     "asm_roman": "ghar"},
        {"english": "book",   "telugu": "పుస్తకం",  "assamese": "কিতাপ", "emoji": "📚", "tel_roman": "pustakam", "asm_roman": "kitaap"},
        {"english": "tree",   "telugu": "చెట్టు",   "assamese": "গছ",    "emoji": "🌳", "tel_roman": "chettu",   "asm_roman": "gash"},
        {"english": "sun",    "telugu": "సూర్యుడు", "assamese": "সূৰ্য",  "emoji": "☀️", "tel_roman": "suryudu",  "asm_roman": "surya"},
        {"english": "moon",   "telugu": "చంద్రుడు", "assamese": "চন্দ্ৰ", "emoji": "🌙", "tel_roman": "chandrudu","asm_roman": "shandra"},
        {"english": "star",   "telugu": "నక్షత్రం", "assamese": "তৰা",   "emoji": "⭐", "tel_roman": "nakshatram","asm_roman": "tora"},
        {"english": "flower", "telugu": "పువ్వు",   "assamese": "ফুল",   "emoji": "🌸", "tel_roman": "puvvu",    "asm_roman": "phul"},
        {"english": "car",    "telugu": "కారు",     "assamese": "গাড়ী", "emoji": "🚗", "tel_roman": "kaaru",    "asm_roman": "gaari"},
        {"english": "boat",     "telugu": "పడవ",      "assamese": "নাও",    "emoji": "⛵", "tel_roman": "padava",   "asm_roman": "naao"},
        {"english": "chair",    "telugu": "కుర్చీ",   "assamese": "চকী",   "emoji": "🪑", "tel_roman": "kurchi",   "asm_roman": "shoki"},
        {"english": "table",    "telugu": "బల్ల",     "assamese": "টেবুল", "emoji": "🍽️", "tel_roman": "balla",    "asm_roman": "tebul"},
        {"english": "clock",    "telugu": "గడియారం",  "assamese": "ঘড়ী",  "emoji": "🕐", "tel_roman": "gadiyaram","asm_roman": "ghori"},
        {"english": "pen",      "telugu": "కలం",      "assamese": "কলম",   "emoji": "✏️", "tel_roman": "kalam",    "asm_roman": "kolom"},
        {"english": "door",     "telugu": "తలుపు",    "assamese": "দুৱাৰ", "emoji": "🚪", "tel_roman": "talupu",   "asm_roman": "duar"},
    ],
    "verbs": [
        {"english": "eat",   "telugu": "తినడం",        "assamese": "খোৱা",        "emoji": "😋", "tel_roman": "tinadam",        "asm_roman": "khoa"},
        {"english": "drink", "telugu": "తాగడం",        "assamese": "পিয়া",        "emoji": "🥤", "tel_roman": "taagadam",       "asm_roman": "pia"},
        {"english": "sleep", "telugu": "నిద్రపోవడం",  "assamese": "শোৱা",        "emoji": "😴", "tel_roman": "nidra povadam",  "asm_roman": "shua"},
        {"english": "walk",  "telugu": "నడవడం",        "assamese": "খোজ কঢ়া",   "emoji": "🚶", "tel_roman": "nadavadam",      "asm_roman": "khoj kara"},
        {"english": "run",   "telugu": "పరుగెత్తడం",  "assamese": "দৌৰা",        "emoji": "🏃", "tel_roman": "parugettadam",   "asm_roman": "daura"},
        {"english": "sit",   "telugu": "కూర్చోవడం",   "assamese": "বহা",          "emoji": "🧘", "tel_roman": "kurchovadam",    "asm_roman": "boha"},
        {"english": "play",  "telugu": "ఆడటం",         "assamese": "খেলা",        "emoji": "⚽", "tel_roman": "aadatam",        "asm_roman": "khela"},
        {"english": "jump",  "telugu": "దూకడం",        "assamese": "জাঁপ দিয়া", "emoji": "🦘", "tel_roman": "dookadam",       "asm_roman": "jaap diya"},
        {"english": "read",  "telugu": "చదవడం",        "assamese": "পঢ়া",         "emoji": "📖", "tel_roman": "chadavadam",     "asm_roman": "pora"},
        {"english": "write", "telugu": "రాయడం",        "assamese": "লিখা",        "emoji": "📝", "tel_roman": "rayadam",        "asm_roman": "likha"},
        {"english": "come",  "telugu": "రావడం",        "assamese": "অহা",          "emoji": "👋", "tel_roman": "raavadam",       "asm_roman": "oha"},
        {"english": "go",    "telugu": "వెళ్ళడం",     "assamese": "যোৱা",        "emoji": "🏁", "tel_roman": "velladam",       "asm_roman": "jua"},
    ],
    "phrases": [],
}


_PHRASE_COLORS = (
    {"english": "red", "telugu": "ఎర్ర", "assamese": "ৰঙা", "tel_roman": "erra", "asm_roman": "ronga"},
    {"english": "blue", "telugu": "నీలి", "assamese": "নীলা", "tel_roman": "nili", "asm_roman": "nila"},
    {"english": "green", "telugu": "ఆకుపచ్చ", "assamese": "সেউজীয়া", "tel_roman": "akupaccha", "asm_roman": "seujia"},
    {"english": "yellow", "telugu": "పసుపు", "assamese": "হালধীয়া", "tel_roman": "pasupu", "asm_roman": "haldhia"},
    {"english": "pink", "telugu": "గులాబీ రంగు", "assamese": "গোলাপী", "tel_roman": "gulabi rangu", "asm_roman": "golapi"},
    {"english": "white", "telugu": "తెల్ల", "assamese": "বগা", "tel_roman": "tella", "asm_roman": "boga"},
    {"english": "black", "telugu": "నల్ల", "assamese": "ক'লা", "tel_roman": "nalla", "asm_roman": "kola"},
    {"english": "orange", "telugu": "నారింజ రంగు", "assamese": "কমলা", "tel_roman": "narinja rangu", "asm_roman": "komola"},
    {"english": "purple", "telugu": "ఊదా రంగు", "assamese": "বেঙুনীয়া", "tel_roman": "uda rangu", "asm_roman": "bengunia"},
    {"english": "brown", "telugu": "గోధుమ రంగు", "assamese": "বাদামী", "tel_roman": "godhuma rangu", "asm_roman": "badami"},
)

_PHRASE_OBJECTS = (
    {"english": "ball", "telugu": "బంతి", "telugu_obj": "బంతిని", "assamese": "বল", "tel_roman": "banti", "tel_obj_roman": "bantini", "asm_roman": "bol", "emoji": "⚽"},
    {"english": "car", "telugu": "కారు", "telugu_obj": "కారును", "assamese": "গাড়ী", "tel_roman": "kaaru", "tel_obj_roman": "kaarunu", "asm_roman": "gaari", "emoji": "🚗"},
    {"english": "book", "telugu": "పుస్తకం", "telugu_obj": "పుస్తకాన్ని", "assamese": "কিতাপ", "tel_roman": "pustakam", "tel_obj_roman": "pustakanni", "asm_roman": "kitaap", "emoji": "📚"},
    {"english": "cup", "telugu": "కప్పు", "telugu_obj": "కప్పును", "assamese": "কাপ", "tel_roman": "kappu", "tel_obj_roman": "kappunu", "asm_roman": "kap", "emoji": "☕"},
    {"english": "hat", "telugu": "టోపీ", "telugu_obj": "టోపీని", "assamese": "টুপী", "tel_roman": "topi", "tel_obj_roman": "topini", "asm_roman": "tupi", "emoji": "🎩"},
    {"english": "shoe", "telugu": "బూటు", "telugu_obj": "బూటును", "assamese": "জোতা", "tel_roman": "butu", "tel_obj_roman": "butunu", "asm_roman": "jota", "emoji": "👟"},
    {"english": "sock", "telugu": "సాక్స్", "telugu_obj": "సాక్సును", "assamese": "মোজা", "tel_roman": "saks", "tel_obj_roman": "saksunu", "asm_roman": "moja", "emoji": "🧦"},
    {"english": "kite", "telugu": "గాలిపటం", "telugu_obj": "గాలిపటాన్ని", "assamese": "ঘুৰি", "tel_roman": "gaalipatam", "tel_obj_roman": "gaalipatanni", "asm_roman": "ghuri", "emoji": "🪁"},
    {"english": "boat", "telugu": "పడవ", "telugu_obj": "పడవను", "assamese": "নাও", "tel_roman": "padava", "tel_obj_roman": "padavanu", "asm_roman": "nao", "emoji": "⛵"},
    {"english": "drum", "telugu": "డ్రమ్", "telugu_obj": "డ్రమ్‌ను", "assamese": "ঢোল", "tel_roman": "dram", "tel_obj_roman": "dramnu", "asm_roman": "dhol", "emoji": "🥁"},
)

_PHRASE_ANIMALS = (
    {"english": "cat", "telugu": "పిల్లి", "assamese": "মেকুৰী", "tel_roman": "pilli", "asm_roman": "mekuri", "emoji": "🐱"},
    {"english": "dog", "telugu": "కుక్క", "assamese": "কুকুৰ", "tel_roman": "kukka", "asm_roman": "kukur", "emoji": "🐶"},
    {"english": "bird", "telugu": "పక్షి", "assamese": "চৰাই", "tel_roman": "pakshi", "asm_roman": "shorai", "emoji": "🐦"},
    {"english": "rabbit", "telugu": "కుందేలు", "assamese": "শহাপহু", "tel_roman": "kundelu", "asm_roman": "shohapahu", "emoji": "🐰"},
    {"english": "monkey", "telugu": "కోతి", "assamese": "বান্দৰ", "tel_roman": "koti", "asm_roman": "bandor", "emoji": "🐒"},
    {"english": "duck", "telugu": "బాతు", "assamese": "হাঁহ", "tel_roman": "baatu", "asm_roman": "haah", "emoji": "🦆"},
    {"english": "horse", "telugu": "గుర్రం", "assamese": "ঘোঁৰা", "tel_roman": "gurram", "asm_roman": "ghora", "emoji": "🐴"},
    {"english": "cow", "telugu": "ఆవు", "assamese": "গৰু", "tel_roman": "aavu", "asm_roman": "guru", "emoji": "🐄"},
    {"english": "sheep", "telugu": "గొర్రె", "assamese": "ভেড়া", "tel_roman": "gorre", "asm_roman": "bhera", "emoji": "🐑"},
    {"english": "tiger", "telugu": "పులి", "assamese": "বাঘ", "tel_roman": "puli", "asm_roman": "bagh", "emoji": "🐯"},
)

_PHRASE_ANIMAL_ACTIONS = (
    {"english": "walking", "telugu": "నడుస్తోంది", "assamese": "খোজ কৰি আছে", "tel_roman": "nadustondi", "asm_roman": "khoj kori ase"},
    {"english": "sleeping", "telugu": "నిద్రపోతోంది", "assamese": "শুই আছে", "tel_roman": "nidrapotondi", "asm_roman": "xui ase"},
    {"english": "eating", "telugu": "తింటోంది", "assamese": "খাই আছে", "tel_roman": "tintondi", "asm_roman": "khai ase"},
    {"english": "playing", "telugu": "ఆడుతోంది", "assamese": "খেলি আছে", "tel_roman": "adutondi", "asm_roman": "kheli ase"},
)

_PHRASE_FOODS = (
    {
        "english_like": "apples",
        "english_want": "an apple",
        "english_give": "an apple",
        "tel_like": "నాకు ఆపిల్ ఇష్టం",
        "tel_want": "నాకు ఒక ఆపిల్ కావాలి",
        "tel_give": "దయచేసి నాకు ఒక ఆపిల్ ఇవ్వండి",
        "asm_like": "মোৰ আপেল ভাল লাগে",
        "asm_want": "মোক এটা আপেল লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক এটা আপেল দিয়ক",
        "tel_like_roman": "naku aapil ishtam",
        "tel_want_roman": "naku oka aapil kavali",
        "tel_give_roman": "dayachesi naku oka aapil ivvandi",
        "asm_like_roman": "mor aapel bhal lage",
        "asm_want_roman": "mok eta aapel lage",
        "asm_give_roman": "anugroho kori mok eta aapel diyok",
        "emoji": "🍎",
    },
    {
        "english_like": "bananas",
        "english_want": "a banana",
        "english_give": "a banana",
        "tel_like": "నాకు అరటిపండు ఇష్టం",
        "tel_want": "నాకు ఒక అరటిపండు కావాలి",
        "tel_give": "దయచేసి నాకు ఒక అరటిపండు ఇవ్వండి",
        "asm_like": "মোৰ কল ভাল লাগে",
        "asm_want": "মোক এটা কল লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক এটা কল দিয়ক",
        "tel_like_roman": "naku aratipandu ishtam",
        "tel_want_roman": "naku oka aratipandu kavali",
        "tel_give_roman": "dayachesi naku oka aratipandu ivvandi",
        "asm_like_roman": "mor kol bhal lage",
        "asm_want_roman": "mok eta kol lage",
        "asm_give_roman": "anugroho kori mok eta kol diyok",
        "emoji": "🍌",
    },
    {
        "english_like": "milk",
        "english_want": "milk",
        "english_give": "milk",
        "tel_like": "నాకు పాలు ఇష్టం",
        "tel_want": "నాకు పాలు కావాలి",
        "tel_give": "దయచేసి నాకు పాలు ఇవ్వండి",
        "asm_like": "মোৰ গাখীৰ ভাল লাগে",
        "asm_want": "মোক গাখীৰ লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক গাখীৰ দিয়ক",
        "tel_like_roman": "naku paalu ishtam",
        "tel_want_roman": "naku paalu kavali",
        "tel_give_roman": "dayachesi naku paalu ivvandi",
        "asm_like_roman": "mor gakheer bhal lage",
        "asm_want_roman": "mok gakheer lage",
        "asm_give_roman": "anugroho kori mok gakheer diyok",
        "emoji": "🥛",
    },
    {
        "english_like": "rice",
        "english_want": "rice",
        "english_give": "rice",
        "tel_like": "నాకు అన్నం ఇష్టం",
        "tel_want": "నాకు అన్నం కావాలి",
        "tel_give": "దయచేసి నాకు అన్నం ఇవ్వండి",
        "asm_like": "মোৰ ভাত ভাল লাগে",
        "asm_want": "মোক ভাত লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক ভাত দিয়ক",
        "tel_like_roman": "naku annam ishtam",
        "tel_want_roman": "naku annam kavali",
        "tel_give_roman": "dayachesi naku annam ivvandi",
        "asm_like_roman": "mor bhat bhal lage",
        "asm_want_roman": "mok bhat lage",
        "asm_give_roman": "anugroho kori mok bhat diyok",
        "emoji": "🍚",
    },
    {
        "english_like": "mangoes",
        "english_want": "a mango",
        "english_give": "a mango",
        "tel_like": "నాకు మామిడి ఇష్టం",
        "tel_want": "నాకు ఒక మామిడి కావాలి",
        "tel_give": "దయచేసి నాకు ఒక మామిడి ఇవ్వండి",
        "asm_like": "মোৰ আম ভাল লাগে",
        "asm_want": "মোক এটা আম লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক এটা আম দিয়ক",
        "tel_like_roman": "naku maamidi ishtam",
        "tel_want_roman": "naku oka maamidi kavali",
        "tel_give_roman": "dayachesi naku oka maamidi ivvandi",
        "asm_like_roman": "mor aam bhal lage",
        "asm_want_roman": "mok eta aam lage",
        "asm_give_roman": "anugroho kori mok eta aam diyok",
        "emoji": "🥭",
    },
    {
        "english_like": "bread",
        "english_want": "bread",
        "english_give": "bread",
        "tel_like": "నాకు రొట్టె ఇష్టం",
        "tel_want": "నాకు రొట్టె కావాలి",
        "tel_give": "దయచేసి నాకు రొట్టె ఇవ్వండి",
        "asm_like": "মোৰ পাউৰুটি ভাল লাগে",
        "asm_want": "মোক পাউৰুটি লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক পাউৰুটি দিয়ক",
        "tel_like_roman": "naku rotte ishtam",
        "tel_want_roman": "naku rotte kavali",
        "tel_give_roman": "dayachesi naku rotte ivvandi",
        "asm_like_roman": "mor pauruti bhal lage",
        "asm_want_roman": "mok pauruti lage",
        "asm_give_roman": "anugroho kori mok pauruti diyok",
        "emoji": "🍞",
    },
    {
        "english_like": "grapes",
        "english_want": "grapes",
        "english_give": "grapes",
        "tel_like": "నాకు ద్రాక్ష ఇష్టం",
        "tel_want": "నాకు ద్రాక్ష కావాలి",
        "tel_give": "దయచేసి నాకు ద్రాక్ష ఇవ్వండి",
        "asm_like": "মোৰ আঙুৰ ভাল লাগে",
        "asm_want": "মোক আঙুৰ লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক আঙুৰ দিয়ক",
        "tel_like_roman": "naku draaksha ishtam",
        "tel_want_roman": "naku draaksha kavali",
        "tel_give_roman": "dayachesi naku draaksha ivvandi",
        "asm_like_roman": "mor angur bhal lage",
        "asm_want_roman": "mok angur lage",
        "asm_give_roman": "anugroho kori mok angur diyok",
        "emoji": "🍇",
    },
    {
        "english_like": "potatoes",
        "english_want": "a potato",
        "english_give": "a potato",
        "tel_like": "నాకు బంగాళాదుంప ఇష్టం",
        "tel_want": "నాకు ఒక బంగాళాదుంప కావాలి",
        "tel_give": "దయచేసి నాకు ఒక బంగాళాదుంప ఇవ్వండి",
        "asm_like": "মোৰ আলু ভাল লাগে",
        "asm_want": "মোক এটা আলু লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক এটা আলু দিয়ক",
        "tel_like_roman": "naku bangaladumpa ishtam",
        "tel_want_roman": "naku oka bangaladumpa kavali",
        "tel_give_roman": "dayachesi naku oka bangaladumpa ivvandi",
        "asm_like_roman": "mor aalu bhal lage",
        "asm_want_roman": "mok eta aalu lage",
        "asm_give_roman": "anugroho kori mok eta aalu diyok",
        "emoji": "🥔",
    },
    {
        "english_like": "tomatoes",
        "english_want": "a tomato",
        "english_give": "a tomato",
        "tel_like": "నాకు టమాటా ఇష్టం",
        "tel_want": "నాకు ఒక టమాటా కావాలి",
        "tel_give": "దయచేసి నాకు ఒక టమాటా ఇవ్వండి",
        "asm_like": "মোৰ টমেটো ভাল লাগে",
        "asm_want": "মোক এটা টমেটো লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক এটা টমেটো দিয়ক",
        "tel_like_roman": "naku tamaata ishtam",
        "tel_want_roman": "naku oka tamaata kavali",
        "tel_give_roman": "dayachesi naku oka tamaata ivvandi",
        "asm_like_roman": "mor tometo bhal lage",
        "asm_want_roman": "mok eta tometo lage",
        "asm_give_roman": "anugroho kori mok eta tometo diyok",
        "emoji": "🍅",
    },
    {
        "english_like": "water",
        "english_want": "water",
        "english_give": "water",
        "tel_like": "నాకు నీళ్లు ఇష్టం",
        "tel_want": "నాకు నీళ్లు కావాలి",
        "tel_give": "దయచేసి నాకు నీళ్లు ఇవ్వండి",
        "asm_like": "মোৰ পানী ভাল লাগে",
        "asm_want": "মোক পানী লাগে",
        "asm_give": "অনুগ্ৰহ কৰি মোক পানী দিয়ক",
        "tel_like_roman": "naku neellu ishtam",
        "tel_want_roman": "naku neellu kavali",
        "tel_give_roman": "dayachesi naku neellu ivvandi",
        "asm_like_roman": "mor paani bhal lage",
        "asm_want_roman": "mok paani lage",
        "asm_give_roman": "anugroho kori mok paani diyok",
        "emoji": "💧",
    },
)

_PHRASE_PEOPLE = (
    {
        "english": "mommy",
        "where_tel": "అమ్మ ఎక్కడ ఉంది?",
        "here_tel": "అమ్మ ఇక్కడ ఉంది",
        "where_asm": "মা ক'ত আছে?",
        "here_asm": "মা ইয়াত আছে",
        "where_tel_roman": "amma ekkada undi",
        "here_tel_roman": "amma ikkada undi",
        "where_asm_roman": "ma kot ase",
        "here_asm_roman": "ma iyat ase",
        "emoji": "👩",
    },
    {
        "english": "daddy",
        "where_tel": "నాన్న ఎక్కడ ఉన్నారు?",
        "here_tel": "నాన్న ఇక్కడ ఉన్నారు",
        "where_asm": "দেউতা ক'ত আছে?",
        "here_asm": "দেউতা ইয়াত আছে",
        "where_tel_roman": "nanna ekkada unnaru",
        "here_tel_roman": "nanna ikkada unnaru",
        "where_asm_roman": "deuta kot ase",
        "here_asm_roman": "deuta iyat ase",
        "emoji": "👨",
    },
    {
        "english": "grandma",
        "where_tel": "అమ్మమ్మ ఎక్కడ ఉన్నారు?",
        "here_tel": "అమ్మమ్మ ఇక్కడ ఉన్నారు",
        "where_asm": "আইতা ক'ত আছে?",
        "here_asm": "আইতা ইয়াত আছে",
        "where_tel_roman": "ammamma ekkada unnaru",
        "here_tel_roman": "ammamma ikkada unnaru",
        "where_asm_roman": "aita kot ase",
        "here_asm_roman": "aita iyat ase",
        "emoji": "👵",
    },
    {
        "english": "grandpa",
        "where_tel": "తాతయ్య ఎక్కడ ఉన్నారు?",
        "here_tel": "తాతయ్య ఇక్కడ ఉన్నారు",
        "where_asm": "দাদা ক'ত আছে?",
        "here_asm": "দাদা ইয়াত আছে",
        "where_tel_roman": "tatayya ekkada unnaru",
        "here_tel_roman": "tatayya ikkada unnaru",
        "where_asm_roman": "dada kot ase",
        "here_asm_roman": "dada iyat ase",
        "emoji": "👴",
    },
    {
        "english": "brother",
        "where_tel": "అన్న ఎక్కడ ఉన్నాడు?",
        "here_tel": "అన్న ఇక్కడ ఉన్నాడు",
        "where_asm": "ভাই ক'ত আছে?",
        "here_asm": "ভাই ইয়াত আছে",
        "where_tel_roman": "anna ekkada unnadu",
        "here_tel_roman": "anna ikkada unnadu",
        "where_asm_roman": "bhai kot ase",
        "here_asm_roman": "bhai iyat ase",
        "emoji": "👦",
    },
    {
        "english": "sister",
        "where_tel": "అక్క ఎక్కడ ఉంది?",
        "here_tel": "అక్క ఇక్కడ ఉంది",
        "where_asm": "ভনী ক'ত আছে?",
        "here_asm": "ভনী ইয়াত আছে",
        "where_tel_roman": "akka ekkada undi",
        "here_tel_roman": "akka ikkada undi",
        "where_asm_roman": "bhoni kot ase",
        "here_asm_roman": "bhoni iyat ase",
        "emoji": "👧",
    },
    {
        "english": "baby",
        "where_tel": "బిడ్డ ఎక్కడ ఉంది?",
        "here_tel": "బిడ్డ ఇక్కడ ఉంది",
        "where_asm": "কেঁচুৱা ক'ত আছে?",
        "here_asm": "কেঁচুৱা ইয়াত আছে",
        "where_tel_roman": "bidda ekkada undi",
        "here_tel_roman": "bidda ikkada undi",
        "where_asm_roman": "kensua kot ase",
        "here_asm_roman": "kensua iyat ase",
        "emoji": "👶",
    },
    {
        "english": "teacher",
        "where_tel": "ఉపాధ్యాయుడు ఎక్కడ ఉన్నారు?",
        "here_tel": "ఉపాధ్యాయుడు ఇక్కడ ఉన్నారు",
        "where_asm": "শিক্ষক ক'ত আছে?",
        "here_asm": "শিক্ষক ইয়াত আছে",
        "where_tel_roman": "upadhyayudu ekkada unnaru",
        "here_tel_roman": "upadhyayudu ikkada unnaru",
        "where_asm_roman": "shikkhok kot ase",
        "here_asm_roman": "shikkhok iyat ase",
        "emoji": "🧑‍🏫",
    },
    {
        "english": "friend",
        "where_tel": "స్నేహితుడు ఎక్కడ ఉన్నాడు?",
        "here_tel": "స్నేహితుడు ఇక్కడ ఉన్నాడు",
        "where_asm": "বন্ধু ক'ত আছে?",
        "here_asm": "বন্ধু ইয়াত আছে",
        "where_tel_roman": "snehitudu ekkada unnadu",
        "here_tel_roman": "snehitudu ikkada unnadu",
        "where_asm_roman": "bondhu kot ase",
        "here_asm_roman": "bondhu iyat ase",
        "emoji": "🧑",
    },
    {
        "english": "doctor",
        "where_tel": "డాక్టర్ ఎక్కడ ఉన్నారు?",
        "here_tel": "డాక్టర్ ఇక్కడ ఉన్నారు",
        "where_asm": "ডাক্তৰ ক'ত আছে?",
        "here_asm": "ডাক্তৰ ইয়াত আছে",
        "where_tel_roman": "daaktar ekkada unnaru",
        "here_tel_roman": "daaktar ikkada unnaru",
        "where_asm_roman": "daktor kot ase",
        "here_asm_roman": "daktor iyat ase",
        "emoji": "🩺",
    },
)

_PHRASE_BODY_PARTS = (
    {
        "english": "hands",
        "wash_tel": "మీ చేతులు కడుక్కోండి",
        "touch_tel": "మీ చేతులను తాకండి",
        "wash_asm": "আপোনাৰ হাত ধুৱক",
        "touch_asm": "আপোনাৰ হাত স্পৰ্শ কৰক",
        "wash_tel_roman": "mi chetulu kadukkondi",
        "touch_tel_roman": "mi chetulanu taakandi",
        "wash_asm_roman": "aponar haat dhuwak",
        "touch_asm_roman": "aponar haat sporxo korok",
        "emoji": "✋",
    },
    {
        "english": "feet",
        "wash_tel": "మీ కాళ్లు కడుక్కోండి",
        "touch_tel": "మీ కాళ్లను తాకండి",
        "wash_asm": "আপোনাৰ ভৰি ধুৱক",
        "touch_asm": "আপোনাৰ ভৰি স্পৰ্শ কৰক",
        "wash_tel_roman": "mi kaallu kadukkondi",
        "touch_tel_roman": "mi kaallanu taakandi",
        "wash_asm_roman": "aponar bhori dhuwak",
        "touch_asm_roman": "aponar bhori sporxo korok",
        "emoji": "🦶",
    },
    {
        "english": "eyes",
        "wash_tel": "మీ కళ్ళు కడుక్కోండి",
        "touch_tel": "మీ కళ్ళను తాకండి",
        "wash_asm": "আপোনাৰ চকু ধুৱক",
        "touch_asm": "আপোনাৰ চকু স্পৰ্শ কৰক",
        "wash_tel_roman": "mi kallu kadukkondi",
        "touch_tel_roman": "mi kallanu taakandi",
        "wash_asm_roman": "aponar soku dhuwak",
        "touch_asm_roman": "aponar soku sporxo korok",
        "emoji": "👁️",
    },
    {
        "english": "ears",
        "wash_tel": "మీ చెవులు కడుక్కోండి",
        "touch_tel": "మీ చెవులను తాకండి",
        "wash_asm": "আপোনাৰ কাণ ধুৱক",
        "touch_asm": "আপোনাৰ কাণ স্পৰ্শ কৰক",
        "wash_tel_roman": "mi chevulu kadukkondi",
        "touch_tel_roman": "mi chevulanu taakandi",
        "wash_asm_roman": "aponar kaan dhuwak",
        "touch_asm_roman": "aponar kaan sporxo korok",
        "emoji": "👂",
    },
    {
        "english": "nose",
        "wash_tel": "మీ ముక్కు కడుక్కోండి",
        "touch_tel": "మీ ముక్కును తాకండి",
        "wash_asm": "আপোনাৰ নাক ধুৱক",
        "touch_asm": "আপোনাৰ নাক স্পৰ্শ কৰক",
        "wash_tel_roman": "mi mukku kadukkondi",
        "touch_tel_roman": "mi mukkunu taakandi",
        "wash_asm_roman": "aponar naak dhuwak",
        "touch_asm_roman": "aponar naak sporxo korok",
        "emoji": "👃",
    },
    {
        "english": "mouth",
        "wash_tel": "మీ నోరు కడుక్కోండి",
        "touch_tel": "మీ నోరును తాకండి",
        "wash_asm": "আপোনাৰ মুখ ধুৱক",
        "touch_asm": "আপোনাৰ মুখ স্পৰ্শ কৰক",
        "wash_tel_roman": "mi noru kadukkondi",
        "touch_tel_roman": "mi norunu taakandi",
        "wash_asm_roman": "aponar mukh dhuwak",
        "touch_asm_roman": "aponar mukh sporxo korok",
        "emoji": "👄",
    },
    {
        "english": "hair",
        "wash_tel": "మీ జుట్టు కడుక్కోండి",
        "touch_tel": "మీ జుట్టును తాకండి",
        "wash_asm": "আপোনাৰ চুলি ধুৱক",
        "touch_asm": "আপোনাৰ চুলি স্পৰ্শ কৰক",
        "wash_tel_roman": "mi juttu kadukkondi",
        "touch_tel_roman": "mi juttunu taakandi",
        "wash_asm_roman": "aponar suli dhuwak",
        "touch_asm_roman": "aponar suli sporxo korok",
        "emoji": "💇",
    },
    {
        "english": "head",
        "wash_tel": "మీ తల కడుక్కోండి",
        "touch_tel": "మీ తలను తాకండి",
        "wash_asm": "আপোনাৰ মূৰ ধুৱক",
        "touch_asm": "আপোনাৰ মূৰ স্পৰ্শ কৰক",
        "wash_tel_roman": "mi tala kadukkondi",
        "touch_tel_roman": "mi talanu taakandi",
        "wash_asm_roman": "aponar mur dhuwak",
        "touch_asm_roman": "aponar mur sporxo korok",
        "emoji": "🧠",
    },
    {
        "english": "tummy",
        "wash_tel": "మీ పొట్ట కడుక్కోండి",
        "touch_tel": "మీ పొట్టను తాకండి",
        "wash_asm": "আপোনাৰ পেট ধুৱক",
        "touch_asm": "আপোনাৰ পেট স্পৰ্শ কৰক",
        "wash_tel_roman": "mi potta kadukkondi",
        "touch_tel_roman": "mi pottanu taakandi",
        "wash_asm_roman": "aponar pet dhuwak",
        "touch_asm_roman": "aponar pet sporxo korok",
        "emoji": "🤰",
    },
    {
        "english": "teeth",
        "wash_english": "Brush your teeth",
        "touch_english": "Show your teeth",
        "wash_tel": "మీ పళ్ళు తోమండి",
        "touch_tel": "మీ పళ్ళను చూపండి",
        "wash_asm": "আপোনাৰ দাঁত মাজক",
        "touch_asm": "আপোনাৰ দাঁত দেখুৱাওক",
        "wash_tel_roman": "mi pallu tomandi",
        "touch_tel_roman": "mi pallanu chupandi",
        "wash_asm_roman": "aponar daat maajok",
        "touch_asm_roman": "aponar daat dekhuwaok",
        "emoji": "🦷",
    },
)

_PHRASE_ACTIVITIES = (
    {
        "english_time": "Time to eat",
        "english_lets": "Let's eat",
        "tel_time": "తినే సమయం",
        "tel_lets": "తిందాం",
        "asm_time": "খোৱাৰ সময়",
        "asm_lets": "খাওঁ আহক",
        "tel_time_roman": "tine samayam",
        "tel_lets_roman": "tindam",
        "asm_time_roman": "khowar xomoy",
        "asm_lets_roman": "khaou ahok",
        "emoji": "🍽️",
    },
    {
        "english_time": "Time to drink water",
        "english_lets": "Let's drink water",
        "tel_time": "నీళ్లు తాగే సమయం",
        "tel_lets": "నీళ్లు తాగుదాం",
        "asm_time": "পানী খোৱাৰ সময়",
        "asm_lets": "পানী খাওঁ আহক",
        "tel_time_roman": "neellu taage samayam",
        "tel_lets_roman": "neellu tagudam",
        "asm_time_roman": "paani khowar xomoy",
        "asm_lets_roman": "paani khaou ahok",
        "emoji": "💧",
    },
    {
        "english_time": "Time to sleep",
        "english_lets": "Let's sleep",
        "tel_time": "నిద్రపోయే సమయం",
        "tel_lets": "పడుకుందాం",
        "asm_time": "শোৱাৰ সময়",
        "asm_lets": "শুওঁ আহক",
        "tel_time_roman": "nidrapoye samayam",
        "tel_lets_roman": "padukundam",
        "asm_time_roman": "xowar xomoy",
        "asm_lets_roman": "xou ahok",
        "emoji": "😴",
    },
    {
        "english_time": "Time to read",
        "english_lets": "Let's read",
        "tel_time": "చదివే సమయం",
        "tel_lets": "చదుద్దాం",
        "asm_time": "পঢ়াৰ সময়",
        "asm_lets": "পঢ়োঁ আহক",
        "tel_time_roman": "chadive samayam",
        "tel_lets_roman": "chaduddam",
        "asm_time_roman": "porhar xomoy",
        "asm_lets_roman": "porhou ahok",
        "emoji": "📖",
    },
    {
        "english_time": "Time to sing",
        "english_lets": "Let's sing",
        "tel_time": "పాట పాడే సమయం",
        "tel_lets": "పాట పాడుదాం",
        "asm_time": "গান গোৱাৰ সময়",
        "asm_lets": "গান গাওঁ আহক",
        "tel_time_roman": "paata paade samayam",
        "tel_lets_roman": "paata paadudam",
        "asm_time_roman": "gaan guwar xomoy",
        "asm_lets_roman": "gaan gaou ahok",
        "emoji": "🎵",
    },
    {
        "english_time": "Time to dance",
        "english_lets": "Let's dance",
        "tel_time": "నృత్యం చేసే సమయం",
        "tel_lets": "డాన్స్ చేద్దాం",
        "asm_time": "নাচৰ সময়",
        "asm_lets": "নাচোঁ আহক",
        "tel_time_roman": "nrityam chese samayam",
        "tel_lets_roman": "dance cheddam",
        "asm_time_roman": "nasor xomoy",
        "asm_lets_roman": "nasou ahok",
        "emoji": "💃",
    },
    {
        "english_time": "Time to clap",
        "english_lets": "Let's clap",
        "tel_time": "చప్పట్లు కొట్టే సమయం",
        "tel_lets": "చప్పట్లు కొడదాం",
        "asm_time": "তালি বজোৱাৰ সময়",
        "asm_lets": "তালি বজাওঁ আহক",
        "tel_time_roman": "chappatlu kotte samayam",
        "tel_lets_roman": "chappatlu koddam",
        "asm_time_roman": "taali bozowar xomoy",
        "asm_lets_roman": "taali bozaou ahok",
        "emoji": "👏",
    },
    {
        "english_time": "Time to wash up",
        "english_lets": "Let's wash up",
        "tel_time": "శుభ్రం అయ్యే సమయం",
        "tel_lets": "శుభ్రం అవుదాం",
        "asm_time": "ধুই লোৱাৰ সময়",
        "asm_lets": "ধুই লওঁ আহক",
        "tel_time_roman": "shubhram ayye samayam",
        "tel_lets_roman": "shubhram avudam",
        "asm_time_roman": "dhui lowar xomoy",
        "asm_lets_roman": "dhui lou ahok",
        "emoji": "🧼",
    },
    {
        "english_time": "Time to brush your teeth",
        "english_lets": "Let's brush our teeth",
        "tel_time": "పళ్ళు తోమే సమయం",
        "tel_lets": "పళ్ళు తోముదాం",
        "asm_time": "দাঁত মজাৰ সময়",
        "asm_lets": "দাঁত মজোঁ আহক",
        "tel_time_roman": "pallu tome samayam",
        "tel_lets_roman": "pallu tomudam",
        "asm_time_roman": "daat mojar xomoy",
        "asm_lets_roman": "daat mojou ahok",
        "emoji": "🪥",
    },
    {
        "english_time": "Time to write",
        "english_lets": "Let's write",
        "tel_time": "రాసే సమయం",
        "tel_lets": "రాయుద్దాం",
        "asm_time": "লিখাৰ সময়",
        "asm_lets": "লিখোঁ আহক",
        "tel_time_roman": "rase samayam",
        "tel_lets_roman": "rayuddam",
        "asm_time_roman": "likhar xomoy",
        "asm_lets_roman": "likhou ahok",
        "emoji": "📝",
    },
)

_PHRASE_COMMANDS = (
    {
        "english": "sit down",
        "please_tel": "దయచేసి కూర్చోండి",
        "can_tel": "మీరు కూర్చోగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি বহক",
        "can_asm": "আপুনি বহিব পাৰিবনে?",
        "please_tel_roman": "dayachesi kurchondi",
        "can_tel_roman": "miru kurchogalara",
        "please_asm_roman": "anugroho kori bohak",
        "can_asm_roman": "apuni bohib paribone",
        "emoji": "🧘",
    },
    {
        "english": "stand up",
        "please_tel": "దయచేసి నిలబడండి",
        "can_tel": "మీరు నిలబడగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি থিয় হওক",
        "can_asm": "আপুনি থিয় হ'ব পাৰিবনে?",
        "please_tel_roman": "dayachesi nilabadandi",
        "can_tel_roman": "miru nilabadagalara",
        "please_asm_roman": "anugroho kori thia houk",
        "can_asm_roman": "apuni thia hob paribone",
        "emoji": "🧍",
    },
    {
        "english": "come here",
        "please_tel": "దయచేసి ఇక్కడికి రండి",
        "can_tel": "మీరు ఇక్కడికి రాగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি ইয়ালৈ আহক",
        "can_asm": "আপুনি ইয়ালৈ আহিব পাৰিবনে?",
        "please_tel_roman": "dayachesi ikkadaiki randi",
        "can_tel_roman": "miru ikkadaiki ragalara",
        "please_asm_roman": "anugroho kori iyaloi ahok",
        "can_asm_roman": "apuni iyaloi ahib paribone",
        "emoji": "👋",
    },
    {
        "english": "go slowly",
        "please_tel": "దయచేసి నెమ్మదిగా వెళ్ళండి",
        "can_tel": "మీరు నెమ్మదిగా వెళ్ళగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি লাহে লাহে যাওক",
        "can_asm": "আপুনি লাহে লাহে যাব পাৰিবনে?",
        "please_tel_roman": "dayachesi nemmadiga vellandi",
        "can_tel_roman": "miru nemmadiga vellagalara",
        "please_asm_roman": "anugroho kori lahe lahe jaok",
        "can_asm_roman": "apuni lahe lahe jab paribone",
        "emoji": "🐢",
    },
    {
        "english": "jump high",
        "please_tel": "దయచేసి ఎత్తుగా దూకండి",
        "can_tel": "మీరు ఎత్తుగా దూకగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি ওপৰলৈ জপিয়াওক",
        "can_asm": "আপুনি ওখকৈ জপিয়াব পাৰিবনে?",
        "please_tel_roman": "dayachesi ettuga dukandi",
        "can_tel_roman": "miru ettuga dukagalara",
        "please_asm_roman": "anugroho kori oporloi jopiyaok",
        "can_asm_roman": "apuni okhoi jopiyab paribone",
        "emoji": "🦘",
    },
    {
        "english": "clap your hands",
        "please_tel": "దయచేసి చప్పట్లు కొట్టండి",
        "can_tel": "మీరు చప్పట్లు కొట్టగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি তালি বজাওক",
        "can_asm": "আপুনি তালি বজাব পাৰিবনে?",
        "please_tel_roman": "dayachesi chappatlu kottandi",
        "can_tel_roman": "miru chappatlu kottagalara",
        "please_asm_roman": "anugroho kori taali bozaok",
        "can_asm_roman": "apuni taali bozab paribone",
        "emoji": "👏",
    },
    {
        "english": "wave hello",
        "please_tel": "దయచేసి హలో చెప్పండి",
        "can_tel": "మీరు హలో చెప్పగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি হেল্ল' কওক",
        "can_asm": "আপুনি হেল্ল' কব পাৰিবনে?",
        "please_tel_roman": "dayachesi halo cheppandi",
        "can_tel_roman": "miru halo cheppagalara",
        "please_asm_roman": "anugroho kori hello kowok",
        "can_asm_roman": "apuni hello kob paribone",
        "emoji": "👋",
    },
    {
        "english": "look up",
        "please_tel": "దయచేసి పైకి చూడండి",
        "can_tel": "మీరు పైకి చూడగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি ওপৰলৈ চাওক",
        "can_asm": "আপুনি ওপৰলৈ চাব পাৰিবনে?",
        "please_tel_roman": "dayachesi paiki chudandi",
        "can_tel_roman": "miru paiki chudagalara",
        "please_asm_roman": "anugroho kori oporloi saok",
        "can_asm_roman": "apuni oporloi sab paribone",
        "emoji": "⬆️",
    },
    {
        "english": "look down",
        "please_tel": "దయచేసి కిందికి చూడండి",
        "can_tel": "మీరు కిందికి చూడగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি তললৈ চাওক",
        "can_asm": "আপুনি তললৈ চাব পাৰিবনে?",
        "please_tel_roman": "dayachesi kindiki chudandi",
        "can_tel_roman": "miru kindiki chudagalara",
        "please_asm_roman": "anugroho kori tololoi saok",
        "can_asm_roman": "apuni tololoi sab paribone",
        "emoji": "⬇️",
    },
    {
        "english": "turn around",
        "please_tel": "దయచేసి వెనక్కి తిరగండి",
        "can_tel": "మీరు వెనక్కి తిరగగలరా?",
        "please_asm": "অনুগ্ৰহ কৰি ঘূৰি যাওক",
        "can_asm": "আপুনি ঘূৰিব পাৰিবনে?",
        "please_tel_roman": "dayachesi venakki tiragandi",
        "can_tel_roman": "miru venakki tiragagalara",
        "please_asm_roman": "anugroho kori ghuri jaok",
        "can_asm_roman": "apuni ghurib paribone",
        "emoji": "🔄",
    },
)

_PHRASE_PLACES = (
    {
        "english": "house",
        "go_tel": "ఇంటికి వెళ్ళు",
        "look_tel": "ఇంటిని చూడండి",
        "go_asm": "ঘৰলৈ যাওক",
        "look_asm": "ঘৰটো চাওক",
        "go_tel_roman": "intiki vellu",
        "look_tel_roman": "intini chudandi",
        "go_asm_roman": "ghorloi jaok",
        "look_asm_roman": "ghorto saok",
        "emoji": "🏠",
    },
    {
        "english": "room",
        "go_tel": "గదికి వెళ్ళు",
        "look_tel": "గదిని చూడండి",
        "go_asm": "কোঠালৈ যাওক",
        "look_asm": "কোঠাটো চাওক",
        "go_tel_roman": "gadiki vellu",
        "look_tel_roman": "gadini chudandi",
        "go_asm_roman": "kothaloi jaok",
        "look_asm_roman": "kothato saok",
        "emoji": "🚪",
    },
    {
        "english": "kitchen",
        "go_tel": "వంటగదికి వెళ్ళు",
        "look_tel": "వంటగదిని చూడండి",
        "go_asm": "পাকঘৰলৈ যাওক",
        "look_asm": "পাকঘৰ চাওক",
        "go_tel_roman": "vantagadiki vellu",
        "look_tel_roman": "vantagadini chudandi",
        "go_asm_roman": "pakghorloi jaok",
        "look_asm_roman": "pakghor saok",
        "emoji": "🍳",
    },
    {
        "english": "bathroom",
        "go_tel": "బాత్రూంకి వెళ్ళు",
        "look_tel": "బాత్రూంను చూడండి",
        "go_asm": "বাথৰুমলৈ যাওক",
        "look_asm": "বাথৰুম চাওক",
        "go_tel_roman": "bathroomki vellu",
        "look_tel_roman": "bathroomnu chudandi",
        "go_asm_roman": "bathrumloi jaok",
        "look_asm_roman": "bathrum saok",
        "emoji": "🛁",
    },
    {
        "english": "garden",
        "go_tel": "తోటకు వెళ్ళు",
        "look_tel": "తోటను చూడండి",
        "go_asm": "বাৰীলৈ যাওক",
        "look_asm": "বাৰী চাওক",
        "go_tel_roman": "totaku vellu",
        "look_tel_roman": "totanu chudandi",
        "go_asm_roman": "bariloi jaok",
        "look_asm_roman": "bari saok",
        "emoji": "🌳",
    },
    {
        "english": "park",
        "go_tel": "పార్కుకు వెళ్ళు",
        "look_tel": "పార్కును చూడండి",
        "go_asm": "পাৰ্কলৈ যাওক",
        "look_asm": "পাৰ্ক চাওক",
        "go_tel_roman": "parkuku vellu",
        "look_tel_roman": "parkunu chudandi",
        "go_asm_roman": "parkloi jaok",
        "look_asm_roman": "park saok",
        "emoji": "🌿",
    },
    {
        "english": "bed",
        "english_go": "Go to bed",
        "go_tel": "పడక దగ్గరకు వెళ్ళు",
        "look_tel": "పడకను చూడండి",
        "go_asm": "বিচনাৰ ওচৰলৈ যাওক",
        "look_asm": "বিচনা চাওক",
        "go_tel_roman": "padaka daggaraku vellu",
        "look_tel_roman": "padakanu chudandi",
        "go_asm_roman": "bichonar osorloi jaok",
        "look_asm_roman": "bichona saok",
        "emoji": "🛏️",
    },
    {
        "english": "door",
        "go_tel": "తలుపు దగ్గరకు వెళ్ళు",
        "look_tel": "తలుపును చూడండి",
        "go_asm": "দুৱাৰৰ ওচৰলৈ যাওক",
        "look_asm": "দুৱাৰ চাওক",
        "go_tel_roman": "talupu daggaraku vellu",
        "look_tel_roman": "talupunu chudandi",
        "go_asm_roman": "duwaror osorloi jaok",
        "look_asm_roman": "duwar saok",
        "emoji": "🚪",
    },
    {
        "english": "window",
        "go_tel": "కిటికీ దగ్గరకు వెళ్ళు",
        "look_tel": "కిటికీని చూడండి",
        "go_asm": "খিৰিকীৰ ওচৰলৈ যাওক",
        "look_asm": "খিৰিকী চাওক",
        "go_tel_roman": "kitiki daggaraku vellu",
        "look_tel_roman": "kitikini chudandi",
        "go_asm_roman": "khirikir osorloi jaok",
        "look_asm_roman": "khiriki saok",
        "emoji": "🪟",
    },
    {
        "english": "table",
        "go_tel": "బల్ల దగ్గరకు వెళ్ళు",
        "look_tel": "బల్లను చూడండి",
        "go_asm": "টেবুলৰ ওচৰলৈ যাওক",
        "look_asm": "টেবুল চাওক",
        "go_tel_roman": "balla daggaraku vellu",
        "look_tel_roman": "ballanu chudandi",
        "go_asm_roman": "tebulor osorloi jaok",
        "look_asm_roman": "tebul saok",
        "emoji": "🪑",
    },
)

_CORE_PHRASES = (
    {"english": "Let's play", "telugu": "ఆడుకుందాం", "assamese": "আহা খেলো", "tel_roman": "adukundam", "asm_roman": "aha khelo", "emoji": "🎉"},
    {"english": "Come here", "telugu": "ఇక్కడికి రా", "assamese": "ইয়ালৈ আহা", "tel_roman": "ikkadiki raa", "asm_roman": "iyaloi aha", "emoji": "👋"},
    {"english": "Good morning", "telugu": "శుభోదయం", "assamese": "সুপ্ৰভাত", "tel_roman": "shubhodayam", "asm_roman": "suprabhat", "emoji": "☀️"},
    {"english": "Good night", "telugu": "శుభ రాత్రి", "assamese": "শুভ ৰাতি", "tel_roman": "shubha ratri", "asm_roman": "shubho rati", "emoji": "🌙"},
    {"english": "Run fast", "telugu": "త్వరగా పరుగెత్తు", "assamese": "দ্রুত দৌৰা", "tel_roman": "tvaraga parugettu", "asm_roman": "druto daura", "emoji": "🏃"},
    {"english": "Pretty flower", "telugu": "అందమైన పువ్వు", "assamese": "সুন্দৰ ফুল", "tel_roman": "andamaina puvvu", "asm_roman": "sundor phul", "emoji": "🌸"},
    {"english": "Let's go", "telugu": "వెళ్దాం", "assamese": "আহা যাওঁ", "tel_roman": "veldham", "asm_roman": "aha jaou", "emoji": "🚀"},
    {"english": "Big elephant", "telugu": "పెద్ద ఏనుగు", "assamese": "ডাঙৰ হাতী", "tel_roman": "pedda enugu", "asm_roman": "dangor hati", "emoji": "🐘"},
    {"english": "More please", "telugu": "ఇంకా కావాలి", "assamese": "আৰু লাগে", "tel_roman": "inka kavali", "asm_roman": "aru lage", "emoji": "🙏"},
    {"english": "So yummy", "telugu": "చాలా రుచిగా ఉంది", "assamese": "বহুত সোৱাদ", "tel_roman": "chala ruchiga undi", "asm_roman": "bohut sowad", "emoji": "😋"},
    {"english": "Wake up", "telugu": "లేవండి", "assamese": "উঠা", "tel_roman": "levandi", "asm_roman": "utha", "emoji": "⏰"},
    {"english": "Very good", "telugu": "చాలా బాగుంది", "assamese": "বহুত ভাল", "tel_roman": "chala bagundi", "asm_roman": "bohut bhal", "emoji": "⭐"},
    {"english": "Thank you", "telugu": "ధన్యవాదాలు", "assamese": "ধন্যবাদ", "tel_roman": "dhanyavadalu", "asm_roman": "dhanyobad", "emoji": "🙏"},
    {"english": "Please help me", "telugu": "దయచేసి నాకు సహాయం చేయండి", "assamese": "অনুগ্ৰহ কৰি মোক সহায় কৰক", "tel_roman": "dayachesi naku sahayam cheyandi", "asm_roman": "anugroho kori mok sohay korok", "emoji": "🤝"},
    {"english": "I am ready", "telugu": "నేను సిద్ధంగా ఉన్నాను", "assamese": "মই সাজু", "tel_roman": "nenu siddhanga unnanu", "asm_roman": "moi xaju", "emoji": "✅"},
    {"english": "Well done", "telugu": "బాగా చేశావు", "assamese": "বঢ়িয়া কৰিলা", "tel_roman": "baga chesavu", "asm_roman": "borhiya korila", "emoji": "👏"},
    {"english": "Great job", "telugu": "చాలా మంచి పని", "assamese": "দারুণ কাম", "tel_roman": "chala manchi pani", "asm_roman": "darun kam", "emoji": "🏆"},
    {"english": "Be careful", "telugu": "జాగ్రత్తగా ఉండు", "assamese": "সাৱধান হওক", "tel_roman": "jagrattaga undu", "asm_roman": "xabadhan houk", "emoji": "⚠️"},
    {"english": "Slow down", "telugu": "నెమ్మదిగా వెళ్లు", "assamese": "লাহে লাহে যাওক", "tel_roman": "nemmadiga vellu", "asm_roman": "lahe lahe jaok", "emoji": "🐢"},
    {"english": "Come with me", "telugu": "నాతో రా", "assamese": "মোৰ লগত আহা", "tel_roman": "naatho raa", "asm_roman": "mor logot aha", "emoji": "🤝"},
    {"english": "Wait for me", "telugu": "నా కోసం ఆగు", "assamese": "মোৰ বাবে ৰওক", "tel_roman": "na kosam agu", "asm_roman": "mor babe rowok", "emoji": "⏳"},
    {"english": "Hold my hand", "telugu": "నా చేయి పట్టుకో", "assamese": "মোৰ হাত ধৰা", "tel_roman": "na cheyi pattuko", "asm_roman": "mor haat dhora", "emoji": "✋"},
    {"english": "I am sorry", "telugu": "నన్ను క్షమించు", "assamese": "মই দুঃখিত", "tel_roman": "nannu kshaminchu", "asm_roman": "moi dukkhito", "emoji": "💛"},
    {"english": "You are kind", "telugu": "నువ్వు దయగలవు", "assamese": "তুমি দয়ালু", "tel_roman": "nuvvu dayagalavu", "asm_roman": "tumi doyalu", "emoji": "🙂"},
    {"english": "Be gentle", "telugu": "మెల్లిగా ఉండు", "assamese": "নরম হওক", "tel_roman": "melliga undu", "asm_roman": "norom houk", "emoji": "🪶"},
    {"english": "Share your toy", "telugu": "నీ బొమ్మను పంచుకో", "assamese": "তোমাৰ খেলনা ভাগ কৰা", "tel_roman": "ni bommanu panchuko", "asm_roman": "tomar khelona bhag kora", "emoji": "🧸"},
    {"english": "My turn please", "telugu": "ఇప్పుడు నా వంతు", "assamese": "এতিয়া মোৰ পাল", "tel_roman": "ippudu naa vantu", "asm_roman": "etiya mor pal", "emoji": "🔄"},
    {"english": "Your turn now", "telugu": "ఇప్పుడు నీ వంతు", "assamese": "এতিয়া তোমাৰ পাল", "tel_roman": "ippudu ni vantu", "asm_roman": "etiya tomar pal", "emoji": "🔄"},
    {"english": "Wake up now", "telugu": "ఇప్పుడే లేవు", "assamese": "এতিয়াই উঠা", "tel_roman": "ippude levu", "asm_roman": "etiyai utha", "emoji": "⏰"},
    {"english": "Time for breakfast", "telugu": "అల్పాహారం సమయం", "assamese": "জলপানৰ সময়", "tel_roman": "alpaharam samayam", "asm_roman": "jolpanor xomoy", "emoji": "🍳"},
    {"english": "Time for lunch", "telugu": "మధ్యాహ్న భోజన సమయం", "assamese": "মধ্যাহ্ন ভোজনৰ সময়", "tel_roman": "madhyahna bhojanam samayam", "asm_roman": "madhyahn bhojonor xomoy", "emoji": "🍽️"},
    {"english": "Time for dinner", "telugu": "రాత్రి భోజన సమయం", "assamese": "ৰাতিৰ আহাৰৰ সময়", "tel_roman": "ratri bhojanam samayam", "asm_roman": "ratir aharor xomoy", "emoji": "🍲"},
    {"english": "Time for bath", "telugu": "స్నానం చేసే సమయం", "assamese": "গা ধোৱাৰ সময়", "tel_roman": "snanam chese samayam", "asm_roman": "ga dhuwar xomoy", "emoji": "🛁"},
    {"english": "Put on your shoes", "telugu": "నీ బూట్లు వేసుకో", "assamese": "তোমাৰ জোতা পিন্ধা", "tel_roman": "ni butlu vesuko", "asm_roman": "tomar jota pindha", "emoji": "👟"},
    {"english": "Put on your socks", "telugu": "నీ సాక్స్ వేసుకో", "assamese": "তোমাৰ মোজা পিন্ধা", "tel_roman": "ni saks vesuko", "asm_roman": "tomar moja pindha", "emoji": "🧦"},
    {"english": "Open the door", "telugu": "తలుపు తెరువు", "assamese": "দুৱাৰ খোলা", "tel_roman": "talupu teruvu", "asm_roman": "duwar khola", "emoji": "🚪"},
    {"english": "Close the door", "telugu": "తలుపు మూయి", "assamese": "দুৱাৰ বন্ধ কৰা", "tel_roman": "talupu muyi", "asm_roman": "duwar bondho kora", "emoji": "🚪"},
    {"english": "Pack your bag", "telugu": "నీ బ్యాగ్ సర్దుకో", "assamese": "তোমাৰ বেগ গুছাই লোৱা", "tel_roman": "ni bag sarduko", "asm_roman": "tomar beg gusai lua", "emoji": "🎒"},
    {"english": "Clean your room", "telugu": "నీ గదిని శుభ్రం చేయి", "assamese": "তোমাৰ কোঠা পৰিষ্কাৰ কৰা", "tel_roman": "ni gadini shubhram cheyi", "asm_roman": "tomar kotha poriskar kora", "emoji": "🧹"},
    {"english": "Pick up your toys", "telugu": "నీ బొమ్మలు తీసుకో", "assamese": "তোমাৰ খেলনাবোৰ উঠাই লোৱা", "tel_roman": "ni bommalu teesuko", "asm_roman": "tomar khelonabor uthai lua", "emoji": "🧸"},
    {"english": "Listen to me", "telugu": "నా మాట విను", "assamese": "মোৰ কথা শুনা", "tel_roman": "na maata vinu", "asm_roman": "mor kotha xuna", "emoji": "👂"},
    {"english": "Look at me", "telugu": "నా వైపు చూడు", "assamese": "মোৰ ফালে চোৱা", "tel_roman": "na vaipu chudu", "asm_roman": "mor fale chowa", "emoji": "👀"},
)


def _phrase_entry(
    english: str,
    telugu: str,
    assamese: str,
    tel_roman: str,
    asm_roman: str,
    emoji: str,
) -> dict:
    return {
        "english": english,
        "telugu": telugu,
        "assamese": assamese,
        "emoji": emoji,
        "tel_roman": tel_roman,
        "asm_roman": asm_roman,
    }


def _indefinite_article(words: str) -> str:
    return "an" if words[0].lower() in "aeiou" else "a"


def _normalize_phrase_english(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", text.lower())).strip()


def _build_color_object_phrases() -> list[dict]:
    phrases = []
    for color in _PHRASE_COLORS:
        for obj in _PHRASE_OBJECTS:
            english_desc = f"{color['english']} {obj['english']}"
            article = _indefinite_article(english_desc)
            tel_desc = f"{color['telugu']} {obj['telugu']}"
            asm_desc = f"{color['assamese']} {obj['assamese']}"
            phrases.append(
                _phrase_entry(
                    f"I see {article} {english_desc}",
                    f"నాకు {tel_desc} కనిపిస్తోంది",
                    f"মই {asm_desc} দেখিছোঁ",
                    f"naku {color['tel_roman']} {obj['tel_roman']} kanipistondi",
                    f"moi {color['asm_roman']} {obj['asm_roman']} dekhiso",
                    obj["emoji"],
                )
            )
            phrases.append(
                _phrase_entry(
                    f"This is {article} {english_desc}",
                    f"ఇది {tel_desc}",
                    f"এইটো {asm_desc}",
                    f"idi {color['tel_roman']} {obj['tel_roman']}",
                    f"eitu {color['asm_roman']} {obj['asm_roman']}",
                    obj["emoji"],
                )
            )
            phrases.append(
                _phrase_entry(
                    f"Find the {english_desc}",
                    f"{color['telugu']} {obj['telugu_obj']} వెతకండి",
                    f"{color['assamese']} {obj['assamese']} বিচাৰি উলিয়াওক",
                    f"{color['tel_roman']} {obj['tel_obj_roman']} vetakandi",
                    f"{color['asm_roman']} {obj['asm_roman']} bisari uliaok",
                    obj["emoji"],
                )
            )
    return phrases


def _build_animal_action_phrases() -> list[dict]:
    phrases = []
    for animal in _PHRASE_ANIMALS:
        for action in _PHRASE_ANIMAL_ACTIONS:
            phrases.append(
                _phrase_entry(
                    f"The {animal['english']} is {action['english']}",
                    f"{animal['telugu']} {action['telugu']}",
                    f"{animal['assamese']} {action['assamese']}",
                    f"{animal['tel_roman']} {action['tel_roman']}",
                    f"{animal['asm_roman']} {action['asm_roman']}",
                    animal["emoji"],
                )
            )
    return phrases


def _build_food_phrases() -> list[dict]:
    phrases = []
    for food in _PHRASE_FOODS:
        phrases.append(
            _phrase_entry(
                f"I like {food['english_like']}",
                food["tel_like"],
                food["asm_like"],
                food["tel_like_roman"],
                food["asm_like_roman"],
                food["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                f"I want {food['english_want']}",
                food["tel_want"],
                food["asm_want"],
                food["tel_want_roman"],
                food["asm_want_roman"],
                food["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                f"Please give me {food['english_give']}",
                food["tel_give"],
                food["asm_give"],
                food["tel_give_roman"],
                food["asm_give_roman"],
                food["emoji"],
            )
        )
    return phrases


def _build_people_phrases() -> list[dict]:
    phrases = []
    for person in _PHRASE_PEOPLE:
        title = person["english"]
        phrases.append(
            _phrase_entry(
                f"Where is {title}?",
                person["where_tel"],
                person["where_asm"],
                person["where_tel_roman"],
                person["where_asm_roman"],
                person["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                f"{title.capitalize()} is here",
                person["here_tel"],
                person["here_asm"],
                person["here_tel_roman"],
                person["here_asm_roman"],
                person["emoji"],
            )
        )
    return phrases


def _build_body_part_phrases() -> list[dict]:
    phrases = []
    for part in _PHRASE_BODY_PARTS:
        phrases.append(
            _phrase_entry(
                part.get("wash_english", f"Wash your {part['english']}"),
                part["wash_tel"],
                part["wash_asm"],
                part["wash_tel_roman"],
                part["wash_asm_roman"],
                part["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                part.get("touch_english", f"Touch your {part['english']}"),
                part["touch_tel"],
                part["touch_asm"],
                part["touch_tel_roman"],
                part["touch_asm_roman"],
                part["emoji"],
            )
        )
    return phrases


def _build_activity_phrases() -> list[dict]:
    phrases = []
    for activity in _PHRASE_ACTIVITIES:
        phrases.append(
            _phrase_entry(
                activity["english_time"],
                activity["tel_time"],
                activity["asm_time"],
                activity["tel_time_roman"],
                activity["asm_time_roman"],
                activity["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                activity["english_lets"],
                activity["tel_lets"],
                activity["asm_lets"],
                activity["tel_lets_roman"],
                activity["asm_lets_roman"],
                activity["emoji"],
            )
        )
    return phrases


def _build_command_phrases() -> list[dict]:
    phrases = []
    for command in _PHRASE_COMMANDS:
        title = command["english"]
        phrases.append(
            _phrase_entry(
                f"Please {title}",
                command["please_tel"],
                command["please_asm"],
                command["please_tel_roman"],
                command["please_asm_roman"],
                command["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                f"Can you {title}?",
                command["can_tel"],
                command["can_asm"],
                command["can_tel_roman"],
                command["can_asm_roman"],
                command["emoji"],
            )
        )
    return phrases


def _build_place_phrases() -> list[dict]:
    phrases = []
    for place in _PHRASE_PLACES:
        phrases.append(
            _phrase_entry(
                place.get("english_go", f"Go to the {place['english']}"),
                place["go_tel"],
                place["go_asm"],
                place["go_tel_roman"],
                place["go_asm_roman"],
                place["emoji"],
            )
        )
        phrases.append(
            _phrase_entry(
                f"Look at the {place['english']}",
                place["look_tel"],
                place["look_asm"],
                place["look_tel_roman"],
                place["look_asm_roman"],
                place["emoji"],
            )
        )
    return phrases


def _build_core_phrases() -> list[dict]:
    return [
        _phrase_entry(
            phrase["english"],
            phrase["telugu"],
            phrase["assamese"],
            phrase["tel_roman"],
            phrase["asm_roman"],
            phrase["emoji"],
        )
        for phrase in _CORE_PHRASES
    ]


def _validate_phrases(phrases: list[dict]) -> None:
    normalized = set()
    for phrase in phrases:
        for key in ("english", "telugu", "assamese", "emoji", "tel_roman", "asm_roman"):
            if not phrase[key]:
                raise ValueError(f"Phrase '{phrase.get('english')}' missing non-empty field '{key}'")
        if "&#" in phrase["assamese"] or "&amp;" in phrase["assamese"]:
            raise ValueError(f"Phrase '{phrase['english']}' contains HTML entities")
        if not phrase["tel_roman"].isascii() or not phrase["asm_roman"].isascii():
            raise ValueError(f"Phrase '{phrase['english']}' has non-ASCII romanization")
        if " a orange " in f" {phrase['english'].lower()} ":
            raise ValueError(f"Phrase '{phrase['english']}' uses the wrong article for orange")
        normalized_english = _normalize_phrase_english(phrase["english"])
        if normalized_english in normalized:
            raise ValueError(f"Duplicate normalized phrase English text: {phrase['english']}")
        normalized.add(normalized_english)
    if len(phrases) != 512:
        raise ValueError(f"Expected 512 phrases, found {len(phrases)}")


def _build_phrases() -> list[dict]:
    phrases = []
    phrases.extend(_build_color_object_phrases())
    phrases.extend(_build_animal_action_phrases())
    phrases.extend(_build_food_phrases())
    phrases.extend(_build_people_phrases())
    phrases.extend(_build_body_part_phrases())
    phrases.extend(_build_activity_phrases())
    phrases.extend(_build_command_phrases())
    phrases.extend(_build_place_phrases())
    phrases.extend(_build_core_phrases())
    _validate_phrases(phrases)
    return phrases


WORD_DATABASE["phrases"] = _build_phrases()

ALL_CATEGORIES = list(WORD_DATABASE.keys())


def get_random_word(category: str, language: str) -> dict:
    """Get a random word from the specified category with translation for given language."""
    if category not in WORD_DATABASE:
        category = random.choice(ALL_CATEGORIES)

    words = WORD_DATABASE[category]
    word = random.choice(words)

    roman_key = "tel_roman" if language == "telugu" else "asm_roman"

    return {
        "english": word["english"],
        "translation": word.get(language, word["english"]),
        "romanized": word.get(roman_key, ""),
        "emoji": word.get("emoji", ""),
        "language": language,
        "category": category,
    }


def get_all_words_for_language(language: str, categories: list) -> list:
    """Get all words for a given language, filtered by categories."""
    result = []
    for cat in categories:
        if cat in WORD_DATABASE:
            for word in WORD_DATABASE[cat]:
                roman_key = "tel_roman" if language == "telugu" else "asm_roman"
                result.append({
                    "english": word["english"],
                    "translation": word.get(language, word["english"]),
                    "romanized": word.get(roman_key, ""),
                    "emoji": word.get("emoji", ""),
                    "language": language,
                    "category": cat,
                })
    return result
