"""Thai text normalisation for Royal Gazette PDFs.

Older Gazette PDFs embed fonts whose shifted glyphs (tone marks / upper
vowels raised or moved left) are mapped to the Private Use Area
U+F700-U+F71A. We map them back to standard Thai code points.
Mapping verified empirically on the ERC corpus (e.g. 'แห\\uf70aง' -> 'แห่ง',
'หน\\uf70bา' -> 'หน้า', 'เป\\uf712น' -> 'เป็น').
"""
import re
import unicodedata

PUA_THAI = {
    0xF700: "ฐ", 0xF701: "ิ", 0xF702: "ี", 0xF703: "ึ", 0xF704: "ื",
    0xF705: "่", 0xF706: "้", 0xF707: "๊", 0xF708: "๋", 0xF709: "์",
    0xF70A: "่", 0xF70B: "้", 0xF70C: "๊", 0xF70D: "๋", 0xF70E: "์",
    0xF70F: "ญ", 0xF710: "ั", 0xF711: "ํ", 0xF712: "็", 0xF713: "่",
    0xF714: "้", 0xF715: "๊", 0xF716: "๋", 0xF717: "์", 0xF718: "ุ",
    0xF719: "ู", 0xF71A: "ฺ",
}
_PUA_TABLE = {k: v for k, v in PUA_THAI.items()}

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
WATERMARK = re.compile(r"ส[ำํ]า?นักงานคณะกรรมการกฤษฎีกา")
TONES = set("่้๊๋")


def fix_pua(text: str) -> str:
    return text.translate(_PUA_TABLE)


def normalize(text: str) -> str:
    text = fix_pua(text)
    text = text.replace("ํา", "ำ")  # ํ + า -> ำ
    # Word->PDF exports drop the nikhahit of ำ and leave "ก ากับ"; สระอา never
    # begins a Thai word, so <consonant or tone mark><space>า is always a broken ำ.
    text = re.sub("(?<=[\u0e01-\u0e2e\u0e48-\u0e4b])[ \xa0]\u0e32", "\u0e33", text)
    text = unicodedata.normalize("NFC", text)
    text = WATERMARK.sub(" ", text)
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def thai_digits_to_arabic(s: str) -> str:
    return s.translate(THAI_DIGITS)


def quality(text: str) -> dict:
    thai = [c for c in text if "ก" <= c <= "๛"]
    n = len(thai)
    tones = sum(1 for c in thai if c in TONES)
    pua = sum(1 for c in text if 0xF700 <= ord(c) <= 0xF71F)
    return {"thai_chars": n, "tone_ratio": round(tones / n, 4) if n else 0.0, "pua_left": pua}
