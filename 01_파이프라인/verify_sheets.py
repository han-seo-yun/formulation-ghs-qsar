#!/usr/bin/env python3
"""dataset_2차배정.xlsx 4개 시트의 '검증여부'를 규칙기반으로 판정한다.

산출: dataset_2차배정_verified.xlsx (원본 무손상)
  - 검증여부   : 미확인 / 검증완료 / 정보없음 / 재검토필요  (기존 드롭다운 4값)
  - 검증근거   : (신규 컬럼) 어떤 규칙이 그 판정을 냈는지 · 사람이 되짚을 수 있게
  - 검증방식   : (신규 컬럼) rule / rule+agent

원칙
  - 이 패스는 '문서 재조사'가 아니라 '내부정합성 검증'이다. 웹 재조사는 Phase 1/2.2가 담당.
  - 값이 참조컬럼과 충돌하거나 규칙을 위반하면 검증완료로 올리지 않고 재검토필요로 내린다.
  - 판정 불가는 조용히 검증완료로 만들지 않고 미확인으로 남긴다.
"""
import json
from pathlib import Path
import re
import shutil
import sys
from collections import Counter, defaultdict

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = str(Path(__file__).resolve().parent.parent)
SRC = f"{ROOT}/dataset_2차배정.xlsx"
DST = f"{ROOT}/dataset_2차배정_verified.xlsx"
CIPAC = f"{ROOT}/formulation_harness/cipac_codes.json"
REPORT = f"{ROOT}/work/verify/verify_report.json"

OK, NONE_, REVIEW, UNK = "검증완료", "정보없음", "재검토필요", "미확인"

# ---------------------------------------------------------------- 공통 유틸

FAIL_KW = ["열리지", "안 열", "열 수 없", "404", "403", "접속", "불가", "실패",
           "로그인", "링크 없음", "부실", "못 찾", "타임아웃", "차단", "깨진",
           "도구 제약", "JS렌더"]
MISMATCH_KW = ["성분 불일치", "다른 제품", "제품명이 다름", "제품명이 없음",
               "주성분 특정 불가", "성분명 없음", "불일치"]
ABSENT_KW = ["SDS 없", "해당 섹션 없", "정보 자체가 없", "기재 없", "미기재",
             "원리적으로 없", "존재하지 않", "값 없음", "표기 없"]
# 작업자가 문제를 진단한 뒤 '해결했다'고 적은 흔적. 이 마커가 있으면 mismatch/source_fail
# 키워드가 같은 메모에 있어도 미해결 결함으로 취급하지 않는다.
RESOLVED_KW = ["새로 찾음", "새로 확인", "새로 받", "교체", "대체 소스", "재확인 완료",
               "확인 완료", "일치 확인", "동일 제품 SDS 확인", "웹 SDS 일치", "SDS 일치",
               "정정", "보완"]


def s(v):
    return "" if v is None else str(v).strip()


def cas_valid(cas):
    """CAS 체크디짓 검증. 형식 위반/체크섬 불일치면 False."""
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", cas.strip())
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    total = sum(int(d) * (i + 1) for i, d in enumerate(digits))
    return total % 10 == int(m.group(3))


def norm_name(x):
    return re.sub(r"[^a-z0-9]", "", x.lower())


def memo_class(memo):
    """메모 텍스트를 사유 클래스로. 해결마커가 있으면 결함으로 보지 않는다."""
    if not memo:
        return None
    if any(k in memo for k in RESOLVED_KW):
        return "resolved"
    if any(k in memo for k in MISMATCH_KW):
        return "mismatch"
    if any(k in memo for k in FAIL_KW):
        return "source_fail"
    if any(k in memo for k in ABSENT_KW):
        return "absent"
    return "other"


# ---------------------------------------------------------------- 시트별 규칙

CIPAC_CODES = None


def load_cipac():
    global CIPAC_CODES
    d = json.load(open(CIPAC, encoding="utf-8"))
    codes = {e["code"] for e in d["current"]} | {e["code"] for e in d["discontinued"]}
    codes |= set(d.get("special_suffix", {}))
    CIPAC_CODES = codes
    return codes


