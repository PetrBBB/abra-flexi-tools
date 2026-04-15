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
    upper = text.upper()
    if "ČESKOSLOVENSKÁ OBCHODNÍ BANKA" in upper or "CSOB" in upper or "ČSOB" in upper:
        return "ČSOB"
    if "RAIFFEISENBANK" in upper or "RAIFFEISEN BANK" in upper:
        return "Raiffeisenbank"
    if "ČESKÁ SPOŘITELNA" in upper or "CESKA SPORITELNA" in upper:
        return "Česká spořitelna"
    return "Neznámá"


def extract_statement_meta(text: str, banka: str) -> Dict[str, str]:
    meta = {"rok": "", "mesic": "", "ucet_pdf": "", "nazev_uctu": ""}

    if banka == "ČSOB":
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

    elif banka == "Česká spořitelna":
        m = re.search(r"Období:\s*(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})", text)
        if m:
            meta["mesic"] = m.group(2)
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
        r"^(?P<datum>\d{2}\.\d{2}\.)\s+(?P<popis1>.+?)\s+(?P<ident>\d{4,6})\s+"
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


def clean_detail_line(line: str) -> str:
    return normalize_spaces(line).replace(";", ",")


def parse_csob_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str):
    first = parse_csob_first_line(block[0])
    if not first:
        return None
    den, mesic_txt = first["datum"].rstrip(".").split(".")
    datum = f"{int(rok):04d}-{int(mesic_txt):02d}-{int(den):02d}"
    castka = parse_amount_from_pdf(first["castka"])
    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)
    ident = first["ident"]
    lines = [clean_detail_line(x) for x in block[1:] if clean_detail_line(x)]
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
    popis_parts = [normalize_spaces(first["popis1"]).replace(";", ",")]
    if ucet_proti:
        popis_parts.append(f"Protiúčet {ucet_proti}")
    if vs:
        popis_parts.append(f"VS {vs}")
    if ident:
        popis_parts.append(f"ID {ident}")
    for extra in detail_texts[:3]:
        popis_parts.append(extra)
    popis = truncate_text(" | ".join(popis_parts), MAX_POPIS)
    return {
        "Interní číslo": f"CSOB-{int(rok):04d}-{int(mesic):02d}-{poradi:04d}",
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
    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if line == "Výpis pohybů":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("Zpráva pro klienta") or line.startswith("V rámci souhrnné položky"):
            break
        if any(line.startswith(p) for p in skip_prefixes):
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


def parse_rb_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str):
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
            nazev_protiuctu = radek3

    # Řádky 4+ jsou další poznámky
    for line in block[3:]:
        line = normalize_spaces(line)
        if line:
            detail_texts.append(line)

    # Sestavíme popis
    popis_parts = [kategorie]
    if typ_transakce:
        popis_parts.append(typ_transakce)
    if nazev_protiuctu:
        popis_parts.append(nazev_protiuctu)
    if ucet_proti:
        popis_parts.append(f"Protiúčet {ucet_proti}")
    if vs:
        popis_parts.append(f"VS {vs}")
    if ks:
        popis_parts.append(f"KS {ks}")
    for extra in detail_texts[:2]:
        if extra.strip():
            popis_parts.append(extra.strip())

    popis = truncate_text(" | ".join([p for p in popis_parts if p]), MAX_POPIS)

    return {
        "Interní číslo": f"RB-{int(rok):04d}-{int(mesic):02d}-{poradi:04d}",
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
    stop_prefixes = ("Konečný zůstatek:", "SHRNUTÍ POHYBŮ NA ÚČTU", "Typ Odepsáno z účtu")

    for raw in lines:
        line = normalize_spaces(raw)
        if not line:
            continue
        if "PŘEHLED POHYBŮ NA ÚČTU" in line:
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


def parse_csas_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str):
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
        if not is_pure_date_line(lines[0]) and not lines[0].startswith("Číslo instrukce:"):
            nazev_protiuctu = lines[0]
            rest_lines = lines[1:]
        else:
            rest_lines = lines
    else:
        rest_lines = []
    for line in rest_lines:
        if is_pure_date_line(line):
            continue
        if re.fullmatch(r"\d{4}", line) and not ks and typ.lower() == "trvalý příkaz":
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
        detail_texts.append(line)
    if nazev_protiuctu and not ucet_proti and typ.lower().startswith("vklad hotovosti"):
        detail_texts.insert(0, nazev_protiuctu)
        nazev_protiuctu = ""
    popis_parts = [typ]
    if nazev_protiuctu:
        popis_parts.append(nazev_protiuctu)
    if ucet_proti:
        popis_parts.append(f"Protiúčet {ucet_proti}")
    if vs:
        popis_parts.append(f"VS {vs}")
    if ks:
        popis_parts.append(f"KS {ks}")
    if ss:
        popis_parts.append(f"SS {ss}")
    for extra in detail_texts[:4]:
        if extra:
            popis_parts.append(extra)
    popis = truncate_text(" | ".join([p for p in popis_parts if p]), MAX_POPIS)
    return {
        "Interní číslo": f"CSAS-{int(rok):04d}-{int(mesic):02d}-{poradi:04d}",
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
    if banka == "ČSOB":
        blocks = split_csob_transaction_blocks(lines)
        for i, block in enumerate(blocks, start=1):
            row = parse_csob_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet)
            if row:
                rows.append(row)
            else:
                skipped += 1
    elif banka == "Raiffeisenbank":
        blocks = split_rb_transaction_blocks(lines)
        for i, block in enumerate(blocks, start=1):
            row = parse_rb_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet)
            if row:
                rows.append(row)
            else:
                skipped += 1
    elif banka == "Česká spořitelna":
        blocks = split_csas_transaction_blocks(lines)
        for i, block in enumerate(blocks, start=1):
            row = parse_csas_block(block, i, meta["rok"], meta["mesic"], typ_dokladu, bankovni_ucet)
            if row:
                rows.append(row)
            else:
                skipped += 1
    else:
        raise ValueError("Nepodporovaná banka")
    return rows, skipped


