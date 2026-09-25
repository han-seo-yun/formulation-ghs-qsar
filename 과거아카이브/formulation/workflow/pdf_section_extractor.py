"""
Phase 0.5 — PDF Section Extractor
highlighted/ 폴더의 PDF/HTML에서 SDS 섹션별 텍스트를 추출한다.
LLM 없이 pymupdf + 정규식으로 동작한다.

실행:
  python workflow/pdf_section_extractor.py
출력:
  ingredient_source_audit/section_extracts/{source_id}_sections.json
"""

import re
import json
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import pymupdf  # pip install pymupdf

BASE_DIR = Path(__file__).parent.parent
HIGHLIGHTED_DIR = BASE_DIR / "ingredient_source_audit/highlighted"
OUTPUT_DIR = BASE_DIR / "ingredient_source_audit/section_extracts"

# ─── 섹션 경계 패턴 ──────────────────────────────────────────────────────────
SECTION_STARTS = {
    "section_1": re.compile(
        r"SECTION\s*1[\.\:\s]|Section\s*1[\.\:\s]|"
        r"1[\.\s]+IDENTIFICATION|1[\.\s]+Product\s+identifier|"
        r"PRODUCT\s+AND\s+COMPANY\s+IDENTIFICATION",
        re.IGNORECASE,
    ),
    "section_2": re.compile(
        r"SECTION\s*2[\.\:\s]|Section\s*2[\.\:\s]|"
        r"2[\.\s]+Hazard|HAZARDS?\s+IDENTIFICATION|"
        r"GHS\s+CLASSIFICATION",
        re.IGNORECASE,
    ),
    "section_3": re.compile(
        r"SECTION\s*3[\.\:\s]|Section\s*3[\.\:\s]|"
        r"3[\.\s]+Composition|COMPOSITION.{0,40}INGRED",
        re.IGNORECASE,
    ),
    "section_14": re.compile(
        r"SECTION\s*14[\.\:\s]|Section\s*14[\.\:\s]|"
        r"14[\.\s]+Transport|TRANSPORT\s+INFORMATION",
        re.IGNORECASE,
    ),
    "section_15": re.compile(
        r"SECTION\s*15[\.\:\s]|Section\s*15[\.\:\s]|"
        r"15[\.\s]+Regulatory|REGULATORY\s+INFORMATION",
        re.IGNORECASE,
    ),
}

ANY_SECTION = re.compile(
    r"SECTION\s*\d+[\.\:\s]|Section\s*\d+[\.\:\s]|"
    r"^\d{1,2}[\.\s]+[A-Z][a-z]",
    re.MULTILINE,
)

# ─── 추출 패턴 ───────────────────────────────────────────────────────────────
RE_SIGNAL   = re.compile(r"\b(DANGER|WARNING|CAUTION)\b", re.IGNORECASE)
RE_H_CODE   = re.compile(r"\bH[1-4]\d{2}\b")
RE_P_CODE   = re.compile(r"\bP[1-5]\d{2}\b")
RE_GHS      = re.compile(
    r"Acute\s+Tox\.?\s*\d|Skin\s+Corr\.?\s*\w+|Eye\s+(?:Dam|Irrit)\.?\s*\d|"
    r"Aquatic\s+(?:Acute|Chronic)\s*\d|Carc\.?\s*\w+|Repr\.?\s*\d|"
    r"STOT\s+(?:SE|RE)\s*\d|Flam\.?\s*(?:Liq|Sol)\.?\s*\d|"
    r"Skin\s+Sens\.?\s*\d|Resp\.?\s+Sens\.?\s*\d|Expl\.?\s*\w+",
    re.IGNORECASE,
)
RE_EPA      = re.compile(
    r"EPA\s*Reg(?:istration)?\s*(?:No\.?|Number|#)?\s*[:\.]?\s*([\d]{3,6}-[\d]{1,6})",
    re.IGNORECASE,
)
RE_PCP      = re.compile(r"PCP\s*Reg(?:istration)?\s*No\.?\s*[:\.]?\s*(\d+)", re.IGNORECASE)
RE_REACH_ID = re.compile(r"\b01-\d{10}-\d{2}-\d{4}\b")
RE_UN       = re.compile(r"\bUN\s*(\d{4})\b")
RE_FORM_CODE = re.compile(r"\b(EC|SC|WP|WG|WDG|GR|FS|EW|SL|SP|CS|DC|RTU|ULV)\b")
RE_ACTIVE   = re.compile(r"ACTIVE\s+INGREDIENT", re.IGNORECASE)