def verify_ingredient(r, C):
    """성분 시트: 추출정보 'name | CAS | pct' 를 참조 name/cas 와 대조."""
    ext, memo = s(r[C["추출정보"]]), s(r[C["메모"]])
    ref_name, ref_cas = s(r[C["ingredient_name"]]), s(r[C["cas"]])
    src = s(r[C["ingredient_source"]]) or s(r[C["신규소스링크"]]) or s(r[C["doc_rel"]])
    mc = memo_class(memo)

    if not ext:
        if mc == "absent":
            return NONE_, "추출정보 공란 + 메모가 '문서에 해당 정보 없음'을 진술"
        if mc in ("source_fail", "mismatch"):
            return REVIEW, f"추출정보 공란 + 메모 사유={mc} (출처 실패/제품 불일치)"
        if memo:
            return REVIEW, "추출정보 공란인데 메모만 있음 — 결론 미기재"
        return UNK, "추출정보·메모 모두 공란 (미착수)"

    parts = [p.strip() for p in ext.split("|")]
    got_name = parts[0] if parts else ""
    got_cas = parts[1] if len(parts) > 1 else ""
    got_pct = parts[2] if len(parts) > 2 else ""
    notes, hard = [], []

    if mc == "mismatch":
        hard.append("메모가 제품/성분 불일치를 진술")
    elif mc == "resolved":
        notes.append("메모: 부실출처를 진단 후 신규 출처로 교체 완료")
    if not src:
        hard.append("출처 전무(ingredient_source·신규소스링크·doc_rel 모두 공란)")

    if got_cas:
        if not cas_valid(got_cas):
            hard.append(f"CAS 체크디짓 불통과({got_cas})")
        elif ref_cas and got_cas.replace(" ", "") != ref_cas.replace(" ", ""):
            hard.append(f"CAS 충돌 참조={ref_cas} vs 추출={got_cas}")
        else:
            notes.append("CAS 체크디짓 통과" + ("·참조일치" if ref_cas else "·참조없음"))
    else:
        notes.append("CAS 미기재")

    if got_name and ref_name:
        a, b = norm_name(got_name), norm_name(ref_name)
        if a == b:
            notes.append("성분명 참조일치")
        elif a in b or b in a:
            notes.append("성분명 부분일치")
        else:
            notes.append(f"성분명 상이(참조={ref_name} / 추출={got_name}) — 정정으로 간주")
    notes.append("함량 확보" if got_pct else "함량 미기재")

    if hard:
        return REVIEW, " ; ".join(hard)
    return OK, " ; ".join(notes)


def verify_formulation(r, C):
    """제형코드 시트: 추출정보가 CIPAC 정본 코드인지 + 참조 formulation_code 와 충돌 없는지."""
    ext, memo = s(r[C["추출정보"]]).upper(), s(r[C["메모"]])
    ref = s(r[C["formulation_code"]]).upper()
    nfr = s(r[C["not_formulation_reason"]])
    src = s(r[C["formulation_src"]]) or s(r[C["신규소스링크"]]) or s(r[C["doc_source"]]) \
        or s(r[C["ingredient_source"]])
    NOT_FORM = {"other", "single_substance", "analytical_standard", "reagent",
                "reference_solution", "mixture_unspecified"}

    if not ext:
        if memo.lower() in NOT_FORM or nfr.lower() in NOT_FORM:
            return NONE_, f"제형이 아님({memo or nfr}) — 제형코드가 원리적으로 존재하지 않음"
        mc = memo_class(memo)
        if mc in ("source_fail", "mismatch"):
            return REVIEW, f"추출정보 공란 + 메모 사유={mc}"
        if memo:
            return REVIEW, f"추출정보 공란 + 미분류 메모: {memo[:40]}"
        return UNK, "추출정보·메모 모두 공란 (미착수)"

    base = ext.split("-")[0].strip()
    if base not in CIPAC_CODES:
        return REVIEW, f"CIPAC 정본 96코드에 없는 값: '{ext}'"
    if ref and ref != ext:
        return REVIEW, f"기존 formulation_code 와 충돌: 참조={ref} vs 추출={ext}"
    if not src:
        return REVIEW, f"코드({ext})는 유효하나 출처 전무"
    tail = "·참조일치" if ref else "·참조공란(신규회수)"
    return OK, f"CIPAC 유효코드 {ext}{tail} ; 출처 보유"


