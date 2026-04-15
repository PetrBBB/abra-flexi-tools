import csv
import io
import re
from typing import List, Dict, Optional, Tuple

import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="PDF výpis -> ABRA Flexi", page_icon="🏦", layout="wide")

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
# Nový formát RB výpisu (2025+):
# Řádek 1: "2. 2. 2026"          ← datum zaúčtování
# Řádek 2: "2. 2. 2026"          ← datum valuta
# Řádek 3: "8553644068"           ← kód transakce
# Řádek 4: "Platba"              ← kategorie
# Řádek 5: "6006604339/0800"     ← protiúčet
# Řádek 6: "Jednorázová úhrada"  ← typ transakce
# Řádek 7: "VS:232026"           ← zpráva / VS
# Řádek 8: "Bures 10-11/2025"   ← poznámka
# Řádek 9: "232026 -3 086.00 CZK" ← VS + částka

_RB_DATE_RE = re.compile(r"^\d{1,2}\.\s*\d{1,2}\.\s*\d{4}$")
_RB_KOD_RE = re.compile(r"^\d{10}$")
_RB_CASTKA_RE = re.compile(r"^(.*?)\s*(-?\d[\d\s\xa0]*\.\d{2})\s*CZK$")


def split_rb_transaction_blocks(lines: List[str]) -> List[List[str]]:
    """
    Rozdělí řádky RB výpisu na bloky. Každý blok začíná datem zaúčtování.
    Přeskočí hlavičku a patičku výpisu.
    """
    relevant = []
    in_section = False
    skip_header_lines = (
        "Datum Kategorie transakce", "Valuta Číslo protiúčtu", "Kód transakce Název protiúčtu",
        "Datum ", "Valuta ", "Kód transakce ",
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
        if any(line.startswith(p) for p in skip_header_lines):
            continue
        relevant.append(line)

    blocks = []
    current = []
    i = 0
    while i < len(relevant):
        line = relevant[i]
        # Nový blok začíná datem zaúčtování (a hned za ním je druhé datum valuta)
        if _RB_DATE_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)
        i += 1
    if current:
        blocks.append(current)
    return blocks


