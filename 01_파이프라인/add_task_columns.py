# -*- coding: utf-8 -*-
"""
dataset_배정_검증완료_20260824.xlsx 에 이번 주 분담 작업(pH수집·성분GHS조사·SDS11판독·
라벨충돌판정·성분판정재확인)을 바로 기입할 수 있는 작업 컬럼을 추가한다.
기존 컬럼·서식·조건부서식은 보존하고, 대상 행에만 새 컬럼을 채운다.
"""
from pathlib import Path

import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = str(Path(__file__).resolve().parent.parent)
PATH = f"{ROOT}/dataset_배정_검증완료_20260824.xlsx"

TASK_FILL = PatternFill("solid", fgColor="FFF3CD")   # 작업대상 행 표시(연한 노랑)
HEAD_FILL = PatternFill("solid", fgColor="7D3C98")   # 신규 작업컬럼 헤더(보라 — 기존 파랑과 구분)
HEAD_FONT = Font(color="FFFFFF", bold=True, size=9)

wb = openpyxl.load_workbook(PATH)


def add_columns(ws, names):
    """헤더 끝에 새 컬럼들을 추가하고 {name: col_idx(1-based)} 반환."""
    start = ws.max_column + 1
    idx = {}
    for i, name in enumerate(names):
        c = start + i
        cell = ws.cell(1, c, name)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        idx[name] = c
    return idx


def add_dropdown(ws, col, values, max_row):
    dv = DataValidation(type="list", formula1=f'"{",".join(values)}"', allow_blank=True)
    ws.add_data_validation(dv)
    L = get_column_letter(col)
    dv.add(f"{L}2:{L}{max_row}")


def header_index(ws):
    return {c.value: i + 1 for i, c in enumerate(ws[1]) if c.value}


# ══════════════════════════════════════════════════════════════
# 1. 특성 시트 — pH 수집 (이서윤 · 김보경)
# ══════════════════════════════════════════════════════════════
ws = wb["특성"]
C = header_index(ws)
new = add_columns(ws, ["pH작업_담당", "pH작업_확보값", "pH작업_근거URL", "pH작업_메모"])

ph = pd.read_csv("/tmp/wk_ph.csv")
ph_map = dict(zip(ph["Formulation_ID"], ph["할당"]))

fid_col = C["Formulation_ID"]
n_ph = 0
for r in range(2, ws.max_row + 1):
    fid = ws.cell(r, fid_col).value
    who = ph_map.get(fid)
    if who:
        ws.cell(r, new["pH작업_담당"]).value = who
        for cname in ("pH작업_담당", "pH작업_확보값", "pH작업_근거URL", "pH작업_메모"):
            ws.cell(r, new[cname]).fill = TASK_FILL
        n_ph += 1
print(f"[특성] pH작업 대상 표시: {n_ph}행")

# ══════════════════════════════════════════════════════════════
# 2. 성분 시트 — GHS 조사(정채윤) + 판정 재확인(한서윤)
# ══════════════════════════════════════════════════════════════
ws = wb["성분"]
C = header_index(ws)
new = add_columns(ws, [
    "GHS조사_CAS대표행", "GHS조사_담당", "GHS조사_눈", "GHS조사_피부", "GHS조사_감작",
    "GHS조사_근거URL", "GHS조사_메모",
    "한서윤_최종판정", "한서윤_메모",
])

ghs = pd.read_csv("/tmp/wk_ghs.csv")
target_cas = set(ghs["CAS"].astype(str))
seen_cas = set()

n_ghs, n_dup, n_review = 0, 0, 0
for r in range(2, ws.max_row + 1):
    cas_best = ws.cell(r, C.get("cas", C.get("Formulation_ID"))).value  # fallback 안전장치
    # 실제 대표 CAS 컬럼 사용
    cas_val = ws.cell(r, C["cas"]).value
    cas_str = str(cas_val).strip() if cas_val is not None else ""

    if cas_str in target_cas:
        if cas_str not in seen_cas:
            seen_cas.add(cas_str)
            ws.cell(r, new["GHS조사_CAS대표행"]).value = cas_str
            ws.cell(r, new["GHS조사_담당"]).value = "정채윤"
            for cname in ("GHS조사_CAS대표행", "GHS조사_담당", "GHS조사_눈", "GHS조사_피부",
                          "GHS조사_감작", "GHS조사_근거URL", "GHS조사_메모"):
                ws.cell(r, new[cname]).fill = TASK_FILL
            n_ghs += 1
        else:
            ws.cell(r, new["GHS조사_CAS대표행"]).value = f"중복({cas_str}, 대표행 참조)"
            n_dup += 1

    method = ws.cell(r, C["검증방식"]).value
    verdict = ws.cell(r, C["검증여부"]).value
    if method == "human(규칙불일치)" or verdict == "제외":
        ws.cell(r, new["한서윤_최종판정"]).fill = TASK_FILL
        ws.cell(r, new["한서윤_메모"]).fill = TASK_FILL
        n_review += 1

