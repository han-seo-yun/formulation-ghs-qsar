#!/usr/bin/env python3
"""화학연구원 공유용 데이터 패키지 — 산출: 06_화학연구원_공유_260916/

## 무엇을 만드는가

화학연구원에 ①제품코드(Formulation_ID)별 성분 리스트 ②확보된 MSDS 근거자료를
전달하기 위한 1차 패키지. 성분 리스트(1,675개 제형·5,287개 성분행)는
`input_dataset_v6.xlsx`에 이미 완비돼 있으므로 그대로 옮긴다. MSDS 근거는
`formulation_audit_team_share_highlighted_only_20260630/`(팀 감사 산출물,
146MB, git 미추적)에 있는 것만 옮긴다 — 이번 판에는 새 문서를 웹에서 찾아오지
않는다(아래 '하지 않은 것' 참조).

## 반드시 알아야 할 것 — "MSDS 근거 670건"은 확보 성공분이 아니다

이 저장소를 처음 조사했을 때 "제형의 40%(670/1,675)에 MSDS가 있다"고 잘못
해석했었다. 실제로는 반대에 가깝다:

- 그 670건의 감사상태는 EVIDENCE_WEAK·MISMATCH·NO_WORKBOOK_INGREDIENTS 뿐이고
  **신뢰도 확정(MATCH)은 0건이다.** `formulation_audit_team_share_highlighted_
  only_20260630/artifacts/share_packages/*.summary.json` 의 selection_rule 이
  이미 "review-focused... 검토가 필요한 행만 담았다"고 명시하고 있다.
- 반대로 **신뢰도가 가장 높은 MATCH 881건은 로컬 파일이 아예 없다** — 대부분
  (694건, `Data_Quality=='original'`)은 NTP 자체 시험자료에 성분이 이미 직접
  보고돼 있어 애초에 외부 확인이 불필요했던 경우다. 나머지 243건은 외부에서
  찾았지만 그 출처 파일이 남지 않은 경우("출처소실")다.
- 그래서 이 스크립트는 파일 유무를 "확보/공백"이 아니라
  `제품단위 / 대체근거 / 불필요_NTP자체보고 / 출처소실 / 공백` 5가지로 나눠
  `제형` 시트에 싣는다. 셋 다 "파일 없음"이지만 이유가 다르다.
- 대체근거 101건(`Supplemental_*`)은 제품 고유 문서가 아니라 순수 성분의
  SDS를 CAS로 매칭한 것이라 `대체근거_참고/`로 따로 분리한다.

## 하지 않은 것 (다음 단계로 분리 — 이번 실행 범위 아님)

- **URL 230건 재수집**: 진짜 공백 311건 중 293건은 `Ingredient_Source` 열에
  원문 URL이 남아 있고 그중 230건은 기존 다운로드 시도 기록(source_manifest.csv)
  에 없는 신규 후보다. 이번 패키지의 `데이터_이슈` 시트에 그 URL 목록만
  남기고, 실제 재수집은 하지 않는다(사용자 결정: "지금 데이터로 먼저 완성").
- **CAS 1,458건 PubChem 보강**: 이름은 있고 CAS가 없는 성분행. 다음 단계.
- 원본 값은 어디도 고치지 않는다(MIX408 중복행 등은 목록만 남긴다).

읽기 전용 입력: input_dataset_v6.xlsx,
formulation_audit_team_share_highlighted_only_20260630/**(전체, 복사만),
성분표_결함_확인용_260915.xlsx(있으면 재사용 — 없으면 동일 규칙으로 재계산).
쓰기 대상은 06_화학연구원_공유_260916/ 하나뿐이다.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

import lib_model as L

AUDIT_ROOT = L.ROOT / "formulation_audit_team_share_highlighted_only_20260630"
AUDIT_XLSX = AUDIT_ROOT / "formulation_ingredients_master_audited.xlsx"
MANIFEST_CSV = AUDIT_ROOT / "ingredient_source_audit" / "source_manifest.csv"
DEFECT_XLSX = Path("/Users/hanseoyun/Desktop/성분표_결함_확인용_260915.xlsx")

OUT = L.ROOT / "06_화학연구원_공유_260916"
MSDS_DIR = OUT / "MSDS_하이라이트사본"
SURR_DIR = OUT / "대체근거_참고"
log = L.make_logger(OUT / "패키지_생성.log")

EXTERNAL_TIER = ("search_result", "cleaned_unstructured", "from_name", "not_found")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def split_paths(v) -> list[str]:
    if pd.isna(v):
        return []
    return [p.strip() for p in str(v).split("|") if p.strip()]


# ------------------------------------------------------------------ 1. 원천 읽기
log("=== 화학연구원 공유 패키지 생성 ===")
FORM = pd.ExcelFile(L.V6).parse("formulation")
ING = pd.ExcelFile(L.V6).parse("ingredient")
assert len(FORM) == 1675 and len(ING) == 5287, "v6 행수 이탈 — 입력 변경 감지"

AUD = pd.ExcelFile(AUDIT_XLSX).parse("Formulations")
assert set(AUD.Formulation_ID) == set(FORM.Formulation_ID), \
    "감사워크북과 v6 의 Formulation_ID 집합이 다르다"

MAN = pd.read_csv(MANIFEST_CSV)
url_by_hash = {}
for _, r in MAN.iterrows():
    hp = r.get("highlighted_path")
    if pd.notna(hp):
        h = Path(hp).stem.replace("_highlighted", "")
        url_by_hash[h] = r["url"]


def domain_of(url) -> str:
    if not isinstance(url, str) or not url:
        return "출처불명"
    try:
        net = urlparse(url).netloc.replace("www.", "")
        return net or "출처불명"
    except Exception:
        return "출처불명"


# ------------------------------------------------------------------ 2. MSDS 확보상태 분류
has_primary = AUD["Audit_Highlighted_Source_File"].notna()
has_supp = AUD["Supplemental_Highlighted_Source_File"].notna()

상태 = pd.Series("공백", index=AUD.index)
상태[~has_primary & ~has_supp & (AUD.Data_Quality == "original")] = "불필요_NTP자체보고"
상태[~has_primary & ~has_supp & (AUD.Data_Quality != "original")
    & (AUD.Audit_Status == "MATCH")] = "출처소실"
상태[has_supp & ~has_primary] = "대체근거"
상태[has_primary] = "제품단위"
AUD["MSDS_확보상태"] = 상태

CNT = 상태.value_counts()
log(f"MSDS_확보상태 분포: {CNT.to_dict()}")
assert CNT.get("제품단위", 0) == 569, f"제품단위 {CNT.get('제품단위')} != 569"
assert CNT.get("대체근거", 0) == 101, f"대체근거 {CNT.get('대체근거')} != 101"
assert CNT.get("불필요_NTP자체보고", 0) == 694, \
    f"불필요_NTP자체보고 {CNT.get('불필요_NTP자체보고')} != 694"
assert CNT.get("출처소실", 0) == 243, f"출처소실 {CNT.get('출처소실')} != 243"
assert CNT.get("공백", 0) == 68, f"공백 {CNT.get('공백')} != 68"

# 재수집 후보 URL — '공백'·'출처소실' 두 상태에서 Ingredient_Source 에 남은 URL
URL_RE = re.compile(r"https?://\S+")


def first_url(s):
    if pd.isna(s):
        return None
    m = URL_RE.findall(str(s))
    return m[0] if m else None


AUD["재수집후보_URL"] = AUD["Ingredient_Source"].map(first_url)
gap = AUD[AUD.MSDS_확보상태.isin(["공백", "출처소실"])]
n_gap_url = gap["재수집후보_URL"].notna().sum()
log(f"진짜 공백(공백+출처소실) {len(gap)}건 중 재수집후보 URL 있는 행 {n_gap_url}건")

# ------------------------------------------------------------------ 3. 파일 복사
if MSDS_DIR.exists():
    shutil.rmtree(MSDS_DIR)
if SURR_DIR.exists():
    shutil.rmtree(SURR_DIR)
MSDS_DIR.mkdir(parents=True)
SURR_DIR.mkdir(parents=True)

MANIFEST_ROWS = []


def copy_primary(fid, paths, audit_status):
    dst_dir = MSDS_DIR / fid
    dst_dir.mkdir(parents=True, exist_ok=True)
    for i, rel in enumerate(paths, 1):
        src = AUDIT_ROOT / rel
        h = Path(rel).stem.replace("_highlighted", "")
        domain = domain_of(url_by_hash.get(h))
        dst = dst_dir / f"{fid}__{i}__{domain}{Path(rel).suffix}"
        shutil.copy2(src, dst)
        MANIFEST_ROWS.append({
            "Formulation_ID": fid, "종류": "제품단위", "등급": "",
            "증거_신뢰도": audit_status, "경로": str(dst.relative_to(OUT)),
            "원경로": rel, "sha256": sha256(dst), "size_bytes": dst.stat().st_size,
        })


SLUG_RE = re.compile(r"_[0-9a-f]{16}_highlighted$")


def copy_supplemental(fid, paths, grade):
    dst_dir = SURR_DIR / fid
    dst_dir.mkdir(parents=True, exist_ok=True)
    for i, rel in enumerate(paths, 1):
        src = AUDIT_ROOT / rel
        stem = Path(rel).stem
        slug = SLUG_RE.sub("", stem) or stem
        tag = f"{i}" if len(paths) > 1 else ""
        dst = dst_dir / f"{fid}__surrogate{tag}__{slug}{Path(rel).suffix}"
        shutil.copy2(src, dst)
        MANIFEST_ROWS.append({
            "Formulation_ID": fid, "종류": "대체근거", "등급": grade,
            "증거_신뢰도": "", "경로": str(dst.relative_to(OUT)),
            "원경로": rel, "sha256": sha256(dst), "size_bytes": dst.stat().st_size,
        })


for _, row in AUD.iterrows():
    fid = row.Formulation_ID
    prim = split_paths(row.Audit_Highlighted_Source_File)
    supp = split_paths(row.Supplemental_Highlighted_Source_File)
    if prim:
        copy_primary(fid, prim, row.Audit_Status)
    elif supp:
        copy_supplemental(fid, supp, row.get("Supplemental_Recovery_Source_Level") or "미표기")

MANIFEST = pd.DataFrame(MANIFEST_ROWS)
n_prod_files = (MANIFEST["종류"] == "제품단위").sum()
n_surr_files = (MANIFEST["종류"] == "대체근거").sum()
n_surr_forms = int(CNT.get("대체근거", 0))          # 제형 수(101). 파일 수(134)와 다르다 —
                                                      # 24개 제형이 대체근거 파일을 2개씩 가짐
log(f"복사된 파일: 제품단위 {n_prod_files} · 대체근거 {n_surr_files}"
    f"(제형 {n_surr_forms}개)")
assert (MANIFEST["종류"] == "제품단위").groupby(MANIFEST.Formulation_ID).any().sum() == 569
assert (MANIFEST["종류"] == "대체근거").groupby(MANIFEST.Formulation_ID).any().sum() == 101
MANIFEST.to_csv(OUT / "근거매니페스트.csv", index=False, encoding="utf-8-sig")

# ------------------------------------------------------------------ 4. 데이터 이슈 (결함 목록, 값은 안 고침)
DEFECT_SHEETS = ("1_성분아닌행", "2_MIX408_중복", "3_고농도90이상",
                  "4_유황계이름분산", "5_농도합초과105")


def recompute_defects(I: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """데스크탑 검토 파일이 없을 때의 대체 경로. 이전 검토에서 쓴 규칙과 동일하다."""
    nm = I.ing_name_best
    out = {}
    문장형 = nm.str.split().str.len().fillna(0) >= 8
    이름결측 = ~I.ing_name_best.notna()
    주석정정 = nm.str.contains("정정|제외 권고|실제 성분 아님|구조식", na=False)
    out["1_성분아닌행"] = I[문장형 | 이름결측 | 주석정정]
    out["2_MIX408_중복"] = I[I.Formulation_ID == "MIX408"]
    out["3_고농도90이상"] = I[I.ing_pct_best >= 90].sort_values("ing_pct_best", ascending=False)
    sulfur = (nm.str.lower().str.contains("sulfur|sulphur", na=False)
              & ~nm.str.contains("sulfonate|sulfonic|sulfamic|sulfate", case=False, na=False))
    out["4_유황계이름분산"] = I[sulfur]
    s = I.groupby("Formulation_ID").ing_pct_best.sum()
    out["5_농도합초과105"] = I[I.Formulation_ID.isin(s[s > 105].index)]
    return out


defect_frames = []
if DEFECT_XLSX.exists():
    log(f"결함 목록 — 기존 검토 파일 재사용: {DEFECT_XLSX}")
    xl = pd.ExcelFile(DEFECT_XLSX)
    for sn in DEFECT_SHEETS:
        if sn in xl.sheet_names:
            defect_frames.append(xl.parse(sn))
else:
    log("결함 목록 — 검토 파일 없음, 동일 규칙으로 재계산")
    for sn, df in recompute_defects(ING).items():
        df = df.copy()
        df.insert(0, "결함유형", sn)
        defect_frames.append(df)

완전미상 = ING[ING.ing_name_best.isna() & ING.ing_cas_best.isna()].copy()
완전미상.insert(0, "결함유형", "6_이름CAS모두미상")
defect_frames.append(완전미상)
DEFECT_ALL = pd.concat(defect_frames, ignore_index=True, sort=False)
log(f"완전미상(이름·CAS 모두 없음) {len(완전미상)}행")
assert len(완전미상) == 227, f"완전미상 {len(완전미상)} != 227"

# ------------------------------------------------------------------ 5. 성분코드별 성분리스트.xlsx
FORM_OUT = FORM[["Formulation_ID", "product_name", "formulation_type_ko",
                 "y_eye", "y_skin", "y_sens"]].merge(
    AUD[["Formulation_ID", "MSDS_확보상태", "Audit_Status", "재수집후보_URL"]],
    on="Formulation_ID", how="left")
FORM_OUT = FORM_OUT.rename(columns={"Audit_Status": "증거_신뢰도"})

ING_COLS = ["Formulation_ID", "ing_name_best", "ing_cas_best", "ing_pct_best",
            "ing_role_final", "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens",
            "ing_ghs_indep_eye_cat", "ing_ghs_indep_eye_tier",
            "ing_ghs_indep_skin_cat", "ing_ghs_indep_skin_tier",
            "ing_ghs_indep_sens_cat", "ing_ghs_indep_sens_tier", "ing_src"]
ING_OUT = ING[ING_COLS]

요약 = pd.DataFrame([
    ("전체 제형(제품코드)", len(FORM_OUT), "input_dataset_v6.xlsx formulation 시트"),
    ("전체 성분행", len(ING_OUT), "input_dataset_v6.xlsx ingredient 시트"),
    ("MSDS 제품단위", n_prod_files, "감사상태 전량 EVIDENCE_WEAK/MISMATCH — MATCH 0건, 검토대상 원자료"),
    ("MSDS 대체근거", n_surr_forms,
     f"제품 고유 문서 아님(파일 {n_surr_files}개, 일부 제형은 2개). "
     "순수성분 SDS를 CAS로 매칭, 등급 낮음"),
    ("근거없음·불필요(NTP자체보고)", int((AUD.MSDS_확보상태 == "불필요_NTP자체보고").sum()),
     "NTP 시험자료에 성분이 직접 보고돼 외부 MSDS가 원래 불필요"),
    ("근거없음·출처소실", int((AUD.MSDS_확보상태 == "출처소실").sum()),
     "외부에서 찾아 신뢰도는 MATCH였으나 출처 파일이 안 남음"),
    ("근거없음·공백", int((AUD.MSDS_확보상태 == "공백").sum()),
     f"진짜 공백. 재수집 후보 URL {n_gap_url}건 존재(이번 판 미수집, 데이터_이슈 참조)"),
    ("이름 있고 CAS 없는 성분행", int((ING.ing_name_best.notna() & ING.ing_cas_best.isna()).sum()),
     "PubChem 보강 대상(다음 단계, 이번 판 미시행)"),
    ("이름·CAS 모두 없는 성분행", len(완전미상), "성분 식별 불가. 값을 만들지 않고 목록만 표시"),
], columns=["구분", "건수", "설명"])

XLSX = OUT / "제품코드별_성분리스트.xlsx"
with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
    요약.to_excel(w, sheet_name="요약", index=False)
    FORM_OUT.to_excel(w, sheet_name="제형", index=False)
    ING_OUT.to_excel(w, sheet_name="성분", index=False)
    DEFECT_ALL.to_excel(w, sheet_name="데이터_이슈", index=False)

from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(XLSX)
for sn in wb.sheetnames:
    ws = wb[sn]
    ws.freeze_panes = "A2"
    for col in ws.columns:
        letter = col[0].column_letter
        width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 42)
wb.save(XLSX)
log(f"xlsx 저장 — 시트 {wb.sheetnames}")

# ------------------------------------------------------------------ 6. PACKAGE_VERSION.json
version = {
    "생성_기준_입력": {
        "input_dataset_v6.xlsx_sha256": sha256(L.V6),
        "formulation_ingredients_master_audited.xlsx_sha256": sha256(AUDIT_XLSX),
    },
    "건수_스냅샷": {
        "제형": len(FORM_OUT), "성분행": len(ING_OUT),
        "MSDS_제품단위": int(n_prod_files),
        "MSDS_대체근거_제형수": n_surr_forms, "MSDS_대체근거_파일수": int(n_surr_files),
        "근거없음_불필요": int((AUD.MSDS_확보상태 == "불필요_NTP자체보고").sum()),
        "근거없음_출처소실": int((AUD.MSDS_확보상태 == "출처소실").sum()),
        "근거없음_공백": int((AUD.MSDS_확보상태 == "공백").sum()),
        "재수집후보_URL": int(n_gap_url),
    },
    "다음_단계_미시행": ["URL 재수집(재수집후보_URL 열 참조)", "CAS 미상 성분 PubChem 보강"],
}
(OUT / "PACKAGE_VERSION.json").write_text(
    json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")

# ------------------------------------------------------------------ 7. README
README = f"""# 화학연구원 공유 패키지 — 신작물보호제 GHS 분류 데이터