def rows_to_csv_bytes(rows: List[Dict[str, str]]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["Interní číslo", "Typ dokladu", "Bank.účet", "Typ pohybu", "Vystaveno",
                    "Částka osvob. bez DPH [Kč]", "Měna", "Variabilní symbol", "Popis"],
        delimiter=";",
    )
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


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
            <p class="header-ver">v2.5 · Česká spořitelna · ČSOB · Raiffeisenbank</p>
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
    st.markdown('<p class="step-label">Krok 2 — Nahrání PDF výpisu</p>', unsafe_allow_html=True)

    typ_dokladu = TYP_DOKLADU_DEFAULT

    uploaded_file = st.file_uploader(
        "Přetáhněte PDF výpis nebo klikněte pro výběr",
        type=["pdf"],
        label_visibility="visible",
    )

    if not uploaded_file:
        st.markdown('<div class="locked-box">⬆ Nahrajte PDF výpis pro pokračování</div>', unsafe_allow_html=True)
        st.stop()

    # ---- Zpracování ----
    pdf_bytes = uploaded_file.read()
    text = extract_text_from_pdf_bytes(pdf_bytes)
    banka = detect_bank(text)
    meta = extract_statement_meta(text, banka)

    if banka == "Neznámá":
        st.error("Nepodporovaná banka. Tato verze podporuje Českou spořitelnu, ČSOB a Raiffeisenbank.")
        st.stop()

    if not meta.get("rok") or not meta.get("mesic"):
        st.error("Nepodařilo se zjistit rok nebo měsíc z PDF.")
        st.stop()

    rows, skipped = parse_transactions(text, banka, meta, typ_dokladu, ucet_potvrzen)

    if not rows:
        st.error("Nepodařilo se zpracovat žádné položky. Zkontrolujte formát výpisu.")
        st.stop()

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Krok 3: Výsledek ----
    st.markdown('<p class="step-label">Krok 3 — Výsledek a stažení</p>', unsafe_allow_html=True)

    df = pd.DataFrame(rows)
    prijmy = df.loc[df["Typ pohybu"] == "typPohybu.prijem", "Částka osvob. bez DPH [Kč]"].astype(float).sum()
    vydaje = df.loc[df["Typ pohybu"] == "typPohybu.vydej", "Částka osvob. bez DPH [Kč]"].astype(float).sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Banka", banka)
    col2.metric("Položek", len(df))
    col3.metric("Příjmy", f"{prijmy:,.2f}".replace(",", " "))
    col4.metric("Výdaje", f"{vydaje:,.2f}".replace(",", " "))

    if skipped:
        st.warning(f"⚠️ {skipped} bloků se nepodařilo zpracovat. Zkontrolujte náhled.")

    st.dataframe(df, use_container_width=True, hide_index=True)

    bank_slug = (banka.lower()
                 .replace("č", "c").replace("š", "s").replace("ř", "r")
                 .replace("á", "a").replace("í", "i").replace("é", "e")
                 .replace("ů", "u").replace("ú", "u").replace(" ", "_"))
    csv_bytes = rows_to_csv_bytes(rows)
    filename = f"flexi_banka_import_{bank_slug}_{meta['rok']}_{meta['mesic']}.csv"

    st.download_button(
        "⬇  Stáhnout CSV pro ABRA Flexi",
        csv_bytes,
        filename,
        "text/csv",
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
