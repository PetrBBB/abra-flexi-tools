import csv
import io
import re
from typing import List, Dict, Optional, Tuple

import pandas as pd
import pdfplumber
import streamlit as st

TYP_DOKLADU_DEFAULT = "STANDARD"
MAX_POPIS = 255


def normalize_spaces(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_amount_from_pdf(s: str) -> float:
    s = s.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    return float(s)


def account_id(meta: Dict[str, str]) -> str:
    """Vrátí krátké ID účtu z meta pro použití v interním čísle."""
    ucet = meta.get("ucet_pdf", "")
    # Vezmeme jen číselnou část před lomítkem, max 10 znaků
    cislo = ucet.split("/")[0].replace("-", "").strip()
    return cislo[:10] if cislo else "UCET"


def truncate_text(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip()


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    texts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            texts.append(txt)
    return "\n".join(texts)


def detect_bank(text: str) -> str:
    # Nejdřív hlavička (první strana) - názvy bank se jinak mohou objevit
    # v popisu transakce (např. obchodník "CSOB 4203 ZDAR N.SAZ").
    head = _detect_bank_in(text[:1500])
    if head != "Neznámá":
        return head
    return _detect_bank_in(text)


def _detect_bank_in(text: str) -> str:
    upper = text.upper()
    if "ČESKOSLOVENSKÁ OBCHODNÍ BANKA" in upper or "CSOB" in upper or "ČSOB" in upper:
        return "ČSOB"
    if "RAIFFEISENBANK" in upper or "RAIFFEISEN BANK" in upper:
        return "Raiffeisenbank"
    if "FIO BANKA" in upper or "FIOBCZPP" in upper:
        return "Fio banka"
    if "KOMERČNÍ BANKA" in upper or "KOMBCZPP" in upper:
        return "Komerční banka"
    if "AIR BANK" in upper or "AIRACZPP" in upper:
        return "Air Bank"
    if "MONETA" in upper or "AGBACZPP" in upper:
        return "Moneta"
    if "ČESKÁ SPOŘITELNA" in upper or "CESKA SPORITELNA" in upper:
        return "Česká spořitelna"
    return "Neznámá"


def extract_statement_meta(text: str, banka: str) -> Dict[str, str]:
    meta = {"rok": "", "mesic": "", "ucet_pdf": "", "nazev_uctu": ""}

    if banka == "ČSOB":
        if "Vybrané pohyby na účtu" in text:
            meta["csob_format"] = "VYBRANE_POHYBY"
            m = re.search(r"Nastavený filtr:.*?do\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
            if m:
                meta["mesic"] = f"{int(m.group(2)):02d}"
                meta["rok"] = m.group(3)
            m = re.search(r"Číslo účtu:\s*([\d/]+)", text)
            if m:
                meta["ucet_pdf"] = m.group(1).strip()
            m = re.search(r"Název účtu:\s*([^\n]+)", text)
            if m:
                meta["nazev_uctu"] = m.group(1).strip()
        else:
            meta["csob_format"] = "STANDARD"
            m = re.search(r"Období:\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*-\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)
            if m:
                meta["mesic"] = f"{int(m.group(2)):02d}"
                meta["rok"] = m.group(3)
            m = re.search(r"Účet:\s*([^\n]+)", text)
            if m:
                meta["ucet_pdf"] = m.group(1).strip()
            m = re.search(r"Název účtu:\s*([^\n]+)", text)
            if m:
                meta["nazev_uctu"] = m.group(1).strip()

    elif banka == "Raiffeisenbank":
        m = re.search(r"za období:\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*-\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text, re.IGNORECASE)
        if m:
            meta["mesic"] = f"{int(m.group(2)):02d}"
            meta["rok"] = m.group(3)
        m = re.search(r"Číslo účtu:\s*([^\n]+)", text)
        if m:
            meta["ucet_pdf"] = m.group(1).strip()
        m = re.search(r"Název účtu:\s*([^\n]+)", text)
        if m:
            meta["nazev_uctu"] = m.group(1).strip()

    elif banka == "Komerční banka":
        if "VYPIS1_NDB" in text or "Výpis z účtu" in text[:500]:
            # NDB formát: "Výpis z účtu 1. 1. 2026 – 28. 2. 2026"
            meta["kb_format"] = "NDB"
            m = re.search(r"Výpis z účtu.*?–.*?(\d{1,2})\. (\d{1,2})\. (\d{4})", text)
            if m:
                meta["mesic"] = f"{int(m.group(2)):02d}"
                meta["rok"] = m.group(3)
            m = re.search(r"Číslo účtu\s+([\d-]+/\d{4})", text)
            if m:
                meta["ucet_pdf"] = m.group(1).strip()
        else:
            # Starý formát VÝPIS PERIODICKÝ
            meta["kb_format"] = "PERIODICKY"
            m = re.search(r"Za období:\s*(\d{2}\.\d{2})\. - (\d{2})\.(\d{2})\.(\d{4})", text)
            if m:
                meta["mesic"] = m.group(3)
                meta["rok"] = m.group(4)
            m = re.search(r"k účtu:\s*([\d-]+/\d{4})", text)
            if m:
                meta["ucet_pdf"] = m.group(1).strip()
            m = re.search(r"Číslo výpisu:\s*(\d+)", text)
            if m:
                meta["cislo_vypisu"] = f"{int(m.group(1)):02d}"

    elif banka == "Air Bank":
        m = re.search(r"Období výpisu:\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*-\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)
        if m:
            meta["mesic"] = f"{int(m.group(5)):02d}"
            meta["rok"] = m.group(6)
        m = re.search(r"Číslo účtu:\s*(\d[\d-]*\s*/\s*\d{4})", text)
        if m:
            meta["ucet_pdf"] = m.group(1).strip()
        m = re.search(r"Číslo výpisu:\s*(\d+)", text)
        if m:
            meta["cislo_vypisu"] = f"{int(m.group(1)):02d}"

    elif banka == "Moneta":
        # Běžný výpis: "Výpis ze dne: 31.12.2025"
        m = re.search(r"Výpis ze dne:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
        if m:
            meta["mesic"] = f"{int(m.group(2)):02d}"
            meta["rok"] = m.group(3)
        # Kreditní karta: "Výpis za období: 01.12.2025-31.12.2025" nebo "Výpis ke dni: 31.12.2025"
        if not meta.get("rok"):
            m = re.search(r"Výpis ke dni:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
            if m:
                meta["mesic"] = f"{int(m.group(2)):02d}"
                meta["rok"] = m.group(3)
        # Číslo výpisu — např. "2025/13" → použijeme jako součást ID
        m = re.search(r"Číslo výpisu:\s*(\d{4})/(\d{1,2})", text)
        if m:
            meta["cislo_vypisu"] = f"{int(m.group(2)):02d}"
        # Účet z běžného výpisu
        m = re.search(r"Bankovní spojení:\s*(\d[\d-]*\s*/\s*\d{4})", text)
        if m:
            meta["ucet_pdf"] = m.group(1).strip()
        # Účet z kreditní karty
        if not meta.get("ucet_pdf"):
            m = re.search(r"Na účet číslo\s*(\d+/\d+)", text)
            if m:
                meta["ucet_pdf"] = m.group(1).strip()
        meta["nazev_uctu"] = ""

    elif banka == "Fio banka":
        m = re.search(r"Výpis za období\s*(\d{1,2})\.(\d{1,2})\.(\d{4})-(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
        if m:
            meta["mesic"] = f"{int(m.group(2)):02d}"
            meta["rok"] = m.group(3)
        m = re.search(r"Číslo účtu:\s*([^\s]+)", text)
        if m:
            meta["ucet_pdf"] = m.group(1).strip()
        m = re.search(r"Měna účtu\s+(\S+)", text)
        if m:
            raw_mena = m.group(1).strip()
            # Normalizace: Kč → CZK, EUR → EUR atd.
            mena_map = {"Kč": "CZK", "kč": "CZK", "CZK": "CZK", "EUR": "EUR",
                        "USD": "USD", "CHF": "CHF", "GBP": "GBP"}
            meta["mena"] = mena_map.get(raw_mena, raw_mena.upper())
        else:
            meta["mena"] = "CZK"

    elif banka == "Česká spořitelna":
        # Podporuje formáty "01.01.2026" i "1.1.2026"
        m = re.search(r"Období:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
        if m:
            meta["mesic"] = f"{int(m.group(2)):02d}"
            meta["rok"] = m.group(3)
        m = re.search(r"Číslo účtu/kód banky:\s*([^\n]+?)\s+Číslo výpisu:", text)
        if m:
            meta["ucet_pdf"] = m.group(1).strip()
        m = re.search(r"Název účtu:\s*([^\n]+)", text)
        if m:
            meta["nazev_uctu"] = m.group(1).strip()

    return meta


# ---------------- ČSOB parser ----------------
def split_csob_transaction_blocks(lines: List[str]) -> List[List[str]]:
    blocks: List[List[str]] = []
    current: List[str] = []
    tx_start = re.compile(r"^\d{2}\.\d{2}\.\s+")

    skip_contains = [
        "VÝPIS Z ÚČTU", "Strana:", "Období:", "Účet:", "Název účtu:", "Datum", "Valuta",
        "Označení platby", "Protiúčet nebo poznámka", "Název protiúčtu", "VS KS SS",
        "Identifikace Částka Zůstatek", "Souhrnné informace", "Počet kreditních položek:",
        "Počet debetních položek:", "Počáteční zůstatek:", "Konečný zůstatek:",
        "Celkové příjmy:", "Celkové výdaje:", "Přehled pohybů na účtu",
        "Prosíme Vás o včasné překontrolování", "Pokud při zúčtování karetní transakce",
        "Vklad na tomto účtu podléhá ochraně", "Víte, že si u nás můžete půjčit",
        "Stačí říci a půjčku Vám vyřídíme", "Uvedené předschválené limity",
        "Československá obchodní banka", "zapsaná v obchodním rejstříku",
    ]

    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if any(x in line for x in skip_contains):
            continue
        if is_csob_junk_line(line):
            continue
        if tx_start.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_csob_first_line(line: str) -> Optional[Dict[str, str]]:
    m = re.match(
        r"^(?P<datum>\d{2}\.\d{2}\.)\s+(?P<popis1>.+?)\s+(?P<ident>\d{2,6})\s+"
        r"(?P<castka>-?\d{1,3}(?: \d{3})*,\d{2})\s+(?P<zustatek>-?\d{1,3}(?: \d{3})*,\d{2})$",
        line,
    )
    return m.groupdict() if m else None


def parse_account_and_symbols(line: str):
    m = re.search(r"(\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})", line)
    if not m:
        return "", "", "", ""
    ucet = m.group(1)
    rest = line[m.end():].strip()
    nums = re.findall(r"\b\d+\b", rest)
    vs = nums[0] if len(nums) >= 1 else ""
    ks = nums[1] if len(nums) >= 2 else ""
    ss = nums[2] if len(nums) >= 3 else ""
    return ucet, vs, ks, ss



def sestavit_popis(
    typ: str = "",           # typ transakce — jde NA KONEC
    nazev: str = "",         # název protiúčtu
    detaily: list = None,    # zprávy, poznámky — jdou NA ZAČÁTEK
    ucet_proti: str = "",    # číslo protiúčtu
    ks: str = "",
    ss: str = "",
    ident: str = "",
) -> str:
    """
    Sestaví popis v pořadí:
    1. Detaily/zprávy (nejdůležitější)
    2. Název protiúčtu
    3. Protiúčet (číslo)
    4. KS, SS
    5. ID
    6. Typ transakce (jen pro info, na konci)
    VS se záměrně vynechává — je v samostatném sloupci.
    """
    parts = []
    # 1. Detaily/zprávy
    for d in (detaily or []):
        d = d.strip()
        if d:
            parts.append(d)
    # 2. Název protiúčtu
    if nazev and nazev.strip():
        parts.append(nazev.strip())
    # 3. Číslo protiúčtu
    if ucet_proti:
        parts.append(f"Protiúčet {ucet_proti}")
    # 4. KS, SS
    if ks:
        parts.append(f"KS {ks}")
    if ss:
        parts.append(f"SS {ss}")
    # 5. ID
    if ident:
        parts.append(f"ID {ident}")
    # 6. Typ transakce — na konec
    if typ and typ.strip():
        t = typ.strip()
        # RB uvádí typ ve dvou sloupcích ("Platba kartou Platba kartou") - sloučit
        h = len(t) // 2
        if len(t) % 2 == 1 and t[:h] == t[h + 1:] and t[h] == " ":
            t = t[:h]
        parts.append(t)
    return truncate_text(" | ".join([p for p in parts if p]), MAX_POPIS)

# Fráze které se mohou vyskytnout v detailech ČSOB ale nejsou součástí transakce
_CSOB_SKIP_PHRASES = (
    "Vážená klientko", "vážený kliente", "Vážený kliente", "zasíláme Vám výpis",
    "informací o zůstatku", "Zkontrolujte si prosím", "Prosíme Vás o včasné",
    "Pokud při zúčtování karetní", "Vklad na tomto účtu podléhá",
    "o systému pojištění", "Víte, že si u nás", "Stačí říci a půjčku",
    "Uvedené předschválené", "předschválené limity",
    "zůstatek na účtu za Vámi", "Nesouhlasíte-li", "bez zbytečného odkladu",
    "Pokud tak neučiníte", "Děkujeme", "odsouhlasený",
    "kontaktujte nás", "považovat zůstatek",
)


def clean_detail_line(line: str) -> str:
    return normalize_spaces(line).replace(";", ",")


def is_csob_junk_line(line: str) -> bool:
    """Vrátí True pokud řádek obsahuje marketingový nebo systémový text ČSOB."""
    return any(phrase in line for phrase in _CSOB_SKIP_PHRASES)


def parse_csob_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    first = parse_csob_first_line(block[0])
    if not first:
        return None
    den, mesic_txt = first["datum"].rstrip(".").split(".")
    datum = f"{int(rok):04d}-{int(mesic_txt):02d}-{int(den):02d}"
    castka = parse_amount_from_pdf(first["castka"])
    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)
    ident = first["ident"]
    lines = [clean_detail_line(x) for x in block[1:]
             if clean_detail_line(x) and not is_csob_junk_line(x)]
    ucet_proti = ""
    vs = ""
    detail_texts = []
    for line in lines:
        found_ucet, found_vs, _, _ = parse_account_and_symbols(line)
        if found_ucet and not ucet_proti:
            ucet_proti = found_ucet
            if found_vs:
                vs = found_vs
        else:
            detail_texts.append(line)
    # Filtrujeme detail_texts: kódy karet (číselné bloky) pryč, zachováme "Místo:" a ostatní text
    clean_details = []
    for d in detail_texts[:4]:
        # Přeskočíme řádky které jsou jen čísla (kód karty, SS, ident)
        if re.match(r"^[\d\s]+$", d):
            continue
        # Přeskočíme řádky s číslem karty (XXXX vzor)
        if re.search(r"XXXXXXXXXXXX|\d{4}\s+\d{4}\s+\d{4}", d):
            continue
        clean_details.append(d)
    popis = sestavit_popis(
        typ=normalize_spaces(first["popis1"]).replace(";", ","),
        detaily=clean_details[:3],
        ucet_proti=ucet_proti,
        ident=ident,
    )
    return {
        "Interní číslo": f"CB-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }


# ---------------- Raiffeisenbank parser ----------------
# Skutečný formát pdfplumber (každá transakce = 3 řádky):
# Řádek 1: "2. 2. 2026 Platba Jednorázová úhrada 232026 -3 086.00 CZK"
# Řádek 2: "2. 2. 2026 6006604339/0800 VS:232026"
# Řádek 3: "8553644068 Bures 10-11/2025"

# Pomocné regexpy pro RB parser
_RB_CASTKA_SUFFIX = re.compile(r"\s+(-?\d{1,3}(?:\s\d{3})*\.\d{2})\s+CZK$")
_RB_DATUM_PREFIX = re.compile(r"^(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})\s+")


def _rb_je_hlavni_radek(line: str) -> bool:
    line = normalize_spaces(line)
    return bool(_RB_DATUM_PREFIX.match(line) and _RB_CASTKA_SUFFIX.search(line))


def split_rb_transaction_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    skip_prefixes = (
        "Datum Kategorie transakce", "Valuta Číslo protiúčtu", "Kód transakce Název protiúčtu",
    )
    skip_contains_rb = (
        "Raiffeisenbank a.s.,", "zapsaná v obchodním rejstříku",
        "Výpis z běžného účtu č.", "Pořadové č. výpisu:",
        "K0000810 v", "sp. zn. B 2051",
    )
    skip_exact_rb = re.compile(r"^Strana \d+/\d+$")
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line == "Výpis pohybů":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("Zpráva pro klienta"):
            break
        if line.startswith("V rámci souhrnné položky"):
            continue  # přeskočíme detail poplatků, ale pokračujeme dál
        if any(line.startswith(p) for p in skip_prefixes):
            continue
        if any(x in line for x in skip_contains_rb):
            continue
        if skip_exact_rb.match(line):
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        if _rb_je_hlavni_radek(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_rb_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    """
    Parsuje blok RB transakce:
    [0] "2. 2. 2026 Platba Jednorázová úhrada 232026 -3 086.00 CZK"
    [1] "2. 2. 2026 6006604339/0800 VS:232026 [KS]"
    [2] "8553644068 [název protiúčtu] [poznámka]"
    [3+] další poznámky (volitelné)
    """
    if not block:
        return None

    # Parsujeme hlavní řádek
    line0 = normalize_spaces(block[0])
    mc = _RB_CASTKA_SUFFIX.search(line0)
    md = _RB_DATUM_PREFIX.match(line0)
    if not mc or not md:
        return None
    datum_str = md.group(1)
    m_d = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$", datum_str)
    if not m_d:
        return None
    datum = f"{int(m_d.group(3)):04d}-{int(m_d.group(2)):02d}-{int(m_d.group(1)):02d}"

    castka_str = mc.group(1)
    zbytek = line0[md.end():mc.start()].strip()

    try:
        castka = parse_amount_from_pdf(castka_str)
    except ValueError:
        return None

    # Detekce poplatku — záporné číslo těsně před CZK částkou (zahraniční platby)
    m_poplatek = re.search(r"(-\d[\d.]+)\s*$", zbytek)
    if m_poplatek:
        try:
            poplatek = float(m_poplatek.group(1))
            if poplatek < 0:
                castka += poplatek  # přičteme záporný poplatek k výdaji
            zbytek = zbytek[:m_poplatek.start()].strip()
        except ValueError:
            pass

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)

    # Z "zbytku" vytáhneme kategorii, typ transakce a případně VS
    # Formát: "Platba Jednorázová úhrada 232026" nebo "Platba kartou Platba kartou"
    vs = ""
    ks = ""
    kategorie = ""
    typ_transakce = ""

    # VS = poslední token zbytku pokud je číslo
    parts = zbytek.split()
    if parts and re.match(r"^\d+$", parts[-1]):
        vs = parts[-1]
        parts = parts[:-1]
    else:
        vs = ""

    # Kategorie + typ ze zbývajících tokenů
    if len(parts) >= 2 and parts[1].lower() == "kartou":
        kategorie = "Platba kartou"
        typ_transakce = " ".join(parts[2:])
    else:
        kategorie = parts[0] if parts else ""
        typ_transakce = " ".join(parts[1:]) if len(parts) > 1 else ""

    # Parsujeme řádek 2: "2. 2. 2026 6006604339/0800 VS:232026 [KS]"
    ucet_proti = ""
    nazev_protiuctu = ""
    detail_texts = []

    # Filtr junk řádků z zápatí stránek RB
    _rb_junk = re.compile(
        r"^(?:\d{2}:\d{2}:\d{2}|\d{4}\.\d{2}\.\d{2}|K\d{7}\s+v\d|\d{8,}\s+•|[0-9A-Za-z]{6,10}K$|[0-9]+\.\d+v$)"
    )
    block = [block[0]] + [
        l for l in block[1:]
        if not _rb_junk.match(normalize_spaces(l))
        and not any(x in normalize_spaces(l) for x in ("K0000810", "sp. zn. B 2051", "Raiffeisenbank a.s.", "•"))
        and not re.match(r"^[\d\s]+$", normalize_spaces(l))
    ]

    if len(block) >= 2:
        radek2 = normalize_spaces(block[1])
        # Odstraníme datum valuta ze začátku
        radek2 = re.sub(r"^\d{1,2}\.\s*\d{1,2}\.\s*\d{4}\s*", "", radek2).strip()
        tokeny = radek2.split()
        for tok in tokeny:
            m_ucet = re.match(r"^(\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})$", tok)
            if m_ucet and not ucet_proti:
                ucet_proti = m_ucet.group(1)
            elif tok.startswith("VS:") and not vs:
                vs = tok[3:]
            elif tok.startswith("KS:") and not ks:
                ks = tok[3:]
            elif re.match(r"^\d{3,4}$", tok) and not ks:
                ks = tok

    # Parsujeme řádek 3: "8553644068 [název protiúčtu] [poznámka]"
    if len(block) >= 3:
        radek3 = normalize_spaces(block[2])
        # Odstraníme kód transakce (10 číslic) ze začátku
        radek3 = re.sub(r"^\d{10}\s*", "", radek3).strip()
        if radek3:
            # Může obsahovat název protiúčtu a poznámku oddělené mezerou
            # Odstraníme čísla z konce (SS, kódy) — název firmy je text
            nazev_protiuctu = re.sub(r"\s+\d{5,}\s*$", "", radek3).strip()
    # Řádky 4+ jsou další poznámky
    for line in block[3:]:
        line = normalize_spaces(line)
        if line:
            detail_texts.append(line)

    # Sestavíme popis
    # U RB: detaily mohou obsahovat zprávu + název firmy — oddělíme
    rb_detaily = [d for d in detail_texts[:2]
                  if not re.match(r"^[\d\s]+$", d)]
    popis = sestavit_popis(
        typ=f"{kategorie} {typ_transakce}".strip() if typ_transakce else kategorie,
        nazev=nazev_protiuctu,
        detaily=rb_detaily,
        ucet_proti=ucet_proti,
        ks=ks,
    )

    return {
        "Interní číslo": f"RB-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }






# ---------------- Fio banka parser ----------------
# Formát každé transakce:
# Řádek 1: "D.M.YYYY Typ operace [protiúčet] -částka,00"
# Řádek 2: "D.M.YYYY [upřesnění]"
# Řádek 3: "ID VS: xxx[, SS: xxx]"
# Řádek 4+: zpráva/popis (volitelné)

_FIO_HLAVNI_RE = re.compile(
    r"^(?P<datum>\d{1,2}\.\d{1,2}\.\d{4})\s+"
    r"(?P<typ>.+?)\s+"
    r"(?:(?P<ucet>\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})\s+)?"
    r"(?P<castka>-?\d[\d\s]*,\d{2})$"
)


def split_fio_transaction_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    skip_prefixes = (
        "Datum účtování", "Datum transakce", "ID operace", "Zpráva pro příjemce",
        "Uživatelský symbol", "Výpis operací", "Číslo účtu:", "Spisová značka:",
        "Výpis z účtu", "Majitel účtu:", "Vklad na tomto účtu", "dispozici na",
        "Fio banka, a.s.,", "vložka 2704", "===== konec sestavy =====",
    )
    skip_contains = ("z 2", "z 3", "z 4", "z 5")
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line == "Výpis operací":
            in_section = True
            continue
        if not in_section:
            continue
        if any(line.startswith(p) for p in skip_prefixes):
            continue
        if any(line.endswith(p) for p in skip_contains) and re.match(r"^\d+", line):
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        if _FIO_HLAVNI_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_fio_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET", mena: str = "CZK"):
    if not block:
        return None
    m = _FIO_HLAVNI_RE.match(normalize_spaces(block[0]))
    if not m:
        return None

    datum_str = m.group("datum")
    parts_d = datum_str.split(".")
    datum = f"{int(parts_d[2]):04d}-{int(parts_d[1]):02d}-{int(parts_d[0]):02d}"

    castka_str = m.group("castka").replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        castka = float(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)
    typ = normalize_spaces(m.group("typ") or "")
    ucet_proti = m.group("ucet") or ""

    vs = ""
    ks = ""
    ss = ""
    detail_texts = []

    for line in block[1:]:
        line = normalize_spaces(line)
        if not line:
            continue
        # Datum transakce nebo upřesnění — "D.M.YYYY text"
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}", line):
            rest = re.sub(r"^\d{1,2}\.\d{1,2}\.\d{4}\s*", "", line).strip()
            if rest and rest not in ("", typ):
                detail_texts.append(rest)
            continue
        # ID operace + symboly — "27422878972 VS: 600091914"
        m_id = re.match(r"^\d{8,}\s+(.*)", line)
        if m_id:
            sym = m_id.group(1).strip()
            m_vs = re.search(r"VS:\s*(\d+)", sym)
            if m_vs and not vs:
                vs = m_vs.group(1)
            m_ks = re.search(r"KS:\s*(\d+)", sym)
            if m_ks and not ks:
                ks = m_ks.group(1)
            m_ss = re.search(r"SS:\s*(\d+)", sym)
            if m_ss and not ss:
                ss = m_ss.group(1)
            continue
        # Zpráva / popis
        # Kód transakce (8+ číslic bez jiného obsahu) do popisu nepatří
        if re.match(r"^\d{8,}$", line):
            continue
        if not line.startswith("Nákup:") or not detail_texts:
            detail_texts.append(line)

    _FIO_TECH = ("Trvalý příkaz", "Okamžitá odchozí platba", "Okamžitá příchozí platba",
                  "Bezhotovostní vklad", "Příjem převodem")
    fio_detaily = [d for d in detail_texts[:3]
                   if d and not any(d.startswith(t) for t in _FIO_TECH)]
    popis = sestavit_popis(
        typ=typ,
        detaily=fio_detaily,
        ucet_proti=ucet_proti,
        ks=ks,
        ss=ss,
    )

    return {
        "Interní číslo": f"FI-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": mena,
        "Variabilní symbol": vs,
        "Popis": popis,
    }




# ---------------- Komerční banka parser ----------------
# Formát každé transakce:
# Řádek 1: "DD.MM.YYYY TYP [název protiúčtu] [VS] [±částka]"
# Řádek 2: "ID_transakce [protiúčet] [KS]"
# Řádek 3: "identifikace"
# Řádek 4+: "Zpráva pro příjemce:" + zpráva (volitelné)

_KB_HLAVNI_DATUM = re.compile(r"^(\d{2}\.\d{2}\.\d{4})\s+(.+)")
_KB_CASTKA_RE = re.compile(r"(?<!\d)(-?\d{1,3}(?:\s\d{3})*,\d{2})$")
_KB_UCET_RE = re.compile(r"^(\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})$")
_KB_ID_RE = re.compile(r"^[A-Z0-9]{8,}(\s+\d+)?$")  # kód transakce KB

_KB_SKIP = (
    "POČÁTEČNÍ ZŮSTATEK", "KONEČNÝ ZŮSTATEK", "Rekapitulace transakcí",
    "Celkový počet transakcí", "Obraty na účtu", "Úroky od začátku roku",
    "Srážková daň od začátku roku", "Zůstatek podle data",
    "Datum Popis transakce", "zúčtování Identifikace", "Datum SS", "transakce",
    "Pokračování na další straně", "Komer", "Praha 1, Na", "ZAPSANÁ V OBCHODNÍM",
    "Vklad na tomto účtu je pojištěn", "Děkujeme za využívání",
    "VÝPIS PERIODICKÝ", "k účtu:", "IBAN:", "typ:", "měna:", "Datum výpisu:",
    "Číslo výpisu:", "Strana:", "Zaslání:", "Frekvence:", "Za období:",
    "BIC / SWIFT", "tel.:", "www.kb.cz", "Dobrý den,",
    "rádi bychom Vás", "Se všemi navrh", "pobočkách. Vaše",
)


def split_kb_transaction_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line.startswith("POČÁTEČNÍ ZŮSTATEK"):
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("KONEČNÝ ZŮSTATEK"):
            break
        if any(line.startswith(p) for p in _KB_SKIP):
            continue
        if any(x in line for x in ("45317054", "VLOŽKA 1360")):
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        md = _KB_HLAVNI_DATUM.match(line)
        mc = _KB_CASTKA_RE.search(line)
        if md and mc:
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_kb_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    if not block:
        return None
    line0 = normalize_spaces(block[0])
    md = _KB_HLAVNI_DATUM.match(line0)
    mc = _KB_CASTKA_RE.search(line0)
    if not md or not mc:
        return None

    datum_str = md.group(1)
    datum = f"{datum_str[6:10]}-{datum_str[3:5]}-{datum_str[0:2]}"

    castka_str = mc.group(1).replace(" ", "").replace(",", ".")
    try:
        castka = float(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)

    # Ze zbytku hlavního řádku: typ + [název] + [VS]
    zbytek = line0[md.end(1)+1:mc.start()].strip()
    tokens = zbytek.split()

    vs = ""
    nazev = ""
    typ = ""
    ucet_proti = ""
    ks = ""
    ss = ""
    zpravy = []

    # VS = poslední token pokud je čistě číselný
    if tokens and re.match(r"^\d+$", tokens[-1]):
        vs = tokens[-1]
        tokens = tokens[:-1]

    # Přesné typy KB transakcí — vždy 2-3 slova na začátku
    _KB_TYPY = (
        "OKAMŽITÁ ODCHOZÍ ÚHRADA", "OKAMŽITÁ PŘÍCHOZÍ ÚHRADA",
        "ODCHOZÍ ÚHRADA", "PŘÍCHOZÍ ÚHRADA",
        "TRVALÝ PŘÍKAZ", "INKASO", "VÝBĚR HOTOVOSTI",
        "PŘÍCHOZÍ PLATBA", "ODCHOZÍ PLATBA",
    )
    zbytek_str = " ".join(tokens)
    typ = ""
    nazev_po_typu = zbytek_str
    for kb_typ in _KB_TYPY:
        if zbytek_str.upper().startswith(kb_typ):
            typ = zbytek_str[:len(kb_typ)].strip()
            nazev_po_typu = zbytek_str[len(kb_typ):].strip()
            break
    if not typ:
        typ = zbytek_str  # fallback

    # Protiúčet může být v nazev_po_typu
    for tok in nazev_po_typu.split():
        if _KB_UCET_RE.match(tok) and not ucet_proti:
            ucet_proti = tok
            nazev_po_typu = nazev_po_typu.replace(tok, "").strip()
            break

    nazev = nazev_po_typu.strip(" -")

    # Zpracujeme řádek 2: "ID [protiúčet] [KS] [SS]"
    for line in block[1:]:
        line = normalize_spaces(line)
        if not line:
            continue
        if line == "Zpráva pro příjemce:":
            continue
        # Identifikace transakce — přeskočíme
        if re.match(r"^\d{3}-\d{8}", line) or re.match(r"^\d{3}-\d{8}", line):
            continue
        # Řádek s ID transakce + protiúčtem
        m_id = re.match(r"^([A-Z0-9]{6,})\s*(\d+)?\s*(\S+)?", line)
        if m_id and not re.search(r"[a-záčďéěíňóřšťúůýž]", line):
            # Může obsahovat protiúčet a KS
            toks = line.split()
            for tok in toks:
                m_u = _KB_UCET_RE.match(tok)
                if m_u and not ucet_proti:
                    ucet_proti = tok
                elif re.match(r"^\d{1,3}$", tok) and not ks:
                    ks = tok
                elif re.match(r"^\d{10,}$", tok):
                    pass  # identifikace transakce
            continue
        # Zpráva / název
        zpravy.append(line)

    popis = sestavit_popis(
        typ=typ,
        nazev=nazev,
        detaily=zpravy[:3],
        ucet_proti=ucet_proti,
        ks=ks,
        ss=ss,
    )

    cislo_vypisu = mesic  # mesic může být cislo_vypisu pro KB
    return {
        "Interní číslo": f"KB-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(cislo_vypisu):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }


# ---------------- Komerční banka NDB parser ("Výpis z účtu") ----------------
# Formát každé transakce:
# Řádek 1: "D. M. YYYY [Název] [protiúčet] [-]X XXX,XX Kč"
# Řádek 2: "Datum provedení Kód transakce Typ transakce VS SS KS" (přeskočit)
# Řádek 3: "D. M. YYYY kód typ VS SS KS"
# Řádek 4+: "Zpráva pro příjemce: ...", "Popis pro mě: ...", nebo plain text

_KB_NDB_HEADER = re.compile(
    r"^(?P<datum>\d{1,2}\.\s*\d{1,2}\.\s*\d{4})\s+"
    r"(?P<rest>(?!-\d)[^\n]+?\s+)?"  # jméno/protiúčet — nesmí začínat zápornou částkou
    r"(?P<castka>-\d{1,3}(?:\s\d{3})*,\d{2}|\d{1,3}(?:\s\d{3})*,\d{2})\s+Kč$"
)

_KB_NDB_DETAIL_HDR = re.compile(
    r"^Datum provedení Kód transakce"
)
_KB_NDB_DETAIL_RE = re.compile(
    r"^\d{1,2}\.\s*\d{1,2}\.\s*\d{4}\s+\S+\s+\S"
)

_KB_NDB_SKIP = (
    "Komerční banka, a. s., se sídlem:",
    "Zapsaná v obchodním rejstříku",
    "Code: VYPIS1_NDB",
    "Celkový počet transakcí",
    "Úroková sazba",
    "Vklad na tomto účtu",
    "k dispozici na",
    "Výpis z účtu",
    "Datum výpisu",
    "Informace o účtu",
    "Hlavní měna účtu",
    "Zůstatky", "Česká koruna",
    "Nezahrnuje limit",
    "MILOŠ", "Podnikající pod",
    "Sídlo:", "IČO:", "Zápis v",
)


def split_kb_ndb_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line == "Transakce":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("Celkový počet transakcí"):
            break
        if any(line.startswith(p) for p in _KB_NDB_SKIP):
            continue
        if re.match(r"^\d+/\d+$", line):  # stránky "1/7"
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        if _KB_NDB_HEADER.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_kb_ndb_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    if not block:
        return None
    m = _KB_NDB_HEADER.match(normalize_spaces(block[0]))
    if not m:
        return None

    datum_str = m.group("datum").replace(" ", "")  # "2.1.2026"
    casti = datum_str.split(".")
    try:
        datum = f"{casti[2]}-{int(casti[1]):02d}-{int(casti[0]):02d}"
    except (IndexError, ValueError):
        return None

    castka_str = m.group("castka").replace(" ", "").replace(",", ".")
    try:
        castka = float(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)

    # Ze zbytku hlavního řádku: [Název] [protiúčet]
    rest = (m.group("rest") or "").strip()
    tokens = rest.split()
    ucet_proti = ""
    nazev_tokens = []
    for tok in tokens:
        if _KB_UCET_RE.match(tok) and not ucet_proti:
            ucet_proti = tok
        elif re.match(r"^-\d+$", tok):
            # Zbytek záporné části částky (artifact lazy regex) — ignorujeme
            pass
        else:
            nazev_tokens.append(tok)
    nazev = " ".join(nazev_tokens).strip()

    # Z detailního řádku: typ transakce a VS
    typ = ""
    vs = ""
    ks = ""
    ss = ""
    zpravy = []

    skip_next = False
    for line in block[1:]:
        line = normalize_spaces(line)
        if not line:
            continue
        if _KB_NDB_DETAIL_HDR.match(line):
            skip_next = True  # přeskočíme záhlaví tabulky
            continue
        if skip_next:
            # Datový řádek: "D. M. YYYY kód_transakce typ VS SS KS"
            skip_next = False
            toks = line.split()
            i = 0
            # Přeskočíme datum (D. M. YYYY) — tokeny ve tvaru "dd." "mm." "yyyy"
            while i < len(toks) and (re.match(r"^\d{1,2}\.$", toks[i]) or
                                      (i > 0 and re.match(r"^\d{4}$", toks[i]))):
                i += 1
            # Kód transakce (alfanumerický, min 5 znaků)
            if i < len(toks) and re.match(r"^[A-Z0-9.]{5,}$", toks[i]):
                i += 1
            # Typ transakce — slova dokud nenarazíme na číslo nebo -
            typ_words = []
            while i < len(toks) and not re.match(r"^(\d+|-)$", toks[i]):
                typ_words.append(toks[i])
                i += 1
            typ = " ".join(typ_words)
            # VS, SS, KS — zbývající (přeskočíme "-" a "0000000000")
            remaining = [t for t in toks[i:] if t not in ("-", "0000000000")]
            if remaining and not vs:
                vs = remaining[0]
            if len(remaining) > 2 and not ks:
                ks = remaining[2] if remaining[2] not in ("0", "0000000000") else ""
            continue

        # ATM info — přeskočíme
        if re.match(r"^(HLINSKO|ATM ID:|bankomatu)", line):
            continue
        # Zpráva pro příjemce / Popis pro mě
        if line.startswith("Zpráva pro příjemce:"):
            z = line[len("Zpráva pro příjemce:"):].strip()
            if z:
                zpravy.append(z)
            continue
        if line.startswith("Popis pro mě:"):
            z = line[len("Popis pro mě:"):].strip()
            if z:
                zpravy.append(z)
            continue
        # Jiný text — může to být zpráva bez prefixu (PLATBA, KAVALÍR, ...)
        if not re.match(r"^\d+/\d+$", line) and not re.match(r"^\d{1,2}\.", line):
            zpravy.append(line)

    # VS z 0000000000 vyčistíme
    vs = vs if vs and vs not in ("0000000000", "0") else ""
    ks = ks if ks and ks not in ("0000000000", "0") else ""
    ss = ss if ss and ss not in ("0000000000", "0") else ""

    popis = sestavit_popis(
        typ=typ,
        nazev=nazev,
        detaily=zpravy[:2],
        ucet_proti=ucet_proti,
        ks=ks,
        ss=ss,
    )

    return {
        "Interní číslo": f"KB-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }

# ---------------- Air Bank parser ----------------
# Formát každé transakce (2 řádky):
# Řádek 1: "DD.MM.YYYY Typ [Název/VS] částka,xx 0,00"
# Řádek 2: "DD.MM.YYYY kód_transakce [protiúčet] [detaily]"
# Řádek 3+: volitelné detaily (zpráva, AV:, ...)

_AB_HLAVNI_RE = re.compile(
    r"^(?P<datum>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<typ>[A-Za-záčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][\w\s]+?)\s+"
    r"(?P<zbytek>.+?)\s+"
    r"(?P<castka>-?\d{1,3}(?:\s\d{3})*,\d{2})\s+"
    r"0,00$"
)

_AB_SKIP = (
    "Zaúčtování Typ", "Provedení Kód transakce", "Pokračování na straně",
    "Air Bank a.s.", "Společnost zapsaná", "Vklad na tomto účtu",
    "o systému pojištění", "Informační přehled", "I banku můžete mít",
    "www.airbank.cz",
)
_AB_SKIP_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$|^\d/\d$|^\d+/\d+$")


def split_airbank_transaction_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line.startswith("Zaúčtování Typ"):
            in_section = True
            continue
        if not in_section:
            continue
        if any(line.startswith(p) for p in _AB_SKIP):
            continue
        if _AB_SKIP_RE.match(line):
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        if _AB_HLAVNI_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_airbank_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    if not block:
        return None
    m = _AB_HLAVNI_RE.match(normalize_spaces(block[0]))
    if not m:
        return None

    datum_str = m.group("datum")
    datum = f"{datum_str[6:10]}-{datum_str[3:5]}-{datum_str[0:2]}"

    castka_str = m.group("castka").replace(" ", "").replace(",", ".")
    try:
        castka = float(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)
    typ = normalize_spaces(m.group("typ"))
    zbytek = normalize_spaces(m.group("zbytek"))
    typ = normalize_spaces(m.group("typ"))

    # Pokud typ je jednoslovný a zbytek začíná malým písmenem, přidáme první slovo zbytku k typu
    # "Příchozí" + "úhrada ..." → "Příchozí úhrada"
    zbytek_slova = zbytek.split()
    if (len(typ.split()) == 1 and zbytek_slova
            and zbytek_slova[0][0].islower()):
        typ = typ + " " + zbytek_slova[0]
        zbytek = " ".join(zbytek_slova[1:]).strip()

    # Ze zbytku prvního řádku vytáhneme VS a název
    vs = ""
    ks = ""
    ss = ""
    nazev = ""

    m_vs = re.search(r"VS(\d+)", zbytek)
    if m_vs:
        vs = m_vs.group(1)
    m_ks = re.search(r"KS(\d+)", zbytek)
    if m_ks:
        ks = m_ks.group(1)
    m_ss = re.search(r"SS(\d+)", zbytek)
    if m_ss:
        ss = m_ss.group(1)

    # Zbytek bez VS/KS/SS je název protiúčtu
    nazev = re.sub(r"VS\d+\s*/?|KS\d+\s*/?|SS\d+\s*/?", "", zbytek).strip(" /")
    nazev = normalize_spaces(nazev)

    # Druhý řádek: kód_transakce, protiúčet, detaily
    ucet_proti = ""
    detail_texts = []

    if len(block) >= 2:
        radek2 = normalize_spaces(block[1])
        # Odstraníme datum z začátku
        radek2 = re.sub(r"^\d{2}\.\d{2}\.\d{4}\s+", "", radek2).strip()
        # Odstraníme kód transakce (12 číslic)
        radek2 = re.sub(r"^\d{12}\s*", "", radek2).strip()
        # Protiúčet
        m_ucet = re.match(r"^(\d{1,6}-\d{1,10}\s*/\s*\d{4}|\d{1,16}\s*/\s*\d{4}|\d{6}-\d{10}\s*/\s*\d{4})", radek2)
        if m_ucet:
            ucet_proti = m_ucet.group(1).replace(" ", "")
            radek2 = radek2[m_ucet.end():].strip()
        # Zbytek jsou detaily
        if radek2:
            # Hledáme VS/KS/SS pokud ještě nemáme
            if not vs:
                mv = re.search(r"VS(\d+)", radek2)
                if mv:
                    vs = mv.group(1)
            detail_texts.append(re.sub(r"VS\d+\s*/?|KS\d+\s*/?|SS\d+\s*/?", "", radek2).strip(" /"))

    # Další řádky jako detaily
    for line in block[2:]:
        line = normalize_spaces(line)
        if line and not line.startswith("Pokračování") and "Air Bank" not in line:
            detail_texts.append(line)

    popis = sestavit_popis(
        typ=typ,
        nazev=nazev,
        detaily=detail_texts[:2],
        ucet_proti=ucet_proti,
        ks=ks,
        ss=ss,
    )

    # Číslo výpisu pro unikátní ID — použijeme mesic (který může být cislo_vypisu)
    return {
        "Interní číslo": f"AB-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }


# ---------------- ČSOB "Vybrané pohyby na účtu" parser (filtrovaný report) ----------------
# Formát každé transakce (1+ řádků):
# Řádek 1: "DD.MM.YYYY [Název protiúčtu] Popis [-]X XXX,XX X XXX,XX" (částka + zůstatek)
# Řádek 2: "[protiúčet] [VS] [KS] [SS]" nebo "Místo: ... Částka: ..."
# Řádek 3+: další detaily (Místo:, Částka:, adresa)

_CSOB_VP_HEADER = re.compile(
    r"^(?P<datum>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<rest>.+?)\s+"
    r"(?P<castka>-?\d{1,3}(?:\s\d{3})*,\d{2})\s+"
    r"(?P<zustatek>\d{1,3}(?:\s\d{3})*,\d{2})$"
)

_CSOB_VP_SKIP = (
    "Vybrané pohyby na účtu", "Ze dne:", "Číslo účtu:", "Měna:", "Název účtu:",
    "Nastavený filtr:", "Datum Název protiúčtu", "Protiúčet nebo poznámka",
    "Disclaimer", "Nejedná se o výpis", "Dokument generován",
    "Československá obchodní banka", "Radlická 333/150", "zapsaná v obchodním",
)

_CSOB_VP_UCET_RE = re.compile(r"^\d{1,6}-\d{1,10}/\d{4}$|^\d{1,16}/\d{4}$")


def split_csob_vp_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line.startswith("Datum Název protiúčtu"):
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("Disclaimer"):
            break
        if any(line.startswith(p) for p in _CSOB_VP_SKIP):
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        if _CSOB_VP_HEADER.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_csob_vp_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    if not block:
        return None
    m = _CSOB_VP_HEADER.match(normalize_spaces(block[0]))
    if not m:
        return None

    datum_str = m.group("datum")
    datum = f"{datum_str[6:10]}-{datum_str[3:5]}-{datum_str[0:2]}"

    castka_str = m.group("castka").replace(" ", "").replace(",", ".")
    try:
        castka = float(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)

    rest = m.group("rest").strip()
    # Rest může obsahovat [Název protiúčtu] + Popis transakce.
    # Rozdělíme podle známých klíčových slov popisu transakce.
    _CSOB_VP_TYP_KEYWORDS = (
        "Příchozí úhrada SEPA", "Odchozí úhrada SEPA",
        "Příchozí úhrada", "Odchozí úhrada",
        "Trvalý příkaz elektronicky", "Transakce platební kartou",
        "Poplatek-platební karta", "Platba kartou na poště",
        "Za vedení účtu",
    )
    typ = rest
    nazev = ""
    for kw in _CSOB_VP_TYP_KEYWORDS:
        idx = rest.find(kw)
        if idx > 0:
            nazev = rest[:idx].strip()
            typ = rest[idx:].strip()
            break
        elif idx == 0:
            typ = rest
            break

    ucet_proti = ""
    vs = ""
    ks = ""
    zpravy = []

    for line in block[1:]:
        line = normalize_spaces(line)
        if not line:
            continue
        # Protiúčet + VS/KS/SS na jednom řádku (číselné tokeny)
        toks = line.split()
        if toks and all(re.match(r"^[\d/-]+$", t) for t in toks):
            for t in toks:
                if _CSOB_VP_UCET_RE.match(t) and not ucet_proti:
                    ucet_proti = t
                elif re.match(r"^\d{4,}$", t) and not vs and not _CSOB_VP_UCET_RE.match(t):
                    vs = t
                elif re.match(r"^\d{1,3}$", t) and not ks:
                    ks = t
            continue
        # Detaily: "Místo: ...", "Částka: ...", adresa
        if line.startswith("Místo:"):
            zpravy.append(line[len("Místo:"):].strip())
            continue
        if line.startswith("Částka:") or line.startswith("Číslo transakce:") or line.startswith("BIC:") \
           or line.startswith("Reference příkazce:") or line.startswith("Identifikace:") \
           or line.startswith("Částka, měna"):
            continue  # technické detaily nepotřebujeme v popisu
        # Zbylý text (adresa, poznámka) — jen pokud není čistě technický
        if not re.match(r"^[A-Z]{2}\d{10,}$", line):  # IBAN
            zpravy.append(line)

    popis = sestavit_popis(
        typ=typ,
        nazev=nazev,
        detaily=zpravy[:2],
        ucet_proti=ucet_proti,
        ks=ks,
    )

    return {
        "Interní číslo": f"CB-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }

# ---------------- Moneta Money Bank parser ----------------
# Formát každé transakce (3+ řádky):
# Řádek 1: "DD.MM.YYYY [protiúčet] kód_transakce [VS] [- ]částka,00"
# Řádek 2: "popis datum_zaúčtování [KS]"
# Řádek 3: "valuta"
# Řádek 4+: "KI: ...", "AV: ..." (volitelné)

# Moneta: regex pro datum a pro částku (parsujeme token po tokenu)
_MONETA_DATUM_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{4})\s+(.*)")
_MONETA_CASTKA_RE = re.compile(r"(?<!\d)(-?\s*\d{1,3}(?:\s\d{3})*,\d{2})$")
_MONETA_UCET_RE = re.compile(r"^(\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})\s+(.*)")
_MONETA_KOD_TRANS_RE = re.compile(r"^[A-Z0-9]{15,}$")  # kód transakce: 15+ alfanum


def _moneta_parse_hlavni(line: str):
    """Parsuje první řádek Moneta transakce. Vrátí dict nebo None."""
    m_d = _MONETA_DATUM_RE.match(line)
    if not m_d:
        return None
    datum = m_d.group(1)
    rest = m_d.group(2).strip()

    m_c = _MONETA_CASTKA_RE.search(rest)
    if not m_c:
        return None
    castka_str = m_c.group(1).strip()
    rest = rest[:m_c.start()].strip()

    ucet = ""
    m_u = _MONETA_UCET_RE.match(rest)
    if m_u:
        ucet = m_u.group(1)
        rest = m_u.group(2).strip()

    tokens = rest.split()
    vs = ""
    kod_found = False
    popis_tokens = []
    for tok in tokens:
        if _MONETA_KOD_TRANS_RE.match(tok) and re.search(r"[A-Z]", tok) and not kod_found:
            kod_found = True
        elif kod_found and re.match(r"^\d{4,12}$", tok) and not vs:
            vs = tok
        elif not kod_found:
            popis_tokens.append(tok)

    popis = " ".join(popis_tokens)
    return {"datum": datum, "ucet": ucet, "popis": popis, "vs": vs, "castka": castka_str}

_MONETA_SKIP = (
    "Výpis z běžného účtu", "Číslo výpisu:", "Výpis ze dne:", "Předchozí výpis",
    "Periodicita výpisu:", "Strana:", "Informace o účtu", "Obchodní místo:",
    "NÁM.", "Bankovní spojení:", "Označení měny:", "Číslo účtu IBAN:",
    "SWIFT kód BIC:", "IČO:", "Souhrnné zúčtování", "Počáteční zůstatek:",
    "Obrat kredit:", "Obrat debet:", "Konečný zůstatek", "Roční kreditní",
    "Roční debetní", "Vklad na tomto účtu", "z vkladů je k dispozici",
    "Přehled transakcí", "Datum Bankovní", "zpracování / Popis", "Valuta odepsání",
    "výpis pokračuje", "www.moneta.cz", "MONETA Money Bank", "IČO: 25672720",
    "Celkový počet transakcí", "Součet obratů", "Žádáme Vás",
    "591 01", "592 02", "Táborská", "Martin Tichý", "Naevia", "Forest",
)


# Regex pro transakci na kreditní kartě Monety:
# "DD.MM.YYYY popis [- ]částka,xx"
_MONETA_CC_RE = re.compile(
    r"^(?P<datum>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<popis>.+?)\s+"
    r"(?P<castka>-?\s*\d{1,3}(?:\s\d{3})*,\d{2})$"
)

_MONETA_CC_SKIP = (
    "TICHÝ", "MARTIN", "karta číslo", "OSTATNÍ ÚHRADY",
)


def _je_moneta_cc_radek(line: str) -> bool:
    """Vrátí True pokud jde o transakci kreditní karty."""
    return bool(_MONETA_CC_RE.match(line))


def split_moneta_transaction_blocks(lines: List[str]) -> List[List[str]]:
    relevant = []
    in_section = False
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if "Přehled transakcí" in line:
            in_section = True
            continue
        if not in_section:
            continue
        if any(line.startswith(p) for p in _MONETA_SKIP):
            continue
        if any(x in line for x in ("Zapsáno u MS", "odd. B, vl.", "zákaznický servis",
                                    "Výpis ke kreditní", "karta číslo", "OSTATNÍ ÚHRADY",
                                    "Datum Popis transakce", "zaúčtování / Bankovní",
                                    "valuta Cizí", "výpis pokračuje")):
            continue
        if re.match(r"^TICHÝ .+ - karta", line):
            continue
        relevant.append(line)

    blocks = []
    current = []
    for line in relevant:
        if _moneta_parse_hlavni(line) is not None or _MONETA_CC_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_moneta_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    if not block:
        return None
    line0 = normalize_spaces(block[0])
    parsed = _moneta_parse_hlavni(line0)
    # Zkusíme CC formát pokud hlavní selže
    if not parsed:
        m_cc = _MONETA_CC_RE.match(line0)
        if m_cc:
            parsed = {
                "datum": m_cc.group("datum"),
                "ucet": "",
                "popis": m_cc.group("popis"),
                "vs": "",
                "castka": m_cc.group("castka"),
            }
    if not parsed:
        return None

    datum_str = parsed["datum"]
    datum = f"{datum_str[6:10]}-{datum_str[3:5]}-{datum_str[0:2]}"

    castka_str = parsed["castka"].replace(" ", "").replace(",", ".")
    try:
        castka = float(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)

    ucet_proti = parsed["ucet"]
    vs = parsed["vs"]
    popis_typ = parsed["popis"]
    ks = ""
    detail_texts = []

    for line in block[1:]:
        line = normalize_spaces(line)
        if not line:
            continue
        # Datum zaúčtování / KS — "15.12.2025 7618" nebo "22.12.2025"
        m2 = re.match(r"^(\S+)\s+(\d{2}\.\d{2}\.\d{4})\s*(\d{4})?$", line)
        if m2:
            if not popis_typ:
                popis_typ = m2.group(1)
            if m2.group(3) and not ks:
                ks = m2.group(3)
            continue
        # Samotné datum valuta nebo datum + KS
        if re.match(r"^\d{2}\.\d{2}\.\d{4}(\s+\d{1,4})?$", line):
            # Zachytíme KS pokud je za datem — ale ne rok (4 číslice > 1900)
            m_ks = re.search(r"\s+(\d{1,4})$", line)
            if m_ks and not ks:
                ks_val = m_ks.group(1)
                if not (len(ks_val) == 4 and int(ks_val) > 1900):
                    ks = ks_val
            continue
        # KS na samostatném řádku
        if re.match(r"^\d{4}$", line) and not ks:
            ks = line
            continue
        # KI: nebo AV: — přeskočíme
        if line.startswith("KI:") or line.startswith("AV:"):
            continue
        # Popis / název protiúčtu — odstraníme datum na konci
        m3 = re.match(r"^(.+?)\s+\d{2}\.\d{2}\.\d{4}(\s+\d{4})?$", line)
        if m3:
            clean = m3.group(1).strip()
            if m3.group(2) and not ks:
                ks = m3.group(2).strip()
            if not popis_typ:
                popis_typ = clean
            else:
                detail_texts.append(clean)
        elif not popis_typ:
            popis_typ = line
        else:
            detail_texts.append(line)

    popis = sestavit_popis(
        typ=popis_typ or "Platba",
        detaily=detail_texts[:2],
        ucet_proti=ucet_proti,
        ks=ks,
    )

    return {
        "Interní číslo": f"MM-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }

# ---------------- Česká spořitelna parser ----------------
def is_csas_start_line(line: str) -> bool:
    """Hlavní řádek ČSAS transakce — musí začínat datem A obsahovat částku na konci.
    Výjimka: 'Ceny za služby' začíná nový blok i bez částky."""
    line = normalize_spaces(line)
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}\s+", line):
        return False
    if re.match(r"^\d{2}\.\d{2}\.\d{4}\s+Ceny za služby", line):
        return True
    return bool(re.search(r'[+-]\d[\d\s]*\.\d{2}$', line))


# Regex pro řádek poplatku uvnitř bloku "Ceny za služby":
# Např.: "Cena za vedení účtu Za vedení Firemního účtu ČS (01.01.2026 - 31.01.2026) -92.00"
# Nebo:  "Cena za vedení účtu -92.00"
_CSAS_POPLATEK_RE = re.compile(
    r"^(?P<nazev>Cena za [^-+\d]+?)\s+(?P<popis>.+?\s+)?(?P<castka>-\d[\d\s]*\.\d{2})$"
)


def parse_csas_poplatek_line(line: str) -> Optional[Dict[str, str]]:
    """
    Parsuje řádek poplatku ve formátu:
    'Cena za vedení účtu Za vedení Firemního účtu ČS (01.01.2026...) -92.00'
    Vrátí dict s 'nazev', 'popis', 'castka' nebo None.
    """
    line = normalize_spaces(line)
    # Hledáme zápornou částku na konci řádku
    m = re.search(r"(-\d[\d\s\xa0]*\.\d{2})$", line)
    if not m:
        return None
    castka_str = m.group(1)
    prefix = line[:m.start()].strip()
    # Prefix musí začínat "Cena za"
    if not prefix.lower().startswith("cena za"):
        return None
    return {"nazev": prefix, "castka": castka_str}


def split_csas_transaction_blocks(lines: List[str]) -> List[List[str]]:
    """
    Rozdělí řádky výpisu ČS na transakční bloky.
    Speciálně zpracuje blok 'Ceny za služby' — každý poplatek
    se stane samostatným blokem s datem z hlavičky.
    """
    blocks = []
    current = []
    in_section = False
    skip_prefixes = (
        "Zaúčtováno", "Provedeno", "Položka", "Částka obratu cizí měny", "Popis",
        "Číslo protiúčtu", "Název protiúčtu", "Kurz měny obratu / Kurz měny účtu",
        "Variabilní symbol", "Konstantní symbol", "Specifický symbol", "Částka",
        "Výpis z účtu", "Podnikatelský účet", "Firemní účet", "Číslo účtu/kód banky:",
        "Česká spořitelna, a.s.", "Pokračování na další straně", "strana ",
        "SBVY", "SBVPXML_", "M|EL|",
        "zapsaná v obchodním", "zapsána v obchodním",
    )
    stop_prefixes = ("Konečný zůstatek:", "KONEČNÝ ZŮSTATEK:", "SHRNUTÍ POHYBŮ NA ÚČTU", "Typ Odepsáno z účtu", "PŘEHLED POHLEDÁVEK")

    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if "PŘEHLED POHYBŮ NA ÚČTU" in line or "Přehled pohybů na účtu od" in line:
            in_section = True
            continue
        if not in_section:
            continue
        if any(line.startswith(p) for p in stop_prefixes):
            # Uložíme poslední blok před ukončením
            if current:
                blocks.append(current)
                current = []
            break
        if any(line.startswith(p) for p in skip_prefixes):
            continue
        if is_csas_start_line(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)

    if current:
        blocks.append(current)

    # Rozložíme bloky "Ceny za služby" na samostatné položky
    expanded = []
    for block in blocks:
        expanded.extend(_expand_csas_poplatky(block))

    return expanded


def _expand_csas_poplatky(block: List[str]) -> List[List[str]]:
    """
    Pokud je blok 'Ceny za služby', rozloží každý poplatek
    na samostatný syntetický blok s datem z hlavičky.
    Jinak vrátí původní blok beze změny.
    """
    if not block:
        return [block]

    first = normalize_spaces(block[0])

    # Zkontrolujeme, zda první řádek je "DD.MM.YYYY Ceny za služby" (bez částky)
    m = re.match(r"^(\d{2}\.\d{2}\.\d{4})\s+Ceny za služby\s*$", first)
    if not m:
        return [block]

    datum = m.group(1)
    result = []

    # Procházíme zbytek bloku a hledáme řádky poplatků
    # Struktura: "Cena za vedení účtu" na jednom řádku, popis na dalším, pak prázdný řádek "Cena za službu Transakce" atd.
    # Alternativně vše na jednom řádku.
    i = 1
    while i < len(block):
        line = normalize_spaces(block[i])
        # Přeskočíme prázdné řádky nebo řádky jen s datem
        if not line or re.match(r"^\d{2}\.\d{2}\.\d{4}$", line):
            i += 1
            continue

        parsed = parse_csas_poplatek_line(line)
        if parsed:
            # Poplatek je celý na jednom řádku (závorka s datem může být na dalším)
            synth = f"{datum} {parsed['nazev']} {parsed['castka']}"
            result.append([synth])
            i += 1
            # Přeskočíme případný následující řádek se závorkou "(DD.MM.YYYY - DD.MM.YYYY)"
            if i < len(block) and re.match(r"^\(", normalize_spaces(block[i])):
                i += 1
        elif line.lower().startswith("cena za"):
            # Název poplatku je na tomto řádku, částka může být na dalším
            nazev = line
            # Hledáme částku — může být na stejném řádku (pak by to zachytil parse_csas_poplatek_line)
            # nebo na jednom z následujících řádků jako samostatné číslo
            popis_radky = []
            j = i + 1
            castka_found = None
            while j < len(block):
                next_line = normalize_spaces(block[j])
                if not next_line:
                    j += 1
                    continue
                # Je to částka na samostatném řádku?
                m_castka = re.match(r"^(-\d[\d\s\xa0]*\.\d{2})$", next_line)
                if m_castka:
                    castka_found = m_castka.group(1)
                    j += 1
                    break
                # Je to záporná částka na konci jinak textového řádku?
                m_castka2 = re.search(r"(-\d[\d\s\xa0]*\.\d{2})$", next_line)
                if m_castka2 and next_line.lower().startswith("cena za"):
                    # Začíná nový poplatek — ukončíme tento
                    break
                if m_castka2:
                    # Popis s částkou na konci
                    castka_found = m_castka2.group(1)
                    popis = next_line[:m_castka2.start()].strip()
                    if popis:
                        popis_radky.append(popis)
                    j += 1
                    break
                # Jinak je to popis
                if not next_line.lower().startswith("cena za"):
                    popis_radky.append(next_line)
                    j += 1
                else:
                    break

            if castka_found:
                popis_str = " | ".join(popis_radky) if popis_radky else ""
                synth_line = f"{datum} {nazev}"
                if popis_str:
                    synth_line += f" | {popis_str}"
                synth_line += f" {castka_found}"
                result.append([synth_line])
                i = j
            else:
                # Nepodařilo se parsovat — přeskočíme
                i += 1
        else:
            i += 1

    return result if result else [block]


def parse_csas_start_line(line: str):
    line = normalize_spaces(line)
    m = re.match(
        r"^(?P<datum>\d{2}\.\d{2}\.\d{4})\s+(?P<typ>.+?)\s+(?:(?P<ucet>\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})\s+)?(?:(?P<vs>\d+)\s+)?(?P<castka>[+-]?\d[\d\s\xa0]*\.\d{2})$",
        line,
    )
    return m.groupdict() if m else None


def is_pure_date_line(line: str) -> bool:
    return bool(re.match(r"^\d{2}\.\d{2}\.\d{4}$", normalize_spaces(line)))


def parse_csas_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str, ucet_id: str = "UCET"):
    first = parse_csas_start_line(block[0])
    if not first:
        return None
    datum = first["datum"][6:10] + "-" + first["datum"][3:5] + "-" + first["datum"][0:2]
    castka = parse_amount_from_pdf(first["castka"])
    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)
    ucet_proti = first.get("ucet") or ""
    vs = first.get("vs") or ""
    typ = first.get("typ") or ""
    lines = [normalize_spaces(x).replace(";", ",") for x in block[1:] if normalize_spaces(x)]
    nazev_protiuctu = ""
    detail_texts = []
    ks = ""
    ss = ""
    if lines:
        if (not is_pure_date_line(lines[0])
                and not lines[0].startswith("Číslo instrukce:")
                and not re.fullmatch(r"\d{1,5}", lines[0])):
            nazev_protiuctu = lines[0]
            rest_lines = lines[1:]
        else:
            rest_lines = lines
    else:
        rest_lines = []
    for line in rest_lines:
        if is_pure_date_line(line):
            continue
        if line.startswith("Číslo instrukce:"):
            continue  # nepotřebná interní informace
        # Izolované číslice jsou KS/SS — zachytíme ale nepřidáváme do detailů
        if re.fullmatch(r"\d{1,5}", line):
            if not ks:
                ks = line
            continue
        mvs = re.search(r"\bVS[: ]?(\d+)\b", line)
        if mvs and not vs:
            vs = mvs.group(1)
        mks = re.search(r"\bKS[: ]?(\d+)\b", line)
        if mks and not ks:
            ks = mks.group(1)
        mss = re.search(r"\bSS[: ]?(\d+)\b", line)
        if mss and not ss:
            ss = mss.group(1)
        # Izolované číslice (KS/SS bez prefixu) do popisu nepatří
        if re.fullmatch(r"\d{1,5}", line):
            continue
        detail_texts.append(line)
    if nazev_protiuctu and not ucet_proti and typ.lower().startswith("vklad hotovosti"):
        detail_texts.insert(0, nazev_protiuctu)
        nazev_protiuctu = ""
    popis = sestavit_popis(
        typ=typ,
        nazev=nazev_protiuctu,
        detaily=detail_texts[:4],
        ucet_proti=ucet_proti,
        ks=ks,
        ss=ss,
    )
    return {
        "Interní číslo": f"CS-{ucet_id[-6:]}-{int(rok)%100:02d}-{int(mesic):02d}-{poradi:03d}",
        "Typ dokladu": typ_dokladu,
        "Bank.účet": bankovni_ucet,
        "Typ pohybu": typ_pohybu,
        "Vystaveno": datum,
        "Částka osvob. bez DPH [Kč]": f"{castka_abs:.2f}",
        "Měna": "CZK",
        "Variabilní symbol": vs,
        "Popis": popis,
    }


def parse_transactions(text: str, banka: str, meta: Dict[str, str], typ_dokladu: str, bankovni_ucet: str):
    lines = text.splitlines()
    rows = []
    skipped = 0
    # Odvozujeme ucet_id z čísla účtu v PDF pro unikátní interní čísla
    ucet_raw = meta.get("ucet_pdf", "")
    ucet_id = ucet_raw.split("/")[0].replace("-", "").strip()[:10] or "UCET"
    if banka == "ČSOB":
        if meta.get("csob_format") == "VYBRANE_POHYBY":
            blocks = split_csob_vp_blocks(lines)
            for i, block in enumerate(blocks, start=1):
                row = parse_csob_vp_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet, ucet_id)
                if row:
                    rows.append(row)
                else:
                    skipped += 1
        else:
            blocks = split_csob_transaction_blocks(lines)
            for i, block in enumerate(blocks, start=1):
                row = parse_csob_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet, ucet_id)
                if row:
                    rows.append(row)
                else:
                    skipped += 1
    elif banka == "Raiffeisenbank":
        blocks = split_rb_transaction_blocks(lines)
        for i, block in enumerate(blocks, start=1):
            row = parse_rb_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet, ucet_id)
            if row:
                rows.append(row)
            else:
                skipped += 1
        blocks = split_kb_transaction_blocks(lines)
        kb_mesic = meta.get("cislo_vypisu") or meta["mesic"]
        for i, block in enumerate(blocks, start=1):
            row = parse_kb_block(block, i, meta["rok"], kb_mesic, typ_dokladu, bankovni_ucet, ucet_id)
            if row:
                rows.append(row)
            else:
                skipped += 1

    elif banka == "Komerční banka":
        if meta.get("kb_format") == "NDB":
            blocks = split_kb_ndb_blocks(lines)
            kb_mesic = meta.get("cislo_vypisu") or meta["mesic"]
            for i, block in enumerate(blocks, start=1):
                row = parse_kb_ndb_block(block, i, meta["rok"], kb_mesic, typ_dokladu, bankovni_ucet, ucet_id)
                if row:
                    rows.append(row)
                else:
                    skipped += 1
        else:
            blocks = split_kb_transaction_blocks(lines)
            kb_mesic = meta.get("cislo_vypisu") or meta["mesic"]
            for i, block in enumerate(blocks, start=1):
                row = parse_kb_block(block, i, meta["rok"], kb_mesic, typ_dokladu, bankovni_ucet, ucet_id)
                if row:
                    rows.append(row)
                else:
                    skipped += 1
    elif banka == "Air Bank":
        blocks = split_airbank_transaction_blocks(lines)
        ab_mesic = meta.get("cislo_vypisu") or meta["mesic"]
        for i, block in enumerate(blocks, start=1):
            row = parse_airbank_block(block, i, meta["rok"], ab_mesic, typ_dokladu, bankovni_ucet, ucet_id)
            if row:
                rows.append(row)
            else:
                skipped += 1

    elif banka == "Moneta":
        blocks = split_moneta_transaction_blocks(lines)
        # Pro Monetu použijeme číslo výpisu místo měsíce (mohou být 2 výpisy ve stejném měsíci)
        moneta_mesic = meta.get("cislo_vypisu") or meta["mesic"]
        for i, block in enumerate(blocks, start=1):
            row = parse_moneta_block(block, i, meta["rok"], moneta_mesic, typ_dokladu, bankovni_ucet, ucet_id)
            if row:
                rows.append(row)
            else:
                skipped += 1

    elif banka == "Fio banka":
        blocks = split_fio_transaction_blocks(lines)
        fio_mena = meta.get("mena", "CZK")
        for i, block in enumerate(blocks, start=1):
            row = parse_fio_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet, ucet_id, fio_mena)
            if row:
                rows.append(row)
            else:
                skipped += 1

    elif banka == "Česká spořitelna":
        blocks = split_csas_transaction_blocks(lines)
        for i, block in enumerate(blocks, start=1):
            row = parse_csas_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet, ucet_id)
            if row:
                rows.append(row)
            else:
                skipped += 1
    else:
        raise ValueError("Nepodporovaná banka")
    return rows, skipped


def rows_to_csv_bytes(rows: List[Dict[str, str]]) -> bytes:
    # Detekujeme zda jsou v datech cizí měna
    meny = {r.get("Měna", "CZK") for r in rows}
    ma_cizi_menu = bool(meny - {"CZK"})

    if ma_cizi_menu:
        # Pro cizí měnu použijeme sloupec "Osvob., bez DPH [měna]"
        castka_sloupec = "Osvob., bez DPH [měna]"
        fieldnames = ["Interní číslo", "Typ dokladu", "Bank.účet", "Typ pohybu", "Vystaveno",
                      castka_sloupec, "Měna", "Variabilní symbol", "Popis"]
        # Přejmenujeme klíč v každém řádku
        rows_out = []
        for r in rows:
            row2 = dict(r)
            row2[castka_sloupec] = row2.pop("Částka osvob. bez DPH [Kč]")
            rows_out.append(row2)
    else:
        fieldnames = ["Interní číslo", "Typ dokladu", "Bank.účet", "Typ pohybu", "Vystaveno",
                      "Částka osvob. bez DPH [Kč]", "Měna", "Variabilní symbol", "Popis"]
        rows_out = rows

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=";")
    writer.writeheader()
    writer.writerows(rows_out)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def zpracuj_pdf(nazev_souboru: str, pdf_bytes: bytes, typ_dokladu: str, bankovni_ucet: str) -> Dict:
    """Zpracuje jeden PDF výpis. Nikdy nevyhazuje výjimku - chybu vrátí v poli 'chyba'."""
    vysl = {"soubor": nazev_souboru, "banka": "", "meta": {}, "rows": [], "skipped": 0, "chyba": ""}
    try:
        text = extract_text_from_pdf_bytes(pdf_bytes)
        banka = detect_bank(text)
        vysl["banka"] = banka
        if banka == "Neznámá":
            vysl["chyba"] = "Nepodporovaná banka"
            return vysl
        meta = extract_statement_meta(text, banka)
        vysl["meta"] = meta
        if not meta.get("rok") or not meta.get("mesic"):
            vysl["chyba"] = "Nepodařilo se zjistit rok nebo měsíc"
            return vysl
        rows, skipped = parse_transactions(text, banka, meta, typ_dokladu, bankovni_ucet)
        vysl["rows"], vysl["skipped"] = rows, skipped
        if not rows:
            vysl["chyba"] = "Nepodařilo se zpracovat žádné položky"
    except Exception as e:  # jeden vadný soubor nesmí shodit celou dávku
        vysl["chyba"] = f"Chyba při zpracování: {e}"
    return vysl


def _cislo_uctu_klic(meta: Dict) -> str:
    """Normalizované číslo účtu z PDF (bez mezer, měny) pro porovnání výpisů."""
    u = (meta.get("ucet_pdf") or "").upper()
    m = re.search(r"[\d-]{4,}\s*/\s*\d{4}", u)
    if m:
        return re.sub(r"\s", "", m.group(0)).lstrip("0")
    return re.sub(r"\s", "", u)


def poradi_klic(v: Dict) -> Tuple:
    """Chronologické řazení: účet, rok, měsíc, číslo výpisu, nejstarší datum pohybu."""
    meta = v.get("meta", {})
    try:
        rok = int(meta.get("rok") or 0)
    except ValueError:
        rok = 0
    try:
        mesic = int(meta.get("mesic") or 0)
    except ValueError:
        mesic = 0
    try:
        cislo = int(meta.get("cislo_vypisu") or 0)
    except ValueError:
        cislo = 0
    datumy = [r.get("Vystaveno", "") for r in v.get("rows", []) if r.get("Vystaveno")]
    nejstarsi = min(datumy) if datumy else ""
    return (v.get("banka", ""), _cislo_uctu_klic(meta), rok, mesic, cislo, nejstarsi, v.get("soubor", ""))


def _fmt_kc(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def _soucty(rows: List[Dict]) -> Tuple[float, float]:
    pr = sum(float(r["Částka osvob. bez DPH [Kč]"]) for r in rows if r["Typ pohybu"] == "typPohybu.prijem")
    vy = sum(float(r["Částka osvob. bez DPH [Kč]"]) for r in rows if r["Typ pohybu"] == "typPohybu.vydej")
    return pr, vy


def _slug(t: str) -> str:
    tab = str.maketrans("čšřáíéůúžýťďňěČŠŘÁÍÉŮÚŽÝŤĎŇĚ ", "csraieuuzytdneCSRAIEUUZYTDNE_")
    return re.sub(r"[^A-Za-z0-9_-]", "", t.translate(tab)).lower()


def _obdobi(meta: Dict) -> str:
    o = f"{meta.get('mesic', '?')}/{meta.get('rok', '?')}"
    if meta.get("cislo_vypisu"):
        o += f" (výpis č. {int(meta['cislo_vypisu'])})"
    return o


def main():
    st.set_page_config(page_title="PDF výpis → ABRA Flexi", page_icon="🏦", layout="centered")

    # ---- CSS ----
    st.markdown("""
    <style>
    .header-box {
        background: #ffffff;
        border: 1px solid #e0e4ea;
        border-radius: 12px;
        padding: 1rem 1.5rem;
        margin-bottom: 1.5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .header-firma {
        font-size: 17px;
        font-weight: 600;
        color: #1a3a5c;
        margin: 0;
    }
    .header-firma-sub {
        font-size: 12px;
        color: #6b7a8d;
        margin: 2px 0 0 0;
    }
    .header-app {
        font-size: 14px;
        font-weight: 500;
        color: #1a3a5c;
        text-align: right;
        margin: 0;
    }
    .header-ver {
        font-size: 11px;
        color: #9aa3ad;
        text-align: right;
        margin: 2px 0 0 0;
    }
    .step-label {
        font-size: 11px;
        font-weight: 600;
        color: #6b7a8d;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        margin-bottom: 6px;
    }
    .hint-box {
        background: #fff8e6;
        border: 1px solid #f0c040;
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 13px;
        color: #7a5500;
        margin-top: 10px;
        line-height: 1.6;
    }
    .locked-box {
        background: #f7f8fa;
        border: 1px dashed #d0d5dd;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        color: #9aa3ad;
        font-size: 14px;
    }
    div[data-testid="stDownloadButton"] button {
        background-color: #1a3a5c !important;
        color: white !important;
        border: none !important;
        width: 100%;
    }
    div[data-testid="stDownloadButton"] button:hover {
        background-color: #254d78 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ---- Header ----
    st.markdown("""
    <div class="header-box">
        <div>
            <p class="header-firma">Účetnictví BP s.r.o.</p>
            <p class="header-firma-sub">www.ucto-bures.cz</p>
        </div>
        <div>
            <p class="header-app">PDF výpis → ABRA Flexi</p>
            <p class="header-ver">v6.0 · ČS · ČSOB · RB · Fio · Moneta · Air Bank · KB</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- Krok 1: Bank. účet ----
    st.markdown('<p class="step-label">Krok 1 — Bankovní účet ve Flexi</p>', unsafe_allow_html=True)

    col_input, col_btn = st.columns([3, 1])
    with col_input:
        bankovni_ucet = st.text_input(
            "Zkratka bankovního účtu",
            value="BANKOVNÍ ÚČET",
            label_visibility="collapsed",
        )
    with col_btn:
        krok1_ok = st.button("✓  Potvrdit", use_container_width=True, type="primary")

    st.markdown("""
    <div class="hint-box">
        ⚠️ <strong>Ověřte zkratku před pokračováním!</strong> Ve Flexi: <strong>Banka → Nový filtr → Bank.účet</strong><br>
        V poli filtru uvidíte např. <strong>BANKOVNÍ ÚČET</strong>: 7748901003/5500 — opište část <strong>před dvojtečkou</strong>.<br>
        Pokud máte více účtů, zkratka se může lišit. Musí přesně odpovídat, jinak import selže.
    </div>
    """, unsafe_allow_html=True)

    # Uložení potvrzené zkratky do session state
    if krok1_ok:
        if bankovni_ucet.strip():
            st.session_state["bankovni_ucet_ok"] = bankovni_ucet.strip()
        else:
            st.warning("Zadejte zkratku bankovního účtu před potvrzením.")

    ucet_potvrzen = st.session_state.get("bankovni_ucet_ok", "")

    if ucet_potvrzen:
        st.success(f"✓ Bankovní účet nastaven: **{ucet_potvrzen}**")
    else:
        st.markdown('<div class="locked-box">⬆ Nejprve potvrďte zkratku bankovního účtu</div>', unsafe_allow_html=True)
        st.stop()

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Krok 2: Nahrání PDF ----
    st.markdown('<p class="step-label">Krok 2 — Nahrání PDF výpisů</p>', unsafe_allow_html=True)

    typ_dokladu = TYP_DOKLADU_DEFAULT

    uploaded_files = st.file_uploader(
        "Přetáhněte jeden nebo více PDF výpisů (např. celý rok najednou)",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="visible",
    )

    if not uploaded_files:
        st.markdown('<div class="locked-box">⬆ Nahrajte PDF výpis(y) pro pokračování</div>', unsafe_allow_html=True)
        st.stop()

    # ---- Zpracování všech souborů ----
    vysledky = []
    progress = st.progress(0.0, text="Zpracovávám výpisy…") if len(uploaded_files) > 1 else None
    for i, uf in enumerate(uploaded_files, start=1):
        vysledky.append(zpracuj_pdf(uf.name, uf.getvalue(), typ_dokladu, ucet_potvrzen))
        if progress:
            progress.progress(i / len(uploaded_files), text=f"Zpracovávám výpisy… {i}/{len(uploaded_files)}")
    if progress:
        progress.empty()

    ok = [v for v in vysledky if not v["chyba"]]
    chybne = [v for v in vysledky if v["chyba"]]

    # Seřadit chronologicky bez ohledu na pořadí nahrání
    ok.sort(key=poradi_klic)

    # Duplicitní výpisy (stejný soubor nahraný 2×) — podle interních čísel
    videna_cisla = set()
    duplicitni = []
    unikatni = []
    for v in ok:
        cisla = {r["Interní číslo"] for r in v["rows"]}
        if cisla & videna_cisla:
            duplicitni.append(v)
        else:
            videna_cisla |= cisla
            unikatni.append(v)
    ok = unikatni

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Krok 3: Výsledek ----
    st.markdown('<p class="step-label">Krok 3 — Výsledek a stažení</p>', unsafe_allow_html=True)

    for v in chybne:
        st.error(f"❌ **{v['soubor']}** — {v['chyba']}. Tento soubor nebude v CSV.")
    for v in duplicitni:
        st.warning(f"⚠️ **{v['soubor']}** — stejný výpis už je v dávce (duplicita), přeskakuji.")

    if not ok:
        st.stop()

    # Kontroly dávky
    ucty = {(v["banka"], _cislo_uctu_klic(v["meta"])) for v in ok}
    if len(ucty) > 1:
        st.warning(
            "⚠️ **Výpisy jsou z více různých účtů.** Všem položkám se přiřadí stejná zkratka "
            f"Bank.účet **{ucet_potvrzen}**. Pokud mají jít na různé účty ve Flexi, "
            "nahrajte každý účet zvlášť (nebo použijte CSV jednotlivých výpisů níže)."
        )
    meny = {r.get("Měna", "CZK") for v in ok for r in v["rows"]}
    if len(meny) > 1:
        st.warning(f"⚠️ Dávka obsahuje více měn ({', '.join(sorted(meny))}). Doporučuji importovat každou měnu zvlášť.")

    # Mezery v řadě měsíců u jednoho účtu
    if len(ucty) == 1 and len(ok) > 1:
        mesice = sorted({(int(v["meta"]["rok"]), int(v["meta"]["mesic"])) for v in ok})
        chybi = []
        for (r1, m1), (r2, m2) in zip(mesice, mesice[1:]):
            r, m = r1, m1 + 1
            while (r, m) < (r2, m2):
                if m > 12:
                    r, m = r + 1, 1
                    continue
                chybi.append(f"{m:02d}/{r}")
                m += 1
        if chybi:
            st.info(f"ℹ️ V řadě výpisů chybí měsíc(e): {', '.join(chybi)}. Pokud to tak má být, ignorujte.")

    # Přehled výpisů v pořadí, jak půjdou do CSV
    if len(ok) > 1:
        prehled = []
        for poradi, v in enumerate(ok, start=1):
            pr, vy = _soucty(v["rows"])
            prehled.append({
                "#": poradi,
                "Soubor": v["soubor"],
                "Banka": v["banka"],
                "Účet": v["meta"].get("ucet_pdf", ""),
                "Období": _obdobi(v["meta"]),
                "Položek": len(v["rows"]),
                "Příjmy": _fmt_kc(pr),
                "Výdaje": _fmt_kc(vy),
                "Nezprac. bloků": v["skipped"] or "",
            })
        st.markdown("**Pořadí výpisů** (seřazeno chronologicky, nezáleží na pořadí nahrání):")
        st.dataframe(pd.DataFrame(prehled), use_container_width=True, hide_index=True)

    rows = [r for v in ok for r in v["rows"]]
    skipped = sum(v["skipped"] for v in ok)
    df = pd.DataFrame(rows)
    prijmy, vydaje = _soucty(rows)
    banky = sorted({v["banka"] for v in ok})
    banka_txt = ", ".join(banky)
    pocet_txt = f"{len(ok)} výpis" + ("y" if 2 <= len(ok) <= 4 else ("ů" if len(ok) >= 5 else ""))

    st.markdown(f"""
    <div style="display:grid; grid-template-columns: repeat(4,1fr); gap:10px; margin-bottom:1rem;">
        <div style="background:#f7f8fa; border-radius:8px; padding:0.75rem 1rem;">
            <div style="font-size:12px; color:#6b7a8d; margin-bottom:4px;">Banka</div>
            <div style="font-size:15px; font-weight:600; color:#1a3a5c;">{banka_txt}</div>
            <div style="font-size:12px; color:#6b7a8d; margin-top:2px;">{pocet_txt}</div>
        </div>
        <div style="background:#f7f8fa; border-radius:8px; padding:0.75rem 1rem;">
            <div style="font-size:12px; color:#6b7a8d; margin-bottom:4px;">Položek</div>
            <div style="font-size:22px; font-weight:600; color:#1a3a5c;">{len(df)}</div>
        </div>
        <div style="background:#f0f7f0; border-radius:8px; padding:0.75rem 1rem;">
            <div style="font-size:12px; color:#6b7a8d; margin-bottom:4px;">Příjmy [Kč]</div>
            <div style="font-size:15px; font-weight:600; color:#1a6e35; word-break:break-all;">{_fmt_kc(prijmy)}</div>
        </div>
        <div style="background:#fff4f4; border-radius:8px; padding:0.75rem 1rem;">
            <div style="font-size:12px; color:#6b7a8d; margin-bottom:4px;">Výdaje [Kč]</div>
            <div style="font-size:15px; font-weight:600; color:#a01e1e; word-break:break-all;">{_fmt_kc(vydaje)}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if skipped:
        st.warning(f"⚠️ {skipped} bloků se nepodařilo zpracovat. Zkontrolujte náhled.")

    st.dataframe(df, use_container_width=True, hide_index=True)

    prvni, posledni = ok[0]["meta"], ok[-1]["meta"]
    bank_slug = _slug(banky[0]) if len(banky) == 1 else "vice_bank"
    if len(ok) == 1:
        filename = f"flexi_banka_import_{bank_slug}_{prvni['rok']}_{prvni['mesic']}.csv"
    else:
        filename = (f"flexi_banka_import_{bank_slug}_{prvni['rok']}_{prvni['mesic']}"
                    f"-{posledni['rok']}_{posledni['mesic']}.csv")

    st.download_button(
        "⬇  Stáhnout CSV pro ABRA Flexi" + (f" (všech {len(ok)} výpisů v jednom)" if len(ok) > 1 else ""),
        rows_to_csv_bytes(rows),
        filename,
        "text/csv",
        use_container_width=True,
    )

    if len(ok) > 1:
        with st.expander("CSV jednotlivých výpisů zvlášť"):
            for poradi, v in enumerate(ok, start=1):
                m = v["meta"]
                fn = f"flexi_banka_import_{_slug(v['banka'])}_{m['rok']}_{m['mesic']}"
                if m.get("cislo_vypisu"):
                    fn += f"_c{int(m['cislo_vypisu'])}"
                st.download_button(
                    f"{poradi}. {_obdobi(m)} — {v['soubor']}",
                    rows_to_csv_bytes(v["rows"]),
                    fn + ".csv",
                    "text/csv",
                    key=f"dl_{poradi}_{v['soubor']}",
                    use_container_width=True,
                )

    with st.expander("Jak importovat do ABRA Flexi"):
        st.markdown("""
1. Stáhni CSV soubor tlačítkem výše.
2. Ve Flexi: **Nástroje → Import → Import z Excelu → Banka**
3. Nejdřív klikni **Vyzkoušet import** — zkontroluj výsledek.
4. Pokud je vše v pořádku, potvrď import.
        """)


if __name__ == "__main__":
    main()
