"""Palavra de ativação "X9" na transcrição do Whisper.

O Whisper escreve o nome de vários jeitos ("X9", "X-9", "xis nove", "chis
nove", "x nove"…). Aceita o nome no começo (depois de "ei", "ô", "fala"…) ou
no fim da frase ("quem carregou ontem, X9?").
"""
import difflib
import re
import unicodedata

FILLERS = {"e", "ei", "eii", "oi", "o", "ow", "fala", "salve", "ai", "ah", "eai", "aew",
           "opa", "hey", "alo", "ola"}
FORMS = {"x9", "xis9", "xisnove", "xnove", "chisnove", "shisnove", "xiznove", "xisnovi",
         "chis9", "x9x9", "xnovi", "cheesnove", "xisnov", "x09"}
_FUZZY_TARGET = "xisnove"


def _norm_word(w):
    w = unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", w).split()


def _tokens(text):
    """[(token, índice da palavra original)]."""
    out = []
    for i, w in enumerate(text.split()):
        for t in _norm_word(w):
            out.append((t, i))
    return out


def _is_wake(joined):
    if joined in FORMS:
        return True
    return (len(joined) >= 5 and joined[0] in "xcs"
            and difflib.SequenceMatcher(None, joined, _FUZZY_TARGET).ratio() >= 0.8)


def strip_wake(text):
    """Texto sem o nome, se a frase chamou o X9; senão None.

    "" significa que chamou mas não perguntou nada ("X9!")."""
    words = text.split()
    toks = _tokens(text)
    if not toks:
        return None
    # começo
    start = 0
    while start < len(toks) and start < 2 and toks[start][0] in FILLERS:
        start += 1
    for n in (1, 2, 3):
        chunk = toks[start:start + n]
        if len(chunk) == n and _is_wake("".join(t for t, _ in chunk)):
            last_word = chunk[-1][1]
            return " ".join(words[last_word + 1:]).lstrip(" ,.!?:;-").strip()
    # fim
    for n in (1, 2, 3):
        chunk = toks[-n:]
        if len(chunk) == n and _is_wake("".join(t for t, _ in chunk)):
            first_word = chunk[0][1]
            return " ".join(words[:first_word]).rstrip(" ,.!?:;-").strip()
    return None
