"""
Language codes, in the one form Remuxarr compares and writes.

A language reaches Remuxarr in three spellings. Matroska and MP4 tags are
meant to carry an ISO 639-2 code, which itself comes in two forms for twenty
languages: bibliographic (/B: ger, fre, chi, dut) and terminologic (/T: deu,
fra, zho, nld). Tools and people also write the two-letter ISO 639-1 code
(en, de). Settings took whatever was typed and the decision engine compared
strings, so with Keep Audio Languages set to eng and ger, a German track
tagged deu was removed, an English track tagged en was removed while a
Japanese default track was kept, and a list holding en removed every track
tagged eng. And MP4 cannot hold a two-letter code at all: FFmpeg writes no
language for it, without an error, so a track tagged en came out of a
conversion undefined, and a Set language of en could never stick.

canonical() turns any of the three into ISO 639-2/B, the form Matroska
specifies and Remuxarr's settings and docs already use (eng, fre, jpn).
Everything that compares a language goes through it: the decision engine's
keep lists and track tags, saved settings, Language Review answers and the
Plex check. A code it does not know is returned as given, lowercased, since
a shape check is all anything here can do with it.

two_letter() is the other direction, for the .srt names Plex reads
(Movie.en.srt): the ISO 639-1 code where the language has one, the
three-letter code where it does not.

from_arr_language() reads the language objects Sonarr and Radarr send, such
as a series' or movie's original language, which carry an English name and
an id rather than a code.

The tables are the ISO 639-2 registry as published (184 two-letter codes;
the twenty languages whose /T code differs from /B), generated from
pycountry's copy of it rather than typed. test_language_codes.py checks them
against each other.
"""

TWO_LETTER = {
    "aa": "aar", "ab": "abk", "ae": "ave", "af": "afr", "ak": "aka", "am": "amh",
    "an": "arg", "ar": "ara", "as": "asm", "av": "ava", "ay": "aym", "az": "aze",
    "ba": "bak", "be": "bel", "bg": "bul", "bi": "bis", "bm": "bam", "bn": "ben",
    "bo": "tib", "br": "bre", "bs": "bos", "ca": "cat", "ce": "che", "ch": "cha",
    "co": "cos", "cr": "cre", "cs": "cze", "cu": "chu", "cv": "chv", "cy": "wel",
    "da": "dan", "de": "ger", "dv": "div", "dz": "dzo", "ee": "ewe", "el": "gre",
    "en": "eng", "eo": "epo", "es": "spa", "et": "est", "eu": "baq", "fa": "per",
    "ff": "ful", "fi": "fin", "fj": "fij", "fo": "fao", "fr": "fre", "fy": "fry",
    "ga": "gle", "gd": "gla", "gl": "glg", "gn": "grn", "gu": "guj", "gv": "glv",
    "ha": "hau", "he": "heb", "hi": "hin", "ho": "hmo", "hr": "hrv", "ht": "hat",
    "hu": "hun", "hy": "arm", "hz": "her", "ia": "ina", "id": "ind", "ie": "ile",
    "ig": "ibo", "ii": "iii", "ik": "ipk", "io": "ido", "is": "ice", "it": "ita",
    "iu": "iku", "ja": "jpn", "jv": "jav", "ka": "geo", "kg": "kon", "ki": "kik",
    "kj": "kua", "kk": "kaz", "kl": "kal", "km": "khm", "kn": "kan", "ko": "kor",
    "kr": "kau", "ks": "kas", "ku": "kur", "kv": "kom", "kw": "cor", "ky": "kir",
    "la": "lat", "lb": "ltz", "lg": "lug", "li": "lim", "ln": "lin", "lo": "lao",
    "lt": "lit", "lu": "lub", "lv": "lav", "mg": "mlg", "mh": "mah", "mi": "mao",
    "mk": "mac", "ml": "mal", "mn": "mon", "mr": "mar", "ms": "may", "mt": "mlt",
    "my": "bur", "na": "nau", "nb": "nob", "nd": "nde", "ne": "nep", "ng": "ndo",
    "nl": "dut", "nn": "nno", "no": "nor", "nr": "nbl", "nv": "nav", "ny": "nya",
    "oc": "oci", "oj": "oji", "om": "orm", "or": "ori", "os": "oss", "pa": "pan",
    "pi": "pli", "pl": "pol", "ps": "pus", "pt": "por", "qu": "que", "rm": "roh",
    "rn": "run", "ro": "rum", "ru": "rus", "rw": "kin", "sa": "san", "sc": "srd",
    "sd": "snd", "se": "sme", "sg": "sag", "sh": "hbs", "si": "sin", "sk": "slo",
    "sl": "slv", "sm": "smo", "sn": "sna", "so": "som", "sq": "alb", "sr": "srp",
    "ss": "ssw", "st": "sot", "su": "sun", "sv": "swe", "sw": "swa", "ta": "tam",
    "te": "tel", "tg": "tgk", "th": "tha", "ti": "tir", "tk": "tuk", "tl": "tgl",
    "tn": "tsn", "to": "ton", "tr": "tur", "ts": "tso", "tt": "tat", "tw": "twi",
    "ty": "tah", "ug": "uig", "uk": "ukr", "ur": "urd", "uz": "uzb", "ve": "ven",
    "vi": "vie", "vo": "vol", "wa": "wln", "wo": "wol", "xh": "xho", "yi": "yid",
    "yo": "yor", "za": "zha", "zh": "chi", "zu": "zul",
}


