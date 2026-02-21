import random

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
        {"english": "frog",     "telugu": "కప్ప",       "assamese": "ভেকুলী",    "emoji": "🐸", "tel_roman": "kappa",      "asm_roman": "bhekuli"},
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
    ],
    "body_parts": [
        {"english": "eye",    "telugu": "కన్ను",  "assamese": "চকু",  "emoji": "👁️", "tel_roman": "kannu",   "asm_roman": "shoku"},
        {"english": "nose",   "telugu": "ముక్కు", "assamese": "নাক",  "emoji": "👃", "tel_roman": "mukku",   "asm_roman": "naak"},
        {"english": "hand",   "telugu": "చేయి",   "assamese": "হাত",  "emoji": "✋", "tel_roman": "cheyi",   "asm_roman": "haat"},
        {"english": "leg",    "telugu": "కాలు",   "assamese": "ভৰি",  "emoji": "🦵", "tel_roman": "kaalu",   "asm_roman": "bhori"},
        {"english": "ear",    "telugu": "చెవి",   "assamese": "কাণ",  "emoji": "👂", "tel_roman": "chevi",   "asm_roman": "kaan"},
        {"english": "mouth",  "telugu": "నోరు",   "assamese": "মুখ",  "emoji": "👄", "tel_roman": "noru",    "asm_roman": "mukh"},
        {"english": "hair",   "telugu": "జుట్టు", "assamese": "চুলি", "emoji": "💇", "tel_roman": "juttu",   "asm_roman": "shuli"},
        {"english": "teeth",  "telugu": "పళ్ళు",  "assamese": "দাঁত", "emoji": "🦷", "tel_roman": "pallu",   "asm_roman": "daat"},
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
        {"english": "bread",  "telugu": "రొట్టె",      "assamese": "পাউৰুটি","emoji": "🍞", "tel_roman": "rotte",       "asm_roman": "pauruti"},
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
        {"english": "boat",   "telugu": "పడవ",      "assamese": "নাও",   "emoji": "⛵", "tel_roman": "padava",   "asm_roman": "naao"},
    ],
}

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
