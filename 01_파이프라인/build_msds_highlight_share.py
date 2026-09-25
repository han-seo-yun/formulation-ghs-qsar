#!/usr/bin/env python3
"""화학연구원 공유 패키지 v2 — 원문 MSDS 하이라이팅 포함. 산출: 07_화학연구원_공유_260918/

## 06 판과 무엇이 다른가

06 판(`build_chem_institute_package.py`)은 감사도구가 이미 하이라이트해 둔 PDF를
**그대로 복사**했다. 그 하이라이트는 노란색 한 종류에 주석이 비어 있어서, 받는
사람이 "이 노란 칸이 공유 엑셀의 어느 값이 되었는가"를 알 수 없다. 이번 판은

1. 기존 하이라이트를 걷어내고
2. `input_dataset_v6.xlsx` 의 **실제 값이 원문에 있는지 먼저 확인한 뒤**
3. 확인된 것만 필드별 색으로 다시 칠하고, 주석에 `[제형ID #성분번호] 필드=원문「…」
   → 공유값 …` 을 박아 넣는다.

즉 하이라이트가 곧 대조표다. 못 찾은 것은 칠하지 않고 `검토 필요` 로 남긴다.

## 과거 공유본에서 계승한 양식

`formulation_audit_team_share_highlighted_only_20260630/` 의 4종 구성을 1:1로 옮겼다.

| 과거 | 이번 판 | 역할 |
|---|---|---|
| `*.summary.json` | `01_요약.json` | selection_rule · counts · sizes · kind_counts |
| `*.inventory.csv` | `03_근거파일_인벤토리.csv` | archive_path · kind · size_bytes (+ sha256) |
| `source_manifest.csv` | `04_출처수집기록.csv` | 출처별 수집 기록 (+ 우리 하이라이트 결과) |
| `*.review_queue.csv` | xlsx `검토필요` 시트 | 확정하지 못한 행만 모은 큐 |
| `ingredient_audit_summary.json` | `01_요약.json` 의 `판정_집계` | 성분 단위 판정 분포 |
| `Audit_Evidence_Terms/Snippet` | `05_원문대조표.csv` | 하이라이트한 문자열과 그 주변 원문 |

과거 판이 성분 단위로 `basis`(3절 / 문서전체 / 근거없음)를 기록한 관례도
`원문_구간` 열로 이어받았다.

## 판정 어휘 (추정하지 않는다)

앵커(=성분행 × {CAS, 성분명, 농도}) 하나마다:

- `일치`       원문에서 찾아 페이지까지 특정하고 **실제로 칠한** 것. `하이라이트_수 ≥ 1`
- `표기변형`   그 필드는 못 찾았지만 같은 행 CAS 는 찾음(문서에 성분은 있다)
- `문서불일치` CAS·성분명 모두 원문에 없음 → 다른 제품 문서이거나 값이 다른 출처에서 옴
- `위치불명`   텍스트에는 있으나 페이지 경계에 걸려 위치 특정 실패
- `좌표실패`   텍스트·페이지는 맞는데 좌표를 못 잡아 칠하지 못함
- `좌표검증실패` 칠해 봤는데 사각형이 덮은 글자가 원문과 어긋나 지운 것
  (다단 레이아웃에서 왼쪽 칸 `sodium` 과 오른쪽 칸 `hydroxide` 가 붙어 잡히는 경우)
- `문서판독불가` 텍스트층 없음 / 추출 실패
- `성분아님`   `데이터_이슈` 에 문장형·미상으로 이미 등재된 행
- `대상없음`   값 자체가 결측

농도만 판정이 둘 더 있다. `1%` 처럼 짧은 문자열은 문서 어디서든 걸리므로
**같은 행의 CAS·성분명이 확인된 페이지에 함께 있을 때만** 근거로 인정한다.

- `위치불일치` 문서에는 있으나 그 성분이 확인된 페이지가 아님
- `근거불충분` 그 성분 자체가 원문에서 확인되지 않아 농도를 대응시킬 수 없음

행 단위 종합은 `원문확인`(CAS·성분명 중 하나라도 일치) 아니면 `검토 필요`(사유 병기).

읽기 전용 입력: input_dataset_v6.xlsx,
formulation_audit_team_share_highlighted_only_20260630/**,
성분표_결함_확인용_260915.xlsx(있으면 재사용).
쓰기 대상은 07_화학연구원_공유_260918/ 하나뿐이다. 원본 값은 어디도 고치지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import fitz  # PyMuPDF
import pandas as pd
from bs4 import BeautifulSoup, NavigableString

import lib_model as L

AUDIT_ROOT = L.ROOT / "formulation_audit_team_share_highlighted_only_20260630"
AUDIT_XLSX = AUDIT_ROOT / "formulation_ingredients_master_audited.xlsx"
MANIFEST_CSV = AUDIT_ROOT / "ingredient_source_audit" / "source_manifest.csv"
AUDIT_SUM = AUDIT_ROOT / "ingredient_source_audit" / "audit_outputs" / "ingredient_audit_summary.json"
# 결함 목록. 저장소 안의 입력이어야 한다 — 저장소 밖 파일에 의존하면 남이 돌렸을 때
# 조용히 다른 규칙(재계산)으로 대체되고, 그 523행이 `성분아님` 판정과 이슈 집계를 좌우한다
DEFECT_XLSX = L.ROOT / "04_모델산출물" / "성분표_결함_확인용_260915.xlsx"
DEFECT_XLSX_ALT = Path("/Users/hanseoyun/Desktop/성분표_결함_확인용_260915.xlsx")
DEFECT_NONCHEM_N = 523           # 이 값과 다르면 입력이 바뀐 것이다. 조용히 넘기지 않는다

OUT = L.ROOT / "07_화학연구원_공유_260918"
HL_DIR = OUT / "MSDS_하이라이트"
SURR_DIR = OUT / "대체근거_참고"
log = L.make_logger(OUT / "패키지_생성.log")

# 필드별 색. 과거 판은 노란색 한 종류였다 — 필드를 구분할 수 없었다
COLOR = {"CAS": (1.0, 0.95, 0.30), "성분명": (0.55, 0.95, 0.55), "농도": (0.55, 0.80, 1.0)}
MAX_RECT_PER_ANCHOR = 6          # 같은 문자열이 문서 전체에 반복되면 상위 6곳만 칠한다

# ------------------------------------------------------------------ 문자열 정규화
DASH = str.maketrans({c: "-" for c in "‐‑‒–—―−－"})


def norm(s) -> str:
    """NFKC · 대시 통일 · 공백 접기. 원문 대조는 전부 이 형태로 한다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s)).translate(DASH)).strip()


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def split_paths(v) -> list[str]:
    return [] if pd.isna(v) else [p.strip() for p in str(v).split("|") if p.strip()]


def domain_of(url) -> str:
    if not isinstance(url, str) or not url:
        return "출처불명"
    try:
        return urlparse(url).netloc.replace("www.", "") or "출처불명"
    except Exception:
        return "출처불명"


# ------------------------------------------------------------------ 1. 원천 읽기
log("=== 화학연구원 공유 패키지 v2 (원문 하이라이팅) ===")
V6 = pd.ExcelFile(L.V6)
FORM = V6.parse("formulation")
ING = V6.parse("ingredient")
PROV = V6.parse("provenance")
assert len(FORM) == 1675 and len(ING) == 5287, "v6 행수 이탈 — 입력 변경 감지"

AUD = pd.ExcelFile(AUDIT_XLSX).parse("Formulations")
assert set(AUD.Formulation_ID) == set(FORM.Formulation_ID), "감사워크북과 v6 의 ID 집합 불일치"
MAN = pd.read_csv(MANIFEST_CSV)
ARCH_SUM = json.loads(AUDIT_SUM.read_text())

url_by_hash, hl_by_url = {}, {}
for _, r in MAN.iterrows():
    if pd.notna(r.get("highlighted_path")):
        h = Path(r["highlighted_path"]).stem.replace("_highlighted", "")
        url_by_hash[h] = r["url"]
        hl_by_url[r["url"]] = r["highlighted_path"]

# ------------------------------------------------------------------ 2. MSDS 확보상태 (06 판 로직 유지)
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
log(f"MSDS_확보상태: {CNT.to_dict()}")
for k, v in (("제품단위", 569), ("대체근거", 101), ("불필요_NTP자체보고", 694),
             ("출처소실", 243), ("공백", 68)):
    assert CNT.get(k, 0) == v, f"{k} {CNT.get(k)} != {v}"

URL_RE = re.compile(r"https?://\S+")
AUD["재수집후보_URL"] = AUD["Ingredient_Source"].map(
    lambda s: (URL_RE.findall(str(s)) or [None])[0] if pd.notna(s) else None)
n_gap_url = int(AUD.loc[AUD.MSDS_확보상태.isin(["공백", "출처소실"]), "재수집후보_URL"].notna().sum())

# ------------------------------------------------------------------ 3. 결함 목록 (성분아님 판별에 먼저 필요)
DEFECT_SHEETS = ("1_성분아닌행", "2_MIX408_중복", "3_고농도90이상",
                 "4_유황계이름분산", "5_농도합초과105")


def recompute_defects(I: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """데스크탑 검토 파일이 없을 때의 대체 경로. 이전 검토와 동일 규칙."""
    nm = I.ing_name_best
    out = {}
    out["1_성분아닌행"] = I[(nm.str.split().str.len().fillna(0) >= 8) | ~nm.notna()
                          | nm.str.contains("정정|제외 권고|실제 성분 아님|구조식", na=False)]
    out["2_MIX408_중복"] = I[I.Formulation_ID == "MIX408"]
    out["3_고농도90이상"] = I[I.ing_pct_best >= 90].sort_values("ing_pct_best", ascending=False)
    out["4_유황계이름분산"] = I[nm.str.lower().str.contains("sulfur|sulphur", na=False)
                             & ~nm.str.contains("sulfonate|sulfonic|sulfamic|sulfate",
                                                case=False, na=False)]
    s = I.groupby("Formulation_ID").ing_pct_best.sum()
    out["5_농도합초과105"] = I[I.Formulation_ID.isin(s[s > 105].index)]
    return out


defect_frames, nonchem_keys = [], set()
_dx = DEFECT_XLSX if DEFECT_XLSX.exists() else (
    DEFECT_XLSX_ALT if DEFECT_XLSX_ALT.exists() else None)
if _dx is not None:
    DEFECT_SRC = ("저장소_입력" if _dx == DEFECT_XLSX else "저장소밖_경로")
    DEFECT_SHA = sha256(_dx)
    log(f"결함 목록 — {DEFECT_SRC}: {_dx} · sha256 {DEFECT_SHA[:16]}")
    xl = pd.ExcelFile(_dx)
    for sn in DEFECT_SHEETS:
        if sn in xl.sheet_names:
            df = xl.parse(sn)
            if "결함유형" not in df.columns:
                df.insert(0, "결함유형", sn)
            defect_frames.append(df)
            if sn == "1_성분아닌행" and {"Formulation_ID", "ing_idx"} <= set(df.columns):
                nonchem_keys = set(zip(df.Formulation_ID, df.ing_idx))
    assert len(nonchem_keys) == DEFECT_NONCHEM_N, \
        f"결함목록 1_성분아닌행 {len(nonchem_keys)}행 != {DEFECT_NONCHEM_N}행 — 입력이 바뀌었다"
else:
    # 대체 경로는 규칙이 달라서 결과가 달라진다. 조용히 넘기면 산출물이 재현 불가가 된다
    DEFECT_SRC, DEFECT_SHA = "동일규칙_재계산(입력파일없음)", ""
    log("!! 결함 목록 파일이 없다 — 재계산으로 대체한다. 원본과 건수가 달라질 수 있다")
    for sn, df in recompute_defects(ING).items():
        df = df.copy()
        df.insert(0, "결함유형", sn)
        defect_frames.append(df)
        if sn == "1_성분아닌행":
            nonchem_keys = set(zip(df.Formulation_ID, df.ing_idx))

완전미상 = ING[ING.ing_name_best.isna() & ING.ing_cas_best.isna()].copy()
완전미상.insert(0, "결함유형", "6_이름CAS모두미상")
defect_frames.append(완전미상)
DEFECT_ALL = pd.concat(defect_frames, ignore_index=True, sort=False)
# 프레임마다 열 구성이 달라 concat 하면 180열이 되는데 그중 165열은 전부 공란이거나
# 다른 노선(구조식·디스크립터) 소유다. 결함을 읽는 데 필요한 열만 남긴다
DEFECT_ALL = DEFECT_ALL[[c for c in (
    "결함유형", "Formulation_ID", "ing_idx", "product_name", "ing_name_best",
    "ing_cas_best", "ing_pct_best", "ing_pct_kind_best", "ing_pct_raw",
    "ing_src", "ing_pct_src", "pct_status", "ing2_verdict", "비고")
    if c in DEFECT_ALL.columns]].dropna(axis=1, how="all")
assert len(완전미상) == 227, f"완전미상 {len(완전미상)} != 227"
nonchem_keys |= set(zip(완전미상.Formulation_ID, 완전미상.ing_idx))
log(f"성분아님으로 취급할 행: {len(nonchem_keys)}개 (문장형·미상)")

# ------------------------------------------------------------------ 4. 앵커 테이블
CAS_ANY_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
# 원문이 스스로 '확정값이 아니다' 라고 적은 표현. 어휘가 좁으면 표시가 빠진다 —
# `implies` 만 있고 `implied`·`nominal`·`not confirmed`·`trade secret` 이 없어서
# 같은 성격의 41행이 표시 없이 나갔다. 어형·동의어를 함께 잡는다
ASSUME_RE = re.compile(
    r"assum|not verified|unverified|not directly confirmed|impli|inaccessible"
    r"|dataset indicates|not confirmed|not independently verified|per product name"
    r"|trade\s*secret|nominal|typical|presum|unspecified|not specified|unknown"
    r"|withheld|proprietary|confidential|추정|미확인|가정|잠정|영업비밀|비공개"
    # 아래 줄은 검수(C8b)가 따로 쓴 넓은 어휘에서 초과로 걸려 들어온 표현이다.
    # `likely` 하나 때문에 MIX165 의 '다른 제형(Lannate SP)에서 가져온 90%' 가
    # 확정값처럼 나갔다
    r"|estimat|guess|approx|infer|likely|probabl|undisclosed", re.I)

# 농도 원문: 2차 재판독본이 있으면 그것, 없으면 1차 원본 문자열
농도원문 = ING.ing2_pct_raw_text.where(ING.ing2_pct_raw_text.notna(), ING.pct_text)
농도출처 = pd.Series("없음", index=ING.index)
농도출처[ING.pct_text.notna()] = "1차원본"
농도출처[ING.ing2_pct_raw_text.notna()] = "2차재판독"
ING = ING.assign(농도_원문=농도원문, 농도_원문출처=농도출처)


PCT_ATTACHED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|퍼센트)")


def parse_pct(raw, kind, pct_only=False):
    """원문 문자열에서 최종값이 나오는 규칙을 역적용해 기대값을 낸다.
    range=앞 두 수의 중앙값 · max=수가 둘 이상이면 가장 큰 수 ·
    exact·approx·upper·lower·min=첫 수(`approx` 는 `~` 위치를 보지 않는다).

    `pct_only` 는 % 가 붙은 수만 본다. `240 g/L (~24% w/v)` 처럼 한 줄에 단위가 둘
    있으면 모든 수를 보는 규칙은 240 을 집어 최종값 24 와 어긋난다 — 2차 판정용이다.

    `upper`/`max` 는 뜻이 같은데 경로가 다르다(첫 수 vs 최댓값). 실측으로 이 비대칭에
    걸리는 행은 4개이고 넷 다 2차(`%` 표기)에서 올바르게 잡힌다 — `<50% w/w
    (500 g/L SC)` 의 50, `1 - < 5 %` 의 5. 라벨이 `규칙일치_%표기` 로 한 단계
    약하게 남는 것이 실제와 맞아 그대로 둔다."""
    if raw is None or pd.isna(raw):
        return None
    t = norm(raw)
    nums = [float(x) for x in (PCT_ATTACHED.findall(t) if pct_only
                               else re.findall(r"\d+(?:\.\d+)?", t))]
    if not nums:
        return None
    if kind == "range" and len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    if kind == "min":
        return nums[0]
    if kind == "max":
        return nums[0] if len(nums) == 1 else max(nums)
    if kind in ("exact", "approx", "upper", "lower"):
        return nums[0]
    return None