def hazard_score(h_codes: list[str], signal: str | None) -> int:
    """H코드와 신호어로 1-5 위험도 점수 계산."""
    critical = {"H300", "H301", "H310", "H311", "H330", "H331",
                "H340", "H341", "H350", "H351", "H360", "H361", "H370"}
    high     = {"H302", "H303", "H312", "H313", "H314", "H332",
                "H334", "H335", "H371", "H372", "H373"}
    codes = set(h_codes)
    if codes & critical:
        return 5 if (signal or "").upper() == "DANGER" else 4
    if codes & high:
        return 3 if (signal or "").upper() == "WARNING" else 2
    if h_codes:
        return 2
    if (signal or "").upper() in {"DANGER", "WARNING"}:
        return 2
    if (signal or "").upper() == "CAUTION":
        return 1
    return 0


def split_sections(text: str) -> dict[str, str]:
    """전체 텍스트를 섹션별로 분할 (최대 2500자/섹션)."""
    positions = {}
    for name, pat in SECTION_STARTS.items():
        m = pat.search(text)
        if m:
            positions[name] = m.start()

    result = {}
    sorted_secs = sorted(positions.items(), key=lambda x: x[1])
    for i, (name, start) in enumerate(sorted_secs):
        end = sorted_secs[i + 1][1] if i + 1 < len(sorted_secs) else start + 3000
        result[name] = text[start : min(end, start + 2500)]
    return result


def parse_section_1(text: str) -> dict:
    epa   = RE_EPA.search(text)
    pcp   = RE_PCP.search(text)
    reach = RE_REACH_ID.search(text)
    code  = RE_FORM_CODE.search(text)
    active = RE_ACTIVE.search(text)

    reg_type = None
    if epa:   reg_type = "EPA"
    elif pcp:  reg_type = "PCP"
    elif reach: reg_type = "REACH"

    # 제품명 — 첫 번째 의미있는 줄
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 5]
    product_name = lines[1] if len(lines) > 1 else lines[0] if lines else None

    return {
        "product_name":            product_name,
        "formulation_code":        code.group(1).upper() if code else None,
        "epa_reg_number":          epa.group(1)  if epa  else None,
        "pcp_reg_number":          pcp.group(1)  if pcp  else None,
        "reach_id":                reach.group() if reach else None,
        "registration_type":       reg_type,
        "active_ingredient_declared": bool(active),
    }


def parse_section_2(text: str) -> dict:
    signal  = RE_SIGNAL.search(text)
    h_codes = list(dict.fromkeys(RE_H_CODE.findall(text)))   # 순서 유지 중복 제거
    p_codes = list(dict.fromkeys(RE_P_CODE.findall(text)))
    classes = list(dict.fromkeys(RE_GHS.findall(text)))
    sw = signal.group(1).upper() if signal else None

    # 수생 독성
    env = None
    if any(c in h_codes for c in ["H400", "H410"]):
        env = "aquatic_acute_and_chronic"
    elif "H411" in h_codes:
        env = "aquatic_chronic"
    elif any(c in h_codes for c in ["H400", "H410", "H411", "H412", "H413"]):
        env = "aquatic"

    return {
        "signal_word":          sw,
        "h_codes":              h_codes,
        "p_codes":              p_codes,
        "ghs_classes":          classes,
        "environmental_hazard": env,
        "hazard_level_score":   hazard_score(h_codes, sw),
    }


def parse_section_14(text: str) -> dict:
    un = RE_UN.search(text)
    return {
        "un_number": un.group(1) if un else None,
    }


def extract_from_pdf(pdf_path: Path) -> dict:
    """단일 PDF 전체 처리."""
    pdf_path = pdf_path.resolve()
    source_id = pdf_path.stem.replace("_highlighted", "")
    try:
        doc = pymupdf.open(str(pdf_path))
        full_text = "".join(page.get_text() for page in doc)
        page_count = len(doc)
        doc.close()
    except Exception as e:
        return {"source_id": source_id, "error": str(e)}

    sections = split_sections(full_text)

    # 섹션 없이 라벨인 경우 — 전체 텍스트 앞 2500자에서 직접 추출
    if not sections:
        head = full_text[:2500]
        sections = {"label_header": head}

    try:
        rel_path = str(pdf_path.relative_to(BASE_DIR.resolve()))
    except ValueError:
        rel_path = str(pdf_path)

    result: dict = {
        "source_id":        source_id,
        "file_path":        rel_path,
        "page_count":       page_count,
        "full_text_length": len(full_text),
        "sections_found":   list(sections.keys()),
    }

    if "section_1" in sections:
        result["section_1"] = parse_section_1(sections["section_1"])
        result["section_1"]["text_excerpt"] = sections["section_1"][:2000]
    if "section_2" in sections:
        result["section_2"] = parse_section_2(sections["section_2"])
        result["section_2"]["text_excerpt"] = sections["section_2"][:2000]
    if "section_14" in sections:
        result["section_14"] = parse_section_14(sections["section_14"])
    if "label_header" in sections:
        head = sections["label_header"]
        epa = RE_EPA.search(head)
        code = RE_FORM_CODE.search(head)
        sw   = RE_SIGNAL.search(head)
        result["label_header"] = {
            "epa_reg_number":            epa.group(1) if epa else None,
            "formulation_code":          code.group(1).upper() if code else None,
            "signal_word":               sw.group(1).upper() if sw else None,
            "active_ingredient_declared": bool(RE_ACTIVE.search(head)),
            "text_excerpt":              head[:300],
        }

    # 전문에서 직접 추출 (섹션 미탐지 보완)
    full_epa  = RE_EPA.search(full_text)
    full_code = RE_FORM_CODE.search(full_text[:1000])   # 앞부분에서만
    if full_epa and "section_1" not in result:
        result.setdefault("fallback", {})["epa_reg_number"] = full_epa.group(1)
    if full_code and "section_1" not in result:
        result.setdefault("fallback", {})["formulation_code"] = full_code.group(1).upper()

    return result