def parse_rb_block(block: List[str], poradi: int, rok: str, mesic: str, typ_dokladu: str, bankovni_ucet: str):
    """
    Parsuje blok RB transakce v novém formátu:
    [0] datum zaúčtování  "2. 2. 2026"
    [1] datum valuta      "2. 2. 2026"
    [2] kód transakce     "8553644068"
    [3] kategorie         "Platba" / "Poplatek"
    [4] protiúčet         "6006604339/0800"  (může chybět)
    [5] typ transakce     "Jednorázová úhrada"
    [6] zpráva/VS         "VS:232026" nebo text
    [7] poznámka          volitelné
    [8] "VS částka CZK"   "232026 -3 086.00 CZK"
    """
    if not block:
        return None

    # Datum zaúčtování — první řádek
    datum_str = normalize_spaces(block[0])
    if not _RB_DATE_RE.match(datum_str):
        return None
    m_d = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$", datum_str)
    if not m_d:
        return None
    datum = f"{int(m_d.group(3)):04d}-{int(m_d.group(2)):02d}-{int(m_d.group(1)):02d}"

    # Projdeme zbytek bloku a hledáme klíčové položky
    kategorie = ""
    ucet_proti = ""
    nazev_protiuctu = ""
    typ_transakce = ""
    vs = ""
    ks = ""
    ss = ""
    castka_str = ""
    detail_texts = []

    for line in block[1:]:
        line = normalize_spaces(line)
        if not line:
            continue

        # Kód transakce — přeskočíme
        if _RB_KOD_RE.match(line):
            continue

        # Datum valuta — přeskočíme
        if _RB_DATE_RE.match(line):
            continue

        # Řádek s částkou a CZK na konci
        m_castka = _RB_CASTKA_RE.match(line)
        if m_castka:
            castka_str = m_castka.group(2)
            prefix = m_castka.group(1).strip()
            # Prefix může obsahovat VS
            if prefix and not vs:
                m_vs = re.match(r"^(\d+)$", prefix)
                if m_vs:
                    vs = m_vs.group(1)
            continue

        # Protiúčet ve formátu číslo/kód
        m_ucet = re.match(r"^(\d{1,6}-\d{1,10}/\d{4}|\d{1,16}/\d{4})$", line)
        if m_ucet and not ucet_proti:
            ucet_proti = m_ucet.group(1)
            continue

        # VS ze zprávy "VS:123456"
        m_vs_zprava = re.match(r"^VS:(\d+)$", line)
        if m_vs_zprava and not vs:
            vs = m_vs_zprava.group(1)
            continue

        # KS ze zprávy "KS:1178"
        m_ks = re.match(r"^KS:(\d+)$", line)
        if m_ks and not ks:
            ks = m_ks.group(1)
            continue

        # Kategorie — první textový řádek (Platba, Poplatek...)
        if not kategorie and re.match(r"^[A-Za-záčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýžA-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ\s]+$", line):
            kategorie = line
            continue

        # Typ transakce — druhý textový řádek
        if kategorie and not typ_transakce and re.match(r"^[A-Za-záčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]", line):
            # Může to být název protiúčtu nebo typ transakce
            if ucet_proti and not nazev_protiuctu:
                nazev_protiuctu = line
            elif not typ_transakce:
                typ_transakce = line
            continue

        # Cokoliv dalšího jde do detailů
        detail_texts.append(line)

    if not castka_str:
        return None

    try:
        castka = parse_amount_from_pdf(castka_str)
    except ValueError:
        return None

    typ_pohybu = "typPohybu.prijem" if castka > 0 else "typPohybu.vydej"
    castka_abs = abs(castka)

    popis_parts = [kategorie or "Platba"]
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
    if ss:
        popis_parts.append(f"SS {ss}")
    for extra in detail_texts[:3]:
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
    return bool(re.match(r"^\d{2}\.\d{2}\.\d{4}\s+", normalize_spaces(line)))


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
            # Poplatek je celý na jednom řádku
            synth = f"{datum} {parsed['nazev']} {parsed['castka']}"
            result.append([synth])
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
    st.title("PDF výpis → CSV pro ABRA Flexi")
    st.caption("Webová aplikace pro převod PDF výpisu na CSV pro import banky do ABRA Flexi. Podporuje: ČSOB, Raiffeisenbank, Česká spořitelna.")

    with st.sidebar:
        st.subheader("Nastavení")
        typ_dokladu = st.text_input("Typ dokladu", value=TYP_DOKLADU_DEFAULT)
        bankovni_ucet = st.text_input("Bank.účet", value="BANKOVNÍ ÚČET")
        st.markdown("---")
        st.caption("v2.1 — opraven parser Raiffeisenbank")

    uploaded_file = st.file_uploader("Nahraj PDF výpis", type=["pdf"])
    if not uploaded_file:
        st.info("Nahrajte PDF výpis banky pro zpracování.")
        st.stop()

    pdf_bytes = uploaded_file.read()
    text = extract_text_from_pdf_bytes(pdf_bytes)
    banka = detect_bank(text)
    meta = extract_statement_meta(text, banka)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Rozpoznaná banka", banka)
    col2.metric("Rok", meta.get("rok") or "—")
    col3.metric("Měsíc", meta.get("mesic") or "—")
    col4.metric("Účet v PDF", meta.get("ucet_pdf") or "—")

    if banka == "Neznámá":
        st.error("Tato verze podporuje ČSOB, Raiffeisenbank a Českou spořitelnu.")
        st.stop()

    if not meta.get("rok") or not meta.get("mesic"):
        st.error("Nepodařilo se zjistit rok nebo měsíc z PDF.")
        st.stop()

    rows, skipped = parse_transactions(text, banka, meta, typ_dokladu, bankovni_ucet)

    if not rows:
        st.error("Nepodařilo se vytvořit žádné položky.")
        st.stop()

    df = pd.DataFrame(rows)
    prijmy = df.loc[df["Typ pohybu"] == "typPohybu.prijem", "Částka osvob. bez DPH [Kč]"].astype(float).sum()
    vydaje = df.loc[df["Typ pohybu"] == "typPohybu.vydej", "Částka osvob. bez DPH [Kč]"].astype(float).sum()

    a, b, c, d = st.columns(4)
    a.metric("Počet položek", len(df))
    b.metric("Příjmy", f"{prijmy:,.2f}".replace(",", " "))
    c.metric("Výdaje", f"{vydaje:,.2f}".replace(",", " "))
    if skipped:
        d.metric("⚠️ Přeskočeno", skipped)

    if skipped:
        st.warning(f"{skipped} bloků se nepodařilo zpracovat. Zkontrolujte náhled, zda nechybí položky.")

    st.subheader("Náhled")
    st.dataframe(df, use_container_width=True, hide_index=True)

    bank_slug = (banka.lower()
                 .replace("č", "c").replace("š", "s").replace("ř", "r")
                 .replace("á", "a").replace("í", "i").replace("é", "e")
                 .replace("ů", "u").replace("ú", "u").replace(" ", "_"))
    csv_bytes = rows_to_csv_bytes(rows)
    filename = f"flexi_banka_import_{bank_slug}_{meta['rok']}_{meta['mesic']}.csv"

    st.download_button(
        "⬇️ Stáhnout CSV pro Flexi",
        csv_bytes,
        filename,
        "text/csv",
        use_container_width=True,
        type="primary",
    )

    with st.expander("Jak použít"):
        st.markdown("""
1. Nahraj textové PDF výpisu.
2. Zkontroluj pole **Typ dokladu** a **Bank.účet** v levém panelu.
3. Zkontroluj náhled — počty příjmů/výdajů musí sedět s výpisem.
4. Stáhni CSV.
5. Ve WUI ABRA Flexi: **Nástroje → Import → Import z Excelu → Banka**
6. Nejdřív použij **Vyzkoušet import**, pak teprve potvrď.
        """)


if __name__ == "__main__":
    main()