# GHS H코드 ↔ 구분 정본 매핑
H_EYE = {"H318": "1", "H319": "2A", "H320": "2B"}
H_SKIN = {"H314": "1", "H315": "2", "H316": "3"}
H_SENS = {"H317": "1", "H334": "1"}
DANGER_H = {"H314", "H318", "H304", "H330", "H310", "H300", "H340", "H350", "H360", "H370"}


def _cat_conflict(observed, sheet_val, table):
    """H코드로 유도한 구분 vs 시트 참조 구분. 충돌이면 메시지, 아니면 None."""
    if not observed or not sheet_val:
        return None
    sv = str(sheet_val).strip()
    # 엑셀이 '1' 을 float 1.0 으로 읽어오므로 정수형 실수는 정수 문자열로 정규화한다
    try:
        f = float(sv)
        if f == int(f):
            sv = str(int(f))
    except ValueError:
        pass
    if sv in ("Not classified", "NC", "-"):
        return f"참조=Not classified 인데 H코드는 구분 {observed} 를 지시"
    # 2 는 2A/2B 상위개념 — 충돌로 보지 않음
    if sv == observed or {sv, observed} <= {"1", "1A", "1B", "1C"} or \
       (sv == "2" and observed in ("2", "2A", "2B")) or \
       (observed == "2" and sv in ("2A", "2B")):
        return None
    return f"구분 충돌 참조={sv} vs H코드유도={observed}"


def verify_toxicity(r, C):
    """독성코드 시트: 신호어·H코드 내부정합 + 시트 참조 GHS 구분과의 충돌."""
    ext, memo = s(r[C["추출정보"]]), s(r[C["메모"]])
    src = s(r[C["tox_source_url"]]) or s(r[C["신규소스링크"]]) or s(r[C["doc_rel"]]) \
        or s(r[C["doc_source"]])
    status = {s(r[C[f"sds_grade_status_{k}"]]) for k in ("eye", "skin", "sens")}
    mc = memo_class(memo)

    if not ext:
        if mc == "absent" or status == {"no_sds"} and mc == "absent":
            return NONE_, "추출정보 공란 + 메모가 '해당 SDS/섹션 자체가 없음'을 진술"
        if mc == "mismatch":
            return REVIEW, "추출정보 공란 + 성분 불일치(다른 제품 SDS 의심)"
        if mc == "source_fail":
            return REVIEW, "추출정보 공란 + 출처 접근 실패(링크불가·403/404·로그인·못찾음)"
        if memo:
            return REVIEW, f"추출정보 공란 + 미분류 메모: {memo[:40]}"
        return UNK, "추출정보·메모 모두 공란 (미착수)"

    hs = set(re.findall(r"H\d{3}", ext))
    # GHS 신호어(Danger/Warning) 외에, 구형 MSDS·EPA FIFRA 라벨은 EPA 4단 신호어
    # (DANGER/WARNING/CAUTION)를 쓴다. CAUTION 은 GHS 신호어가 아니므로 별도 취급한다.
    signal = "Danger" if re.search(r"\bDanger\b|위험", ext, re.I) else \
             ("Warning" if re.search(r"\bWarning\b|경고", ext, re.I) else
              ("Caution(EPA)" if re.search(r"\bCaution\b|주의", ext, re.I) else ""))
    pre_ghs = bool(re.search(r"pre-GHS|구형|비-GHS|GHS 이전|EPA 라벨|EPA label|FIFRA|"
                             r"명시 없음|None stated", ext, re.I))
    hard, notes = [], []

    if mc == "mismatch":
        hard.append("메모가 제품/성분 불일치를 진술")
    if not src:
        hard.append("추출값은 있으나 출처 전무")
    if not hs and not signal:
        if pre_ghs:
            notes.append("구형/비-GHS 문서로 신호어·H코드 자체가 없음이 명시됨")
        else:
            hard.append("신호어도 H코드도 파싱되지 않음")
    if signal == "Caution(EPA)":
        notes.append("EPA FIFRA 라벨 신호어 CAUTION — GHS 신호어 아님(EPA cat III/IV 시사)")

    # 신호어 ↔ H코드 정합
    if hs & DANGER_H and signal == "Warning":
        hard.append(f"신호어 부정합: {sorted(hs & DANGER_H)} 는 Danger 인데 Warning 표기")
    elif signal:
        notes.append(f"신호어 {signal} 정합")

    # H코드 → 구분 유도 후 시트 참조와 대조
    for label, table, col in (("눈", H_EYE, "cat_eye_ghs"),
                              ("피부", H_SKIN, "cat_skin_ghs"),
                              ("감작성", H_SENS, "cat_sens_ghs")):
        hit = [table[h] for h in hs if h in table]
        obs = sorted(hit, key=lambda x: (x != "1", x))[0] if hit else None
        c = _cat_conflict(obs, r[C[col]], table)
        if c:
            hard.append(f"{label} {c}")
        elif obs:
            notes.append(f"{label} 구분 {obs}(H코드 근거)")

    if hs:
        notes.append(f"H코드 {len(hs)}건 파싱")
    if mc == "source_fail":
        hard.append("메모에 출처 접근 실패 흔적이 남아 있음 — 값의 근거 재확인 필요")

    if hard:
        return REVIEW, " ; ".join(hard)
    if not (hs & (set(H_EYE) | set(H_SKIN) | set(H_SENS))):
        return OK, " ; ".join(notes + ["단, 눈/피부/감작성 H코드는 없음 — 타깃 기여 없음"])
    return OK, " ; ".join(notes)


