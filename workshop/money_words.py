"""Сумма прописью (рубли) для договоров."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


_ONES = (
    ("", ""),
    ("один", "одна"),
    ("два", "две"),
    ("три", "три"),
    ("четыре", "четыре"),
    ("пять", "пять"),
    ("шесть", "шесть"),
    ("семь", "семь"),
    ("восемь", "восемь"),
    ("девять", "девять"),
)
_TEENS = (
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
)
_TENS = (
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
)
_HUNDREDS = (
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
)


def _triad(n: int, feminine: bool) -> str:
    h, rem = divmod(n, 100)
    parts: list[str] = []
    if h:
        parts.append(_HUNDREDS[h])
    if 10 <= rem <= 19:
        parts.append(_TEENS[rem - 10])
    else:
        t, o = divmod(rem, 10)
        if t:
            parts.append(_TENS[t])
        if o:
            parts.append(_ONES[o][1 if feminine else 0])
    return " ".join(parts)


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    n = n % 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def amount_to_words_rub(amount) -> str:
    """Return e.g. «одна тысяча двести рублей 00 копеек»."""
    try:
        value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        value = Decimal("0.00")
    if value < 0:
        value = Decimal("0.00")
    rub = int(value)
    kop = int((value - rub) * 100)

    if rub == 0:
        rub_words = "ноль"
    else:
        chunks: list[str] = []
        billions, rem = divmod(rub, 1_000_000_000)
        millions, rem = divmod(rem, 1_000_000)
        thousands, ones = divmod(rem, 1000)
        if billions:
            chunks.append(_triad(billions, False))
            chunks.append(_plural(billions, ("миллиард", "миллиарда", "миллиардов")))
        if millions:
            chunks.append(_triad(millions, False))
            chunks.append(_plural(millions, ("миллион", "миллиона", "миллионов")))
        if thousands:
            chunks.append(_triad(thousands, True))
            chunks.append(_plural(thousands, ("тысяча", "тысячи", "тысяч")))
        if ones or not chunks:
            chunks.append(_triad(ones, False))
        rub_words = " ".join(p for p in chunks if p)

    rub_unit = _plural(rub, ("рубль", "рубля", "рублей"))
    kop_unit = _plural(kop, ("копейка", "копейки", "копеек"))
    return f"{rub_words} {rub_unit} {kop:02d} {kop_unit}"