def extract_from_html(html_path: Path) -> dict:
    """HTML 파일 처리 (간이 태그 제거)."""
    source_id = html_path.stem.replace("_highlighted", "")
    try:
        raw = html_path.read_text(errors="replace")
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s{2,}", " ", text)
    except Exception as e:
        return {"source_id": source_id, "error": str(e)}

    sections = split_sections(text)
    result: dict = {
        "source_id":        source_id,
        "file_path":        str(html_path.relative_to(BASE_DIR)),
        "page_count":       0,
        "full_text_length": len(text),
        "sections_found":   list(sections.keys()),
    }
    if "section_2" in sections:
        result["section_2"] = parse_section_2(sections["section_2"])
    if "section_1" in sections:
        result["section_1"] = parse_section_1(sections["section_1"])

    # fallback: 전문 스캔
    epa  = RE_EPA.search(text[:3000])
    code = RE_FORM_CODE.search(text[:1000])
    sw   = RE_SIGNAL.search(text[:3000])
    if any([epa, code, sw]):
        result["label_header"] = {
            "epa_reg_number":  epa.group(1)  if epa  else None,
            "formulation_code": code.group(1).upper() if code else None,
            "signal_word":     sw.group(1).upper()  if sw   else None,
            "active_ingredient_declared": bool(RE_ACTIVE.search(text[:3000])),
        }
    return result


def process_file(path: Path) -> dict:
    if path.suffix == ".pdf":
        return extract_from_pdf(path)
    return extract_from_html(path)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = (
        list(HIGHLIGHTED_DIR.glob("*_highlighted.pdf")) +
        list(HIGHLIGHTED_DIR.glob("*_highlighted.html"))
    )
    print(f"=== PDF Section Extractor ===")
    print(f"처리 대상: {len(files)}개 파일")

    # 캐시: 이미 처리된 파일 스킵
    already = {p.stem.replace("_sections", "") for p in OUTPUT_DIR.glob("*_sections.json")}
    todo = [f for f in files if f.stem.replace("_highlighted", "") not in already]
    print(f"미처리: {len(todo)}개 (캐시 {len(already)}개 스킵)")

    # 병렬 처리 (CPU 코어 활용)
    stats = {"ok": 0, "error": 0, "section2_found": 0, "epa_found": 0, "code_found": 0}
    workers = min(8, os.cpu_count() or 4)

    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_file, f): f for f in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            sid = result["source_id"]
            out_path = OUTPUT_DIR / f"{sid}_sections.json"
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

            if "error" in result:
                stats["error"] += 1
            else:
                stats["ok"] += 1
                if "section_2" in result:
                    stats["section2_found"] += 1
                # EPA Reg 발견
                s1 = result.get("section_1", {})
                lh = result.get("label_header", {})
                fb = result.get("fallback", {})
                if s1.get("epa_reg_number") or lh.get("epa_reg_number") or fb.get("epa_reg_number"):
                    stats["epa_found"] += 1
                # 제형 코드 발견
                if s1.get("formulation_code") or lh.get("formulation_code") or fb.get("formulation_code"):
                    stats["code_found"] += 1

            if i % 50 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] ok={stats['ok']} err={stats['error']} "
                      f"sec2={stats['section2_found']} epa={stats['epa_found']} code={stats['code_found']}")

    total = stats["ok"]
    print(f"\n=== 완료 ===")
    print(f"  성공: {stats['ok']}개 / 실패: {stats['error']}개")
    if total:
        print(f"  Section 2(위험도) 추출: {stats['section2_found']}/{total} ({stats['section2_found']/total:.1%})")
        print(f"  EPA Reg 번호 발견:      {stats['epa_found']}/{total} ({stats['epa_found']/total:.1%})")
        print(f"  제형 코드 발견:         {stats['code_found']}/{total} ({stats['code_found']/total:.1%})")
    print(f"  출력 위치: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
