#!/usr/bin/env python3
"""
수동 채움 작업 시트 생성 — ING_농도채움_작업시트.xlsx

용도: 작업자(한서윤)가 SDS를 보고 농도를 채워 넣을 수 있는 간편 작업 시트.
입력은 SDS 원문 표기(ing_pct_src_detail)만 받으면, 나머지(대표값·범위·Qualifiers)가
자동 계산되어 표시된다. 작업자는 확인 후 ing_pct_best를 수정·확정한다.

작업자가 채우는 칼럼:
  - ing_pct_src_detail : SDS Section 3 원문 표기 (예: "10~18%", "≤2%", "≥90%", "~5%", "5.2%", "traces", "계산불가")
  - ing_pct_src        : 정보원 (예: "수동_SDS판독_정채윤_GHS 조사.xlsx_Section3")
  - ing_pct_best       : 최종 대표값 (자동 계산값을 확인하고 수정 가능)

자동 계산 칼럼(작업자가 건드리지 않아도 됨, 확인용):
  - ing_pct_qual       : exact / range / le_bound / ge_bound / approx / none
  - ing_pct_range_low   : 범위의 하한 (le_bound는 상한, ge_bound는 하한)
  - ing_pct_range_high  : 범위의 상한
  - ing_pct_best_calc   : 대표값 자동 계산 (중간값·상한·하한·근사값)
  - ing_pct_best_note   : 대표값 선택 규칙 메모

입출력:
  입력 : 04_모델산출물/input_dataset_v6.xlsx (ingredient 시트), ING_농도오버레이_수동_작업목록.csv
  출력 : 04_모델산출물/v8_농도채움/ING_농도채움_작업시트.xlsx
"""

from __future__ import annotations

import re
import numpy as np
import pandas as pd
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
V6 = ROOT / "04_모델산출물" / "input_dataset_v6.xlsx"
CSV_LIST = ROOT / "04_모델산출물" / "v8_농도채움" / "ING_농도오버레이_수동_작업목록.csv"
OUT = ROOT / "04_모델산출물" / "v8_농도채움" / "ING_농도채움_작업시트.xlsx"


# ================================================================ 파싱 함수