add_dropdown(ws, new["한서윤_최종판정"], ["확정", "판단보류", "제외확정"], ws.max_row)
print(f"[성분] GHS조사 대표행: {n_ghs} (목표 331), 중복행 표시: {n_dup}, 한서윤 재확인 대상: {n_review} (목표 699)")

# ══════════════════════════════════════════════════════════════
# 3. 독성코드 시트 — SDS §11 판독(최소빈) + 라벨충돌 판정(한서윤)
# ══════════════════════════════════════════════════════════════
ws = wb["독성코드"]
C = header_index(ws)
new = add_columns(ws, [
    "S11_담당",
    "S11_원문_눈", "S11_판독_눈",
    "S11_원문_피부", "S11_판독_피부",
    "S11_원문_감작", "S11_판독_감작",
    "S11_메모",
    "충돌_담당", "충돌_눈_최종", "충돌_피부_최종", "충돌_감작_최종", "충돌_메모",
])

s11 = pd.read_csv("/tmp/wk_s11.csv")
s11_by_fid = {}
for _, row in s11.iterrows():
    s11_by_fid.setdefault(row["Formulation_ID"], []).append((row["엔드포인트"], row["원문(§11)"]))

conflict = pd.read_csv("/tmp/wk_conflict.csv")
conflict_by_fid = {}
for _, row in conflict.iterrows():
    conflict_by_fid.setdefault(row["Formulation_ID"], []).append(row["엔드포인트"])

EP_COL = {"눈": "눈", "피부": "피부", "감작": "감작"}
n_s11, n_conf = 0, 0
for r in range(2, ws.max_row + 1):
    fid = ws.cell(r, C["Formulation_ID"]).value

    eps = s11_by_fid.get(fid)
    if eps:
        ws.cell(r, new["S11_담당"]).value = "최소빈"
        ws.cell(r, new["S11_담당"]).fill = TASK_FILL
        for ep, raw in eps:
            oc = new[f"S11_원문_{EP_COL[ep]}"]
            pc = new[f"S11_판독_{EP_COL[ep]}"]
            ws.cell(r, oc).value = str(raw)[:900]
            ws.cell(r, oc).fill = TASK_FILL
            ws.cell(r, pc).fill = TASK_FILL
        ws.cell(r, new["S11_메모"]).fill = TASK_FILL
        n_s11 += 1

    ceps = conflict_by_fid.get(fid)
    if ceps:
        ws.cell(r, new["충돌_담당"]).value = "한서윤"
        ws.cell(r, new["충돌_담당"]).fill = TASK_FILL
        for ep in ceps:
            cc = new[f"충돌_{EP_COL[ep]}_최종"]
            ws.cell(r, cc).fill = TASK_FILL
        ws.cell(r, new["충돌_메모"]).fill = TASK_FILL
        n_conf += 1

for cname in ("S11_판독_눈", "S11_판독_피부", "S11_판독_감작"):
    add_dropdown(ws, new[cname], ["1", "2A", "2B", "3", "1A", "1B", "1C", "정보없음", "판단보류"], ws.max_row)
for cname in ("충돌_눈_최종", "충돌_피부_최종", "충돌_감작_최종"):
    add_dropdown(ws, new[cname], ["확정", "판단보류"], ws.max_row)

print(f"[독성코드] S11 판독 대상 제형: {n_s11} (목표 최대 797행이 이 제형들에 분산), 라벨충돌 대상 제형: {n_conf} (목표 222행이 분산)")

wb.save(PATH)
print("\n저장 완료 →", PATH)