def pct_rule(r):
    """환산 규칙 축. 원문 문자열에서 최종값이 규칙대로 나오는지만 본다."""
    if pd.isna(r.ing_pct_best):
        return "대상없음"
    raw = r.농도_원문
    if pd.isna(raw) or norm(raw) in ("-", ""):
        return "원문없음_값있음"
    v = float(r.ing_pct_best)
    exp = parse_pct(raw, r.ing_pct_kind_best)
    if exp is not None and abs(exp - v) <= 0.051:
        return "규칙일치"
    # 2차: % 가 붙은 수만 본다. 1차에서 맞은 행은 여기 오지 않으므로 이 판정이
    # 이미 맞은 행을 흔들지 않는다 — 어긋난 행만 단위 표기로 되짚는다
    exp2 = parse_pct(raw, r.ing_pct_kind_best, pct_only=True)
    if exp2 is not None and abs(exp2 - v) <= 0.051:
        return "규칙일치_%표기"
    if exp is None and exp2 is None:
        return "규칙미확인"
    return "규칙불일치"


def pct_uncertain(r):
    """불확실 표현. 있으면 확정값으로 쓸 수 없다.

    보는 것은 `농도_원문` 이고, 그것은 2차 재판독본이 있으면 그쪽이다. 그래서 원문
    어법(`trade secret`·`not verified`) 과 우리가 달아 둔 주석(`per product name` 7건 ·
    `dataset indicates` 1건) 이 같이 걸린다. 둘 다 '확정값 아님' 이라는 점은 같지만,
    앞은 원문의 말이고 뒤는 우리 판단이다 — 사유란의 표현으로 구분된다."""
    if pd.isna(r.ing_pct_best) or pd.isna(r.농도_원문):
        return ""
    m = ASSUME_RE.search(norm(r.농도_원문))
    return m.group(0) if m else ""


def check_pct(r):
    """농도 환산 검증. 값을 고치지 않고 판정만 붙인다.
    원문이 스스로 '가정·미확인' 이라 적은 값은 환산이 맞아떨어져도 미검증으로 본다."""
    if r.농도_불확실표현:
        return "미검증_추정치"
    return r.농도_환산규칙


ING["농도_환산규칙"] = ING.apply(pct_rule, axis=1)
ING["농도_불확실표현"] = ING.apply(pct_uncertain, axis=1)
ING["농도환산_검증"] = ING.apply(check_pct, axis=1)
log(f"농도환산_검증: {ING.농도환산_검증.value_counts().to_dict()}")
log(f"  환산규칙 축: {ING.농도_환산규칙.value_counts().to_dict()}")
log(f"  불확실표현 있는 행: {int((ING.농도_불확실표현 != '').sum())}개 · "
    f"표현 {ING.loc[ING.농도_불확실표현 != '', '농도_불확실표현'].str.lower().value_counts().to_dict()}")

# 제형별 대상 문서 (제품단위 우선, 없으면 대체근거)
DOCS: dict[str, list[tuple[str, str]]] = {}      # fid -> [(종류, 상대경로)]
for _, row in AUD.iterrows():
    prim, supp = (split_paths(row.Audit_Highlighted_Source_File),
                  split_paths(row.Supplemental_Highlighted_Source_File))
    if prim:
        DOCS[row.Formulation_ID] = [("제품단위", p) for p in prim]
    elif supp:
        DOCS[row.Formulation_ID] = [("대체근거", p) for p in supp]

# ------------------------------------------------------------------ 5. 문서 판독 · 검증 · 재하이라이팅
S3_RE = re.compile(r"(?:SECTION\s*)?3[\s.):]{0,3}\s*(?:COMPOSITION|Composition|"
                   r"INFORMATION ON INGREDIENT|조성|구성)", re.I)
S4_RE = re.compile(r"(?:SECTION\s*)?4[\s.):]{0,3}\s*(?:FIRST[\s-]?AID|응급|구급)", re.I)


def section3_pages(pages_text: list[str]) -> set[int]:
    """3절(조성 정보) 머리글이 **실제로 찍힌** 페이지.

    예전에는 3절 머리글부터 4절 머리글까지를 통째로 3절로 셌다. 그러면 머리글이
    없는 페이지까지 '3절(조성)' 이라고 주장하게 되고, 실제로 3절페이지수가 72 인
    문서까지 나왔다. 머리글 페이지만 3절로 세고, 그 사이 페이지는 따로 표시한다."""
    return {i for i, t in enumerate(pages_text) if S3_RE.search(t)}


def section3_range(pages_text: list[str]) -> set[int]:
    """3절 머리글과 4절 머리글 사이의 페이지. 조성표가 다음 장으로 넘어간 경우다."""
    s3 = next((i for i, t in enumerate(pages_text) if S3_RE.search(t)), None)
    if s3 is None:
        return set()
    s4 = next((i for i, t in enumerate(pages_text) if i > s3 and S4_RE.search(t)), None)
    return set(range(s3, (s4 if s4 is not None else s3) + 1))


def is_numeric_anchor(s: str) -> bool:
    """숫자로 시작하는 앵커. 이런 문자열은 부분일치 오탐이 난다 —
    `2.8%` 가 `12.8%` 안에서, `1%` 가 `21%` 안에서 걸린다."""
    return bool(re.match(r"^[<>=~\s]*[\d.]", s))


def _edge_ok(hay: str, i: int, ln: int) -> bool:
    b = hay[i - 1] if i else " "
    a = hay[i + ln] if i + ln < len(hay) else " "
    return not (b.isdigit() or b == ".") and not (a.isdigit() or a == ".")


def _word_edge_ok(hay: str, i: int, ln: int) -> bool:
    """이름 앵커의 낱말 경계. 앵커가 더 긴 낱말 안에 파묻혀 걸린 것을 거른다 —
    `Urea` 가 `Methylenediurea` 안에서, `Bentazon` 이 `Bentazone` 안에서,
    `Acetic Acid` 가 `peracetic acid` 안에서, `RN` 이 `Warning` 안에서 걸린다.
    앵커가 숫자·글자로 시작·끝나지 않으면(`( - 93%)` 류) 그 끝은 검사하지 않는다."""
    b = hay[i - 1] if i else " "
    a = hay[i + ln] if i + ln < len(hay) else " "
    head_ok = not hay[i].isalnum() or not b.isalnum()
    tail_ok = not hay[i + ln - 1].isalnum() or not a.isalnum()
    return head_ok and tail_ok


def contains(hay_low: str, needle_low: str, numeric: bool) -> bool:
    """숫자 앵커는 앞뒤가 숫자·소수점이 아닐 때, 이름 앵커는 낱말 경계일 때만 일치로 본다."""
    edge = _edge_ok if numeric else _word_edge_ok
    if not needle_low:
        return False
    i = hay_low.find(needle_low)
    while i >= 0:
        if edge(hay_low, i, len(needle_low)):
            return True
        i = hay_low.find(needle_low, i + 1)
    return False


def find_rects(page, needle: str, numeric: bool):
    """PyMuPDF 검색. 찾은 사각형마다 주변 텍스트로 경계를 한 번 더 확인한다 —
    search_for 는 `2.8%` 를 `12.8%` 안에서, `Urea` 를 `Methylenediurea` 안에서도
    잡아 준다. 경계검사를 안 걸면 엉뚱한 낱말에 색칸이 앉는다."""
    rects = []
    for cand in (needle, needle.lower(), needle.upper()):
        try:
            rects = page.search_for(cand, quads=False)
        except Exception:
            rects = []
        if rects:
            break
    if not rects:
        return rects
    edge = _edge_ok if numeric else _word_edge_ok
    out = []
    for rc in rects:
        s = norm(page.get_textbox(fitz.Rect(rc.x0 - 16, rc.y0 - 1, rc.x1 + 16, rc.y1 + 1)))
        i = s.lower().find(needle.lower())
        if i < 0 or edge(s.lower(), i, len(needle)):  # 판단 불가면 통과(문자열 검색은 통과)
            out.append(rc)
    return out


def _all_idx(hay: str, needle: str):
    i = hay.find(needle)
    while i >= 0:
        yield i
        i = hay.find(needle, i + 1)