## 이 문서를 먼저 읽어야 하는 이유

`MSDS_하이라이트사본/`의 파일은 **원본이 아니다.** 팀 감사 도구가 CAS·성분명에
하이라이트 마킹을 입힌 파생 사본이며, 순수 원본(original_path)은 로컬 어디에도
남아 있지 않다. 또한 이 파일들은 "확보에 성공한 신뢰할 만한 근거"가 아니라
**감사 과정에서 검토가 필요하다고 걸린 사례**다(신뢰도 확정 MATCH 0건,
전량 EVIDENCE_WEAK·MISMATCH). 반대로 신뢰도가 가장 높은 사례(MATCH 881건)는
파일이 남아 있지 않다 — 대부분 NTP 자체 시험자료에 성분이 이미 직접 보고돼
외부 확인이 필요 없었기 때문이다. `제품코드별_성분리스트.xlsx`의 `요약`·`제형`
시트에 이 구분을 그대로 표시했으니, 감사상태(`증거_신뢰도`) 없이 문서 존재
여부만으로 신뢰도를 판단하지 않기를 권한다.

## 구성

| 항목 | 내용 |
|---|---|
| `제품코드별_성분리스트.xlsx` | 요약 / 제형(1,675) / 성분(5,287) / 데이터_이슈 4개 시트 |
| `MSDS_하이라이트사본/<제품코드>/` | 제품 단위 근거 {n_prod_files}건(제형 {n_prod_files}개) |
| `대체근거_참고/<제품코드>/` | 성분 단위 대체 근거 제형 {n_surr_forms}개(파일 {n_surr_files}개) — 등급 낮음, 참고용 |
| `근거매니페스트.csv` | 위 두 폴더 전체 파일의 경로·등급·신뢰도·sha256 |
| `PACKAGE_VERSION.json` | 이 패키지가 어느 입력 스냅샷(sha256)에서 나왔는지 |