PARAM_RANGE = {
    "ph": (0, 14), "melting point": (-273, 4000), "boiling point": (-273, 6000),
    "flash point": (-150, 600), "relative density": (0.1, 25),
    "specific gravity": (0.1, 25), "density": (0.1, 25),
}
# 밀도는 SDS 마다 단위가 g/cm3 · g/L · kg/m3 · lb/ft3 로 섞여 있다. g/cm3 기준으로
# 환산한 뒤 범위를 본다. 환산 없이 검사하면 '1169 g/L' 가 전부 오탐이 된다.
DENSITY_UNIT = [
    (r"lbs?\s*/\s*(?:cu\s*ft|ft\s*3|ft³)", 0.0160185), (r"lbs?\s*/\s*gal", 0.119826),
    (r"kg\s*/\s*m\s*3|kg\s*/\s*m³", 0.001),
    (r"(?<![kK])g\s*/\s*[lL]\b|(?<![kK])g\s*/\s*dm3", 0.001),
    (r"kg\s*/\s*l\b|g\s*/\s*(?:cm3|cm³|ml)", 1.0),
]


def verify_physchem(r, C):
    """특성 시트: 'value (Parameter)' 파싱 성공 + 물리적 범위 sanity."""
    ext, memo = s(r[C["추출정보"]]), s(r[C["메모"]])
    src = s(r[C["신규소스링크"]]) or s(r[C["tox_source_url"]]) or s(r[C["doc_rel"]]) \
        or s(r[C["ingredient_source"]])
    mc = memo_class(memo)

    if not ext:
        if mc == "absent":
            return NONE_, "추출정보 공란 + 메모가 'Section 9 자체가 없음'을 진술"
        if mc in ("source_fail", "mismatch"):
            return REVIEW, f"추출정보 공란 + 메모 사유={mc}"
        return (REVIEW, f"추출정보 공란 + 미분류 메모: {memo[:40]}") if memo else \
               (UNK, "추출정보·메모 모두 공란 (미착수)")

    items = [p.strip() for p in ext.split("|") if p.strip()]
    hard, notes = [], []
    if mc == "mismatch":
        hard.append("메모가 '근거 문서가 다른 제품/주성분 특정 불가'를 진술")
    if not src:
        hard.append("추출값은 있으나 출처 전무")

    parsed = 0
    for it in items:
        m = re.search(r"\(([^)]+)\)\s*$", it)
        if not m:
            continue
        parsed += 1
        pname = m.group(1).strip().lower()
        num = re.search(r"(-?\d+(?:\.\d+)?)", it)
        if not num or pname not in PARAM_RANGE:
            continue
        lo, hi = PARAM_RANGE[pname]
        v = float(num.group(1))
        if "density" in pname or "gravity" in pname:
            for pat, fac in DENSITY_UNIT:
                if re.search(pat, it, re.I):
                    v *= fac
                    break
        if not (lo <= v <= hi):
            hard.append(f"{pname} 물리범위 이탈: {v:g} g/cm3 환산 (허용 {lo}~{hi}) 원문='{it[:40]}'")

    if parsed == 0:
        hard.append(f"'값 (Parameter)' 형식으로 파싱된 항목 0개 — 원문 검토 필요")
    else:
        notes.append(f"파라미터 {parsed}/{len(items)}개 파싱·범위 정상")

    ph = re.search(r"(-?\d+(?:\.\d+)?)(?:\s*-\s*(-?\d+(?:\.\d+)?))?\s*\(pH\)", ext, re.I)
    if ph:
        notes.append("pH 확보(GHS 강산·강염기 예외 게이트 사용 가능)")

    if hard:
        return REVIEW, " ; ".join(hard)
    return OK, " ; ".join(notes)