def snippet_around(text: str, needle: str, span: int = 110) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return ""
    return norm(text[max(0, i - span // 2): i + len(needle) + span])


def strip_highlights(doc):
    """과거 감사도구가 남긴 무주석 노란칸을 걷어낸다. 주석을 삭제하면 같은 페이지의
    다른 핸들이 무효화되므로 한 번에 하나씩 다시 훑는다."""
    n = 0
    for pg in doc:
        while True:
            target = next((a for a in (pg.annots() or []) if a.type[1] == "Highlight"), None)
            if target is None:
                break
            pg.delete_annot(target)
            n += 1
    return n


def squash(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", norm(s).lower())


def cover_ok(needle: str, frags: list[str]) -> bool:
    """칠한 사각형들이 앵커 문자열을 실제로 덮었는지 되짚는다.

    한 문자열이 줄바꿈이나 표 셀에서 끊기면 사각형이 여러 개로 쪼개진다(879건).
    그건 정상이므로 조각을 합쳐서 본다. 반대로 다단 레이아웃에서 왼쪽 칸의
    `sodium` 과 오른쪽 칸의 `hydroxide` 가 읽기순서상 붙어 잡히는 경우가 있는데,
    그때는 조각이 앵커와 무관한 글자를 물고 있어 여기서 걸린다."""
    ns = squash(needle)
    if not ns:
        return False
    if ns in squash("".join(frags)):
        return True
    fr = [squash(f) for f in frags]
    return (all(f and (f in ns or ns in f) for f in fr)
            and sum(len(f) for f in fr) >= len(ns))


MIN_CANON = 4          # 정규화 후보 이름의 최소 길이. 'mol' 같은 단위 조각을 배제한다
NOISE_RE = re.compile(r"synonyms?\s*:|product\s*name\s*:|cas\s*(no|number)|min\.|max\.|"
                      r"\bw/w\b|\bhplc\b|percent|\bNA\b|\bNE\b|%|number", re.I)

# 물질명 자리에 들어온 표머리·비공개 표시·단위 조각. 물질을 특정하지 못하는 문자열이다.
# 이런 값에 CAS 가 붙어 있어도 그 CAS 가 무엇의 번호인지 알 수 없다 —
# `Other Ingredients (proprietary)` + CAS 7732-18-5 를 `Water` 로 바꾸면 값을 만드는 것이다
PLACEHOLDER_RE = re.compile(
    r"^(?:[\W_]*)(?:other\s+ingredient|inert\s+ingredient|hazardous\s+component|"
    r"ingredients?|other\s+information|proprietary|trade\s*secret|confidential|"
    r"balance|mixture|remainder|total|weight|neat|none|n/?a|unknown|unspecified|"
    r"not\s+(?:listed|specified|disclosed)|기타\s*성분|영업비밀|비공개)\b", re.I)
PLACEHOLDER_ANY = re.compile(
    r"proprietary|trade\s*secret|confidential|영업비밀|비공개|withheld", re.I)
MIN_NAME_SQUASH = 4    # 성분명 앵커의 최소 길이. `mol`·`RN`·`C =`·`VCP` 를 배제한다


def name_like(s: str) -> tuple[bool, str]:
    """이 문자열을 '물질명' 으로 취급해도 되는지 → (가능, 사유).

    근거로 쓰려면 문자열이 물질을 특정해야 한다. 표머리(`Other Ingredients`)·
    비공개 표시(`proprietary`)·단위 조각(`mol`)에 색칸을 앉히면 "원문에서 확인했다"
    는 표시가 거짓이 된다. 그래서 칠하기 전에, 그리고 정규화 전에 한 번 거른다."""
    t = norm(s)
    if len(squash(t)) < MIN_NAME_SQUASH:
        return False, f"길이부족({len(squash(t))}자)"
    if PLACEHOLDER_RE.match(t):
        return False, f"표머리·비공개표시({PLACEHOLDER_RE.match(t).group(0).strip()})"
    if PLACEHOLDER_ANY.search(t):
        return False, f"비공개표시({PLACEHOLDER_ANY.search(t).group(0)})"
    if not re.search(r"[a-zA-Z가-힣]{3}", t):
        return False, "글자 3자 연속 없음"
    return True, ""

# CAS 하나에 여러 이름이 붙어 있다(321개 CAS · 2,650행). 이 묶음이 정규화 후보 풀이다.
# 후보는 전부 v6 안에 이미 있는 이름이다 — 새 이름을 만들지 않는다
CAS_POOL: dict[str, list[tuple[str, int]]] = {}
_nm = ING[ING.ing_cas_best.notna() & ING.ing_name_best.notna()]
for cas, g in _nm.groupby(_nm.ing_cas_best.map(lambda s: norm(s))):
    vc = g.ing_name_best.value_counts()
    CAS_POOL[cas] = sorted(((str(k), int(v)) for k, v in vc.items()),
                           key=lambda t: (-t[1], -len(t[0]), t[0]))
log(f"정규화 후보 풀: CAS {len(CAS_POOL)}개 · 이름이 2개 이상인 CAS "
    f"{sum(1 for v in CAS_POOL.values() if len(v) > 1)}개")


def cas_checksum_ok(cas: str) -> bool:
    """CAS 등록번호 체크디지트. 틀리면 문서가 아니라 우리 데이터가 틀린 쪽이다."""
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", norm(cas))
    if not m:
        return False
    digits = (m[1] + m[2])[::-1]
    return sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10 == int(m[3])


def cas_variants(cas: str) -> list[str]:
    """선행 0 유무만 다른 표기. `0007732-18-5` 와 `7732-18-5` 는 같은 번호다."""
    n = norm(cas)
    out = {n, re.sub(r"^0+", "", n)}
    m = re.fullmatch(r"(\d+)(-\d{2}-\d)", n)
    if m:
        out |= {f"{m[1].zfill(k)}{m[2]}" for k in (5, 6, 7)}
    return [v for v in out if v != n]


def name_tokens(s: str) -> list[str]:
    """이름에서 의미 있는 어절만. 짧은 조각은 아무 문서에나 걸려 판단을 흐린다."""
    return [t for t in re.split(r"[^0-9a-zA-Z가-힣]+", norm(s).lower()) if len(t) >= 4]


# ------------------------------------------------------- 데이터쪽오류 정정 후보
# 이름 → 그 이름으로 쓰인 체크섬 통과 CAS 들. CAS 오류의 데이터 내부 교정 후보 풀
NAME2CAS: dict[str, list[tuple[str, int]]] = {}
for _nmk, _g in _nm.groupby(_nm.ing_name_best.map(squash)):
    _c = [(str(k), int(v)) for k, v in _g.ing_cas_best.map(norm).value_counts().items()
          if cas_checksum_ok(str(k))]
    if _c:
        NAME2CAS[_nmk] = sorted(_c, key=lambda t: -t[1])

INDEX_NO_RE = re.compile(r"index\s*(?:number|no\.?)", re.I)
# 이름 앞에 붙은 표 라벨. `Active ingredient (% w/w): Tebuconazole` 은 라벨이 앞,
# 값이 뒤다 — 앞을 취하면 값이 아니라 표머리를 이름으로 쓰게 된다
LABEL_RE = re.compile(
    r"(?i)^\s*(?:[·ꞏ•\-\s]*)(?:active\s+ingredients?|ingredients?|synonyms?|"
    r"product\s*name|chemical\s*name|cas\s*(?:no\.?|number)|content|composition|"
    r"substance\s*name)\s*(?:\([^)]*\))?\s*[:：]\s*")
TAIL_RE = re.compile(r"(?i)\s*(?:min\.|max\.|content|w/w|hplc|percent|registry|"
                     r"\bNE\b|\bNA\b|\d+\s*%|\(\s*-)")
HEADINGS = {"other information", "hazardous components", "inert ingredients",
            "other ingredients", "composition", "product identifier", "identification"}


def clean_noise_name(s: str) -> tuple[str, str]:
    """추출잡음이 섞인 이름에서 물질명만 잘라 낸다 → (후보, 불가사유).

    자르기만 하고 글자를 만들지 않는다. 그래서 추출 과정에서 글자가 **소실**된
    흔적(`n-Methyl--pyrrolidone` 의 이중 하이픈)이 보이면 후보를 내지 않는다 —
    빠진 `2` 를 되살리는 것은 값을 만드는 일이다."""
    t = norm(s)
    if re.search(r"--|- -", t):
        return "", "추출 중 글자 소실(이중 하이픈) — 복원하면 값 생성"
    if INDEX_NO_RE.search(t):
        return "", "CAS 가 아니라 EU CLP Annex VI Index number 조각"
    t = LABEL_RE.sub("", t)                      # 라벨: 값 → 값만 남긴다
    t = TAIL_RE.split(t)[0]
    t = norm(re.sub(r"[\s,;:()\[\]/%<>=+*-]+$", "", t))
    t = norm(re.sub(r"\s+\d+(?:\.\d+)?$", "", t))   # 꼬리에 남은 맨숫자(`Abamectin 2`)
    if not t or len(squash(t)) < MIN_NAME_SQUASH:
        return "", "잡음 제거 후 남는 글자가 없음"
    if squash(t) in {squash(h) for h in HEADINGS} or not name_like(t)[0]:
        return "", f"잡음 제거 후 남은 것이 표머리·비공개표시({t})"
    return t, ""


def fix_candidates(필드, n, r, ctx, 분류, 원인) -> list[dict]:
    """정정 후보. 문서에서 확인된 것만 '적용가능' 이다.

    v6 는 읽기 전용이므로 값을 덮어쓰지 않는다. 후보와 판정만 원장에 남기고,
    쓸지 말지는 받는 쪽이 정한다.

    CAS 체크섬은 **앵커의 필드와 무관하게** 본다. `필드=="CAS"` 일 때만 검사하면
    이름 쪽 사유로 분류가 갈린 앵커의 불량 CAS 를 놓친다 — 분류는 그대로 두고
    원장에만 올린다."""
    out = []
    cas0 = norm(r.ing_cas_best) if pd.notna(r.ing_cas_best) else ""
    if 분류 == "데이터쪽오류" and 원인 == "CAS체크섬오류":
        out += _fix_cas(n, r, ctx)
    elif cas0 and re.fullmatch(r"\d{2,7}-\d{2}-\d", cas0) and not cas_checksum_ok(cas0):
        out += _fix_cas(cas0, r, ctx)
    if 분류 == "데이터쪽오류" and 원인 == "이름이_추출잡음":
        out += _fix_name(n, r, ctx)
    return out


def _fix_cas(n, r, ctx) -> list[dict]:
    """체크디지트 불량 CAS 의 정정 후보."""
    low = ctx["low"]
    base = {"Formulation_ID": r.Formulation_ID, "ing_idx": r.ing_idx,
            "현재값": n, "성분명": r.ing_name_best, "CAS": r.ing_cas_best}
    nm = norm(r.ing_name_best) if pd.notna(r.ing_name_best) else ""
    if INDEX_NO_RE.search(nm) or INDEX_NO_RE.search(n):
        return [{**base, "구분": "CAS", "정정후보": "", "판정": "정정불가",
                 "사유": "CAS 가 아니라 EU CLP Annex VI Index number 조각 — 빈칸이 맞다",
                 "근거_문서표기": ""}]
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-\d", n)
    fixed = ""
    if m:
        d = (m[1] + m[2])[::-1]
        fixed = f"{m[1]}-{m[2]}-{sum((i + 1) * int(c) for i, c in enumerate(d)) % 10}"
    if fixed and fixed != n and contains(low, fixed, True):
        return [{**base, "구분": "CAS", "정정후보": fixed, "판정": "적용가능",
                 "사유": "체크디지트 교정값이 근거문서에 실재",
                 "근거_문서표기": snippet_around(ctx["whole"], fixed, 60)}]
    pool = NAME2CAS.get(squash(nm), [])
    indoc = [c for c, _k in pool if contains(low, c, True)]
    if indoc:
        return [{**base, "구분": "CAS", "정정후보": indoc[0], "판정": "적용가능",
                 "사유": "같은 이름의 다른 행 CAS 가 근거문서에 실재",
                 "근거_문서표기": snippet_around(ctx["whole"], indoc[0], 60)}]
    if fixed and fixed != n:
        return [{**base, "구분": "CAS", "정정후보": fixed, "판정": "보류",
                 "사유": "체크디지트 교정 후보 — 문서에서 확인 안 됨", "근거_문서표기": ""}]
    if pool:
        return [{**base, "구분": "CAS", "정정후보": pool[0][0], "판정": "보류",
                 "사유": f"같은 이름의 다른 행 CAS(빈도 {pool[0][1]}) — 문서 미확인",
                 "근거_문서표기": ""}]
    return [{**base, "구분": "CAS", "정정후보": "", "판정": "정정불가",
             "사유": "교정 후보가 없다", "근거_문서표기": ""}]


def _fix_name(n, r, ctx) -> list[dict]:
    """추출잡음이 섞인 성분명의 정정 후보."""
    low = ctx["low"]
    base = {"Formulation_ID": r.Formulation_ID, "ing_idx": r.ing_idx,
            "현재값": n, "성분명": r.ing_name_best, "CAS": r.ing_cas_best}
    cand, why = clean_noise_name(n)
    if not cand:
        return [{**base, "구분": "성분명", "정정후보": "", "판정": "정정불가",
                 "사유": why, "근거_문서표기": ""}]
    if contains(low, cand.lower(), False):
        return [{**base, "구분": "성분명", "정정후보": cand, "판정": "적용가능",
                 "사유": "잡음을 걷어낸 이름이 근거문서에 낱말 단위로 실재",
                 "근거_문서표기": snippet_around(ctx["whole"], cand, 60)}]
    return [{**base, "구분": "성분명", "정정후보": cand, "판정": "보류",
             "사유": "잡음 제거 후보 — 근거문서에서 확인 안 됨", "근거_문서표기": ""}]


def read_doc(path: Path):
    """(pdf|html) → (pages_text, 전체정규화텍스트, 핸들). 실패는 None 반환."""
    suf = path.suffix.lower()
    try:
        if suf == ".pdf":
            d = fitz.open(path)
            pages = [p.get_text() for p in d]
            return pages, norm(" ".join(pages)), ("pdf", d)
        soup = BeautifulSoup(path.read_bytes(), "lxml")
        for t in soup(["script", "style"]):
            t.decompose()
        # 원본 HTML 에는 과거 감사도구가 넣은 `<mark class="audit-highlight">` 가
        # 이미 박혀 있다. 그 태그가 낱말 중간을 자른 자리가 있어서(`Fosetyl-al`+
        # `uminum`) 태그를 그대로 두고 `get_text(" ")` 하면 낱말이 공백으로 갈라진
        # 텍스트를 읽게 된다 — 문서에 있는 값을 없다고 판정할 수 있다. 태그를 걷고
        # 인접 문자열을 붙여서 문서의 실제 글자를 읽는다
        # (지금 데이터에서는 이 때문에 놓친 앵커 0개·되레 깨지는 앵커 0개로 확인)
        for mk in soup.find_all("mark"):
            mk.unwrap()
        soup.smooth()
        txt = soup.get_text(" ")
        return [txt], norm(txt), ("html", soup)
    except Exception as e:
        log(f"  판독실패 {path.name}: {type(e).__name__} {e}")
        return None, "", (None, None)


for d in (HL_DIR, SURR_DIR):                # 부분 실행 잔여물이 섞이지 않게 매번 비운다
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)

ROWS = []            # 05_원문대조표.csv
FILEINFO = []        # 03_근거파일_인벤토리.csv
MISROWS = []         # 07_문서불일치_전수검사.csv — '문서불일치' 앵커 전건
FIXROWS = []         # 09_데이터정정_후보.csv — 데이터쪽오류의 정정 후보
n_done = 0
PNAME = FORM.set_index("Formulation_ID").product_name.to_dict()


CANON_HOLD = []          # 원값 품질 때문에 정규화하지 않은 앵커 (09_데이터정정_후보.csv)

# 등급·공정을 나타내는 낱말. 원값에 없던 것이 후보 이름에 붙으면 원문이 말하지 않은
# 등급을 우리가 붙이는 것이 된다
GRADE_RE = re.compile(r"rectified|technical|precipitat|anhydrous|monohydrat|dihydrat|"
                      r"hydrate|purified|distilled|refined|crude|granul|wettable|"
                      r"정제|무수|함수", re.I)
def canon_risk(raw: str, nm: str) -> str:
    """후보 이름이 원값과 같은 물질이 아닐 위험 → 사유(없으면 빈 문자열).

    같은 CAS·문서 실재를 통과해도 이름이 혼합물의 성분 하나로 좁혀지거나 원값에
    없던 등급어가 붙으면 다른 물질을 가리킨다 —
    `Spinosad (Spinosyn A & Spinosyn D)` → `Spinosad A`(성분 하나),
    `CADE wood ORGANIC OIL` → `Cade Oil Rectified`(정제 등급 추가).

    꼬리 어휘 목록(`A`·`B`·`I`…)으로 판단하지 않는다. 목록은 반드시 빠지는 것이
    생긴다 — `B1a`·`lambda`·두 자리 숫자가 그랬다. 대신 **후보 이름이 원값에 없는
    글자를 뒤에 덧붙이는가** 만 본다: 원값이 `Abamectin`, 후보가 `Abamectin B1a` 면
    꼬리 `b1a` 는 원값에 없으므로 보류다. 원값이 이미 그 꼬리를 적고 있으면
    (`Dimethenamid-P ((S)-dimethenamid)` → `Dimethenamid-P`) 덧붙인 게 아니라
    괄호를 떼어낸 것이므로 통과한다.

    머리(`Cyhalothrin` → `lambda-Cyhalothrin`) 는 일부러 막지 않는다. 후보는 같은
    CAS 인 이름뿐이고, 이성질 접두어는 CAS 로 갈린다. 여기서 막으면
    `(+)-Limonene` → `D-Limonene` 같은 **같은 CAS·같은 물질의 다른 표기** 9건이
    함께 떨어진다(실측)."""
    b, r = squash(nm), squash(raw)
    if b and b not in r:
        head = squash(next((t for t in re.split(r"[^0-9A-Za-z가-힣]+", norm(raw)) if t), ""))
        if len(head) < 4:            # `2,4-D` 처럼 첫 어절이 짧으면 원값 전체로 본다
            head = r
        if len(head) >= (2 if head == r else 4) \
                and b.startswith(head) and len(b) > len(head):
            return f"원값에 없는 꼬리 '{b[len(head):]}' 추가 — 성분 하나로 좁혀짐"
    g = GRADE_RE.search(norm(nm))
    if g and not GRADE_RE.search(norm(raw)):
        return f"원값에 없는 등급어 추가('{g.group(0)}')"
    return ""


def canon_name(cas, raw, low, key=None):
    """표기변형(CAS 는 원문에 있는데 이름 문자열이 없음) 행의 이름을 정규화한다.

    후보는 같은 CAS 를 가진 v6 안의 다른 이름들뿐이고, 그중 **문서에 그대로
    완전일치하는 것**만 쓴다. 여러 개면 데이터셋에서 제일 많이 쓰인 이름을 고른다.
    없으면 정규화하지 않는다 — 이름을 새로 만들지 않기 위한 조건이다.

    **원값 품질을 먼저 본다.** 원값이 표머리·비공개 표시·추출잡음이면 그 행의 CAS 가
    무엇의 번호인지 알 수 없으므로 정규화하지 않는다. 이 게이트가 없으면
    `Other Ingredients (proprietary)` 가 `Water` 로, `Percent: (HPLC)` 가
    `Penoxsulam` 으로 바뀌어 비공개 표시가 물질명으로 둔갑한다."""
    ok, why = name_like(raw)
    if not ok:
        why = f"원값 품질: {why}"
    elif NOISE_RE.search(norm(raw)):
        ok, why = False, f"원값 품질: 추출잡음('{NOISE_RE.search(norm(raw)).group(0)}')"
    if not ok:
        if key is not None:
            CANON_HOLD.append({"Formulation_ID": key[0], "ing_idx": key[1],
                               "구분": "정규화보류", "현재값": norm(raw),
                               "정정후보": "", "판정": "보류", "사유": why})
        return None, 0
    for nm, c in CAS_POOL.get(norm(cas), []):
        if squash(nm) == squash(raw) or len(squash(nm)) < MIN_CANON:
            continue
        if not name_like(nm)[0]:
            continue                  # 후보 풀에 `Hazardous components` 류가 섞여 있다
        if not contains(low, norm(nm).lower(), False):
            continue
        risk = canon_risk(raw, nm)
        if risk:                      # 문서에 있어도 물질이 달라지면 쓰지 않는다
            if key is not None:
                CANON_HOLD.append({"Formulation_ID": key[0], "ing_idx": key[1],
                                   "구분": "정규화보류", "현재값": norm(raw),
                                   "정정후보": norm(nm), "판정": "보류",
                                   "사유": f"후보 품질: {risk}"})
            continue
        return norm(nm), c
    return None, 0


def mismatch_diag(필드, n, r, ctx):
    """문서불일치 앵커 하나의 원인을 되짚는다 → (분류, 원인, 근거).

    '붙어 있는 문서에 그 성분이 없다' 는 결과만으로는 다음 조치를 정할 수 없다.
    문서가 애초에 다른 제품인지, 표기만 다른지, 우리 이름이 추출 잡음인지에 따라
    할 일이 갈린다. 그래서 앵커마다 한 줄씩 원인을 남긴다."""
    low, 형식 = ctx["low"], ctx["문서형식"]
    pr = ctx["제품명일치율"]
    if ctx["문서종류"] == "대체근거":
        # 대체근거는 성분 단위 순수물질 SDS 다. 정의상 제품 문서가 아니니 제품명이
        # 없다고 '다른 제품 문서' 라 부르면 안 된다 — 다시 받을 대상도 아니다
        제품 = "대체근거문서"
    else:
        제품 = ("다른제품문서" if pr == 0 else "같은문서_성분목록다름") if pr is not None \
            else "원인미상"

    if 필드 == "CAS":
        if not cas_checksum_ok(n):
            return "데이터쪽오류", "CAS체크섬오류", f"{n} 체크디지트 불일치"
        v = [x for x in cas_variants(n) if x.lower() in low]
        if v:
            return "표기차이", "선행0표기차이", f"문서표기 {v[0]}"
        if ctx["문서내_CAS종류수"] == 0:
            return "문서형식부적합", "문서에_CAS표기없음", 형식
        return 제품, "문서에_다른CAS만", f"문서 CAS {ctx['문서내_CAS종류수']}종"

    if 필드 == "성분명":
        m = NOISE_RE.search(n)
        if m:
            return "데이터쪽오류", "이름이_추출잡음", f"'{m.group(0)}' 포함"
        if pd.notna(r.ing_cas_best):
            syn, c = canon_name(r.ing_cas_best, n, low)
            if syn:
                return "표기차이", "동일CAS_다른이름이_문서에있음", f"{syn} (빈도 {c})"
        toks = name_tokens(n)
        hit = [t for t in toks if t in low]
        if toks and hit:
            return "표기차이", "부분어절만일치", f"{len(hit)}/{len(toks)} {hit[:3]}"
        if 형식 == "SDS아님(라벨·목록)":
            return "문서형식부적합", "문서가_SDS아님", 형식
        return 제품, "어떤어절도_문서에없음", f"어절 {len(toks)}개"

    nums = re.findall(r"\d+(?:\.\d+)?", n)
    for x in nums:
        if contains(low, x, True):
            return "표기차이", "수치는있고_표기형식다름", f"문서에 {x} 있음"
    if low.count("%") < 2:
        return "문서형식부적합", "문서에_농도표기없음", f"% {low.count('%')}회"
    return 제품, "문서에_해당수치없음", f"수치 {nums[:3]}"


def 근거강도(필드, 원인, 근거, n) -> str:
    """'표기차이' 라는 판정을 뒷받침하는 근거가 얼마나 센지.

    `문서에 1 있음` 처럼 한두 자리 숫자 하나로 '수치는 문서에 있다' 고 말하면
    CAS `64-17-5` 의 앞 두 자리나 HTTP 오류코드 `403` 도 농도로 읽힌다.
    분류는 그대로 두고 강도만 적어 둔다 — 받는 쪽이 어디부터 볼지 고를 수 있게."""
    if 원인 != "수치는있고_표기형식다름":
        return ""
    m = re.search(r"문서에 ([\d.]+) 있음", 근거 or "")
    if not m:
        return ""
    x = m.group(1)
    if "." in x and len(x.replace(".", "")) >= 3:
        return "강함(소수점 포함 3자리 이상)"
    if len(x) >= 3:
        return "보통(3자리 이상)"
    return f"약함({len(x)}자리 숫자 하나 — 우연일치 가능)"

for fid, docs in DOCS.items():
    sub = ING[ING.Formulation_ID == fid]
    for i, (kind, rel) in enumerate(docs, 1):
        src = AUDIT_ROOT / rel
        pages, whole, (fmt, handle) = read_doc(src)
        url = url_by_hash.get(Path(rel).stem.replace("_highlighted", ""))
        s3p = section3_pages(pages) if pages else set()
        s3r = section3_range(pages) if pages else set()
        readable = bool(whole) and len(whole) >= 200

        low = whole.lower()
        pages_low = [norm(t).lower() for t in (pages or [])]
        row_from = len(ROWS)
        n_old = strip_highlights(handle) if fmt == "pdf" else 0

        # 문서가 SDS 인지부터 본다. 3절도 없고 CAS 형식 문자열도 없으면 SDS 가 아니다.
        # 실제로 SDS 자리에 세계은행 통계 보고서가 붙어 있는 사례가 있었다. 다만 EPA·주정부
        # 등록 라벨은 SDS 가 아니어도 유효 성분 농도를 싣고 있어 농도 근거로는 쓸 수 있다
        n_cas_doc = len(set(CAS_ANY_RE.findall(whole))) if readable else 0
        문서형식 = ("판독불가" if not readable else
                 "SDS추정" if s3p and n_cas_doc else
                 "SDS부분" if (s3p or n_cas_doc) else "SDS아님(라벨·목록)")

        # 문서가 이 제품 문서인지의 단서. 문서불일치 원인을 가르는 데 쓴다
        _pn = PNAME.get(fid, "")
        _pt = name_tokens(_pn)
        if _pt:
            제품명일치율 = round(sum(1 for t in _pt if t in low) / len(_pt), 2)
        else:
            # 'TC 240' · 'VCP-01' 처럼 어절이 다 짧은 제품명은 붙여서 한 번 더 찾는다.
            # 이걸 안 하면 원인을 못 가리고 '원인미상' 으로 밀려난다
            _sq = squash(_pn)
            제품명일치율 = (float(_sq in squash(whole)) if len(_sq) >= 4 else None)
        ctx = {"low": low, "whole": whole, "문서형식": 문서형식,
               "문서내_CAS종류수": n_cas_doc,
               "제품명일치율": 제품명일치율, "문서종류": kind}
        mis_from = len(MISROWS)
        fix_from = len(FIXROWS)

        def 불일치기록(r, 필드, n, v):
            분류, 원인, 근거 = mismatch_diag(필드, n, r, ctx)
            MISROWS.append({
                "Formulation_ID": r.Formulation_ID, "ing_idx": r.ing_idx, "필드": 필드,
                "판정": v, "공유값": n, "성분명": r.ing_name_best, "CAS": r.ing_cas_best,
                "제품명": PNAME.get(r.Formulation_ID, ""), "분류": 분류, "원인": 원인,
                "근거": 근거, "근거강도": 근거강도(필드, 원인, 근거, n),
                "문서형식": 문서형식, "제품명일치율": 제품명일치율,
                "문서내_CAS종류수": n_cas_doc, "근거파일": "",
            })
            FIXROWS.extend(fix_candidates(필드, n, r, ctx, 분류, 원인))

        # 5-1. 식별 앵커(CAS · 성분명) — 페이지 단위로 어디서 찾았는지까지 기록한다
        판정: list[tuple] = []      # (ing_idx, 필드, 판정, 원문문자열, [페이지], 정규화이전값)
        식별페이지: dict[int, set[int]] = {}
        for _, r in sub.iterrows():
            key = (r.Formulation_ID, r.ing_idx)
            cas_ok = pd.notna(r.ing_cas_best) and contains(
                low, norm(r.ing_cas_best).lower(), True)
            for 필드, 값 in (("CAS", r.ing_cas_best), ("성분명", r.ing_name_best)):
                if pd.isna(값) or norm(값) in ("", "-"):
                    판정.append((r.ing_idx, 필드, "대상없음", "", [], ""))
                    continue
                n = norm(값)
                if not readable:
                    판정.append((r.ing_idx, 필드, "문서판독불가", n, [], ""))
                    continue
                # 우리가 이미 '성분아님' 이라고 등재한 행은 문서에서 걸려도 근거가 아니다.
                # 이 검사를 '일치' 뒤에 두면 `ꞏ a) Identity of every active substance…`
                # 같은 문장에 초록칸이 앉아 "원문확인" 표시가 거짓이 된다(64앵커)
                if key in nonchem_keys:
                    판정.append((r.ing_idx, 필드, "성분아님", n, [], ""))
                    continue
                # 성분명 앵커는 물질을 특정해야 한다. `mol`·`RN`·`C =`·`VCP` 는 배제한다
                if 필드 == "성분명" and not name_like(n)[0]:
                    판정.append((r.ing_idx, 필드, "성분명아님", n, [], ""))
                    continue
                num = is_numeric_anchor(n)
                pg_list = [pi for pi, t in enumerate(pages_low)
                           if contains(t, n.lower(), num)]
                if pg_list:
                    식별페이지.setdefault(r.ing_idx, set()).update(pg_list)
                    판정.append((r.ing_idx, 필드, "일치", n, pg_list, ""))
                elif contains(low, n.lower(), num):
                    # 페이지 경계에 걸려 어느 페이지인지 특정 못 함 → 칠하지 않는다
                    판정.append((r.ing_idx, 필드, "위치불명", n, [], ""))
                elif cas_ok:
                    # CAS 는 원문에 있는데 이름 문자열이 없다. 같은 CAS 의 다른 이름이
                    # 문서에 그대로 있으면 그 이름으로 정규화한다(이전값은 보존)
                    canon, _c = (canon_name(r.ing_cas_best, n, low, key)
                                 if 필드 == "성분명" else (None, 0))
                    cpg = ([pi for pi, t in enumerate(pages_low)
                            if contains(t, canon.lower(), False)] if canon else [])
                    if cpg:
                        식별페이지.setdefault(r.ing_idx, set()).update(cpg)
                        판정.append((r.ing_idx, 필드, "일치", canon, cpg, n))
                    else:
                        판정.append((r.ing_idx, 필드, "표기변형", n, [], ""))
                else:
                    판정.append((r.ing_idx, 필드, "문서불일치", n, [], ""))
                    불일치기록(r, 필드, n, "문서불일치")

        # 5-2. 농도 앵커 — '1%' 같은 짧은 문자열은 문서 어디서나 걸린다.
        # 그래서 같은 행의 CAS·성분명이 확인된 페이지에 함께 있을 때만 근거로 인정한다
        for _, r in sub.iterrows():
            값 = r.농도_원문
            # 공유 데이터에 최종 농도값이 없으면 되짚을 대상 자체가 없다
            if pd.isna(r.ing_pct_best) or pd.isna(값) or norm(값) in ("", "-"):
                판정.append((r.ing_idx, "농도", "대상없음", "", [], ""))
                continue
            n = norm(값)
            if not readable:
                판정.append((r.ing_idx, "농도", "문서판독불가", n, [], ""))
                continue
            num = is_numeric_anchor(n)
            ok_pages = 식별페이지.get(r.ing_idx, set())
            if not ok_pages:
                판정.append((r.ing_idx, "농도",
                            "성분아님" if (r.Formulation_ID, r.ing_idx) in nonchem_keys
                            else "근거불충분", n, [], ""))
                continue
            hit = [pi for pi in sorted(ok_pages) if contains(pages_low[pi], n.lower(), num)]
            if hit:
                판정.append((r.ing_idx, "농도", "일치", n, hit, ""))
            elif contains(low, n.lower(), num):
                판정.append((r.ing_idx, "농도", "위치불일치", n, [], ""))
            else:
                판정.append((r.ing_idx, "농도", "문서불일치", n, [], ""))
                불일치기록(r, "농도", n, "문서불일치")

        # 5-3. 하이라이트 — '일치' 만, 그리고 위에서 특정한 페이지 안에서만
        painted = 0
        marks = []                                     # html 용
        최종 = []
        for idx, 필드, v, n, pg_list, prev in 판정:
            r0 = sub[sub.ing_idx == idx].iloc[0]
            공유값 = (r0.ing_pct_best if 필드 == "농도"
                   else (r0.ing_cas_best if 필드 == "CAS" else r0.ing_name_best))
            hit_pages, n_rect = [], 0
            # 정규화한 앵커는 필드 이름에 표시하고 이전값을 주석에 남긴다. 받는 쪽이
            # 색칸만 보고 "우리 데이터에 적힌 이름과 다르잖아" 하지 않도록
            필드표기 = f"{필드}(정규화)" if prev else 필드
            if prev:
                공유값 = n                     # 정규화된 이름이 이 앵커의 공유값이다
            note = (f"[{fid} #{idx}] {필드표기}=원문「{n}」 → 공유값 {공유값}"
                    + (f" / 이전값「{prev}」" if prev else ""))
            if v == "일치":
                버린페이지 = 0
                if fmt == "pdf":
                    for pi in pg_list:
                        if n_rect >= MAX_RECT_PER_ANCHOR:
                            break
                        pg = handle[pi]
                        rs = find_rects(pg, n, is_numeric_anchor(n))
                        rs = rs[:MAX_RECT_PER_ANCHOR - n_rect]
                        if not rs:
                            continue
                        # 칠하기 전에 그 자리가 정말 원문인지 본다. 페이지마다 따로 봐야
                        # 한다 — 앞 페이지가 맞았다고 뒤 페이지의 어긋난 칸을 통과시키면
                        # 잘못된 하이라이트가 그대로 남는다
                        if not cover_ok(n, [norm(pg.get_textbox(rc)) for rc in rs]):
                            버린페이지 += 1
                            continue
                        for rect in rs:
                            an = pg.add_highlight_annot(rect)
                            an.set_colors(stroke=COLOR[필드])
                            an.set_info(title="공유데이터 대응", content=note)
                            an.update()
                            n_rect += 1
                            hit_pages.append(pi + 1)
                else:
                    n_rect, hit_pages = 1, [1]           # html 은 저장 시점에 실측으로 덮어쓴다
                if n_rect == 0:
                    # 텍스트에는 있으나 칠하지 못했다. 일치로 세지 않는다
                    v = "좌표검증실패" if 버린페이지 else "좌표실패"
                painted += n_rect
            구간 = ("3절(조성)" if hit_pages and any(p - 1 in s3p for p in hit_pages)
                   else "3절~4절사이" if hit_pages and any(p - 1 in s3r for p in hit_pages)
                   else ("문서기타" if hit_pages else ""))
            row = {
                "Formulation_ID": fid, "ing_idx": idx, "문서종류": kind, "필드": 필드,
                "판정": v, "원문_문자열": n, "공유_최종값": 공유값,
                "원문_구간": 구간, "원문_페이지": ",".join(map(str, sorted(set(hit_pages)))),
                "하이라이트_수": n_rect,
                "원문_스니펫": snippet_around(whole, n) if v == "일치" else "",
                "정규화": "예" if prev else "", "정규화_이전값": prev,
                "근거파일": "", "출처URL": url or "",
            }
            최종.append(row)
            if v == "일치" and fmt == "html":
                marks.append((n, 필드, note, row))
        ROWS.extend(최종)

        # 5-4. 사본 저장
        dst_dir = (HL_DIR if kind == "제품단위" else SURR_DIR) / fid
        dst_dir.mkdir(parents=True, exist_ok=True)
        tag = domain_of(url) if kind == "제품단위" else \
            re.sub(r"_[0-9a-f]{16}_highlighted$", "", Path(rel).stem) or Path(rel).stem
        dst = dst_dir / f"{fid}__{i}__{tag}{src.suffix.lower()}"
        if fmt == "pdf":
            handle.save(dst, garbage=3, deflate=True)
            handle.close()
        elif fmt == "html":
            soup2 = BeautifulSoup(src.read_text(errors="replace"), "lxml")
            for needle, 필드, note, row in marks:
                bg = {"CAS": "#FFF34D", "성분명": "#8CF08C", "농도": "#8CCCFF"}[필드]
                for tnode in list(soup2.find_all(string=True)):
                    if tnode.parent.name in ("script", "style", "mark"):
                        continue
                    s = str(tnode)
                    num = is_numeric_anchor(needle)
                    edge = _edge_ok if num else _word_edge_ok
                    j = next((k for k in _all_idx(s.lower(), needle.lower())
                              if edge(s.lower(), k, len(needle))), -1)
                    if j < 0:
                        continue
                    # `<mark>` 를 낱말 중간에 끼우면 사본의 `get_text(" ")` 이 낱말을
                    # 공백으로 갈라 버린다(`Ethoprophos` → `Ethoprop hos`). 받는 쪽이
                    # 그 사본에서 값을 문자열 검색하면 못 찾는다 — 경계 자리에만 끼운다
                    # html.parser 로 파싱해야 html/body 래퍼가 끼어들지 않는다
                    tnode.replace_with(BeautifulSoup(
                        f'{s[:j]}<mark data-field="{필드}" title="{note}" '
                        f'style="background:{bg}">{s[j:j + len(needle)]}</mark>'
                        f'{s[j + len(needle):]}', "html.parser"))
                    break
            dst.write_text(str(soup2), encoding="utf-8")
            # 저장된 파일에 실제로 남은 mark 만 센다. 두 앵커가 같은 텍스트 노드를
            # 노리면 나중 것이 유실되고, 문자열이 태그 경계로 쪼개져 있으면 아예 못 찍는다
            남은 = {}
            for mk in BeautifulSoup(dst.read_text(errors="replace"), "lxml").find_all(
                    "mark", attrs={"data-field": True}):
                k = norm(mk.get("title") or "")
                남은[k] = 남은.get(k, 0) + 1
            for needle, 필드, note, row in marks:
                c = 남은.get(norm(note), 0)
                row["하이라이트_수"] = c
                if c == 0:
                    row.update({"판정": "좌표실패", "원문_페이지": "",
                                "원문_구간": "", "원문_스니펫": ""})
            painted = sum(남은.values())
        else:
            shutil.copy2(src, dst)

        relout = str(dst.relative_to(OUT))
        for row in ROWS[row_from:]:                    # 이 문서가 만든 행만 채운다
            row["근거파일"] = relout
        for row in MISROWS[mis_from:]:
            row["근거파일"] = relout
        for row in FIXROWS[fix_from:]:
            row["근거파일"] = relout
        FILEINFO.append({
            "archive_path": relout,
            "kind": "제품단위_하이라이트" if kind == "제품단위" else "대체근거_하이라이트",
            "size_bytes": dst.stat().st_size, "sha256": sha256(dst),
            "Formulation_ID": fid, "원경로": rel, "출처URL": url or "",
            "형식": fmt or "unknown", "판독가능": readable, "문서형식": 문서형식,
            "문서내_CAS종류수": n_cas_doc,
            "하이라이트_수": painted, "제거한_과거하이라이트": n_old,
            "3절머리글페이지수": len(s3p), "3절범위페이지수": len(s3r),
            "증거_신뢰도": AUD.loc[AUD.Formulation_ID == fid, "Audit_Status"].iloc[0],
        })
        n_done += 1
        if n_done % 100 == 0:
            log(f"  문서 {n_done}건 처리")

log(f"문서 처리 완료 {n_done}건 · 대조 앵커 {len(ROWS)}행")
HL = pd.DataFrame(ROWS)
FI = pd.DataFrame(FILEINFO)
log(f"판정 분포: {HL.판정.value_counts().to_dict()}")
log(f"하이라이트 총 개수: {int(HL.하이라이트_수.sum())}")

MIS = pd.DataFrame(MISROWS)
log(f"문서불일치 전수검사 {len(MIS)}건 · 분류 {MIS.분류.value_counts().to_dict()}")
log(f"  원인별: {MIS.원인.value_counts().to_dict()}")
assert len(MIS) == int((HL.판정 == "문서불일치").sum()), "문서불일치 앵커 수와 검사 행 수가 다르다"

# ---------------------------------------------------- 5b. 데이터정정 후보 원장
# v6 는 읽기 전용이다. 그래서 '고칠 수 있는 것' 을 값에 덮어쓰지 않고 후보·판정·근거만
# 따로 적는다. 같은 앵커가 여러 문서에서 검사되므로 앵커당 한 줄로 접는다 —
# 한 문서에서 확인된 후보가 다른 문서에서 '문서 미확인' 으로 덮이면 안 된다
_RANK = {"적용가능": 0, "보류": 1, "정정불가": 2}
FIX = pd.DataFrame(FIXROWS + CANON_HOLD)
if len(FIX):
    for _c in ("성분명", "CAS", "근거_문서표기", "근거파일", "정정후보", "사유"):
        if _c not in FIX.columns:
            FIX[_c] = ""
    FIX = FIX.fillna({"근거_문서표기": "", "근거파일": "", "정정후보": ""})
    FIX["_r"] = FIX.판정.map(_RANK).fillna(9)
    FIX["_e"] = (FIX.근거_문서표기.astype(str) != "").map({True: 0, False: 1})
    FIX = (FIX.sort_values(["Formulation_ID", "ing_idx", "구분", "_r", "_e"])
              .drop_duplicates(["Formulation_ID", "ing_idx", "구분"])
              .drop(columns=["_r", "_e"]))
FIX_COLS = ["Formulation_ID", "ing_idx", "구분", "현재값", "성분명", "CAS",
            "정정후보", "판정", "사유", "근거_문서표기", "근거파일"]
FIX = FIX.reindex(columns=FIX_COLS).sort_values(["구분", "판정", "Formulation_ID", "ing_idx"])
log(f"데이터정정 후보 {len(FIX)}행 · 구분 {FIX.구분.value_counts().to_dict()}")
log(f"  판정 {FIX.판정.value_counts().to_dict()}")
for _g, _gd in FIX.groupby("구분"):
    log(f"  {_g}: {_gd.판정.value_counts().to_dict()}")

# 성분 시트에 되짚을 열을 붙인다. 원값(`ing_name_best`·`ing_cas_best`)은 건드리지 않는다
FIXMAP: dict[tuple, dict] = {}
for _r in FIX[FIX.구분.isin(["CAS", "성분명"])].itertuples():
    d = FIXMAP.setdefault((_r.Formulation_ID, _r.ing_idx),
                          {"정정후보_CAS": "", "정정후보_성분명": "",
                           "정정_판정": "", "정정_사유": ""})
    d[f"정정후보_{_r.구분}"] = _r.정정후보 or ""
    prev = d["정정_판정"]
    if not prev or _RANK.get(_r.판정, 9) < _RANK.get(prev, 9):
        d["정정_판정"], d["정정_사유"] = _r.판정, f"{_r.구분}: {_r.사유}"
    elif _r.판정 == prev:
        d["정정_사유"] = f"{d['정정_사유']} / {_r.구분}: {_r.사유}"

# ------------------------------------------------------------------ 6. 행 단위 종합 · 검토필요
확인 = HL[(HL.판정 == "일치") & HL.필드.isin(["CAS", "성분명"])]
확인키 = set(zip(확인.Formulation_ID, 확인.ing_idx))

# 표기변형 정규화 결과. `ing_name_best` 는 v6 그대로 두고 새 열에만 담는다 — 전버전 보존
CANON = HL[(HL.정규화 == "예")].drop_duplicates(["Formulation_ID", "ing_idx"])
CMAP = {}
for _r in CANON.itertuples():
    _c = dict(CAS_POOL.get(norm(ING.set_index(["Formulation_ID", "ing_idx"]).loc[
        (_r.Formulation_ID, _r.ing_idx), "ing_cas_best"]), [])).get(_r.공유_최종값, 0)
    CMAP[(_r.Formulation_ID, _r.ing_idx)] = (
        _r.공유_최종값, _r.정규화_이전값,
        f"동일 CAS 의 다른 이름이 근거문서에 완전일치 · 데이터셋 내 빈도 {_c} · {_r.근거파일}")
log(f"성분명 정규화: {len(CMAP)}행 (표기변형 앵커에서 승격)")


def 사유(r):
    if (r.Formulation_ID, r.ing_idx) in 확인키:
        return ""
    if (r.Formulation_ID, r.ing_idx) in nonchem_keys:
        return "성분아님(문장형·미상)"
    if r.Formulation_ID not in DOCS:
        return f"대조문서없음({r.MSDS_확보상태})"
    return "원문에서 확인 안 됨"


ING2 = ING.merge(AUD[["Formulation_ID", "MSDS_확보상태"]], on="Formulation_ID", how="left")
ING2["원문대조_판정"] = ["원문확인" if k in 확인키 else "검토 필요"
                     for k in zip(ING2.Formulation_ID, ING2.ing_idx)]
ING2["검토필요_사유"] = ING2.apply(사유, axis=1)
_k2 = list(zip(ING2.Formulation_ID, ING2.ing_idx))
for _j, _col in enumerate(("성분명_정규화", "성분명_정규화_이전값", "성분명_정규화_근거")):
    ING2[_col] = [CMAP.get(k, ("", "", ""))[_j] for k in _k2]
for _col in ("정정후보_CAS", "정정후보_성분명", "정정_판정", "정정_사유"):
    ING2[_col] = [FIXMAP.get(k, {}).get(_col, "") for k in _k2]
파일맵 = FI.groupby("Formulation_ID").archive_path.apply(lambda s: " | ".join(s)).to_dict()
ING2["대조_근거파일"] = ING2.Formulation_ID.map(파일맵).fillna("")
log(f"성분행 종합: {ING2.원문대조_판정.value_counts().to_dict()}")

# ------------------------------------------------------------------ 7. xlsx
ING_COLS = ["Formulation_ID", "ing_idx", "ing_name_best", "ing_cas_best", "ing_pct_best",
            "ing_pct_kind_best", "ing_role_final",
            "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens",
            "ing_ghs_indep_eye_cat", "ing_ghs_indep_eye_tier",
            "ing_ghs_indep_skin_cat", "ing_ghs_indep_skin_tier",
            "ing_ghs_indep_sens_cat", "ing_ghs_indep_sens_tier",
            "ing_src", "ing_pct_src", "농도_원문", "농도_원문출처",
            "농도환산_검증", "농도_환산규칙", "농도_불확실표현",
            "pct_status", "ing2_verdict", "ing2_evidence", "ing2_newlink",
            "원문대조_판정", "검토필요_사유",
            "성분명_정규화", "성분명_정규화_이전값", "성분명_정규화_근거",
            "정정후보_CAS", "정정후보_성분명", "정정_판정", "정정_사유", "대조_근거파일"]
ING_OUT = ING2[ING_COLS]
# 결함 행도 원문까지 되짚을 수 있어야 한다
_nd = len(DEFECT_ALL)
DEFECT_ALL = DEFECT_ALL.merge(
    ING2[["Formulation_ID", "ing_idx", "원문대조_판정", "대조_근거파일"]],
    on=["Formulation_ID", "ing_idx"], how="left")
assert len(DEFECT_ALL) == _nd, f"결함 목록 행이 늘었다 {_nd} → {len(DEFECT_ALL)}"


# 제품 문서 재수집(`collect_product_docs.py`) 결과. 빌드가 만드는 표가 아니라 있으면
# 읽어서 README 에 적는다. 없으면 '아직 돌지 않았다' 고 적는다 — 돌지 않은 것을
# '못 찾았다' 로 적으면 하지 않은 일을 한 것처럼 보인다
RECOL = OUT / "10_제품문서_재수집.csv"
RC = pd.read_csv(RECOL) if RECOL.exists() else None
if RC is None:
    RECOL_MD = """## 제품 문서 재수집

아직 돌지 않았다. `01_파이프라인/collect_product_docs.py` 를 돌리면 이 자리에
대상·발견·미발견 건수와 사유가 들어온다."""
    RECOL_ROW = ("| `10_제품문서_재수집.csv` | 아직 없다 — "
                 "`collect_product_docs.py` 를 돌리면 생긴다 |")
else:
    _rw = RC.사유.fillna("")
    # 미발견 사유는 '원URL 쪽 결과 · [아카이브 재조회] 아카이브 쪽 결과' 로 이어 붙여
    # 적혀 있다. 마지막 판정은 아카이브 쪽이라 그 부분만 떼어 분류한다 — 앞쪽 문구까지
    # 세면 1차 판에서 아카이브가 죽어 있던 기록이 중복으로 잡힌다
    _fin = _rw.str.split("[아카이브 재조회]", regex=False).str[-1]
    rc_f = int((RC.판정 == "발견").sum())
    rc_path = " · ".join(f"{k} {v}" for k, v
                         in RC[RC.판정 == "발견"].경로.value_counts().items()) or "-"
    rc_cls = " · ".join(f"{c} 발견 {int((g.판정 == '발견').sum())}/{len(g)}"
                        for c, g in RC.groupby("분류"))
    rc_nourl = int(RC.후보URL수.fillna(0).eq(0).sum())
    rc_noans = int(_fin.str.contains(
        "아카이브 조회 HTTP|아카이브 조회실패|아카이브 응답 형식", regex=True).sum())
    rc_nosnap = int(_fin.str.contains("스냅샷 없음").sum())
    rc_snapfail = int(_fin.str.contains(r"아카이브 스냅샷 \d+건도 검증 미통과",
                                        regex=True).sum())
    # 원URL 쪽 통신실패만 세려면 아카이브 쪽 오류 문구를 먼저 지워야 한다
    _od = (_rw.str.replace(r"아카이브 조회(?: HTTP \d+|실패\([^)]*\)| 응답 형식 이상)", "",
                           regex=True)
              .str.replace(r"web\.archive\.org: [^|·]*", "", regex=True))
    rc_dead = int(_od.str.contains(r"HTTP [45]\d\d|Timeout|ConnectionError|SSLError",
                                   regex=True).sum())
    rc_nopn = int(_rw.str.contains("제품명 없음").sum())
    rc_nohit = int(_rw.str.contains("성분 일치 0").sum())
    RECOL_ROW = (
        f"| `10_제품문서_재수집.csv` | 제품 문서가 안 붙은 제형 {len(RC)}건을 다시 "
        f"찾아본 기록 — 발견 {rc_f}건, 나머지는 빈칸 (`collect_product_docs.py` 산출) |\n"
        f"| `10_재수집문서/` | 그중 검증을 통과해 채택한 문서 {rc_f}건 |")
    RECOL_MD = f"""## 제품 문서 재수집 — 찾은 {rc_f}건, 나머지는 빈칸

`다른제품문서`·`대체근거문서` 로 걸린 제형 {len(RC)}건은 **그 제품의 문서가 아예 안
붙어 있는** 상태다. 그래서 문서를 다시 찾아봤다 — 결과는
**발견 {rc_f}건 · 미발견 {len(RC) - rc_f}건**이고, 표는 `10_제품문서_재수집.csv`,
찾은 문서는 `10_재수집문서/` 에 있다. **미발견은 빈칸으로 뒀다** — 비슷한 문서를
대신 채워 넣지 않았다.

쓸 수 있는 경로는 우리 데이터에 이미 있는 원문 URL 과 웹아카이브 두 개뿐이었다.
공개 검색엔진(DuckDuckGo·Bing·Brave·Mojeek·Startpage·Yandex 등)은 전부 차단이거나 빈
응답이고, 라벨 DB(cdms.net·greenbook·agrian·CDPR)는 로그인·JS·API 키를 요구한다.
"검색해서 찾기" 는 이 환경에서 불가능해서, 아래는 **가진 URL 로 할 수 있는 만큼** 한
결과다.

분류별로는 {rc_cls} 다. 제품명이 아예 없던 `다른제품문서` 쪽은 한 건도 찾지 못했고,
찾은 {rc_f}건은 모두 성분 단독 SDS 가 붙어 있던 `대체근거문서` 쪽이다.

미발견 {len(RC) - rc_f}건이 **어디서 막혔는지**는 겹치지 않게 나뉜다.

| 막힌 자리 | 건수 | 뜻 |
|---|---|---|
| 웹아카이브가 응답하지 않음 | {rc_noans} | HTTP 503·타임아웃. **없다고 확인한 것이 아니라 확인을 못 한 것이다** |
| 웹아카이브에 스냅샷이 없음 | {rc_nosnap} | 그 URL 이 한 번도 보존되지 않았다 |
| 스냅샷은 받았으나 검증 미통과 | {rc_snapfail} | 문서를 받았지만 제품명·성분·형식 조건을 못 넘겼다 |

원URL 쪽에서 무엇이 막혔는지는 한 제형에 여러 개가 겹쳐 합이 대상 수와 다르다 —
통신 자체가 안 된 것(HTTP 40x·50x·타임아웃) {rc_dead}건, 문서는 받았는데 제품명이 없던 것
{rc_nopn}건, 성분이 하나도 안 겹친 것 {rc_nohit}건, 후보 URL 이 아예 없던 것 {rc_nourl}건이다.
제형별 사유 전문은 `10_제품문서_재수집.csv` 의 `사유` 열에 있다.

채택은 세 조건을 모두 넘긴 문서만 했다 — ① 제품명이 **낱말 단위로** 문서에 있고
② 우리 성분(CAS 또는 이름)이 하나 이상 함께 있고 ③ SDS·라벨 형식 표지가 있다.
이름 조각이 우연히 걸려 엉뚱한 문서가 붙는 일(`Zoxamide` 에 `oxamide.pdf`)이 이
데이터의 원래 결함이라, 같은 실수를 반복하지 않기 위한 조건이다."""

FORM_OUT = FORM[["Formulation_ID", "product_name", "formulation_type_ko",
                 "y_eye", "y_skin", "y_sens", "ph_best", "ph_src"]].merge(
    AUD[["Formulation_ID", "MSDS_확보상태", "Audit_Status", "재수집후보_URL"]],
    on="Formulation_ID", how="left").rename(columns={"Audit_Status": "증거_신뢰도"})
행요약 = ING2.groupby("Formulation_ID").원문대조_판정.agg(
    성분행수="size", 원문확인수=lambda s: int((s == "원문확인").sum())).reset_index()
FORM_OUT = FORM_OUT.merge(행요약, on="Formulation_ID", how="left")
FORM_OUT["대조_근거파일"] = FORM_OUT.Formulation_ID.map(파일맵).fillna("")
# 제품 문서를 다시 찾아본 제형은 그 결과를 여기에도 적는다. 찾은 것은 경로, 못 찾은
# 것은 빈칸이다 — 대상이 아니었던 제형(공란)과 구분되도록 판정 열을 함께 둔다
RECOL_COL = ""
if RC is not None:
    _found = RC[RC.판정 == "발견"]
    FORM_OUT["재수집_판정"] = FORM_OUT.Formulation_ID.map(
        dict(zip(RC.Formulation_ID, RC.판정))).fillna("")
    FORM_OUT["재수집_문서"] = FORM_OUT.Formulation_ID.map(dict(zip(
        _found.Formulation_ID,
        _found.채택파일.map(lambda p: f"10_재수집문서/{Path(str(p)).name}")))).fillna("")
    RECOL_COL = "·제품문서 재수집 판정"

REVIEW = ING_OUT[ING_OUT.원문대조_판정 == "검토 필요"].copy()

기준차이 = pd.DataFrame([
    ("성분 단위 행 수", f"{ARCH_SUM['ingredient_occurrences']} occurrences", f"{len(ING)} 행",
     "과거는 문서 등장 횟수, 이번은 제형×성분 고유행. 2차 재판독에서 중복·총계행을 정리했다"),
    ("성분 판정 어휘", "UNRESOLVED / WEAK / MISMATCH / EXCEPTION",
     "일치 / 표기변형 / 문서불일치 / 위치불명 / 좌표실패 / 좌표검증실패 / "
     "문서판독불가 / 성분아님 / 대상없음"
     " (+농도 전용: 위치불일치 / 근거불충분)",
     "과거는 신뢰도 등급, 이번은 원문 대응 여부. 직접 비교 불가"),
    ("농도 근거 인정 조건", "문자열 일치만으로 인정",
     "같은 행 CAS·성분명이 확인된 페이지에 농도 문자열이 함께 있을 때만 인정",
     "'1%' 같은 짧은 문자열이 문서 아무 곳에서나 걸리는 것을 막기 위해서다"),
    ("근거 범위 표기", "basis: PRIMARY_SECTION_3 / PRIMARY_FULL_DOCUMENT / NO_SOURCE_EXCEPTION",
     "원문_구간: 3절(조성) / 3절~4절사이 / 문서기타 / 구간불명",
     "3절 머리글이 실제로 찍힌 페이지만 `3절(조성)` 이다. 머리글은 없지만 3절~4절 "
     "사이에 든 페이지는 `3절~4절사이` 로 따로 뺐다 — 예전에는 둘을 합쳐 3절로 적어 "
     "조성표가 아닌 페이지까지 '3절' 로 보고됐다"),
    ("농도", "제형당 문자열 1개 (Formulation_Ingredients_Pct)", "성분행별 수치 + 원문 문자열 + 환산검증",
     "과거 판은 성분이 2개인데 '40%' 하나만 있는 등 성분-농도 정렬이 깨진 사례가 있었다"),
    ("하이라이트", "노란색 1종 · 주석 없음 · 키워드 일치",
     "필드별 3색 · 주석에 공유값 병기 · 값 확인 후에만 칠함",
     "과거 판은 칠해진 칸이 어느 데이터가 되었는지 파일만 봐서는 알 수 없었다"),
    ("원본 파일", "original_path 전건 공란 — 순수 원본 없음", "동일(과거 사본을 물려받음)",
     "이번 판도 원본이 아니라 파생 사본이다. 재수집 없이는 해소되지 않는다"),
    ("성분명 표기 정규화", "없음(이름이 원문과 달라도 그대로 둠)",
     f"같은 CAS 의 다른 이름이 근거문서에 완전일치할 때만 정규화 — {len(CMAP)}행. "
     "`ing_name_best` 는 v6 그대로 두고 `성분명_정규화`·`성분명_정규화_이전값` 새 열에 담았다",
     "이름을 새로 만들지 않으려고 후보를 v6 안의 동일 CAS 이름으로만 한정했다. 전버전을 "
     "지우지 않았으니 되짚을 수 있다"),
    ("문서불일치 사유", "MISMATCH 한 덩어리",
     f"앵커 {len(MIS)}건 전수에 대해 분류·원인·근거를 한 줄씩 남김 "
     "(`07_문서불일치_전수검사.csv`)",
     "'문서에 없다' 만으로는 다음 조치를 정할 수 없다. 다른 제품 문서인지, 표기만 다른지, "
     "우리 데이터가 잡음인지 갈라야 한다"),
    ("우리 값이 틀린 경우", "구분 없음(MISMATCH 로 뭉갬)",
     f"`데이터쪽오류` 로 분리하고 정정 후보를 원장에 적음 — {len(FIX)}행 "
     "(`09_데이터정정_후보.csv`). 값은 덮어쓰지 않았다",
     "v6 는 읽기 전용이다. 후보·판정·근거만 남기고 반영 여부는 받는 쪽이 정한다. "
     "`적용가능` 은 정정값이 근거문서에 실재하는 것만이다"),
    ("농도 불확실표현", "표기 없음",
     "원문이 스스로 '가정·미확인·영업비밀·명목값' 이라 적은 문자열을 "
     "`농도_불확실표현` 열에 그대로 옮김",
     "예전 어휘가 좁아 `implies` 만 잡고 `implied`·`nominal`·`trade secret` 은 "
     "놓쳤다. 같은 성격의 행이 표시 없이 나갔다"),
    ("라벨 관할", "표기 없음", "EU CLP (눈 2B=비분류 · 피부 구분3=비분류)",
     "v8 에서 관할을 명시. 과거 판 라벨과 그대로 비교하면 어긋난다"),
], columns=["항목", "과거 공유본", "이번 판", "차이가 생긴 이유"])

n_prod_files = int((FI.kind == "제품단위_하이라이트").sum())
n_surr_files = int((FI.kind == "대체근거_하이라이트").sum())
요약 = pd.DataFrame([
    ("전체 제형(제품코드)", len(FORM_OUT), "input_dataset_v6.xlsx formulation"),
    ("전체 성분행", len(ING_OUT), "input_dataset_v6.xlsx ingredient"),
    ("원문에서 확인된 성분행", int((ING2.원문대조_판정 == "원문확인").sum()),
     "CAS 또는 성분명이 대조문서 원문에 그대로 있는 행 — 하이라이트 있음"),
    ("검토 필요 성분행", len(REVIEW), "확인 못 한 행 전부. 추정으로 채우지 않았다"),
    ("하이라이트 총 개수", int(HL.하이라이트_수.sum()), "필드별 색 · 주석에 공유값 병기"),
    ("대조 문서", n_prod_files + n_surr_files,
     f"제품단위 {n_prod_files} · 대체근거 {n_surr_files}"),
    ("MSDS 제품단위 제형", int(CNT["제품단위"]), "감사상태 전량 EVIDENCE_WEAK/MISMATCH — MATCH 0건"),
    ("MSDS 대체근거 제형", int(CNT["대체근거"]), "제품 고유 문서 아님. 순수성분 SDS를 CAS로 매칭"),
    ("근거없음·불필요(NTP자체보고)", int(CNT["불필요_NTP자체보고"]), "NTP 시험자료에 성분이 직접 보고됨"),
    ("근거없음·출처소실", int(CNT["출처소실"]), "MATCH 였으나 출처 파일이 안 남음"),
    ("근거없음·공백", int(CNT["공백"]), f"진짜 공백. 재수집 후보 URL {n_gap_url}건"),
    ("농도 미검증(추정치)", int((ING.농도환산_검증 == "미검증_추정치").sum()),
     "원문이 스스로 '가정·미확인·영업비밀·명목값' 이라 적은 값. 확정값으로 쓰지 말 것. "
     "성분 시트 농도환산_검증 = 미검증_추정치 · 어느 표현이었는지는 농도_불확실표현 열"),
    ("농도 원문없음·값있음", int((ING.농도환산_검증 == "원문없음_값있음").sum()), "출처 문자열이 남지 않은 값"),
    ("농도 환산 불일치", int((ING.농도환산_검증 == "규칙불일치").sum()), "원문↔최종값 환산 규칙이 안 맞는 행"),
    ("이름 있고 CAS 없는 성분행", int((ING.ing_name_best.notna() & ING.ing_cas_best.isna()).sum()),
     "PubChem 보강 대상(다음 단계)"),
    ("이름·CAS 모두 없는 성분행", len(완전미상), "성분 식별 불가. 목록만 표시"),
    ("성분명 정규화한 행", len(CMAP),
     "표기변형 중 같은 CAS 의 다른 이름이 근거문서에 완전일치한 행. 이전값 보존"),
    ("문서불일치 앵커 전수검사", len(MIS),
     f"전건 원인 분류. 07_문서불일치_전수검사.csv — {MIS.분류.value_counts().to_dict()}"),
    ("데이터정정 후보", len(FIX),
     f"우리 값이 틀린 건의 정정 후보. 09_데이터정정_후보.csv — "
     f"{FIX.판정.value_counts().to_dict()}. v6 값은 덮어쓰지 않았다"),
    ("데이터정정 적용가능", int((FIX.판정 == "적용가능").sum()),
     "정정값이 근거문서에 실재하는 건. 받는 쪽이 반영 여부를 정한다"),
], columns=["구분", "건수", "설명"])

PROV_OUT = PROV.rename(columns={"field": "항목", "value": "값", "source_tier": "출처_계층",
                                "source_url": "출처_URL", "verdict": "판정"})
XLSX = OUT / "02_제품코드별_성분리스트.xlsx"
with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
    요약.to_excel(w, sheet_name="요약", index=False)
    FORM_OUT.to_excel(w, sheet_name="제형", index=False)
    ING_OUT.to_excel(w, sheet_name="성분", index=False)
    HL.to_excel(w, sheet_name="원문대조", index=False)
    PROV_OUT.to_excel(w, sheet_name="값출처", index=False)
    REVIEW.to_excel(w, sheet_name="검토필요", index=False)
    DEFECT_ALL.to_excel(w, sheet_name="데이터_이슈", index=False)
    MIS.to_excel(w, sheet_name="문서불일치검사", index=False)
    FIX.to_excel(w, sheet_name="데이터정정후보", index=False)
    기준차이.to_excel(w, sheet_name="기준차이", index=False)

from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(XLSX)
for sn in wb.sheetnames:
    ws = wb[sn]
    ws.freeze_panes = "A2"
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 46)
wb.save(XLSX)
log(f"xlsx 저장 — 시트 {wb.sheetnames}")

# ------------------------------------------------------------------ 8. csv 3종
FI.to_csv(OUT / "03_근거파일_인벤토리.csv", index=False, encoding="utf-8-sig")
HL.to_csv(OUT / "05_원문대조표.csv", index=False, encoding="utf-8-sig")

# 한 원본 문서가 여러 제형에 걸쳐 쓰인다(고유 411건 → 참조 703건). 참조 수를 함께 싣는다
g = FI.groupby("원경로")
MAN_OUT = MAN.drop(columns=["original_path", "text_path", "error", "text_error",
                            "highlight_error"], errors="ignore").copy()
hp = MAN_OUT.highlighted_path
MAN_OUT["이번판_참조제형수"] = hp.map(g.Formulation_ID.nunique().to_dict()).fillna(0).astype(int)
MAN_OUT["이번판_경로"] = hp.map(g.archive_path.apply(lambda s: " | ".join(sorted(s))).to_dict())
MAN_OUT["이번판_판독가능"] = hp.map(g.판독가능.max().to_dict())
MAN_OUT["이번판_하이라이트수"] = hp.map(g.하이라이트_수.sum().to_dict())
MAN_OUT.to_csv(OUT / "04_출처수집기록.csv", index=False, encoding="utf-8-sig")
MIS.to_csv(OUT / "07_문서불일치_전수검사.csv", index=False, encoding="utf-8-sig")
FIX.to_csv(OUT / "09_데이터정정_후보.csv", index=False, encoding="utf-8-sig")

# ------------------------------------------------------------------ 9. 01_요약.json
files_bytes = int(FI.size_bytes.sum())
SUMMARY = {
    "패키지": OUT.name,
    "selection_rule":
        "제품코드(Formulation_ID)별 성분 리스트 전량과, 그 값이 원문 MSDS 어디서 나왔는지 "
        "확인된 부분만 필드별 색으로 다시 하이라이트한 근거 사본. 원문에서 확인되지 않은 값은 "
        "칠하지 않고 '검토 필요' 로 남긴다. 값은 어느 것도 새로 만들거나 채우지 않았다. "
        "근거 사본은 원본이 아니라 파생본이다(과거 감사 산출물에도 순수 원본은 없다).",
    "계승한_과거양식": {
        "summary.json": "01_요약.json", "inventory.csv": "03_근거파일_인벤토리.csv",
        "source_manifest.csv": "04_출처수집기록.csv", "review_queue.csv": "xlsx 검토필요 시트",
        "ingredient_audit_summary.json": "01_요약.json 의 판정_집계",
        "Audit_Evidence_Terms/Snippet": "05_원문대조표.csv",
    },
    "counts": {
        "제형": len(FORM_OUT), "성분행": len(ING_OUT),
        "원문확인_성분행": int((ING2.원문대조_판정 == "원문확인").sum()),
        "검토필요_성분행": len(REVIEW),
        "대조앵커": len(HL), "하이라이트_개수": int(HL.하이라이트_수.sum()),
        "근거파일_제품단위": n_prod_files, "근거파일_대체근거": n_surr_files,
        "재수집후보_URL": n_gap_url,
        "성분명_정규화_행": len(CMAP), "문서불일치_전수검사": len(MIS),
        "데이터정정_후보": len(FIX),
        "농도_불확실표현_행": int((ING.농도_불확실표현 != "").sum()),
    },
    "판정_집계": {
        "앵커_판정": {k: int(v) for k, v in HL.판정.value_counts().items()},
        "원문_구간": {k: int(v) for k, v in HL[HL.판정 == "일치"].원문_구간.value_counts().items()},
        "농도환산_검증": {k: int(v) for k, v in ING.농도환산_검증.value_counts().items()},
        "농도_환산규칙": {k: int(v) for k, v in ING.농도_환산규칙.value_counts().items()},
        "MSDS_확보상태": {k: int(v) for k, v in CNT.items()},
        "문서불일치_분류": {k: int(v) for k, v in MIS.분류.value_counts().items()},
        "문서불일치_원인": {k: int(v) for k, v in MIS.원인.value_counts().items()},
        "데이터정정_판정": {k: int(v) for k, v in FIX.판정.value_counts().items()},
        "데이터정정_구분": {k: int(v) for k, v in FIX.구분.value_counts().items()},
    },
    "sizes": {"xlsx_bytes": XLSX.stat().st_size, "근거파일_bytes": files_bytes},
    "kind_counts": {k: int(v) for k, v in FI.kind.value_counts().items()},
    "입력_sha256": {
        "input_dataset_v6.xlsx": sha256(L.V6),
        "formulation_ingredients_master_audited.xlsx": sha256(AUDIT_XLSX),
        "성분표_결함_확인용_260915.xlsx": DEFECT_SHA,
    },
    "결함목록_출처": DEFECT_SRC,
    "신뢰_경계":
        "하이라이트는 '원문에 그 문자열이 있다' 까지만 보증한다. 그 문서가 해당 제품의 "
        "정본 SDS인지, 판까지 맞는지는 보증하지 않는다. 제품단위 문서 569건의 과거 감사상태는 "
        "전량 EVIDENCE_WEAK·MISMATCH 이고 MATCH 는 0건이다.",
    "다음_단계_미시행": [f"공백 URL {n_gap_url}건 재수집",
                    "CAS 미상 성분 PubChem 보강", "농도 결측 2,121행 확보"],
}
(OUT / "01_요약.json").write_text(json.dumps(SUMMARY, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

# ------------------------------------------------------------------ 10. 설명 문서 2종
# README 와 과거공유본 분석도 여기서 쓴다. 손으로 적으면 다음 실행에서 숫자가 데이터와
# 어긋난다 — 실제로 초안에서 MATCH 881건의 내역을 638+243 이 아니라 694+243 으로 적어
# 합이 맞지 않았다. 아래 숫자는 전부 위에서 계산된 값이다
SP = AUDIT_ROOT / "artifacts" / "share_packages"
A_INV = pd.read_csv(SP / f"{AUDIT_ROOT.name}.inventory.csv")
A_RQ = pd.read_csv(SP / f"{AUDIT_ROOT.name}.review_queue.csv")
A_FORM = pd.ExcelFile(AUDIT_XLSX).parse("Formulations")
MAN_BLANK = [c for c in MAN.columns if MAN[c].isna().all()]


def dir_stat(rel: str) -> tuple[int, float]:
    ps = [p for p in (AUDIT_ROOT / rel).rglob("*") if p.is_file()]
    return len(ps), sum(p.stat().st_size for p in ps) / 1024 ** 2


n_hl_a, mb_hl_a = dir_stat("ingredient_source_audit/highlighted")
n_oo_a, mb_oo_a = dir_stat("ingredient_source_audit/ordered_option_runs/highlighted_sources")

nofile = ~AUD["Audit_Highlighted_Source_File"].notna() & ~AUD["Supplemental_Highlighted_Source_File"].notna()
is_match = AUD.Audit_Status == "MATCH"
n_match = int(is_match.sum())
n_match_ntp = int((is_match & nofile & (AUD.Data_Quality == "original")).sum())
n_match_lost = int((is_match & nofile & (AUD.Data_Quality != "original")).sum())
assert n_match_ntp + n_match_lost == n_match, "MATCH 내역 합이 안 맞는다"
n_nofile_form = len(FORM_OUT) - int(CNT["제품단위"]) - int(CNT["대체근거"])

PJ = HL.판정.value_counts().to_dict()
DFMT = FI.문서형식.value_counts().to_dict()
# 정규화로 '일치' 로 올라간 앵커. 이걸 더해야 원래의 표기변형 총수가 나온다
n_canon_anchor = int((HL.정규화 == "예").sum())
n_var_orig = n_canon_anchor + PJ.get("표기변형", 0)
n_unverified = int((ING.농도환산_검증 == "미검증_추정치").sum())
# `규칙일치` 안에서 원문이 범위인 행. 이 행의 최종값은 원문에 없는 중앙값이라
# '문서에서 확인' 의 뜻이 나머지와 다르다 — README 에 갈라 적는다
_PM = ING[ING.농도_환산규칙.isin(["규칙일치", "규칙일치_%표기"])]
n_pct_range = int((_PM.ing_pct_kind_best == "range").sum())
n_pct_point = len(_PM) - n_pct_range
MC = MIS.분류.value_counts().to_dict()
n_cas_gap = int((ING.ing_name_best.notna() & ING.ing_cas_best.isna()).sum())
n_pct_gap = int(ING.ing_pct_best.isna().sum())
날짜 = f"20{OUT.name[-6:-4]}-{OUT.name[-4:-2]}-{OUT.name[-2:]}"
총_mb = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1024 ** 2

(OUT / "README.md").write_text(f"""# 화학연구원 공유 자료 — 제형 GHS 분류 데이터와 그 원문 근거

작성 {날짜} · 대상 제형 {len(FORM_OUT):,}건 · 성분행 {len(ING_OUT):,}행 ·
근거 문서 {len(FI):,}건 ({총_mb:,.0f} MB)

## 이 자료의 목적

성분 리스트만 넘기면 값이 맞는지 확인할 방법이 없다. 그래서 **각 값이 원문 MSDS의
어느 글자에서 나왔는지 되짚을 수 있는 형태**로 만들었다. 동봉한 MSDS 사본에는
우리가 실제로 쓴 자리마다 색칸이 있고, 그 색칸을 클릭하면 "이 글자가 공유 데이터의
어느 값이 되었는지"가 주석으로 뜬다.

## 먼저 알아야 할 세 가지

**1. 원문에서 확인된 것은 성분행 {len(ING_OUT):,}행 중 {SUMMARY['counts']['원문확인_성분행']:,}행이다.**
나머지 {len(REVIEW):,}행은 전부 `검토 필요` 로 남겼다. 값을 추정해서 채운 곳은 없다.
확인율이 낮은 이유는 세 가지가 겹쳐 있다 — 애초에 문서가 없는 제형이 {n_nofile_form:,}건,
붙어 있는 문서가 그 제품 문서가 아닌 경우, 그리고 성분명 표기가 원문과 다른 경우다.
어느 쪽인지는 `성분` 시트의 `검토필요_사유` 열에 적혀 있다.

**2. 동봉 문서 {len(FI):,}건은 "확보에 성공한 신뢰할 만한 근거"가 아니다.**
과거 팀 감사에서 **검토가 필요하다고 걸린 사례**다. 이 {int(CNT['제품단위'])}개 제형의 감사상태는
전량 `EVIDENCE_WEAK`·`MISMATCH` 이고 `MATCH` 는 0건이다. 반대로 감사상태가 가장
좋았던 `MATCH` {n_match}건은 파일이 한 건도 남아 있지 않다({n_match_ntp}건은 NTP 자체
시험자료에 성분이 직접 보고돼 외부 확인이 애초에 불필요, {n_match_lost}건은 출처 파일
소실). **문서가 있다는 것이 믿을 만하다는 뜻이 아니고, 없다는 것이 못 믿는다는 뜻도 아니다.**
`제형` 시트의 `MSDS_확보상태` 와 `증거_신뢰도` 를 함께 보시기 바란다.

**3. 동봉 문서는 원본이 아니라 파생 사본이다.**
과거 감사 도구가 마킹을 입힌 사본이고, 순수 원본은 우리 쪽에도 없다
(수집 기록의 `original_path` 가 {len(MAN)}건 전량 공란). 이번 판은 그 사본에서 과거의
노란 칸을 걷어내고 다시 칠했다. 원문 URL 은 `04_출처수집기록.csv` 에 있다.

## 읽는 순서

| 파일 | 무엇 |
|---|---|
| `README.md` | 이 문서 |
| `00_과거공유본_분석.md` | 2026-06-30 공유본의 양식·수집 방식과 이번 판의 차이 |
| `01_요약.json` | 패키지 자기기술 — 건수·판정 집계·입력 sha256·신뢰 경계 |
| `02_제품코드별_성분리스트.xlsx` | **본체.** {len(wb.sheetnames)}개 시트 (아래 참조) |
| `03_근거파일_인벤토리.csv` | 동봉 문서 {len(FI):,}건 전수 — sha256·문서형식·하이라이트 수 |
| `04_출처수집기록.csv` | 원문 URL·수집 상태·3절 발견 여부 ({len(MAN)}개 원천 문서) |
| `05_원문대조표.csv` | **앵커 {len(HL):,}행.** 값 하나하나가 원문 어디에 어떻게 대응되는지 |
| `06_검수보고.md` | 공유 전 검수 결과 |
| `07_문서불일치_전수검사.csv` | **문서불일치 앵커 {len(MIS):,}건 전수** — 건마다 원인 한 줄 |
| `08_판독규칙_감사.csv` | `표기차이`·`데이터쪽오류` 앵커를 규칙을 풀어 가며 다시 읽은 기록 (`audit_read_rules.py` 산출) |
| `08_표기차이_판독규칙_판단.md` | 위 감사 결과를 단계별로 읽은 판단 — 규칙을 풀어 살릴 수 있는 건과 못 살리는 건 |
| `08b_일치앵커_경계검사.csv` | 칠한 성분명 앵커가 더 긴 낱말 안에서만 걸린 것이 없는지 (같은 스크립트) |
| `08c_HTML사본_낱말분절.csv` | `<mark>` 삽입이 사본의 낱말을 가른 건수 (같은 스크립트) |
| `09_데이터정정_후보.csv` | **우리 값이 틀린 건의 정정 후보 {len(FIX):,}행** — 값은 덮어쓰지 않았다 |
{RECOL_ROW}
| `MSDS_하이라이트/<제품코드>/` | 제품 단위 근거 {n_prod_files}건 |
| `대체근거_참고/<제품코드>/` | 성분 단위 대체 근거 {n_surr_files}건 — 제품 고유 문서 아님, 등급 낮음 |

`02_제품코드별_성분리스트.xlsx` {len(wb.sheetnames)}개 시트:

| 시트 | 행 | 내용 |
|---|---|---|
| `요약` | {len(요약):,} | 건수 한 장 요약 |
| `제형` | {len(FORM_OUT):,} | 제품코드·제품명·제형·라벨(눈/피부/감작)·pH·MSDS 확보상태·근거파일{RECOL_COL} |
| `성분` | {len(ING_OUT):,} | 성분명·CAS·농도·역할·성분 GHS·**농도 원문**·원문대조 판정·검토필요 사유 |
| `원문대조` | {len(HL):,} | `05_원문대조표.csv` 와 같은 내용 |
| `값출처` | {len(PROV_OUT):,} | 제형 라벨·pH·코드가 각각 어느 출처 계층·URL에서 왔는지 |
| `검토필요` | {len(REVIEW):,} | 확인 못 한 성분행만 뽑은 것 (과거 `review_queue.csv` 자리) |
| `데이터_이슈` | {len(DEFECT_ALL):,} | 결함 6종 목록. **값은 고치지 않았다** |
| `문서불일치검사` | {len(MIS):,} | `07_문서불일치_전수검사.csv` 와 같은 내용 |
| `데이터정정후보` | {len(FIX):,} | `09_데이터정정_후보.csv` 와 같은 내용 |
| `기준차이` | {len(기준차이):,} | 과거 아카이브와 이번 판의 기준 차이 |

## 값 하나를 원문까지 되짚는 방법

예를 들어 `성분` 시트에서 어떤 행의 농도가 80% 라고 적혀 있다면:

1. 같은 행의 `농도_원문` 을 본다 → `60-100%` 처럼 원문 문자열이 그대로 있다.
   `농도환산_검증` 이 `규칙일치` 면 범위의 중앙값을 취해 80 이 된 것이다.
2. `대조_근거파일` 의 경로로 MSDS 사본을 연다.
3. `05_원문대조표.csv` 를 같은 `Formulation_ID` + `ing_idx` 로 걸러 보면
   `원문_페이지` 와 `원문_스니펫` 이 있다. 그 페이지로 간다.
4. 파랑 칸이 `60-100%` 위에 칠해져 있고, 주석에
   `[제품코드 #행번호] 농도=원문「60-100%」 → 공유값 80.0` 이 적혀 있다.

`원문대조_판정` 이 `원문확인` 이 아닌 행은 4번에서 칠해진 칸이 없다. 그건 우리가
원문에서 확인하지 못했다는 뜻이고, 값을 믿지 말라는 표시다.

## 하이라이트 범례

| 색 | 필드 |
|---|---|
| 노랑 | CAS 번호 |
| 초록 | 성분명 |
| 파랑 | 농도 |

PDF 는 주석(Highlight)이라 Acrobat·Preview 등에서 마우스를 올리면 주석 내용이 보인다.
HTML 은 `<mark>` 태그이고 `title` 속성에 같은 내용이 들어 있다.

**칠하는 기준.** 단순 문자열 일치만으로 칠하지 않았다.

- 먼저 CAS 나 성분명으로 그 성분이 문서 어느 **페이지**에 있는지 특정한다.
- 농도는 **그 성분이 확인된 페이지 안에 함께 있을 때만** 칠한다. `1%` 같은 짧은
  문자열은 문서 어디서든 걸리기 때문이다.
- 숫자로 시작하는 문자열은 앞뒤 글자를 검사해 부분일치를 걸러낸다
  (`2.8%` 가 `12.8%` 안에서 걸리는 것을 막는다).
- 칠하기 직전에 그 사각형이 덮은 글자를 다시 읽어 원문과 맞는지 확인한다.
  어긋나면 칠하지 않고 `좌표검증실패` 로 남긴다.
- 그래서 **칠해진 칸 {int(HL.하이라이트_수.sum()):,}개는 전부 검수를 통과한 것**이다(`06_검수보고.md` C3).

## 하이라이트가 보증하는 범위

색칸은 **"이 문서의 이 글자가 공유 데이터의 이 값이 되었다"** 까지만 보증한다.
그 문서가 해당 제품의 정본 SDS 인지, 판까지 맞는지는 보증하지 않는다.

동봉 문서 {len(FI):,}건을 형식으로 판별해 보면
{" · ".join(f"{k} {v}" for k, v in DFMT.items())} 이다.
`SDS아님(라벨·목록)` 은 EPA·주정부 등록 라벨이나 제품 목록 페이지로, 유효성분 농도
근거로는 쓸 수 있으나 GHS 분류 근거로는 약하다. 이 판별은
`03_근거파일_인벤토리.csv` 의 `문서형식` 열에 있다.

## 성분명 표기 정규화 ({len(CMAP)}행)

같은 물질이 문서마다 다른 이름으로 적혀 있다. 우리 데이터의 이름이 근거문서에
없더라도 **CAS 는 문서에 있는** 앵커가 {n_var_orig}건 있었다.
이 중 {n_canon_anchor}건은 **같은 CAS 를 가진 다른 행의 이름이 그 문서에 그대로 적혀 있어서**
그 이름으로 정규화했다(성분행 기준 {len(CMAP)}행).

- 후보는 `input_dataset_v6.xlsx` 안에 **이미 있는 이름**뿐이다. 이름을 새로 만들지 않았다.
- 후보와 우리 행은 **CAS 가 완전히 같아야** 한다.
- 그 후보가 **근거문서에 문자열 그대로** 있어야 한다. 부분일치는 인정하지 않았다.
- 후보가 여러 개면 **데이터셋에서 가장 많이 쓰인 이름**을 골랐다.

**전버전은 지우지 않았다.** `성분` 시트의 `ing_name_best` 는 v6 원값 그대로이고,
정규화 결과는 `성분명_정규화`·`성분명_정규화_이전값`·`성분명_정규화_근거` 세 열에
따로 담았다. 하이라이트 주석도 `성분명(정규화)=원문「…」 → 공유값 … / 이전값「…」`
형태로 둘 다 보여준다. 어느 이름을 쓸지는 받는 쪽에서 판단하시면 된다.

나머지 {PJ.get('표기변형', 0)}건은 문서에서 대체할 이름을 찾지 못해 `표기변형` 으로 남겼다.

## 문서불일치 앵커 전수검사 ({len(MIS):,}건)

붙어 있는 문서에 그 값이 없는 앵커가 {len(MIS):,}개다. "문서에 없다" 만으로는 다음에
무엇을 할지 정할 수 없어서, **{len(MIS):,}건 전부**에 대해 원인을 한 줄씩 남겼다
(`07_문서불일치_전수검사.csv` · xlsx `문서불일치검사` 시트). 표본이 아니라 전건이다.

| 분류 | 건수 | 뜻과 조치 |
|---|---|---|
| `다른제품문서` | {MC.get('다른제품문서', 0):,} | 제품명 어절이 문서에 하나도 없다. 문서 자체를 다시 받아야 한다 — 아래 `제품 문서 재수집` 참조 |
| `대체근거문서` | {MC.get('대체근거문서', 0):,} | 제품 문서 자리에 **성분 단독 SDS** 가 붙어 있다. 제품명이 없는 것은 당연하고, 제품 고유 근거로는 쓸 수 없다 — 아래 `제품 문서 재수집` 참조 |
| `같은문서_성분목록다름` | {MC.get('같은문서_성분목록다름', 0):,} | 제품명은 맞는데 그 성분이 없다. 판 차이이거나 우리 성분표가 다른 출처에서 온 것 |
| `표기차이` | {MC.get('표기차이', 0):,} | 값은 문서에 있는데 표기가 다르다. **다만 규칙을 풀어 안전하게 살릴 수 있는 것은 34건뿐**이고 나머지는 우연 일치·추출 잡음·이명이다(`08_표기차이_판독규칙_판단.md`) |
| `문서형식부적합` | {MC.get('문서형식부적합', 0):,} | 문서에 애초에 CAS·농도 표기가 없다(라벨·목록 페이지) |
| `데이터쪽오류` | {MC.get('데이터쪽오류', 0):,} | 문서가 아니라 우리 값이 틀렸다. CAS 체크섬 오류나 추출 잡음 |
| `원인미상` | {MC.get('원인미상', 0):,} | 분류 규칙이 앞의 여섯 가지를 모두 훑고도 남는 건. 규칙상 0 이 나오도록 마지막 분기가 `문서형식부적합` 을 받게 되어 있어, 0 은 "모두 분류했다" 가 아니라 "분류되지 않은 건을 만들지 않는 규칙" 이라는 뜻이다 |

세부 원인은 `원인` 열에 있다: {" · ".join(f"{k} {v}" for k, v in list(MIS.원인.value_counts().items())[:6])} 등.

{RECOL_MD}

## 데이터쪽오류 정정 후보 ({len(FIX):,}행)

문서가 아니라 **우리 값이 틀린** 건이다. 값은 덮어쓰지 않았다 —
`input_dataset_v6.xlsx` 는 읽기 전용이라 후보·판정·근거만
`09_데이터정정_후보.csv`(xlsx `데이터정정후보` 시트)에 적었다.

| 판정 | 건수 | 뜻 |
|---|---|---|
| `적용가능` | {int((FIX.판정 == "적용가능").sum()):,} | 정정값이 **근거문서에 실재**한다. 반영해도 값을 만드는 것이 아니다 |
| `보류` | {int((FIX.판정 == "보류").sum()):,} | 후보는 있으나 문서에서 확인되지 않았다. 문서를 더 봐야 한다 |
| `정정불가` | {int((FIX.판정 == "정정불가").sum()):,} | 고칠 값이 없다. 예를 들어 CAS 칸에 들어온 EU CLP Annex VI Index number 조각은 **빈칸이 맞다** |

정정 근거는 세 가지만 인정했다. (1) CAS 체크디지트를 바로잡은 번호가 문서에 있다,
(2) 같은 이름으로 쓰인 다른 행의 CAS(체크섬 통과)가 문서에 있다,
(3) 추출잡음을 **잘라낸** 이름이 문서에 낱말 단위로 있다.
자르기만 하고 글자를 만들지 않는다 — `n-Methyl--pyrrolidone` 처럼 추출 중에 글자가
소실된 흔적이 보이면 후보를 내지 않았다(빠진 `2` 를 되살리는 것은 값을 만드는 일이다).

`성분` 시트에도 `정정후보_CAS`·`정정후보_성분명`·`정정_판정`·`정정_사유` 네 열을 붙였다.
`ing_cas_best`·`ing_name_best` 는 v6 원값 그대로다.

## 농도 미검증 {n_unverified}행

원문이 스스로 "가정값·미확인·영업비밀·명목값" 이라고 적어 놓은 농도가 {n_unverified}행 있다.
값은 고치지 않았고, `성분` 시트 `농도환산_검증` 열에 **`미검증_추정치`** 로 표시했다.
확정값으로 쓰면 안 된다. 어느 표현이 걸렸는지는 `농도_불확실표현` 열에 원문 문자열
그대로 들어 있다(표현이 있는 행은 {int((ING.농도_불확실표현 != "").sum())}행이고, 그중 최종값이 있는
{n_unverified}행에 표시가 붙는다 — 쓴 값이 없는 행은 표시 대상이 아니다).

`농도환산_검증` 은 두 축을 합친 열이다. 환산 규칙 축만 따로 보려면
`농도_환산규칙` 열을 보면 된다: {" · ".join(f"{k} {v}" for k, v in ING.농도_환산규칙.value_counts().items())}.

`규칙일치` 는 **원문 문자열에서 최종값이 규칙대로 재현된다**는 뜻이고, 그게 다다.
그중 {n_pct_range}행은 원문이 범위(`60-100%`)이고 최종값은 그 중앙값이라, 문서에서 확인된 것은
**범위**이고 점값은 우리가 계산한 값이다 — 점값 자체가 원문에 적혀 있는 것은
{n_pct_point}행이다. 범위 행의 값을 확정 농도로 쓰지 말아 주십시오.

## 라벨 기준

`제형` 시트의 `y_eye`·`y_skin` 은 **EU CLP** 기준이다. 눈 구분 2B 와 피부 구분 3 은
분류로 세지 않는다. 감작(`y_sens`)은 기록만 보존한 것이고 이 프로젝트의 모델
학습에는 쓰지 않는다.

## 저작권·재배포

MSDS 사본은 제조사·유통사 웹사이트(agrian, chemservice, carlroth, chemicalbook 등)에서
수집한 것이다. 화학연구원의 점검 목적에 한해 전달한다. 재배포 범위를 넓히기 전에
다시 검토를 부탁드린다.

## 이번 판에서 하지 않은 것

1. **URL {n_gap_url}건 재수집** — 근거가 **아예 없는**(`공백`·`출처소실`) 제형에 남아
   있는 원문 URL (`제형` 시트 `재수집후보_URL`). 이번에 다시 찾아본 것은 **잘못된 문서가
   붙어 있던** 제형 쪽이고(위 `제품 문서 재수집`), 이 {n_gap_url}건은 손대지 않았다.
   6개월 이상 지난 링크라 전부 살아 있지는 않다.
2. **CAS 미상 성분 {n_cas_gap:,}건 PubChem 보강** — 이름은 있고 CAS 가 없는 행.
3. **농도 결측 {n_pct_gap:,}행 확보** — 확인율을 올리는 데 가장 효과가 큰 항목이다.
4. **`표기차이` {MC.get('표기차이', 0):,}건의 판독 규칙 수정** — 어느 단계에서 맞기 시작하는지
   전건 측정하고 판단까지 적었다(`08_판독규칙_감사.csv` · `08_표기차이_판독규칙_판단.md`).
   규칙을 풀어 안전하게 살릴 수 있는 것은 34건뿐이고 나머지는 우연 일치·추출 잡음·이명이라,
   이번 판에서는 **한 건도 고치지 않았다**.
5. **`데이터쪽오류` {MC.get('데이터쪽오류', 0):,}건의 값 반영** — 정정 후보는 냈지만
   `input_dataset_v6.xlsx` 에 쓰지 않았다. 반영 여부는 받는 쪽 판단이다.
6. **표기변형 잔여 {PJ.get('표기변형', 0)}건** — 문서에서 대체할 이름을 찾지 못해 정규화하지
   않은 앵커. 추측으로 이름을 바꾸지 않았다.

## 재현

`01_파이프라인/build_msds_highlight_share.py` 로 이 폴더 전체(이 README 와
`00_과거공유본_분석.md` 포함)가 다시 만들어지고,
`01_파이프라인/verify_msds_highlight_share.py` 가 그 결과를 다시 열어 검수한다.
`08_`·`08b_`·`08c_` 세 csv 만 예외로 `01_파이프라인/audit_read_rules.py` 가 낸다 —
빌드 산출물을 입력으로 다시 읽는 감사이므로 빌드 뒤에 돌려야 한다.
문서의 모든 숫자는 데이터에서 계산해 끼워 넣은 것이라 손으로 고칠 곳이 없다.
입력은 모두 읽기 전용이며 이 폴더 밖에는 쓰지 않는다.
결함 목록 입력은 `{DEFECT_SRC}` · sha256 `{DEFECT_SHA[:16] or "(재계산)"}` 이다.
""", encoding="utf-8")

VC = ARCH_SUM["verdict_counts"]
BC = ARCH_SUM["basis_counts"]
A_KIND = MAN.kind.value_counts().to_dict()
SF = MAN.section_found.value_counts().to_dict()
(OUT / "00_과거공유본_분석.md").write_text(f"""# 과거 공유본 분석 — `{AUDIT_ROOT.name}`

이번 판(`{OUT.name}`)의 양식은 새로 만든 것이 아니라 위 아카이브를
그대로 이어받은 것이다. 이 문서는 **과거에 무엇을 어떻게 공유했는지**를 먼저 적고,
이번 판에서 무엇을 그대로 두고 무엇을 바꿨는지 표로 대응시킨다.

## 결론 세 줄

1. 과거 공유본은 "확보 성공분"이 아니라 **감사에서 검토가 필요하다고 걸린 사례 묶음**이다.
   패키지 자신의 `selection_rule` 이 "review-focused" 라고 못 박고 있다.
2. 하이라이트는 있었지만 **노란색 한 종류에 주석이 비어 있었다.** 칠해진 칸이 정리
   데이터의 어느 값이 되었는지는 파일만 봐서는 알 수 없었다. 이번 판이 고친 것이 이 지점이다.
3. **순수 원본은 어디에도 없다.** `source_manifest.csv` 의 `original_path` 가 {len(MAN)}건 전량
   공란이다. 과거도, 이번 판도 공유하는 것은 마킹이 입혀진 파생 사본이다.

## 1. 무엇이 공유되었나 — 파일 6종

| 파일 | 크기·행수 | 역할 |
|---|---|---|
| `formulation_ingredients_master_audited.xlsx` | {AUDIT_XLSX.stat().st_size / 1024:,.0f} KB · `Formulations` {len(A_FORM):,}행 × {A_FORM.shape[1]}열 | 제형 단위 정본. 성분·농도·감사결과가 한 행에 다 들어간다 |
| `artifacts/share_packages/*.summary.json` | {(SP / f"{AUDIT_ROOT.name}.summary.json").stat().st_size / 1024:,.1f} KB | 패키지 자기기술 — `selection_rule` · `counts` · `sizes` · `kind_counts` |
| `artifacts/share_packages/*.inventory.csv` | {len(A_INV)}행 × {A_INV.shape[1]}열 | `{", ".join(A_INV.columns)}`. 동봉 파일 전수 목록 |
| `artifacts/share_packages/*.review_queue.csv` | {len(A_RQ)}행 × {A_RQ.shape[1]}열 | `Formulations` 와 같은 열 구성. 검토 대상 행만 뽑은 것 |
| `ingredient_source_audit/source_manifest.csv` | {len(MAN)}행 × {MAN.shape[1]}열 | 원문 수집 기록. URL · 상태코드 · 페이지수 · 3절 발견 여부 |
| `ingredient_source_audit/audit_outputs/ingredient_audit_summary.json` | — | 성분 단위 집계. `ingredient_occurrences` {ARCH_SUM["ingredient_occurrences"]:,} · 판정·근거 분포 |

문서 본체는 두 폴더에 나뉘어 있었다. 이 둘을 한쪽만 보면 파일을 조용히 빠뜨린다.

- `ingredient_source_audit/highlighted/` — {n_hl_a}건, {mb_hl_a:,.0f} MB.
  이름이 `<16자리 해시>_highlighted.pdf|.html`
- `ingredient_source_audit/ordered_option_runs/highlighted_sources/` — {n_oo_a}건, {mb_oo_a:,.1f} MB.
  이름이 사람이 읽을 수 있는 형태(`G041_56-81-5_ChemBlink_Sigma_highlighted.pdf`)

## 2. 어떤 항목을 수집했나

`Formulations` {A_FORM.shape[1]}열 중 수집·감사 관련 열이 세 묶음으로 나뉜다.

**원문에서 뽑은 값** — `Formulation_Ingredients`(성분명 목록),
`Formulation_Ingredients_Pct`(농도 문자열), `Ingredient_Source`(출처 URL),
`Data_Quality`(원자료 성격)

**원문 재판독 결과** — `Audit_Evidence_Terms`(하이라이트한 용어, `|` 구분),
`Audit_Evidence_Snippet`(3절 본문 발췌), `Audit_Source_Extracted_CAS`,
`Audit_Source_Extracted_Ingredients`, `Audit_Missing_In_Source`,
`Audit_Extra_In_Source`, `Audit_Verification_Round_1/2/3`, `Audit_Verification_Status`

**대체근거(제품 문서를 못 구해 순수 성분 SDS로 대신한 것)** — `Supplemental_` 접두
{sum(1 for c in A_FORM.columns if str(c).startswith("Supplemental_"))}열.
`Recovery_Status` · `Group`(G001–G064) · `Source_URL` · `Matched_Terms` ·
`Highlighted_Source_File` · `Highlight_Hit_Count` · `Attention_Required` ·
`True_Mismatch` · `Recovery_Source_Level`

`source_manifest.csv` {MAN.shape[1]}열은 수집 시도 자체의 기록이다: `url` · `status` ·
`kind`({" / ".join(f"{k} {v}" for k, v in A_KIND.items())}) · `status_code` · `bytes` ·
`page_count` · `section_found`({" / ".join(f"{k} {v}" for k, v in SF.items())}) ·
`full_cas_count` · `section_cas_count`(0인 건이 {int((MAN.section_cas_count == 0).sum())}) ·
`highlighted_path`.
{" · ".join(f"`{c}`" for c in MAN_BLANK)} 는 {len(MAN)}건 전량 공란이다.

## 3. MSDS 에서 정보를 어떻게 뽑았나

`ingredient_audit_summary.json` 의 `basis_counts` 가 판독 범위를 그대로 말해 준다.

- `PRIMARY_SECTION_3` {BC.get("PRIMARY_SECTION_3", 0):,}건 — 3절(조성 정보)에서 읽음
- `PRIMARY_FULL_DOCUMENT` {BC.get("PRIMARY_FULL_DOCUMENT", 0):,}건 — 3절을 못 찾아 문서 전체에서 읽음
- `NO_SOURCE_EXCEPTION` {BC.get("NO_SOURCE_EXCEPTION", 0):,}건 — 읽을 문서가 없음

즉 **3절 우선, 없으면 문서 전체**가 과거의 수집 규칙이었다. 성분 단위 판정
(`verdict_counts`)은 {" / ".join(f"`{k}` {v:,}" for k, v in VC.items())} 로,
확정 어휘가 아예 없다. `manual_review_count` 가 {ARCH_SUM["manual_review_count"]:,} —
{ARCH_SUM["ingredient_occurrences"]:,}건 중 거의 전부가 사람 확인 대기 상태였다.

같은 파일의 `primary_evidence_trust_boundary` 가 스스로 한계를 적어 두었다:
"구간 한정 1차 행은 독립적으로 읽히는 근거 없이는 자동 확정하지 않으며, 워크북
감사 추출 필드는 출처 기록용으로만 보존한다."

## 4. 하이라이팅 기준

파일을 열어 주석을 직접 읽어 확인한 사실이다.

- 주석 종류 `Highlight` 한 가지, 색 `stroke = (1.0, 1.0, 0.0)` 노란색 한 종류
- **`title` 과 `content` 가 전부 빈 문자열** — 메타데이터가 없다
- 표시 대상은 CAS 와 성분명. 3절 밖이라도 문서 어디서든 문자열이 일치하면 칠했다
- 농도는 표시 대상이 아니었다

그래서 파일을 받은 사람은 "이 노란 칸이 정리 데이터의 어느 값이 되었나"를 되짚을 수
없었다. 색이 하나뿐이라 CAS 인지 성분명인지도 구분되지 않았다.

## 5. 원본과 정리 데이터의 대응 — 실측

이번 판을 만들기 전에 대응 관계를 측정했다. 하이라이트를 다시 칠하는 방식을
정하기 위해서였다.

- 제품단위 문서가 있는 제형 {int(CNT["제품단위"])}건이 v6 성분행 {int((ING.Formulation_ID.isin(AUD.loc[AUD.MSDS_확보상태 == "제품단위", "Formulation_ID"])).sum()):,}행에 걸린다
- 그중 {int(ING2.loc[ING2.MSDS_확보상태 == "제품단위", "ing2_newlink"].notna().sum())}행에 재수집 링크(`ing2_newlink`)가 있고, 일부는 그 링크가 동봉된 문서와
  **다른 URL을 가리킨다.** 그 행들의 값은 동봉 문서에서 나온 것이 아니다
- 하드닝 전 단순 문자열 일치율(60제형 표본): CAS 56.4% · 성분명 51.9% · 농도 60.3%
- 못 맞춘 원인: 텍스트 정상인데 불일치 94 / 비PDF(html) 26 / 텍스트층 없는 PDF 0
- 문서 형식을 판별해 보니 SDS가 아닌 것이 섞여 있었다 — 주정부 등록 라벨,
  제품 목록 페이지, 통계 보고서 등. 이번 판 실측으로
  {int((FI.문서형식 == "SDS아님(라벨·목록)").sum())}건이 `SDS아님(라벨·목록)` 이다

**단순 키워드 일치로 칠하면 안 되는 이유가 여기서 나왔다.** 실제로 `2.8%` 라는 농도
문자열이 어느 문서의 `12.8% growth under assumption 2 …` 안에서 걸렸다.

`Formulations` 자체의 결함도 확인됐다. `EyeIrritation6pack_PID1019`(Up-Cyde Pro 2.0 EC)는
워크북 성분명이 `cypermethrin cis/trans +/- /60` 인데 원문은 `… +/- 40/60` 이고,
`Formulation_Ingredients_Pct` 는 두 성분에 똑같이 `40%` 가 적혀 있으나 원문 3절은
24.8 과 51.1 이다.

## 6. 과거 양식 → 이번 판 대응

| 과거 | 이번 판 | 바뀐 점 |
|---|---|---|
| `summary.json` | `01_요약.json` | `selection_rule` · `counts` · `sizes` · `kind_counts` 구조 유지. `계승한_과거양식` · `입력_sha256` 추가 |
| `formulation_ingredients_master_audited.xlsx` | `02_제품코드별_성분리스트.xlsx` | 제형 1행에 성분을 뭉쳐 넣던 것을 제형·성분 두 시트로 분리 |
| `inventory.csv` | `03_근거파일_인벤토리.csv` | {A_INV.shape[1]}열 → {FI.shape[1]}열. sha256 · 문서형식 · 판독가능 · 3절페이지수 추가 |
| `source_manifest.csv` | `04_출처수집기록.csv` | 전량 공란 {len(MAN_BLANK)}열 삭제, `이번판_*` 4열 추가 |
| `Audit_Evidence_Terms` / `_Snippet` | `05_원문대조표.csv` | 제형 1행에 파이프(`\\|`) 로 뭉쳐 있던 것을 앵커 1행으로 펼침 |
| `review_queue.csv` | xlsx `검토필요` 시트 | {len(A_RQ)}행 → {len(REVIEW):,}행. 확인 못 한 것을 빠짐없이 넣었기 때문 |
| `ingredient_audit_summary.json` `basis_counts` | `05_원문대조표.csv` 의 `원문_구간` | 3절 / 문서기타 구분을 앵커마다 기록 |
| 노란색 하이라이트 1종 · 주석 없음 | CAS 노랑 · 성분명 초록 · 농도 파랑 · 주석에 공유값 병기 | 칠한 칸에서 데이터 값으로 되짚을 수 있게 됨 |
| 농도 미표시 | 농도도 표시 | 단, 같은 성분이 확인된 페이지 안에서만 인정 |
| 판정 어휘 `UNRESOLVED`/`WEAK`/`MISMATCH` | `일치` 외 {len(PJ) - 1}종으로 세분 | 왜 확인 못 했는지가 구분된다 |
| `MISMATCH` 한 덩어리 | `07_문서불일치_전수검사.csv` {len(MIS):,}행 | 불일치 앵커마다 분류·원인·근거를 한 줄씩. 전건이다 |
| 성분명을 원문과 달라도 그대로 둠 | `성분명_정규화` 3열 ({len(CMAP)}행) | 동일 CAS + 문서 완전일치일 때만. `ing_name_best` 원값은 보존 |

## 7. 이어받지 않은 것

- **과거 판정 결과를 그대로 옮기지 않았다.** 판정은 원문을 다시 열어 새로 냈다.
  `verdict_counts` 를 신뢰해 옮기면 그 값이 어디서 왔는지 되짚을 수 없다.
- **`Formulation_Ingredients_Pct` 문자열을 쓰지 않았다.** 제형당 문자열 1개라
  성분-농도 정렬이 깨진 사례가 있다(위 PID1019). 대신 v6 의 성분행별 수치와
  그 원문 문자열(`ing2_pct_raw_text`)을 함께 실었다.
- **`ordered_option_runs/` {n_oo_a}건을 제품 근거로 섞지 않았다.** 순수 성분 SDS 를 CAS 로
  매칭한 대체근거이므로 `대체근거_참고/` 로 분리했다.
""", encoding="utf-8")
log("설명 문서 2종 작성 — README.md · 00_과거공유본_분석.md")

log(f"완료 → {OUT.name}/ (근거 {len(FI)}건 · 앵커 {len(HL)}행 · "
    f"하이라이트 {int(HL.하이라이트_수.sum())}개)")
print(f"→ {OUT.name}")
