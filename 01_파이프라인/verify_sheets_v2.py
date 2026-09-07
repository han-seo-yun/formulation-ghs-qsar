#!/usr/bin/env python3
"""dataset_배정_검증미완.xlsx 를 검증완료분_260830.xlsx 와 같은 규칙엔진으로 검증한다.

work/verify_sheets.py 의 규칙 함수(verify_ingredient/formulation/toxicity/physchem)를
그대로 재사용 — '같은 방식'을 코드 재사용으로 보장한다.

차이점(성분 시트 한정): 이번 라운드에 팀이 2차 검증자로서 검증여부를 이미
2,105행 채워 놓았다(재검토필요/검증완료/제외/정보없음 — '제외'는 표준 4값 드롭다운
밖의 신규 값). 이 사람 판정을 규칙엔진이 덮어쓰지 않는다 — 대신:
  - 사람 판정이 있으면 그것을 최종 검증여부로 유지하고 검증방식="human"
  - 규칙엔진도 같은 행에 대해 별도로 판정해 교차검증하고, 불일치하면 검증근거에 flag
  - 사람 판정이 없는 3,182행만 규칙엔진 판정을 그대로 채움(검증방식="rule")
다른 3개 시트(제형코드/독성코드/특성)는 원본과 셀 단위로 동일(사람 판정 없음)이므로
규칙엔진만 그대로 적용한다.

산출: dataset_배정_검증완료_20260824.xlsx (원본 dataset_배정_검증미완.xlsx 무손상)
"""
import json
import sys
from collections import Counter, defaultdict

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, "/Users/hanseoyun/Desktop/260830/work")
from verify_sheets import (  # noqa: E402  (규칙 재사용)
    verify_ingredient, verify_formulation, verify_toxicity, verify_physchem,
    load_cipac, s,
)

SRC = "/Users/hanseoyun/Desktop/260830/dataset_배정_검증미완.xlsx"
DST = "/Users/hanseoyun/Desktop/260830/dataset_배정_검증완료_20260824.xlsx"
REPORT = "/Users/hanseoyun/Desktop/260830/work/verify/verify_report_20260824.json"

OK, NONE_, REVIEW, UNK = "검증완료", "정보없음", "재검토필요", "미확인"
EXCLUDE = "제외"
HUMAN_VALUES = {OK, NONE_, REVIEW, UNK, EXCLUDE}

SHEETS = {
    "성분": verify_ingredient,
    "제형코드": verify_formulation,
    "독성코드": verify_toxicity,
    "특성": verify_physchem,
}


def compat(human, rule):
    """사람 판정과 규칙 판정이 '같은 결론'으로 볼 수 있는지. 제외는 규칙엔진에 없는
    범주라 항상 별도로 표시(불일치로 세지 않되 human 전용으로 표시)."""
    if human == EXCLUDE:
        return "human_only"
    return "agree" if human == rule else "conflict"


def main():
    import shutil
    load_cipac()
    shutil.copy(SRC, DST)
    wb = openpyxl.load_workbook(DST)
    report = {}

    for name, fn in SHEETS.items():
        ws = wb[name]
        merged = [str(m) for m in ws.merged_cells.ranges]
        if merged:
            for m in list(ws.merged_cells.ranges):
                keep = ws.cell(m.min_row, m.min_col).value
                ws.unmerge_cells(str(m))
                ws.cell(m.min_row, m.min_col).value = keep
            print(f"[{name}] 병합셀 {len(merged)}건 해제")
        hdr = [c.value for c in ws[1]]
        C = {h: i for i, h in enumerate(hdr) if h}
        vcol = C["검증여부"] + 1
        ncol = ws.max_column + 1
        while ws.cell(1, ncol).value:
            ncol += 1
        ws.cell(1, ncol).value = "검증근거"
        ws.cell(1, ncol + 1).value = "검증방식"

        tally = Counter()
        method_tally = Counter()
        conflicts = []
        excluded_rows = []

        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            rowlist = list(row)
            rule_verdict, rule_why = fn(rowlist, C)
            existing = s(rowlist[C["검증여부"]])

            if existing in HUMAN_VALUES:
                verdict = existing
                method = "human"
                rel = compat(existing, rule_verdict)
                if rel == "conflict":
                    why = (f"[팀 2차검증 유지] 사람판정={existing} vs 규칙판정={rule_verdict} "
                           f"불일치 — 규칙사유: {rule_why[:300]}")
                    method = "human(규칙불일치)"
                    conflicts.append({
                        "row": i, "product_name": s(rowlist[C.get("product_name", 1)]),
                        "human": existing, "rule": rule_verdict, "rule_why": rule_why[:200],
                    })
                elif rel == "human_only":
                    why = f"[팀 2차검증: 제외 판정 — 규칙엔진 범주 밖] 규칙 참고판정={rule_verdict} ({rule_why[:200]})"
                    excluded_rows.append({
                        "row": i, "product_name": s(rowlist[C.get("product_name", 1)]),
                        "ingredient_name": s(rowlist[C.get("ingredient_name", 3)]),
                    })
                else:
                    why = f"[팀 2차검증 = 규칙판정 일치: {existing}] {rule_why[:300]}"
            else:
                verdict = rule_verdict
                method = "rule"
                why = rule_why

            ws.cell(i, vcol).value = verdict
            ws.cell(i, ncol).value = why[:480]
            ws.cell(i, ncol + 1).value = method
            tally[verdict] += 1
            method_tally[method] += 1

        ws.data_validations.dataValidation = []
        dv = DataValidation(type="list",
                             formula1='"미확인,검증완료,정보없음,재검토필요,제외"',
                             allow_blank=True)
        ws.add_data_validation(dv)
        col = openpyxl.utils.get_column_letter(vcol)
        dv.add(f"{col}2:{col}{ws.max_row}")

        report[name] = {
            "total": sum(tally.values()),
            "tally": dict(tally),
            "method_tally": dict(method_tally),
            "n_conflicts": len(conflicts),
            "conflicts_sample": conflicts[:30],
            "n_excluded": len(excluded_rows),
            "excluded_sample": excluded_rows[:30],
        }
        print(f"[{name}] " + "  ".join(f"{k}={v}" for k, v in tally.most_common()) +
              f"   | 방식: " + "  ".join(f"{k}={v}" for k, v in method_tally.most_common()) +
              (f"   | 불일치 {len(conflicts)}건" if conflicts else ""))

    wb.save(DST)
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ {DST}\n→ {REPORT}")


if __name__ == "__main__":
    main()