SHEETS = {
    "성분": verify_ingredient,
    "제형코드": verify_formulation,
    "독성코드": verify_toxicity,
    "특성": verify_physchem,
}


def main():
    load_cipac()
    shutil.copy(SRC, DST)
    wb = openpyxl.load_workbook(DST)
    report = {}
    unmerged_counts = {}

    for name, fn in SHEETS.items():
        ws = wb[name]
        # 병합셀 해제. 독성코드 3295건 / 제형코드 54건이 병합되어 있어 검증여부 컬럼이
        # MergedCell(읽기전용)로 잠기고 드롭다운 sqref 도 깨져 있었다. 병합범위는 좌상단만
        # 값을 보유하므로 해제는 값 손실이 없다 — 해제 후 좌상단 값을 되돌려 놓는다.
        merged = [str(m) for m in ws.merged_cells.ranges]
        if merged:
            for m in list(ws.merged_cells.ranges):
                keep = ws.cell(m.min_row, m.min_col).value
                ws.unmerge_cells(str(m))
                ws.cell(m.min_row, m.min_col).value = keep
            print(f"[{name}] 병합셀 {len(merged)}건 해제")
        unmerged_counts[name] = len(merged)
        hdr = [c.value for c in ws[1]]
        C = {h: i for i, h in enumerate(hdr) if h}
        vcol = C["검증여부"] + 1
        # 신규 감사 컬럼 2개
        ncol = ws.max_column + 1
        while ws.cell(1, ncol).value:
            ncol += 1
        ws.cell(1, ncol).value = "검증근거"
        ws.cell(1, ncol + 1).value = "검증방식"

        tally = Counter()
        reasons = defaultdict(Counter)
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            verdict, why = fn(list(row), C)
            ws.cell(i, vcol).value = verdict
            ws.cell(i, ncol).value = why[:480]
            ws.cell(i, ncol + 1).value = "rule"
            tally[verdict] += 1
            reasons[verdict][why.split(" ; ")[0][:60]] += 1

        # 드롭다운 복원 (독성코드는 이전 저장에서 sqref 가 11셀로 깨져 있었다)
        ws.data_validations.dataValidation = []
        dv = DataValidation(type="list", formula1='"미확인,검증완료,정보없음,재검토필요"',
                            allow_blank=True)
        ws.add_data_validation(dv)
        col = openpyxl.utils.get_column_letter(vcol)
        dv.add(f"{col}2:{col}{ws.max_row}")

        report[name] = {"total": sum(tally.values()), "unmerged": unmerged_counts[name],
                        "tally": dict(tally),
                        "top_reasons": {k: dict(v.most_common(8)) for k, v in reasons.items()}}
        print(f"[{name}] " + "  ".join(f"{k}={v}" for k, v in tally.most_common()))

    wb.save(DST)
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ {DST}\n→ {REPORT}")


if __name__ == "__main__":
    main()