## 저작권 및 재배포 관련

MSDS 근거자료는 제조사·유통사 웹사이트(agrian, chemservice 등)에서 수집한
것이다. 이 패키지는 화학연구원의 점검 목적에 한해 전달하는 것을 전제로
한다 — 재배포 범위를 넓히기 전에 재검토를 권한다.

## 다음 단계 (이번 패키지에는 포함하지 않음)

1. **URL {n_gap_url}건 재수집** — 진짜 공백 상태(`데이터_이슈`의 재수집후보_URL
   열)에 남아 있는 URL로 재수집을 시도하면 커버리지를 끌어올릴 여지가 있다.
   6개월 이상 지난 링크라 전부 성공하지는 않을 것이다.
2. **CAS 미상 성분 {int((ING.ing_name_best.notna() & ING.ing_cas_best.isna()).sum())}건 PubChem 보강** —
   이름은 있고 CAS가 없는 성분행. 기존 파이프라인에 이미 선례가 있는 방식(이름 →
   PubChem 조회)을 재사용할 수 있다.

## 재현

`01_파이프라인/build_chem_institute_package.py` 실행 시 이 폴더 전체가
다시 생성된다(입력은 전부 읽기 전용, 다른 경로에 쓰지 않음).
"""
(OUT / "README.md").write_text(README, encoding="utf-8")

log(f"완료 → {OUT.relative_to(L.ROOT)}/ "
    f"(xlsx 4시트, 근거파일 {n_prod_files + n_surr_files}건, "
    f"근거매니페스트 {len(MANIFEST)}행)")
print(f"→ {OUT.relative_to(L.ROOT)}")
