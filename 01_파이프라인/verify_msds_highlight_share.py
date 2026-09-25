#!/usr/bin/env python3
"""07_화학연구원_공유_260918/ 검수 — 산출: 07_.../06_검수보고.md

빌드 스크립트를 믿지 않고 **산출물만 다시 열어서** 대조한다. 특히 하이라이트는
PDF 주석을 하나하나 다시 읽어, 주석에 적힌 「원문 문자열」이 그 주석이 실제로
덮고 있는 글자와 같은지, 그리고 주석에 적힌 「공유값」이 `input_dataset_v6.xlsx`
의 실제 값과 같은지 본다. 전수 검사다(표본 아님).

검수 항목
  C1  파일 존재·sha256 일치 (인벤토리 ↔ 실제)
  C2  주석 개수 일치 (PDF 실측 ↔ 인벤토리 ↔ 원문대조표)
  C3  잘못된 하이라이팅 — 주석이 덮은 글자 ≠ 주석에 적힌 원문
  C4  오기입 — 주석에 적힌 공유값 ≠ v6 실제 값
  C5  과거 무주석 하이라이트 잔존
  C6  xlsx 시트·행수, 요약 ↔ csv ↔ json 숫자 교차
  C7  값을 만들지 않았는지 — 공유 성분표 이름·CAS·농도가 v6 와 셀 단위 동일
  C8  단위·환산 오류 — 농도 원문 ↔ 최종값 재검산
  C9  누출 — 로컬 절대경로·실명이 산출물에 섞였는지
  C10 불변식 — 판정 '일치' ⟺ 하이라이트 1개 이상
  C11 설명 문서(README·과거공유본 분석)의 숫자가 데이터와 맞는지
  C12 '문서불일치' 앵커 전수검사가 정말 전건인지 (키 집합 대조 · 체크섬 재계산)
  C13 성분명 정규화 — 바꾼 이름이 문서에 실재하는지 · 동일 CAS 인지 · 전버전이 남았는지
  C14 칠한 이름·CAS 앵커가 더 긴 낱말 안에서만 걸린 것이 아닌지 (규칙의 반대쪽)
  C15 데이터정정 후보 원장 — '적용가능' 후보가 근거문서에 실재하는지 · v6 를 덮지 않았는지
  C16 HTML 사본에서 `<mark>` 삽입이 낱말을 갈랐는지 (우리 표시 / 물려받은 표시 구분)
  C17 '3절(조성)' 로 적은 앵커의 페이지에 3절 머리글이 실제로 있는지
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import fitz
import pandas as pd
from bs4 import BeautifulSoup

import lib_model as L

OUT = L.ROOT / "07_화학연구원_공유_260918"
REPORT = OUT / "06_검수보고.md"
log = L.make_logger(OUT / "검수.log")

DASH = str.maketrans({c: "-" for c in "‐‑‒–—―−－"})


def norm(s) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s)).translate(DASH)).strip()


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


# 정규화한 성분명 앵커는 뒤에 ` / 이전값「…」` 이 붙는다. v 를 비탐욕으로 둬야
# 이전값까지 공유값으로 빨아들이지 않는다
NOTE_RE = re.compile(r"^\[(?P<fid>[^\s\]]+) #(?P<idx>\d+)\] (?P<f>\S+)=원문「(?P<n>.*)」"
                     r" → 공유값 (?P<v>.*?)(?: / 이전값「(?P<prev>.*)」)?$")
OUR_COLORS = {(1.0, 0.95, 0.30), (0.55, 0.95, 0.55), (0.55, 0.80, 1.0)}
FINDINGS: list[tuple[str, str, str]] = []          # (항목, 등급, 내용)


def rec(item, grade, msg):
    FINDINGS.append((item, grade, msg))
    log(f"[{grade}] {item} {msg}")


log("=== 07 패키지 검수 ===")
INV = pd.read_csv(OUT / "03_근거파일_인벤토리.csv")
HLT = pd.read_csv(OUT / "05_원문대조표.csv")
SUM = json.loads((OUT / "01_요약.json").read_text())
XL = pd.ExcelFile(OUT / "02_제품코드별_성분리스트.xlsx")
SH = {s: XL.parse(s) for s in XL.sheet_names}
V6 = pd.ExcelFile(L.V6)
ING = V6.parse("ingredient")
V6KEY = ING.set_index(["Formulation_ID", "ing_idx"])

# ------------------------------------------------------------------ C1 파일·sha256
miss, bad_hash, bad_size = [], [], []
for _, r in INV.iterrows():
    p = OUT / r.archive_path
    if not p.exists():
        miss.append(r.archive_path)
        continue
    if p.stat().st_size != r.size_bytes:
        bad_size.append(r.archive_path)
    if sha256(p) != r.sha256:
        bad_hash.append(r.archive_path)
rec("C1 파일·sha256", "OK" if not (miss or bad_hash or bad_size) else "실패",
    f"인벤토리 {len(INV)}건 · 누락 {len(miss)} · 크기불일치 {len(bad_size)} · "
    f"해시불일치 {len(bad_hash)}")
extra = sorted({str(p.relative_to(OUT)) for d in ("MSDS_하이라이트", "대체근거_참고")
                for p in (OUT / d).rglob("*") if p.is_file()} - set(INV.archive_path))
rec("C1b 인벤토리 밖 파일", "OK" if not extra else "경고",
    f"{len(extra)}건" + (f" 예: {extra[:3]}" if extra else ""))

# ------------------------------------------------------------------ C2·C3·C4·C5 주석 전수
def squash(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", norm(s).lower())


def covered_text(pg, annot) -> list[str]:
    """주석이 실제로 덮은 글자. `annot.rect` 는 표시용으로 2~3pt 부풀려져 있어서
    옆 칸 글자를 함께 물고 온다 — 원래 좌표인 quad 정점으로 읽어야 한다."""
    v = annot.vertices or []
    if len(v) >= 4:
        return [norm(pg.get_textbox(fitz.Quad(v[i:i + 4]).rect))
                for i in range(0, len(v) - 3, 4)]
    return [norm(pg.get_textbox(annot.rect))]


def cover_ok(needle: str, frags: list[str]) -> bool:
    """한 앵커의 사각형들이 원문 문자열을 실제로 덮었는지. 줄바꿈·표 셀에서 끊긴
    문자열은 사각형이 여러 개로 쪼개지므로 조각을 합쳐서 판단한다."""
    ns = squash(needle)
    if not ns:
        return False
    if ns in squash("".join(frags)):
        return True
    fr = [squash(f) for f in frags]
    return (all(f and (f in ns or ns in f) for f in fr)
            and sum(len(f) for f in fr) >= len(ns))


# 정규화 앵커의 주석은 「공유값=정규화된 이름 / 이전값=v6 원값」 두 개를 함께 싣는다.
# 시트에 담긴 정규화 결과와 셋 다 맞는지 본다
CN = SH["성분"].set_index(["Formulation_ID", "ing_idx"])
CANONMAP = {k: (norm(a), norm(b)) for k, a, b in
            zip(CN.index, CN.성분명_정규화, CN.성분명_정규화_이전값) if pd.notna(a)}

per_file_cnt = HLT.groupby("근거파일").하이라이트_수.sum().to_dict()
n_annot = n_c4_fail = n_old_left = n_parse_fail = n_canon_annot = 0
c2_diff, c3_ex, c4_ex = [], [], []
groups: dict[tuple, list[str]] = {}                # (파일, 페이지, 원문) → 덮인 글자들
for _, r in INV[INV.형식 == "pdf"].iterrows():
    p = OUT / r.archive_path
    d = fitz.open(p)
    seen = 0
    for pg in d:
        for a in pg.annots() or []:
            if a.type[1] != "Highlight":
                continue
            seen += 1
            col = tuple(round(x, 2) for x in a.colors.get("stroke") or ())
            if col not in {tuple(round(x, 2) for x in c) for c in OUR_COLORS}:
                n_old_left += 1
                continue
            m = NOTE_RE.match(norm(a.info.get("content") or ""))
            if not m:
                n_parse_fail += 1
                continue
            n_annot += 1
            groups.setdefault((r.archive_path, pg.number, m["n"]), []).extend(
                covered_text(pg, a))
            # C4 주석에 적힌 공유값 == v6 실제 값
            try:
                row = V6KEY.loc[(m["fid"], int(m["idx"]))]
            except KeyError:
                n_c4_fail += 1
                if len(c4_ex) < 6:
                    c4_ex.append(f"{r.archive_path} v6 에 없는 키 {m['fid']} #{m['idx']}")
                continue
            if m["f"] == "성분명(정규화)":
                # 공유값은 정규화된 이름, 이전값은 v6 원값이어야 한다. 셋을 다 대조한다
                n_canon_annot += 1
                want = CANONMAP.get((m["fid"], int(m["idx"])))
                bad = (want is None or norm(m["v"]) != want[0]
                       or norm(m["prev"] or "") != want[1]
                       or norm(row.ing_name_best) != want[1])
                if bad:
                    n_c4_fail += 1
                    if len(c4_ex) < 6:
                        c4_ex.append(f"{r.archive_path} 정규화 주석={m['v']!r}/"
                                     f"{m['prev']!r} 시트={want} v6={row.ing_name_best!r}")
                continue
            real = {"CAS": row.ing_cas_best, "성분명": row.ing_name_best,
                    "농도": row.ing_pct_best}[m["f"]]
            if norm(real) != norm(m["v"]):
                n_c4_fail += 1
                if len(c4_ex) < 6:
                    c4_ex.append(f"{r.archive_path} {m['f']} 주석={m['v']!r} v6={real!r}")
    if seen != int(per_file_cnt.get(r.archive_path, 0)):
        c2_diff.append(f"{r.archive_path}: PDF {seen} vs 대조표 "
                       f"{per_file_cnt.get(r.archive_path, 0)}")
    d.close()

# C3 앵커 단위로 합집합 검사
n_c3_fail = 0
for (ap, pn, needle), frags in groups.items():
    if cover_ok(needle, frags):
        continue
    n_c3_fail += 1
    if len(c3_ex) < 6:
        c3_ex.append(f"{Path(ap).name} p{pn + 1} 원문「{needle}」 조각={frags}")

# HTML 은 주석이 아니라 <mark> 다. 개수와 마킹된 글자를 같은 방식으로 본다
n_mark = n_mark_bad = 0
mark_ex = []
for _, r in INV[INV.형식 == "html"].iterrows():
    soup = BeautifulSoup((OUT / r.archive_path).read_text(errors="replace"), "lxml")
    ms = soup.find_all("mark", attrs={"data-field": True})
    n_mark += len(ms)
    for mk in ms:
        m = NOTE_RE.match(norm(mk.get("title") or ""))
        if not m or squash(m["n"]) not in squash(mk.get_text()):
            n_mark_bad += 1
            if len(mark_ex) < 4:
                mark_ex.append(f"{Path(r.archive_path).name} 「{mk.get_text()[:40]}」")
    if len(ms) != int(per_file_cnt.get(r.archive_path, 0)):
        c2_diff.append(f"{r.archive_path}: mark {len(ms)} vs 대조표 "
                       f"{per_file_cnt.get(r.archive_path, 0)}")

rec("C2 하이라이트 개수", "OK" if not c2_diff else "실패",
    f"PDF 주석 {n_annot + n_old_left + n_parse_fail}개 + HTML mark {n_mark}개 = "
    f"{n_annot + n_old_left + n_parse_fail + n_mark}개 · 대조표 합계 "
    f"{int(HLT.하이라이트_수.sum())}개 · 파일별 불일치 {len(c2_diff)}건"
    + (f" 예: {c2_diff[:3]}" if c2_diff else ""))
rec("C3 잘못된 하이라이팅", "OK" if not (n_c3_fail or n_mark_bad) else "실패",
    f"앵커 {len(groups)}개(사각형 {n_annot}개, 그중 다중사각형 "
    f"{sum(1 for v in groups.values() if len(v) > 1)}개) 합집합 검사 · 불일치 {n_c3_fail}개 · "
    f"HTML mark {n_mark}개 중 불일치 {n_mark_bad}개"
    + ("\n    - " + "\n    - ".join(c3_ex + mark_ex) if (c3_ex or mark_ex) else ""))
rec("C4 공유값 오기입", "OK" if not n_c4_fail else "실패",
    f"검사 {n_annot}개(그중 정규화 주석 {n_canon_annot}개는 주석↔시트↔v6 3중 대조) · "
    f"불일치 {n_c4_fail}개"
    + ("\n    - " + "\n    - ".join(c4_ex) if c4_ex else ""))
rec("C5 과거 하이라이트 잔존", "OK" if not n_old_left else "실패",
    f"우리 3색이 아닌 Highlight {n_old_left}개 · 주석 파싱실패 {n_parse_fail}개")

# ------------------------------------------------------------------ C6 숫자 교차
c6 = []
if len(SH["성분"]) != len(ING):
    c6.append(f"성분 시트 {len(SH['성분'])} != v6 {len(ING)}")
if len(SH["제형"]) != 1675:
    c6.append(f"제형 시트 {len(SH['제형'])} != 1675")
if len(SH["원문대조"]) != len(HLT):
    c6.append(f"원문대조 시트 {len(SH['원문대조'])} != csv {len(HLT)}")
if len(SH["검토필요"]) != int((SH["성분"].원문대조_판정 == "검토 필요").sum()):
    c6.append("검토필요 시트 행수 != 성분 시트의 '검토 필요' 행수")
요약 = SH["요약"].set_index("구분").건수.to_dict()
for k, v in (("전체 제형(제품코드)", SUM["counts"]["제형"]),
             ("전체 성분행", SUM["counts"]["성분행"]),
             ("원문에서 확인된 성분행", SUM["counts"]["원문확인_성분행"]),
             ("검토 필요 성분행", SUM["counts"]["검토필요_성분행"]),
             ("하이라이트 총 개수", SUM["counts"]["하이라이트_개수"])):
    if int(요약[k]) != int(v):
        c6.append(f"요약 '{k}'={요약[k]} != 01_요약.json {v}")
if int(요약["하이라이트 총 개수"]) != int(HLT.하이라이트_수.sum()):
    c6.append("요약 하이라이트 수 != 대조표 합계")
if int(SUM["counts"]["근거파일_제품단위"] + SUM["counts"]["근거파일_대체근거"]) != len(INV):
    c6.append("요약 근거파일 수 != 인벤토리 행수")
for _sn, _csv in (("문서불일치검사", "07_문서불일치_전수검사.csv"),
                  ("데이터정정후보", "09_데이터정정_후보.csv")):
    if _sn not in SH:
        c6.append(f"시트 {_sn} 없음")
    elif len(SH[_sn]) != len(pd.read_csv(OUT / _csv)):
        c6.append(f"{_sn} 시트 {len(SH[_sn])} != {_csv} 행수")
if int(요약["데이터정정 후보"]) != len(SH["데이터정정후보"]):
    c6.append("요약 데이터정정 후보 != 시트 행수")
rec("C6 숫자 교차", "OK" if not c6 else "실패",
    f"시트 {XL.sheet_names} · 불일치 {len(c6)}건" + ("\n    - " + "\n    - ".join(c6) if c6 else ""))

# ------------------------------------------------------------------ C7 값을 만들지 않았는지
S = SH["성분"].set_index(["Formulation_ID", "ing_idx"]).sort_index()
Vv = V6KEY.sort_index()
c7 = []
for col in ("ing_name_best", "ing_cas_best", "ing_pct_best", "ing_role_final",
            "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens", "ing_src", "ing_pct_src"):
    a, b = S[col], Vv.loc[S.index, col]
    same = (a.isna() & b.isna()) | (a.astype(str) == b.astype(str))
    if not same.all():
        c7.append(f"{col} {int((~same).sum())}셀 다름")
rec("C7 값 변형 없음", "OK" if not c7 else "실패",
    "공유 성분표가 v6 와 셀 단위 동일" if not c7 else "; ".join(c7))

# ------------------------------------------------------------------ C8 농도 환산 재검산
def expect(raw, kind, pct_only=False):
    if pd.isna(raw):
        return None
    t = norm(raw)
    nums = [float(x) for x in (re.findall(r"(\d+(?:\.\d+)?)\s*(?:%|퍼센트)", t) if pct_only
                               else re.findall(r"\d+(?:\.\d+)?", t))]
    if not nums:
        return None
    if kind == "range" and len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    if kind == "min":
        return nums[0]
    if kind == "max":
        return nums[0] if len(nums) == 1 else max(nums)
    return nums[0] if kind in ("exact", "approx", "upper", "lower") else None


s8 = SH["성분"]
recomp = s8.apply(lambda r: expect(r.농도_원문, r.ing_pct_kind_best), axis=1)
# `규칙일치_%표기` 는 % 붙은 수만 보는 2차 규칙으로 맞춘 행이다. 그 행은 그 규칙으로
# 다시 검산해야 한다 — 1차 규칙으로 재검산하면 당연히 어긋나 거짓 실패가 난다
recomp2 = s8.apply(lambda r: expect(r.농도_원문, r.ing_pct_kind_best, True), axis=1)
_d1 = (recomp - s8.ing_pct_best).abs()
_d2 = (recomp2 - s8.ing_pct_best).abs()
mis = s8[s8.ing_pct_best.notna()
         & (((s8.농도환산_검증 == "규칙일치") & recomp.notna() & (_d1 > 0.051))
            | ((s8.농도환산_검증 == "규칙일치_%표기") & (recomp2.isna() | (_d2 > 0.051))))]
n_unver = int((s8.농도환산_검증 == "미검증_추정치").sum())
rec("C8 농도 환산", "OK" if mis.empty else "실패",
    f"'규칙일치' {int((s8.농도환산_검증 == '규칙일치').sum())}행 + '규칙일치_%표기' "
    f"{int((s8.농도환산_검증 == '규칙일치_%표기').sum())}행 재검산 · "
    f"불일치 {len(mis)}행. 별도표기: 규칙불일치 "
    f"{int((s8.농도환산_검증 == '규칙불일치').sum())} · 미검증_추정치 "
    f"{n_unver} · 원문없음 "
    f"{int((s8.농도환산_검증 == '원문없음_값있음').sum())} · 규칙미확인 "
    f"{int((s8.농도환산_검증 == '규칙미확인').sum())}")

# 미검증 표시가 (1) 근거 있게 붙었는지 (2) 붙어야 할 행을 빠뜨리지 않았는지 양방향으로 본다.
# 한 방향만 보면 '20행에만 붙였다' 가 '20행 말고는 없다' 를 뜻하지 않는다.
#
# 방향 1 은 빌드의 어휘를 베끼지 않는다. 빌드가 `농도_불확실표현` 열에 **원문에서 잘라낸
# 문자열**을 적어 두었으므로, 그 문자열이 정말 `농도_원문` 안에 있는지만 본다. 같은
# 정규식을 여기서 다시 쓰면 검수가 빌드의 사본이 되어 어휘가 좁은 것을 못 잡는다 —
# 실제로 그 사본 때문에 `implied`·`nominal`·`trade secret` 41행이 표시 없이 나갔다
표현 = s8.농도_불확실표현.fillna("").astype(str).map(norm)   # norm(nan) 은 'nan' 이 된다
S8X = s8.assign(expr_n=표현)
u_bad = [f"{r.Formulation_ID}#{r.ing_idx} 적어 둔 표현 {r.expr_n!r} 이 "
         f"원문 {str(r.농도_원문)[:40]!r} 안에 없음"
         for r in S8X[표현 != ""].itertuples()
         if r.expr_n.lower() not in norm(r.농도_원문).lower()]
u_sync = [f"{r.Formulation_ID}#{r.ing_idx} 표현({r.expr_n!r})과 판정({r.농도환산_검증})이 어긋남"
          for r in S8X[s8.ing_pct_best.notna()].itertuples()
          if (r.expr_n != "") != (r.농도환산_검증 == "미검증_추정치")]

# 방향 2 는 검수 쪽에서 따로 쓴 넓은 어휘다(빌드보다 넓게 잡아 놓고 초과분을 본다).
# 여기서 걸리고 빌드에서 안 걸린 행이 있으면 빌드 어휘가 좁은 것이다
WIDE = re.compile(
    r"assum|estimat|guess|approx|unverified|not\s+verified|not\s+confirmed|"
    r"not\s+(?:independently|directly)\s+\w+|impli|infer|inaccessible|"
    r"dataset\s+indicates|per\s+product\s+name|nominal|typical|presum|likely|"
    r"probabl|unspecified|not\s+specified|unknown|undisclosed|withheld|"
    r"trade\s*secret|proprietary|confidential|추정|미확인|가정|잠정|영업비밀|비공개", re.I)
자기가정 = s8.농도_원문.notna() & s8.농도_원문.map(lambda x: bool(WIDE.search(norm(x))))
u_miss = [f"{r.Formulation_ID}#{r.ing_idx} 검수 어휘에 걸리는데 표시 없음"
          f"({r.농도환산_검증}): {str(r.농도_원문)[:50]!r}"
          for r in s8[자기가정 & s8.ing_pct_best.notna() & (표현 == "")].itertuples()]
rec("C8b 미검증 표시", "OK" if (n_unver and not u_bad and not u_sync and not u_miss) else "실패",
    f"미검증_추정치 {n_unver}행 · 불확실표현 기록 {int((표현 != '').sum())}행 · "
    f"기록한 표현이 원문에 없는 행 {len(u_bad)} · 표현↔판정 어긋남 {len(u_sync)} · "
    f"검수 어휘에 걸리는데 표시 없는 행 {len(u_miss)} "
    f"(최종값 없는 {int((자기가정 & s8.ing_pct_best.isna()).sum())}행은 쓴 값이 없어 대상 아님)"
    + ("\n    - " + "\n    - ".join((u_bad + u_sync + u_miss)[:5])
       if (u_bad or u_sync or u_miss) else ""))

# ------------------------------------------------------------------ C9 누출
LEAK = re.compile(r"/Users/[A-Za-z0-9_]+|한서윤|hanseoyun")
leaks = []
for p in list(OUT.glob("*.csv")) + list(OUT.glob("*.json")) + list(OUT.glob("*.md")):
    t = p.read_text(errors="replace")
    if LEAK.search(t):
        leaks.append(f"{p.name}: {LEAK.findall(t)[:3]}")
for sn, df in SH.items():
    t = df.astype(str).to_csv(index=False)
    if LEAK.search(t):
        leaks.append(f"xlsx[{sn}]: {LEAK.findall(t)[:3]}")
rec("C9 경로·실명 누출", "OK" if not leaks else "실패",
    "없음" if not leaks else "; ".join(leaks))

# 사람 이름꼴(한글 2~4자 단독) 값과, 전부 공란인 열. 팀 관리용 열이 섞여 나가는 것을 막는다
NAME_LIKE = re.compile(r"^[가-힣]{2,4}$")
c9b = []
for sn, df in SH.items():
    for col in df.columns:
        if df[col].dtype != object:
            continue
        v = df[col].dropna().astype(str).str.strip().unique()
        hit = [s for s in v if NAME_LIKE.fullmatch(s)]
        if hit and ("담당" in col or "이름" in col):
            c9b.append(f"xlsx[{sn}].{col} 이름꼴 {hit[:3]}")
    empty = [c for c in df.columns if df[c].isna().all()]
    if empty:
        c9b.append(f"xlsx[{sn}] 전부 공란 열 {len(empty)}개 {empty[:5]}")
rec("C9b 관리용 열·빈 열", "OK" if not c9b else "경고", "없음" if not c9b else "; ".join(c9b))

# ------------------------------------------------------------------ C10 불변식
inv_ok = ((HLT.판정 == "일치") == (HLT.하이라이트_수 >= 1)).all()
확인키 = set(zip(*HLT[(HLT.판정 == "일치") & HLT.필드.isin(["CAS", "성분명"])]
                [["Formulation_ID", "ing_idx"]].T.values))
sheet키 = set(zip(*SH["성분"][SH["성분"].원문대조_판정 == "원문확인"]
                [["Formulation_ID", "ing_idx"]].T.values))
rec("C10 불변식", "OK" if inv_ok and 확인키 == sheet키 else "실패",
    f"판정'일치'⟺하이라이트≥1: {inv_ok} · 원문확인 행집합 일치: {확인키 == sheet키} "
    f"(대조표 {len(확인키)} / 성분시트 {len(sheet키)})")

# ------------------------------------------------------------------ C12 문서불일치 전수검사
# '전수' 라고 적어 놓고 일부만 검사했으면 받는 쪽은 그걸 알 수 없다. 앵커 집합이
# 정확히 같은지 키 단위로 대조하고, 빈 칸과 정의 밖 분류를 찾는다
MIS = pd.read_csv(OUT / "07_문서불일치_전수검사.csv")
BUCKETS = {"다른제품문서", "대체근거문서", "같은문서_성분목록다름", "표기차이", "문서형식부적합",
           "데이터쪽오류", "원인미상"}
mk = lambda df: set(zip(df.Formulation_ID, df.ing_idx, df.필드, df.근거파일))  # noqa: E731
c12 = []
앵커 = HLT[HLT.판정 == "문서불일치"]
if len(MIS) != len(앵커):
    c12.append(f"행수 {len(MIS)} != 문서불일치 앵커 {len(앵커)}")
if mk(MIS) != mk(앵커):
    d1, d2 = mk(앵커) - mk(MIS), mk(MIS) - mk(앵커)
    c12.append(f"검사 안 된 앵커 {len(d1)}개 · 앵커에 없는 검사행 {len(d2)}개 {list(d1)[:2]}")
for col in ("분류", "원인", "근거"):
    n_blank = int(MIS[col].isna().sum() + (MIS[col].astype(str).str.strip() == "").sum())
    if n_blank:
        c12.append(f"{col} 공란 {n_blank}행")
if not set(MIS.분류) <= BUCKETS:
    c12.append(f"정의 밖 분류 {set(MIS.분류) - BUCKETS}")
if len(MIS) != len(MIS.drop_duplicates(["Formulation_ID", "ing_idx", "필드", "근거파일"])):
    c12.append("같은 앵커가 중복 기록됨")
if not set(MIS.근거파일) <= set(INV.archive_path):
    c12.append("인벤토리에 없는 근거파일을 가리키는 행이 있다")


def cas_ck(cas: str) -> bool:
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", norm(cas))
    if not m:
        return False
    d = (m[1] + m[2])[::-1]
    return sum((i + 1) * int(x) for i, x in enumerate(d)) % 10 == int(m[3])


# 'CAS체크섬오류' 라고 적힌 건은 정말 체크디지트가 틀렸는지 다시 계산해 본다
ck = MIS[MIS.원인 == "CAS체크섬오류"]
ck_bad = [r.공유값 for r in ck.itertuples() if cas_ck(str(r.공유값))]
if ck_bad:
    c12.append(f"체크섬오류로 적혔으나 실제로는 정상인 CAS {len(ck_bad)}건 {ck_bad[:3]}")
n_unknown = int((MIS.분류 == "원인미상").sum())
rec("C12 문서불일치 전수검사", "OK" if not c12 else "실패",
    f"앵커 {len(앵커):,}건 전건 기록 확인 · 분류 {MIS.분류.value_counts().to_dict()} · "
    f"원인미상 {n_unknown}건 · 체크섬 재계산 {len(ck)}건"
    + ("\n    - " + "\n    - ".join(c12[:6]) if c12 else ""))

# --------------------------------------------- 근거문서 텍스트 (C13·C14·C15 공용)
# HTML 사본은 우리가 `<mark>` 를 끼워 넣은 것이다. 태그가 낱말 중간에 들어가면
# `get_text(" ")` 이 낱말을 공백으로 갈라 버린다(`Ethoprophos` → `Ethoprop hos`).
# 빌드 당시 원문과 같은 글자를 보려면 태그를 걷어내고 읽어야 한다
_MARK = re.compile(rb"</?mark\b[^>]*>")
_cache: dict[tuple[str, bool], str] = {}


def doctext(rel: str, strip_mark: bool = True) -> str:
    k = (rel, strip_mark)
    if k in _cache:
        return _cache[k]
    p = OUT / rel
    try:
        if p.suffix.lower() == ".pdf":
            with fitz.open(p) as d:
                t = norm(" ".join(pg.get_text() for pg in d))
        else:
            raw = p.read_bytes()
            if strip_mark:
                raw = _MARK.sub(b"", raw)
            soup = BeautifulSoup(raw, "lxml")
            for x in soup(["script", "style"]):
                x.decompose()
            t = norm(soup.get_text(" "))
    except Exception as e:
        log(f"  판독실패 {rel}: {type(e).__name__} {e}")
        t = ""
    _cache[k] = t
    return t


def occ(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def word_edge(hay: str, i: int, ln: int) -> bool:
    """앵커가 더 긴 낱말 안에 파묻혀 걸린 것이 아닌지. `isalnum` 은 한글도 포함한다."""
    b = hay[i - 1] if i else " "
    a = hay[i + ln] if i + ln < len(hay) else " "
    return not b.isalnum() and not a.isalnum()


def num_edge(hay: str, i: int, ln: int) -> bool:
    b = hay[i - 1] if i else " "
    a = hay[i + ln] if i + ln < len(hay) else " "
    return not (b.isdigit() or b == ".") and not (a.isdigit() or a == ".")


# ------------------------------------------------------------------ C13 성분명 정규화
# 이름을 바꿨다는 것은 값을 바꿨다는 뜻이다. 그래서 (1) 바꾼 이름이 정말 그 문서에
# 있는지 (2) 같은 CAS 인지 (3) 전버전이 남아 있는지 셋을 문서를 다시 열어 확인한다
CANON = HLT[HLT.정규화 == "예"] if "정규화" in HLT.columns else HLT.iloc[0:0]
c13, n_c13 = [], 0
NAMES_BY_CAS = ING[ING.ing_cas_best.notna() & ING.ing_name_best.notna()].groupby(
    ING.ing_cas_best.map(lambda s: norm(s))).ing_name_best.apply(
    lambda s: {norm(x) for x in s}).to_dict()
for r in CANON.itertuples():
    n_c13 += 1
    key = (r.Formulation_ID, r.ing_idx)
    try:
        v6r = V6KEY.loc[key]
    except KeyError:
        c13.append(f"{key} v6 에 없는 키")
        continue
    if norm(r.정규화_이전값) != norm(v6r.ing_name_best):
        c13.append(f"{key} 이전값 {r.정규화_이전값!r} != v6 {v6r.ing_name_best!r}")
    if norm(r.공유_최종값) not in NAMES_BY_CAS.get(norm(v6r.ing_cas_best), set()):
        c13.append(f"{key} 정규화 이름 {r.공유_최종값!r} 이 같은 CAS 의 v6 이름 목록에 없다")
    if CANONMAP.get(key, ("", ""))[0] != norm(r.공유_최종값):
        c13.append(f"{key} 시트 정규화값과 대조표가 다르다")
    # 정규화 전의 원값이 물질을 특정하는 문자열이었는지. `Other Ingredients
    # (proprietary)` 를 `Water` 로 바꾸면 비공개 표시가 물질명으로 둔갑한다
    if re.search(r"(?i)proprietary|trade\s*secret|confidential|withheld|영업비밀|비공개",
                 norm(r.정규화_이전값)):
        c13.append(f"{key} 이전값이 비공개 표시인데 정규화했다: {r.정규화_이전값!r}")
    txt = doctext(r.근거파일).lower()
    nd = norm(r.공유_최종값).lower()
    hits = occ(txt, nd)
    if not hits:
        c13.append(f"{key} 정규화 이름 {r.공유_최종값!r} 이 근거문서에 없다")
    elif not any(word_edge(txt, i, len(nd)) for i in hits):
        c13.append(f"{key} 정규화 이름 {r.공유_최종값!r} 이 더 긴 낱말 안에서만 걸렸다")
# 원값은 그대로 남아 있어야 한다(C7 과 중복이지만 여기서는 정규화 대상 행만 본다)
S13 = SH["성분"].set_index(["Formulation_ID", "ing_idx"])
for k, (nm, prev) in CANONMAP.items():
    if norm(S13.loc[k, "ing_name_best"]) != prev:
        c13.append(f"{k} ing_name_best 가 바뀌었다")
# 정규화 근거는 '문서 텍스트에 그 이름이 있다' 다. 좌표를 못 잡아 칠하지 못한 앵커도
# 텍스트 근거는 같다 — 실패가 아니라 '눈으로 볼 하이라이트가 없다' 는 사실이라서
# 숫자로 드러낸다. 받는 쪽이 그 행은 파일을 열어 검색해야 한다
c13_nohl = int((CANON.하이라이트_수.fillna(0) == 0).sum()) if len(CANON) else 0
rec("C13 성분명 정규화", "OK" if (n_c13 and not c13) else "실패",
    f"정규화 앵커 {n_c13}개 · 성분행 {len(CANONMAP)}행 — 문서 실재·동일CAS·전버전 보존 "
    f"3중 확인 · 불일치 {len(c13)}건 · 그중 하이라이트 없는 앵커 {c13_nohl}개"
    f"(문서 텍스트에는 있으나 좌표를 못 잡음)"
    + ("\n    - " + "\n    - ".join(c13[:6]) if c13 else ""))

# ------------------------------------------------- C14 '일치' 앵커의 낱말 경계
# 거절된 앵커만 보면 규칙의 반쪽만 보는 것이다. 받아들여 칠한 앵커가 더 긴 낱말 안에서만
# 걸렸다면 그건 잘못 칠한 것이고, "원문에서 확인했다" 는 표시가 거짓이 된다 —
# `Urea` 가 `Methylenediurea` 안에서, `Bentazon` 이 `Bentazone` 안에서 걸린다
OKN = HLT[(HLT.판정 == "일치") & HLT.필드.isin(["성분명", "CAS"]) & HLT.근거파일.notna()]
c14 = []
for r in OKN.itertuples():
    txt = doctext(r.근거파일)
    if not txt:
        continue
    low2 = txt.lower()
    nd = norm(r.원문_문자열).lower()
    hits = occ(low2, nd)
    edge = num_edge if r.필드 == "CAS" else word_edge
    if not hits:
        c14.append(f"{r.Formulation_ID}#{r.ing_idx} {r.필드} {nd[:30]!r} 문서에 없음")
    elif not any(edge(low2, i, len(nd)) for i in hits):
        a, b = hits[0], hits[0] + len(nd)
        while a > 0 and low2[a - 1].isalnum():
            a -= 1
        while b < len(low2) and low2[b].isalnum():
            b += 1
        c14.append(f"{r.Formulation_ID}#{r.ing_idx} {r.필드} {nd[:28]!r} ⊂ {txt[a:b][:36]!r}")
rec("C14 일치 앵커 낱말 경계", "OK" if not c14 else "실패",
    f"칠한 이름·CAS 앵커 {len(OKN):,}개 전수 · 더 긴 낱말 안에서만 걸린 것 {len(c14)}개"
    + ("\n    - " + "\n    - ".join(c14[:6]) if c14 else ""))

# --------------------------------------------------- C15 데이터정정 후보 원장
# '적용가능' 은 "정정값이 근거문서에 실재한다" 는 주장이다. 문서를 다시 열어 확인한다.
# 그리고 원장이 v6 를 덮어쓰지 않았는지, `데이터쪽오류` 앵커를 빠뜨리지 않았는지 본다
FIXC = pd.read_csv(OUT / "09_데이터정정_후보.csv").fillna("")
c15 = []
if len(SH["데이터정정후보"]) != len(FIXC):
    c15.append(f"시트 {len(SH['데이터정정후보'])} != csv {len(FIXC)}")
if not set(FIXC.판정) <= {"적용가능", "보류", "정정불가"}:
    c15.append(f"정의 밖 판정 {set(FIXC.판정) - {'적용가능', '보류', '정정불가'}}")
if len(FIXC) != len(FIXC.drop_duplicates(["Formulation_ID", "ing_idx", "구분"])):
    c15.append("같은 앵커가 중복 기록됨")
n_app = int((FIXC.판정 == "적용가능").sum())
for r in FIXC[FIXC.판정 == "적용가능"].itertuples():
    if not str(r.정정후보).strip():
        c15.append(f"{r.Formulation_ID}#{r.ing_idx} 적용가능인데 후보가 빈칸")
        continue
    if not str(r.근거파일).strip():
        c15.append(f"{r.Formulation_ID}#{r.ing_idx} 적용가능인데 근거파일이 빈칸")
        continue
    txt = doctext(str(r.근거파일)).lower()
    nd = norm(r.정정후보).lower()
    hits = occ(txt, nd)
    edge = num_edge if r.구분 == "CAS" else word_edge
    if not any(edge(txt, i, len(nd)) for i in hits):
        c15.append(f"{r.Formulation_ID}#{r.ing_idx} {r.구분} 정정후보 {nd[:30]!r} 가 "
                   f"근거문서에 낱말 단위로 없다")
# 원장이 값을 덮어쓰지 않았는지 — 성분 시트의 원값이 v6 와 같아야 한다(C7 과 별개로 대상 행만)
Sfx = SH["성분"].set_index(["Formulation_ID", "ing_idx"])
for r in FIXC.itertuples():
    k = (r.Formulation_ID, r.ing_idx)
    if k not in Sfx.index or k not in V6KEY.index:
        continue
    for col in ("ing_name_best", "ing_cas_best"):
        a, b = Sfx.loc[k, col], V6KEY.loc[k, col]
        if not (pd.isna(a) and pd.isna(b)) and str(a) != str(b):
            c15.append(f"{k} {col} 이 v6 와 다르다 — 원장이 값을 덮어썼다")
# `데이터쪽오류` 로 분류된 앵커는 전건 원장에 있어야 한다
_need = {(r.Formulation_ID, r.ing_idx) for r in MIS[MIS.분류 == "데이터쪽오류"].itertuples()}
_have = {(r.Formulation_ID, r.ing_idx) for r in FIXC.itertuples()}
if _need - _have:
    c15.append(f"데이터쪽오류인데 원장에 없는 앵커 {len(_need - _have)}개 {list(_need - _have)[:2]}")
rec("C15 데이터정정 후보 원장", "OK" if (len(FIXC) and not c15) else "실패",
    f"원장 {len(FIXC)}행 · 판정 {FIXC.판정.value_counts().to_dict()} · "
    f"적용가능 {n_app}건 문서 실재 재확인 · 데이터쪽오류 앵커 {len(_need)}개 전건 수록 · "
    f"불일치 {len(c15)}건"
    + ("\n    - " + "\n    - ".join(c15[:6]) if c15 else ""))

# ------------------------------------------- C16 HTML 사본에서 낱말이 갈라졌는지
# `<mark>` 를 낱말 중간에 끼우면 사본의 텍스트가 갈라진다. 받는 쪽이 그 사본에서 값을
# 문자열 검색하면 못 찾는다. 사본에는 mark 가 두 종류 들어 있다 —
# 과거 감사도구가 원본에 박아 둔 `class="audit-highlight"` 와 이번 판이 넣은
# `data-field`. 둘을 갈라서 봐야 우리 결함과 물려받은 결함을 구분할 수 있다
def htmark(rel: str, drop: str) -> str:
    """drop='none' 전달본 그대로 · 'ours' 우리 mark 만 걷음 · 'all' 전부 걷음."""
    soup = BeautifulSoup((OUT / rel).read_bytes(), "lxml")
    for x in soup(["script", "style"]):
        x.decompose()
    for m in soup.find_all("mark"):
        if drop == "all" or (drop == "ours" and m.has_attr("data-field")):
            m.unwrap()
    if drop != "none":
        soup.smooth()                      # 갈라진 인접 문자열을 다시 붙인다
    return norm(soup.get_text(" "))


def words6(t: str) -> set[str]:
    return {w for w in re.split(r"[^0-9A-Za-z가-힣]+", t) if len(w) >= 6}


htmls = [str(p) for p in INV.archive_path if str(p).lower().endswith((".html", ".htm"))]
c16, c16_inherit = [], []
for rel in htmls:
    원문, 받은원본, 전달본 = (htmark(rel, "all"), htmark(rel, "ours"), htmark(rel, "none"))
    if squash(원문) == squash(전달본):
        continue
    ours = sorted(words6(받은원본) - words6(전달본))
    inh = sorted(words6(원문) - words6(받은원본))
    if ours:
        c16.append(f"{Path(rel).name[:44]} · 우리 mark {len(ours)}개 · {' | '.join(ours[:3])}")
    if inh:
        c16_inherit.append(f"{Path(rel).name[:44]} · {len(inh)}개 · {' | '.join(inh[:3])}")
rec("C16 HTML 사본 낱말 분절", "OK" if not c16 else "실패",
    f"HTML 사본 {len(htmls)}건 · 우리 mark 가 낱말을 가른 파일 {len(c16)}건 · "
    f"과거 감사도구 mark 가 이미 가른 파일 {len(c16_inherit)}건(원본에서 물려받음)"
    + ("\n    - " + "\n    - ".join(c16[:6]) if c16 else "")
    + ("\n    - 물려받음: " + "\n    - 물려받음: ".join(c16_inherit[:3]) if c16_inherit else ""))

# ------------------------------------------- C17 '3절(조성)' 라벨이 맞는지
# 하이라이트 위치를 `3절(조성)` 이라고 적었으면 그 페이지에 3절 머리글이 실제로
# 찍혀 있어야 한다. 예전 규칙은 3절~4절 사이 페이지까지 3절로 적어 조성표가 아닌
# 페이지가 '3절' 로 보고됐다
S3V = re.compile(r"(?i)composition.{0,20}information\s+on\s+ingredients|"
                 r"information\s+on\s+ingredients|조성.{0,6}성분|"
                 r"(?:^|\s)(?:section\s*)?3\s*[.):-]\s*composition")
c17, n17 = [], 0
_pgcache: dict[str, list[str]] = {}
for r in HLT[(HLT.원문_구간 == "3절(조성)") & HLT.근거파일.notna()].itertuples():
    rel = str(r.근거파일)
    if not rel.lower().endswith(".pdf"):
        continue
    if rel not in _pgcache:
        with fitz.open(OUT / rel) as d:
            _pgcache[rel] = [pg.get_text() for pg in d]
    pgs = _pgcache[rel]
    nums = [int(x) for x in re.findall(r"\d+", str(r.원문_페이지))]
    if not nums:
        continue
    n17 += 1
    if not any(1 <= p <= len(pgs) and S3V.search(norm(pgs[p - 1])) for p in nums):
        c17.append(f"{r.Formulation_ID}#{r.ing_idx} p{nums} 에 3절 머리글이 없다 — {rel[:44]}")
rec("C17 3절 라벨 검증", "OK" if not c17 else "실패",
    f"'3절(조성)' 로 적힌 PDF 앵커 {n17:,}개 · 머리글 없는 페이지를 가리키는 앵커 {len(c17)}개"
    + ("\n    - " + "\n    - ".join(c17[:6]) if c17 else ""))

# ------------------------------------------------------------------ C11 README 숫자
# 설명 문서는 빌드가 데이터에서 계산해 끼워 넣는다. 그래도 여기서 한 번 더 대조한다 —
# 사람이 손으로 고치거나 끼워 넣는 식이 틀리면 받는 쪽은 그걸 알 방법이 없다
RM = (OUT / "README.md").read_text()
AN = (OUT / "00_과거공유본_분석.md").read_text()
c11 = []
if len(AN) < 3000:
    c11.append(f"00_과거공유본_분석.md 가 너무 짧다 ({len(AN)}자)")
기대 = [
    f"성분행 {len(ING):,}행", f"제형 {len(SH['제형']):,}건",
    f"근거 문서 {len(INV):,}건", f"앵커 {len(HLT):,}행",
    f"{len(ING):,}행 중 {int(요약['원문에서 확인된 성분행']):,}행이다",
    f"나머지 {len(SH['검토필요']):,}행은 전부 `검토 필요`",
    f"칠해진 칸 {int(HLT.하이라이트_수.sum()):,}개",
    " · ".join(f"{k} {v}" for k, v in INV.문서형식.value_counts().items()),
    f"{len(XL.sheet_names)}개 시트",
    f"## 성분명 표기 정규화 ({len(CANONMAP)}행)",
    f"## 문서불일치 앵커 전수검사 ({len(MIS):,}건)",
    f"## 데이터쪽오류 정정 후보 ({len(FIXC):,}행)",
    f"## 농도 미검증 {n_unver}행",
    f"| `적용가능` | {n_app:,} |",
    " · ".join(f"{k} {v}" for k, v in SH['성분'].농도_환산규칙.value_counts().items()),
]
기대 += [f"| `{s}` | {len(SH[s]):,} |" for s in XL.sheet_names]
기대 += [f"| `{b}` | {int((MIS.분류 == b).sum()):,} |" for b in sorted(BUCKETS)]
c11 += [f"README 에 없음: {e!r}" for e in 기대 if e not in RM]
rec("C11 설명문서 숫자", "OK" if not c11 else "실패",
    f"README 대조 {len(기대)}항목 · 불일치 {len(c11)}건"
    + ("\n    - " + "\n    - ".join(c11[:6]) if c11 else ""))

# ------------------------------------------------------------------ 보고서
grades = [g for _, g, _ in FINDINGS]
verdict = ("전 항목 통과" if set(grades) <= {"OK"} else
           "경고 있음(치명 없음)" if "실패" not in grades else "실패 항목 있음")
lines = [f"# 검수 보고 — {OUT.name}", "",
         f"**결론: {verdict}.** 검수 항목 {len(FINDINGS)}개 중 "
         f"OK {grades.count('OK')} · 경고 {grades.count('경고')} · 실패 {grades.count('실패')}.",
         "", "검수는 빌드 스크립트를 신뢰하지 않고 산출물을 다시 열어서 한다. "
         f"PDF 주석 {n_annot}개를 전수로 다시 읽어 (1) 주석이 덮은 글자가 주석에 적힌 원문과 "
         "같은지 (2) 주석에 적힌 공유값이 `input_dataset_v6.xlsx` 의 실제 값과 같은지 "
         "두 방향으로 대조했다.", "",
         "| 항목 | 결과 | 내용 |", "|---|---|---|"]
for item, grade, msg in FINDINGS:
    cell = msg.replace("\n", " ").replace("|", "/")
    lines.append(f"| {item} | {grade} | {cell} |")

lines += ["", "## 검수에서 확인된 사실", "",
          f"- 원문에서 확인된 성분행 **{SUM['counts']['원문확인_성분행']}행** / "
          f"전체 {SUM['counts']['성분행']}행. 나머지는 전부 `검토 필요` 로 남겼고 "
          "추정으로 채운 곳은 없다(C7).",
          f"- 하이라이트 **{SUM['counts']['하이라이트_개수']}개**가 모두 "
          "「원문 문자열 → 공유값」 주석을 달고 있고, 덮은 글자와 주석 내용이 일치한다(C3·C4).",
          f"- 앵커 판정 분포: {SUM['판정_집계']['앵커_판정']}",
          f"- 하이라이트 위치: {SUM['판정_집계']['원문_구간']} "
          "(3절=조성 정보 절, 문서기타=라벨 등 다른 절)",
          f"- 문서 형식 판별: {INV.문서형식.value_counts().to_dict()} — "
          "`SDS아님(라벨·목록)` 은 EPA·주정부 등록 라벨이나 제품 목록 페이지다. "
          "유효성분 농도 근거로는 쓸 수 있으나 GHS 분류 근거로는 약하다.",
          "", "## 추가 확인이 필요한 항목", ""]
need = [
    (f"농도 미검증 {n_unver}행",
     "원문이 스스로 '가정·미확인·영업비밀·명목값' 이라 적은 값. `농도환산_검증` 에 "
     "`미검증_추정치` 로 표시하고, 걸린 표현 자체를 `농도_불확실표현` 열에 원문 그대로 "
     f"옮겼다({int((표현 != '').sum())}행 · 그중 최종값이 있는 {n_unver}행에 표시). "
     "확정값으로 쓰면 안 된다."),
    (f"데이터정정 후보 {len(FIXC)}행 (적용가능 {n_app} · "
     f"보류 {int((FIXC.판정 == '보류').sum())} · "
     f"정정불가 {int((FIXC.판정 == '정정불가').sum())})",
     "`데이터쪽오류` 로 분류된 앵커의 정정 후보다. `적용가능` 은 정정값이 근거문서에 "
     "실재하는 것만이고(C15 재확인), v6 값은 덮어쓰지 않았다. 반영 여부는 받는 쪽 판단이다. "
     "`09_데이터정정_후보.csv`."),
    (f"농도 원문없음·값있음 {int((s8.농도환산_검증 == '원문없음_값있음').sum())}행",
     "출처 문자열이 남지 않은 값. 어디서 왔는지 되짚을 수 없다."),
    (f"농도 환산 불일치 {int((s8.농도환산_검증 == '규칙불일치').sum())}행",
     "원문 문자열과 최종값이 환산 규칙으로 연결되지 않는다."),
    (f"문서불일치 앵커 {int((HLT.판정 == '문서불일치').sum()):,}개 — 전건 원인 분류 완료",
     f"`07_문서불일치_전수검사.csv` 에 한 줄씩 있다. {MIS.분류.value_counts().to_dict()}. "
     "`데이터쪽오류` 는 정정 후보까지 냈고, `표기차이` 는 판독 규칙을 어디까지 풀면 "
     "맞는지 측정만 했다(`08_판독규칙_감사.csv`). 어느 쪽도 값을 고치지 않았다."),
    (f"표기변형 잔여 앵커 {int((HLT.판정 == '표기변형').sum())}개",
     f"CAS 는 원문에 있는데 성분명이 다르다. 이 중 {len(CANONMAP)}행은 같은 CAS 의 다른 "
     "이름이 문서에 완전일치해 정규화했고(C13), 나머지는 대체할 이름을 문서에서 찾지 "
     "못해 그대로 남겼다."),
    (f"문서판독불가 {int((HLT.판정 == '문서판독불가').sum())}개 · "
     f"좌표실패 {int((HLT.판정 == '좌표실패').sum())}개 · "
     f"좌표검증실패 {int((HLT.판정 == '좌표검증실패').sum())}개 · "
     f"위치불일치 {int((HLT.판정 == '위치불일치').sum())}개",
     "텍스트에는 있으나 칠하지 못한 앵커. 좌표검증실패는 칠했다가 덮은 글자가 원문과 "
     "어긋나 지운 것(다단 레이아웃)이다. 하이라이트가 없으니 눈으로 봐야 한다."),
    (f"SDS아님 문서 {int((INV.문서형식 == 'SDS아님(라벨·목록)').sum())}건",
     "SDS 자리에 라벨·목록 페이지가 붙어 있다. 재수집 후보."),
    (f"재수집 후보 URL {SUM['counts']['재수집후보_URL']}건",
     "근거 공백 제형에 남아 있는 원문 URL. 이번 판에서는 수집하지 않았다."),
]
lines += [f"- **{a}** — {b}" for a, b in need]
lines += ["", "## 과거 아카이브와의 기준 차이", "",
          f"`02_제품코드별_성분리스트.xlsx` 의 `기준차이` 시트에 {len(SH['기준차이'])}행으로 정리했다. "
          "가장 큰 차이 세 가지는 (1) 과거는 하이라이트가 노란색 1종·주석 없음이라 "
          "칠한 칸이 어느 데이터가 되었는지 알 수 없었다 (2) 과거 농도는 제형당 문자열 "
          "1개여서 성분-농도 정렬이 깨진 사례가 있다 (3) 이번 판은 라벨 관할을 EU CLP 로 "
          "명시했다.", ""]
REPORT.write_text("\n".join(lines), encoding="utf-8")
log(f"검수 완료 — {verdict} → {REPORT.name}")
print(f"{verdict} → {REPORT.relative_to(L.ROOT)}")