TERMINOLOGIC = {
    "bod": "tib", "ces": "cze", "cym": "wel", "deu": "ger", "ell": "gre",
    "eus": "baq", "fas": "per", "fra": "fre", "hye": "arm", "isl": "ice",
    "kat": "geo", "mkd": "mac", "mri": "mao", "msa": "may", "mya": "bur",
    "nld": "dut", "ron": "rum", "slk": "slo", "sqi": "alb", "zho": "chi",
}


# Sonarr's and Radarr's languages, by the name their API and webhooks give
# (a series' or movie's originalLanguage is {"id": 2, "name": "French"}), in
# the form canonical() returns. By name, because the two number their
# languages differently from 26 on: 26 is Arabic in Sonarr and Hindi in
# Radarr. Every name either defines on its release branch (Sonarr main at
# bc54afb, Radarr master at a96bf7c; Sonarr's are a subset of Radarr's)
# except Unknown, Any and Original, which name no language. Generated: the
# code each project's IsoLanguages gives, and, for the eleven it gives none
# (Afrikaans, Albanian, Bengali, Georgian, Kannada, Marathi, Mongolian,
# Romansh, Tagalog, Telugu, Urdu), the ISO 639-2 registry's entry of that
# name. test_language_codes.py checks the table against both projects' lists.
ARR_LANGUAGE_NAMES = {
    "Afrikaans": "afr", "Albanian": "alb", "Arabic": "ara", "Bengali": "ben",
    "Bosnian": "bos", "Bulgarian": "bul", "Catalan": "cat", "Chinese": "chi",
    "Croatian": "hrv", "Czech": "cze", "Danish": "dan", "Dutch": "dut",
    "English": "eng", "Estonian": "est", "Finnish": "fin", "Flemish": "dut",
    "French": "fre", "Georgian": "geo", "German": "ger", "Greek": "gre",
    "Hebrew": "heb", "Hindi": "hin", "Hungarian": "hun", "Icelandic": "ice",
    "Indonesian": "ind", "Italian": "ita", "Japanese": "jpn",
    "Kannada": "kan", "Korean": "kor", "Latvian": "lav", "Lithuanian": "lit",
    "Macedonian": "mac", "Malayalam": "mal", "Marathi": "mar",
    "Mongolian": "mon", "Norwegian": "nor", "Persian": "per", "Polish": "pol",
    "Portuguese": "por", "Portuguese (Brazil)": "por", "Romanian": "rum",
    "Romansh": "roh", "Russian": "rus", "Serbian": "srp", "Slovak": "slo",
    "Slovenian": "slv", "Spanish": "spa", "Spanish (Latino)": "spa",
    "Swedish": "swe", "Tagalog": "tgl", "Tamil": "tam", "Telugu": "tel",
    "Thai": "tha", "Turkish": "tur", "Ukrainian": "ukr", "Urdu": "urd",
    "Vietnamese": "vie",
}


# Where the .srt names Remuxarr has always written differ from ISO 639-1:
# Norwegian Bokmal and Nynorsk were named "no", not "nb" and "nn". Kept, so a
# file processed again does not get a second sidecar under the other name.
_SRT_NAME_EXCEPTIONS = {"nob": "no", "nno": "no"}

_TO_TWO_LETTER = {b: two for two, b in TWO_LETTER.items()} | _SRT_NAME_EXCEPTIONS


def canonical(code: str | None) -> str | None:
    """
    ISO 639-2/B for any spelling of a language: "en", "eng", "de", "deu"
    and "ger" give "eng", "eng", "ger", "ger", "ger". A code not in the
    tables comes back lowercased and stripped, and None or "" as given.
    """
    if not code:
        return code
    code = code.strip().lower()
    if len(code) == 2:
        return TWO_LETTER.get(code, code)
    return TERMINOLOGIC.get(code, code)


def two_letter(code: str) -> str:
    """The code a .srt name carries: ISO 639-1 if the language has one."""
    code = canonical(code)
    return _TO_TWO_LETTER.get(code, code)


def from_arr_language(language) -> str | None:
    """
    The code for a Sonarr or Radarr language object ({"id", "name"}), or None
    when it names no language: Unknown, Any, Original, a name this table does
    not have, or anything that is not such an object. Read by name alone; the
    id means a different language in each service.
    """
    if not isinstance(language, dict):
        return None
    return ARR_LANGUAGE_NAMES.get(language.get("name"))