def parse_pct_src_detail(s: str | float | None) -> dict:
    """SDS 원문 표기 → ing_pct_qual, range_low, range_high, best_calc, note.

    반환 dict:
      qual       : exact / range / le_bound / ge_bound / approx / none
      low        : 하한 (float or NaN)
      high       : 상한 (float or NaN)
      best       : 대표값 (float or NaN)
      note       : 대표값 선택 규칙 메모 (str)
    """
    if s is None or (isinstance(s, float) and np.isnan(s)) or str(s).strip() == "":
        return {"qual": "none", "low": np.nan, "high": np.nan,
                "best": np.nan, "note": "입력 없음 — SDS 확인 필요"}

    s = str(s).strip()

    # --- exact: 단일 수치 (%) ---
    m = re.match(r"^\s*([\d.]+)\s*%\s*$", s)
    if m:
        v = float(m.group(1))
        return {"qual": "exact", "low": v, "high": v,
                "best": v, "note": f"정확한 값 {v}%"}

    # --- 범위: ~ 또는 - 로 연결 (10~18%, 10-18%, 10∼18%) ---
    m = re.match(r"^\s*([\d.]+)\s*[~∼\-]\s*([\d.]+)\s*%\s*$", s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        mid = (lo + hi) / 2.0
        return {"qual": "range", "low": lo, "high": hi,
                "best": mid,
                "note": f"범위 {lo}~{hi}%, 중간값 {mid}%"}

    # --- le_bound: ≤ X% (상한) ---
    m = re.match(r"^\s*≤\s*([\d.]+)\s*%\s*$", s)
    if m:
        v = float(m.group(1))
        return {"qual": "le_bound", "low": np.nan, "high": v,
                "best": v,
                "note": f"상한 ≤{v}%, 상한값 {v}% 사용 (보수적: 높을수록 유해)"}

    # --- ge_bound: ≥ X% (하한) ---
    m = re.match(r"^\s*≥\s*([\d.]+)\s*%\s*$", s)
    if m:
        v = float(m.group(1))
        return {"qual": "ge_bound", "low": v, "high": np.nan,
                "best": v,
                "note": f"하한 ≥{v}%, 하한값 {v}% 사용"}

    # --- approx: ~X% / 약 X% ---
    m = re.match(r"^\s*~\s*([\d.]+)\s*%\s*$", s)
    if m:
        v = float(m.group(1))
        return {"qual": "approx", "low": v, "high": v,
                "best": v, "note": f"근사값 ~{v}%, 그 값 사용"}
    m = re.match(r"^\s*약\s*([\d.]+)\s*%\s*$", s)
    if m:
        v = float(m.group(1))
        return {"qual": "approx", "low": v, "high": v,
                "best": v, "note": f"약 {v}%, 그 값 사용"}

    # --- traces / 미량 / < X% ---
    if s.lower() in ("traces", "미량", "소량", "trace"):
        return {"qual": "approx", "low": np.nan, "high": np.nan,
                "best": np.nan,
                "note": "traces/미량 — 정확한 수치 없음. 0 또는 미량값 중 선택 필요"}

    m = re.match(r"^\s*<\s*([\d.]+)\s*%\s*$", s)
    if m:
        v = float(m.group(1))
        return {"qual": "le_bound", "low": np.nan, "high": v,
                "best": v,
                "note": f"미만 <{v}%, 상한값 {v}% 사용 (보수적)"}

    # --- 그 외 인식 불가 ---
    return {"qual": "none", "low": np.nan, "high": np.nan,
            "best": np.nan,
            "note": f"인식 불가: {s} — 수동 확인 필요"}


# ================================================================ 작업 시트 생성

def main():
    # 1. 작업 대상 목록 로드 (우선순위 0 우선, 전체 포함)
    if CSV_LIST.exists():
        df_list = pd.read_csv(CSV_LIST, encoding="utf-8-sig")
        print(f"작업 목록 CSV 로드: {len(df_list)}행")
    else:
        print(f"작업 목록 CSV 없음: {CSV_LIST} — input_dataset_v6.xlsx 에서 직접 추출")
        d = pd.read_excel(V6, sheet_name="ingredient")
        # ing_pct_best 결측 & 위험 pct_status 제외
        RISK = {"unrecoverable_blanked", "nonchem_name_blanked"}
        df_list = d[d["ing_pct_best"].isna() & ~d["pct_status"].isin(RISK)].copy()
        df_list["Formulation_ID"] = df_list["Formulation_ID"].astype(str)

    # 우선순위 컬럼이 없으면 생성 (CSV에 없는 경우 대비)
    if "우선순위" not in df_list.columns:
        df_list["우선순위"] = 2
        df_list["has_ghs_eye"] = df_list["ing_ghs_indep_eye_cat"].notna()
        df_list["has_ghs_skin"] = df_list["ing_ghs_indep_skin_cat"].notna()
        df_list["has_ghs_both"] = df_list["has_ghs_eye"] & df_list["has_ghs_skin"]
        df_list.loc[df_list["has_ghs_both"], "우선순위"] = 0
        mask_any = df_list["has_ghs_eye"] | df_list["has_ghs_skin"]
        df_list.loc[mask_any & (df_list["우선순위"] == 2), "우선순위"] = 1

    # 우선순위순 정렬 (0 → 1 → 2), 같은 우선순위 내 GHS both 우선
    df_list = df_list.sort_values(
        ["우선순위", "has_ghs_both", "has_ghs_eye", "has_ghs_skin"],
        ascending=[True, False, False, False])

    print(f"작업 시트 대상: {len(df_list)}행")

    # 2. 기존 ing_pct_best 값 불러오기 (input_dataset_v6)
    d = pd.read_excel(V6, sheet_name="ingredient")
    d["_idx"] = range(len(d))

    # 제품코드(form_code_best) — formulation 시트에서 Formulation_ID 기준으로 가져오기
    fdf = pd.read_excel(V6, sheet_name="formulation")
    fdf["Formulation_ID"] = fdf["Formulation_ID"].astype(str)
    form_code_map = fdf.set_index("Formulation_ID")["form_code_best"].to_dict()
    formulation_code_map = fdf.set_index("Formulation_ID")["formulation_code"].to_dict()
    d_idx = d.set_index("_idx")

    # 3. 작업 시트 행 구성
    rows = []
    for i, row in df_list.iterrows():
        idx = i  # CSV에 _idx 컬럼이 있으면 그걸로, 없으면 행 번호
        ing_pct_best_old = d.loc[d.index[d["ing_pct_best"].isna()][0] if False else i, "ing_pct_best"] \
            if i < len(d) else np.nan

        # 기존 ing_pct_best (현재 저장값)
        old_val = row.get("ing_pct_best", np.nan)
        if pd.isna(old_val) or old_val == "":
            old_val = np.nan

        # 기존 pct_text (SDS 표기 참고)
        pct_text = row.get("pct_text", "")
        if pct_text is None or (isinstance(pct_text, float) and np.isnan(pct_text)):
            pct_text = ""
        pct_text = str(pct_text).strip()

        # SDS 정보
        sds_exist = row.get("SDS존재", False)
        sds_file = row.get("SDS파일", "")
        if isinstance(sds_exist, float) and np.isnan(sds_exist):
            sds_exist = False
        if not sds_exist:
            sds_file = ""
        else:
            sds_file = str(sds_file).strip()

        # 동일CAS 참조
        cas_ref = row.get("동일CAS_농도참조", np.nan)
        if pd.isna(cas_ref):
            cas_ref = np.nan
        cas_ref_n = row.get("동일CAS_참조수", 0)
        if pd.isna(cas_ref_n):
            cas_ref_n = 0

        # 현재 ing_pct_best가 이미 채워져 있으면, 그 값을 '기존 대표값'으로 표시
        # (작업자가 SDS 확인 후 새로 입력할 초기 참고용)
        기존_대표값 = old_val
        기존_pct_text_display = pct_text if pct_text and pct_text != "nan" else ""

        row_out = {
            # ---- 고정 정보 (작업자가 건드리지 않음) ----
            "순번": len(rows) + 1,
            "우선순위": row.get("우선순위", 2),
            "Formulation_ID": str(row.get("Formulation_ID", "")),
            "ingredient_name": str(row.get("ingredient_name", "")),
            "cas": str(row.get("cas", "")) if pd.notna(row.get("cas", np.nan)) else "",
            "ing_ghs_indep_eye_cat": str(row.get("ing_ghs_indep_eye_cat", "")) if pd.notna(row.get("ing_ghs_indep_eye_cat", np.nan)) else "",
            "ing_ghs_indep_skin_cat": str(row.get("ing_ghs_indep_skin_cat", "")) if pd.notna(row.get("ing_ghs_indep_skin_cat", np.nan)) else "",
            "ing_ghs_indep_sens_cat": str(row.get("ing_ghs_indep_sens_cat", "")) if pd.notna(row.get("ing_ghs_indep_sens_cat", np.nan)) else "",
            "동일CAS_농도참조": cas_ref if not np.isnan(cas_ref) and cas_ref != "" else "",
            "동일CAS_참조수": int(cas_ref_n) if not (isinstance(cas_ref_n, float) and np.isnan(cas_ref_n)) else 0,
            "SDS존재": "Y" if sds_exist else "N",
            "SDS파일": sds_file,
            # 제품코드 (formulation 시트에서 가져옴)
            "제품코드_formcode_best": str(form_code_map.get(str(row.get("Formulation_ID", "")), "")) if str(form_code_map.get(str(row.get("Formulation_ID", "")), "")) != "nan" else "",
            "원액제형코드_formulation_code": str(formulation_code_map.get(str(row.get("Formulation_ID", "")), "")) if str(formulation_code_map.get(str(row.get("Formulation_ID", "")), "")) != "nan" else "",
            "기존_ing_pct_best": 기존_대표값 if not (isinstance(기존_대표값, float) and np.isnan(기존_대표값)) else "",
            "기존_pct_text": 기존_pct_text_display,

            # ---- 작업자 입력 (작업자가 채움) ----
            "ing_pct_src_detail": "",   # ← SDS 원문 표기 (작업자가 입력)
            "ing_pct_src": "",          # ← 정보원 (작업자가 입력)
            "ing_pct_best": "",         # ← 최종 대표값 (자동 계산값 확인 후 수정 가능)

            # ---- 자동 계산 (작업자가 건드리지 않아도 됨, 확인용) ----
            "ing_pct_qual": "",
            "ing_pct_range_low": "",
            "ing_pct_range_high": "",
            "ing_pct_best_calc": "",     # 자동 대표값
            "ing_pct_best_note": "",     # 대표값 선택 규칙 메모
        }
        rows.append(row_out)

    # 4. Excel 생성
    wb = Workbook()
    ws = wb.active
    ws.title = "작업목록"

    # 스타일
    header_font = Font(bold=True, color="FFFFFF", size=10)
    header_fill = PatternFill("solid", fgColor="2F5496")
    hi_priority_fill = PatternFill("solid", fgColor="FFF2CC")  # 우선순위 0 → 노란색
    input_fill = PatternFill("solid", fgColor="E2EFDA")         # 작업자 입력 칼럼 → 연두색
    calc_fill = PatternFill("solid", fgColor="F2F2F2")          # 자동 계산 → 연회색
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")
    center = Alignment(horizontal="center", vertical="center")

    # 컬럼 정의 (표시 순서)
    cols = [
        ("순번", 6),
        ("우선순위", 8),
        ("Formulation_ID", 22),
        ("ingredient_name", 40),
        ("cas", 16),
        ("ing_ghs_indep_eye_cat", 14),
        ("ing_ghs_indep_skin_cat", 14),
        ("ing_ghs_indep_sens_cat", 14),
        ("동일CAS_농도참조", 14),
        ("동일CAS_참조수", 11),
        ("SDS존재", 8),
        ("SDS파일", 22),
        ("제품코드_formcode_best", 14),
        ("원액제형코드_formulation_code", 16),
        ("기존_ing_pct_best", 14),
        ("기존_pct_text", 14),
        # ── 작업자 입력 ──
        ("ing_pct_src_detail", 18),
        ("ing_pct_src", 24),
        ("ing_pct_best", 12),
        # ── 자동 계산 (확인용) ──
        ("ing_pct_qual", 12),
        ("ing_pct_range_low", 12),
        ("ing_pct_range_high", 12),
        ("ing_pct_best_calc", 12),
        ("ing_pct_best_note", 36),
    ]

    # 헤더
    for ci, (cname, width) in enumerate(cols, 1):
        cell = ws.cell(row=1, column=ci, value=cname)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border
        ws.column_dimensions[get_column_letter(ci)].width = width

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows)+1}"

    # 데이터
    for ri, row in enumerate(rows, 2):
        priority = row["우선순위"]
        row_fill = hi_priority_fill if priority == 0 else None

        for ci, (cname, _) in enumerate(cols, 1):
            val = row.get(cname, "")
            if val is None:
                val = ""
            if isinstance(val, float) and np.isnan(val):
                val = ""
            if isinstance(val, np.float64) and np.isnan(val):
                val = ""
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.border = border
            cell.alignment = wrap if cname in ("ingredient_name", "SDS파일", "ing_pct_src_detail",
                                                "ing_pct_src", "ing_pct_best_note") else Alignment(vertical="top")

            # 입력 칼럼 강조
            if cname in ("ing_pct_src_detail", "ing_pct_src", "ing_pct_best"):
                cell.fill = input_fill
                cell.font = Font(bold=False, color="1F4E79")
            elif cname in ("ing_pct_qual", "ing_pct_range_low", "ing_pct_range_high",
                           "ing_pct_best_calc", "ing_pct_best_note"):
                cell.fill = calc_fill
                cell.font = Font(italic=True, color="595959")
            elif row_fill:
                cell.fill = row_fill

        # 우선순위 0 행은 노란색 + 볼드 Formulation_ID
        if priority == 0:
            ws.cell(row=ri, column=3).font = Font(bold=True, color="C00000")

    # 5. 범례 시트
    ws2 = wb.create_sheet("범례_입력규칙")
    legend = [
        ("범주 표기 입력 규칙 — ing_pct_src_detail", ""),
        ("", ""),
        ("정확한 값", '예: "5.2%" → ing_pct_best = 5.2'),
        ("범위 (중간값)", '예: "10~18%" → ing_pct_best = 14.0 (중간값)'),
        ("범위 (중간값, ~로 연결)", '예: "10~18%" = "10-18%" = "10∼18%" → 중간값'),
        ("상한 (≤)", '예: "≤2%" → ing_pct_best = 2.0 (상한값 사용, 보수적)'),
        ("하한 (≥)", '예: "≥90%" → ing_pct_best = 90.0 (하한값 사용)'),
        ("미만 (<)", '예: "<1%" → ing_pct_best = 1.0 (상한값 사용, 보수적)'),
        ("초과 (>)", '예: ">50%" → ing_pct_best = 50.0 (하한값 사용)'),
        ("근사 (~ 또는 약)", '예: "~5%" 또는 "약 5%" → ing_pct_best = 5.0'),
        ("traces / 미량", '예: "traces", "미량" → ing_pct_best = (0 또는 미량값 중 선택, 메모)'),
        ("계산 불가 / 확인 불가", '예: "계산불가", "확인불가" → ing_pct_best = 비움 (빈칸)'),
        ("", ""),
        ("주의", "값을 만들지 않는다 — SDS에 없는 값을 추정해서 넣지 않는다."),
        ("주의", "범위·상한·하한·근사는 '중간값·상한·하한·근사값' 중 하나를 선택하되,"),
        ("주의", "선택한 값과 선택 이유를 ing_pct_note(자동 계산 메모)에 기록된다."),
        ("주의", "실제 SDS 표기와 다르게 환산한 경우, ing_pct_src_detail에 원문 표기를 정확히 입력한다."),
        ("", ""),
        ("정보원 입력 (ing_pct_src)", ""),
        ("형식", '"수동_SDS판독_[SDS파일명]_[Section/절]"'),
        ("예", '"수동_SDS판독_정채윤_GHS 조사.xlsx_Section3"'),
        ("예", '"수동_SDS판독_보경_dataset.xlsx_Section3"'),
        ("", ""),
        ("우선순위", ""),
        ("0 (노란색 행)", "GHS 눈·피부 동시 보유 — 농도 채우면 CT 가산식에 직접 영향. 최우선 작업."),
        ("1", "GHS 눈 또는 피부 한쪽만 보유 — 중간 우선순위."),
        ("2 (흰색 행)", "GHS 없음 — 농도만 채움. CT 가산식에는 간접 영향. 시간 대비 효과 낮음."),
    ]
    for ri, (a, b) in enumerate(legend, 1):
        c1 = ws2.cell(row=ri, column=1, value=a)
        c2 = ws2.cell(row=ri, column=2, value=b)
        c2.alignment = Alignment(wrap_text=True)
        if a.startswith(("정확한 값", "범위", "상한", "하한", "미만", "초과", "근사", "traces", "계산 불가")):
            c1.font = Font(bold=True)
        if a == "주의":
            c1.font = Font(bold=True, color="C00000")

    ws2.column_dimensions["A"].width = 26
    ws2.column_dimensions["B"].width = 90

    # 6. 저장
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"저장: {OUT}")
    print(f"  작업 행: {len(rows)}행 (우선순위 0: {sum(1 for r in rows if r['우선순위']==0)}행)")
    print()
    print("작업 순서:")
    print("  1. SDS 파일 열기 (SDS파일 컬럼 참조)")
    print("  2. Section 3에서 해당 성분의 함량(%) 확인")
    print("  3. ing_pct_src_detail에 SDS 원문 표기 입력 (예: '10~18%')")
    print("  4. ing_pct_src에 정보원 입력 (예: '수동_SDS판독_정채윤_GHS 조사.xlsx_Section3')")
    print("  5. ing_pct_best_calc / ing_pct_best_note 확인 → 마음에 들면 ing_pct_best에 그대로")
    print("     마음에 안 들면 ing_pct_best를 직접 수정")
    print("  6. 저장 후, 이 시트를 읽어 오버레이 CSV/Excel에 반영")


if __name__ == "__main__":
    main()
