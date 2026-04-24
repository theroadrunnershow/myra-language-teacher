SUPPORTED_LESSON_LANGUAGES = ("telugu", "assamese", "tamil", "malayalam")
VALID_LANGUAGES = set((*SUPPORTED_LESSON_LANGUAGES, "english"))

LANGUAGE_CODES = {
    "telugu": "te",
    "assamese": "as",
    "tamil": "ta",
    "malayalam": "ml",
    "english": "en",
}

TRANSLATE_LANGUAGE_CODES = {
    language: LANGUAGE_CODES[language]
    for language in SUPPORTED_LESSON_LANGUAGES
}

ROMANIZATION_KEYS = {
    "telugu": "tel_roman",
    "assamese": "asm_roman",
    "tamil": "tam_roman",
    "malayalam": "mal_roman",
}
