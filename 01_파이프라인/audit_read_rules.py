"""문서불일치 앵커의 판독규칙 감사 — `표기차이` 와 `데이터쪽오류` 전건.

`build_msds_highlight_share.py` 가 앵커를 '문서에 없다' 로 판정한 근거는
`contains()` 문자열 일치 하나다. 그래서 "왜 안 맞았는지" 는 판정만 보고는 알 수 없다.
이 스크립트는 근거문서를 다시 열어 **규칙을 한 단계씩 느슨하게 풀어 보면서**
어느 단계에서 맞기 시작하는지를 앵커마다 기록한다.

단계(성분명):
  L0 원문 그대로            = 현행 규칙. 여기서 맞으면 '문서불일치' 가 아니었을 것
  L1 구두점·공백 무시        squash() 수준. 하이픈·괄호·쉼표·줄바꿈만 다른 경우
  L2 어절 전부 존재          어순이 다르거나 중간에 글자가 끼어든 경우
  L3 어절 일부만 존재        이명이거나 우리 이름에 꼬리표가 붙은 경우
단계(농도):
  L0 원문 그대로
  L1 공백 무시
  L2 숫자만 일치(경계검사 포함)
  L3 숫자도 없음

읽기 전용이다. 값을 고치지 않고 `08_판독규칙_감사.csv` 만 낸다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_model as L                                            # noqa: E402

OUT = L.ROOT / "07_화학연구원_공유_260918"
MIS_CSV = OUT / "07_문서불일치_전수검사.csv"
log = L.make_logger(L.ROOT / "01_파이프라인" / "판독규칙_감사.log")

DASH = dict.fromkeys(map(ord, "‐‑‒–—―−－"), "-")


def norm(s) -> str:
    return "" if s is None or (isinstance(s, float) and pd.isna(s)) \
        else re.sub(r"\s+", " ", str(s).translate(DASH)).strip()


def squash(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", norm(s).lower())


def nospace(s: str) -> str:
    return re.sub(r"\s+", "", norm(s).lower())


def toks(s: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-zA-Z가-힣]+", norm(s).lower()) if len(t) >= 4]


def _edge_ok(hay: str, i: int, ln: int) -> bool:
    b = hay[i - 1] if i else " "
    a = hay[i + ln] if i + ln < len(hay) else " "
    return not (b.isdigit() or b == ".") and not (a.isdigit() or a == ".")


def num_in(hay: str, x: str) -> bool:
    i = hay.find(x)
    while i >= 0:
        if _edge_ok(hay, i, len(x)):
            return True
        i = hay.find(x, i + 1)
    return False


def cas_ck(cas: str) -> bool:
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", norm(cas))
    if not m:
        return False
    d = (m[1] + m[2])[::-1]
    return sum((i + 1) * int(c) for i, c in enumerate(d)) % 10 == int(m[3])


def cas_fix(cas: str) -> str:
    """체크디지트만 바로잡은 번호. 앞 두 블록이 맞다는 가정 아래의 후보값이다."""
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-\d", norm(cas))
    if not m:
        return ""
    d = (m[1] + m[2])[::-1]
    return f"{m[1]}-{m[2]}-{sum((i + 1) * int(c) for i, c in enumerate(d)) % 10}"


_CACHE: dict[str, str] = {}
MARK_RE = re.compile(rb"</?mark\b[^>]*>")


def doctext(rel: str, strip_mark: bool = True) -> str:
    """근거문서의 정규화 텍스트.

    HTML 사본은 우리가 `<mark>` 를 끼워 넣은 것이다. 태그가 단어 중간에 들어가면
    `get_text(" ")` 이 단어를 공백으로 갈라 버려서(`Ethoprophos` → `Ethoprop hos`)
    빌드 당시 원문과 달라진다. 그래서 감사에서는 mark 태그를 걷어내고 읽는다.
    `strip_mark=False` 면 받는 쪽이 실제로 보게 되는 텍스트가 나온다."""
    key = f"{rel}|{strip_mark}"
    if key in _CACHE:
        return _CACHE[key]
    p = OUT / rel
    try:
        if p.suffix.lower() == ".pdf":
            with fitz.open(p) as d:                  # 주석은 텍스트를 바꾸지 않는다
                t = norm(" ".join(pg.get_text() for pg in d))
        else:
            raw = p.read_bytes()
            if strip_mark:
                raw = MARK_RE.sub(b"", raw)
            soup = BeautifulSoup(raw, "lxml")
            for x in soup(["script", "style"]):
                x.decompose()
            t = norm(soup.get_text(" "))
    except Exception as e:
        log(f"  판독실패 {rel}: {type(e).__name__} {e}")
        t = ""
    _CACHE[key] = t
    return t


def occurrences(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def _span(doc: str, i: int, ln: int) -> str:
    """공백 제거 문자열의 [i, i+ln) 을 원문 위치로 되돌려 주변 글자까지 보여 준다."""
    cnt, start, end = 0, -1, -1
    for k, ch in enumerate(doc):
        if ch.isspace():
            continue
        if cnt == i:
            start = k
        cnt += 1
        if cnt == i + ln:
            end = k + 1
            break
    if start < 0:
        return ""
    return norm(doc[max(0, start - 26): (end if end > 0 else start) + 26])


def _span_squash(doc: str, i: int, ln: int) -> str:
    """squash 문자열의 [i, i+ln) 을 원문 자리로 되돌린다.

    `_span` 은 공백만 지운 문자열용이라 squash 인덱스에는 못 쓴다 — squash 는
    구두점까지 지우기 때문에 인덱스가 어긋난다. 이 함수가 필요한 이유는
    "squash 로 걸렸다" 만으로는 그 자리가 정말 그 물질을 적은 자리인지 알 수 없어서다."""
    keep, start, end, cnt = re.compile(r"[0-9a-z가-힣]"), -1, -1, 0
    for k, ch in enumerate(doc.lower()):
        if not keep.match(ch):
            continue
        if cnt == i:
            start = k
        cnt += 1
        if cnt == i + ln:
            end = k + 1
            break
    if start < 0:
        return ""
    return norm(doc[max(0, start - 30): (end if end > 0 else start) + 30])


_SQMAP: dict[int, list[int]] = {}


def squash_map(doc: str) -> list[int]:
    """squash 문자열의 각 글자가 원문 몇 번째 글자였는지. 캐시한다(문서가 크다)."""
    k = id(doc)
    if k not in _SQMAP:
        keep = re.compile(r"[0-9a-z가-힣]")
        _SQMAP[k] = [i for i, c in enumerate(doc.lower()) if keep.match(c)]
    return _SQMAP[k]


def squash_aligned(doc: str, sq: str, needle_sq: str) -> bool:
    """squash 일치가 **낱말 경계에 맞춰** 걸린 자리가 있는가.

    이 검사가 필요한 이유: squash 는 구두점을 다 지우므로 `Permethrin` 이 문서의
    `Cypermethrin` 안에서 걸린다. 경계에 맞지 않는 일치는 '표기만 다르다' 가 아니라
    '다른 물질 안에 파묻힌 우연' 이다 — 규칙을 풀어도 살릴 수 없는 건이다."""
    if not needle_sq:
        return False
    idx = squash_map(doc)
    for j in occurrences(sq, needle_sq):
        s, e = idx[j], idx[j + len(needle_sq) - 1]
        b = doc[s - 1] if s else " "
        a = doc[e + 1] if e + 1 < len(doc) else " "
        if not b.isalnum() and not a.isalnum():
            return True
    return False


def word_edge_ok(hay: str, i: int, ln: int) -> bool:
    """앵커가 더 긴 낱말 안에 파묻혀 걸린 것이 아닌지. `Ethoprop` 이
    `Ethoprophos` 안에서 걸리는 것을 막는 검사다(현행 규칙에는 없다)."""
    b = hay[i - 1] if i else " "
    a = hay[i + ln] if i + ln < len(hay) else " "
    return not b.isalnum() and not a.isalnum()      # isalnum 은 한글도 포함한다


# ------------------------------------------------------------------ 입력
MIS = pd.read_csv(MIS_CSV)
log(f"문서불일치 전수검사 {len(MIS)}행 · 분류 {MIS.분류.value_counts().to_dict()}")

ING = pd.read_excel(L.V6, sheet_name="ingredient")
# 같은 CAS 에 붙어 있는 이름 풀(정규화 후보). 빈도 내림차순
POOL: dict[str, list[tuple[str, int]]] = {}
_nm = ING[ING.ing_cas_best.notna() & ING.ing_name_best.notna()]
for cas, g in _nm.groupby(_nm.ing_cas_best.map(norm)):
    vc = g.ing_name_best.value_counts()
    POOL[cas] = sorted(((str(k), int(v)) for k, v in vc.items()),
                       key=lambda t: (-t[1], -len(t[0]), t[0]))
# 이름 → 그 이름으로 쓰인 CAS 들(체크섬 통과분만). CAS 오류 교정 후보 풀
NAME2CAS: dict[str, list[tuple[str, int]]] = {}
for nm, g in _nm.groupby(_nm.ing_name_best.map(lambda s: squash(s))):
    vc = g.ing_cas_best.map(norm).value_counts()
    c = [(str(k), int(v)) for k, v in vc.items() if cas_ck(str(k))]
    if c:
        NAME2CAS[nm] = sorted(c, key=lambda t: -t[1])

ROWS = []
TARGET = MIS[MIS.분류.isin(["표기차이", "데이터쪽오류"])]
log(f"감사 대상 {len(TARGET)}행 · 문서 {TARGET.근거파일.nunique()}건")

for r in TARGET.itertuples():
    doc = doctext(r.근거파일)
    low, sq, nsp = doc.lower(), squash(doc), nospace(doc)
    n = norm(r.공유값)
    rec = {"Formulation_ID": r.Formulation_ID, "ing_idx": r.ing_idx, "필드": r.필드,
           "분류": r.분류, "원인": r.원인, "공유값": n, "성분명": r.성분명, "CAS": r.CAS,
           "근거파일": r.근거파일, "단계": "", "세부": "", "문서표기": "", "조치가능": ""}

    if r.필드 == "농도":
        nums = re.findall(r"\d+(?:\.\d+)?", n)
        hit = [x for x in nums if num_in(low, x)]
        # 공백무시 일치도 경계검사를 그대로 붙여야 한다. 안 붙이면 `100%` 가
        # `560100 %` 안에서 걸려 '공백만 다르다' 는 잘못된 결론이 난다
        sp_i = -1
        if nsp:
            for j in occurrences(nsp, nospace(n)):
                if _edge_ok(nsp, j, len(nospace(n))):
                    sp_i = j
                    break
        if sp_i >= 0:
            rec["단계"], rec["세부"] = "L1_공백무시", "공백만 다름"
            rec["문서표기"] = _span(doc, sp_i, len(nospace(n)))
        elif len(hit) == len(nums) and nums:
            rec["단계"], rec["세부"] = "L2_숫자만", f"숫자 {len(hit)}개 전부 있음"
        elif hit:
            rec["단계"], rec["세부"] = "L2_숫자일부", f"{len(hit)}/{len(nums)} {hit[:3]}"
        else:
            rec["단계"], rec["세부"] = "L3_숫자없음", f"숫자 {nums[:3]} 없음"
        if sp_i < 0 and hit:                # 문서가 그 수치를 어떻게 적었는지 실제 자리
            k = next((j for j in occurrences(low, hit[0])
                      if _edge_ok(low, j, len(hit[0]))), -1)
            if k >= 0:
                rec["문서표기"] = norm(doc[max(0, k - 26): k + len(hit[0]) + 26])
                # 그 숫자를 문서가 '농도' 로 적었는지. 뒤에 %·퍼센트·w/w 가 붙어 있지
                # 않으면 표의 다른 칸이나 CAS 조각일 수 있어서 같은 숫자여도 근거가 못 된다
                tail = doc[k + len(hit[0]): k + len(hit[0]) + 14]
                rec["세부"] += (" · 단위표기 있음" if re.search(r"%|퍼센트|w\s*/\s*w", tail, re.I)
                               else " · 뒤에 단위표기 없음")
        rec["조치가능"] = "예_공백정규화" if rec["단계"] == "L1_공백무시" else "아니오"

    else:                                        # CAS · 성분명
        if sq and squash(n) in sq:
            rec["단계"], rec["세부"] = "L1_구두점무시", "구두점·공백만 다름"
            i = sq.find(squash(n))
            rec["문서표기"] = _span_squash(doc, i, len(squash(n)))
            # squash 일치는 구두점을 다 지운 뒤의 일치라서, 서로 관계없는 두 낱말
            # 사이를 가로질러 걸릴 수 있다. 원문 자리가 낱말 경계에서 시작·끝나는지를
            # 따로 표시해야 '표기만 다르다' 와 '우연히 걸렸다' 를 가를 수 있다
            j = low.find(norm(n).lower())
            rec["세부"] += (" · 원문에 그대로도 있음" if j >= 0 else
                           " · 원문에는 그대로 없음(구두점 제거 후에만 걸림)")
            ok = squash_aligned(doc, sq, squash(n))
            rec["세부"] += " · 낱말경계 정합" if ok else " · 더긴낱말안에서만_일치"
            rec["조치가능"] = "조건부_squash일치" if ok else "아니오"
        else:
            tk = toks(n)
            present = [t for t in tk if t in low]
            # 어절 일치도 낱말 경계를 봐야 한다. `acid` 는 아무 문서에나 있고
            # `methyl` 은 다른 물질명 안에 있다 — 경계에 맞은 어절만 근거가 된다
            aligned = [t for t in present
                       if any(word_edge_ok(low, i, len(t)) for i in occurrences(low, t))]
            if tk and len(present) == len(tk):
                rec["단계"] = "L2_어절전부"
                rec["세부"] = (f"어절 {len(tk)}개 전부 있음(연속 아님)"
                              f" · 그중 낱말경계 정합 {len(aligned)}개")
                rec["조치가능"] = "조건부_어절전부" if len(aligned) == len(tk) else "아니오"
            elif present:
                rec["단계"] = "L3_어절일부"
                rec["세부"] = (f"{len(present)}/{len(tk)} {present[:3]}"
                              f" · 그중 낱말경계 정합 {len(aligned)}개")
                rec["조치가능"] = "아니오"
            else:
                rec["단계"] = "L4_어절없음"
                rec["세부"] = f"어절 {len(tk)}개 모두 없음"
                rec["조치가능"] = "아니오"
            # 우리 이름의 어느 쪽이 문서에 있는지(꼬리표 판별). 앞/뒤 연속 어절 최장
            if tk:
                pre = max((k for k in range(1, len(tk) + 1)
                           if squash("".join(tk[:k])) in sq), default=0)
                suf = max((k for k in range(1, len(tk) + 1)
                           if squash("".join(tk[-k:])) in sq), default=0)
                rec["세부"] += f" · 앞{pre}/뒤{suf} 연속일치"
        # 같은 CAS 의 다른 이름이 문서에 있는가(정규화 가능성)
        if pd.notna(r.CAS):
            alt = [(nm, c) for nm, c in POOL.get(norm(r.CAS), [])
                   if squash(nm) != squash(n) and len(squash(nm)) >= 4
                   and norm(nm).lower() in low]
            if alt:
                rec["문서표기"] = f"동일CAS 이름 문서에 있음: {alt[0][0]} (빈도 {alt[0][1]})"

    # 데이터쪽오류 교정 후보
    if r.분류 == "데이터쪽오류" and r.원인 == "CAS체크섬오류":
        fx = cas_fix(n)
        cand = []
        if fx and num_in(low, fx):
            cand.append(f"문서에_{fx}")
        if fx and fx != n and not cand:
            cand.append(f"체크디지트교정후보_{fx}(문서확인안됨)")
        byname = NAME2CAS.get(squash(r.성분명) if pd.notna(r.성분명) else "", [])
        for c, k in byname[:2]:
            cand.append(f"같은이름_다른행CAS_{c}(빈도{k}{'·문서에있음' if num_in(low, c) else ''})")
        rec["세부"] = " | ".join(cand) or "후보없음"
        rec["조치가능"] = "예_문서확인" if any(x.startswith("문서에_") for x in cand) \
            else ("조건부_데이터내근거" if byname else "아니오")

    if r.분류 == "데이터쪽오류" and r.원인 == "이름이_추출잡음":
        # 잡음을 걷어낸 후보. 자르기만 하고 새 글자를 만들지 않는다
        cln = re.sub(r"(?i)^\s*(synonyms?|product\s*name|chemical\s*name|cas\s*(no|number))"
                     r"\s*[:：]\s*", "", n)
        cln = re.split(r"(?i)\s*(?:min\.|max\.|content|w/w|hplc|percent|registry|"
                       r"\bNE\b|\bNA\b|\d+\s*%|\(\s*-)", cln)[0]
        cln = norm(re.sub(r"[\s,;:()\[\]/%<>=-]+$", "", cln))
        ok_doc = bool(cln) and len(squash(cln)) >= 4 and norm(cln).lower() in low
        pool_ok = ""
        if pd.notna(r.CAS):
            pool_ok = next((nm for nm, _ in POOL.get(norm(r.CAS), [])
                            if squash(nm) == squash(cln)), "")
        rec["세부"] = (f"잡음제거 후보「{cln}」 · 문서실재 {'예' if ok_doc else '아니오'}"
                      f" · v6동일CAS이름 {'예' if pool_ok else '아니오'}")
        rec["문서표기"] = cln
        rec["조치가능"] = "예_문서확인" if ok_doc else "아니오"

    ROWS.append(rec)

A = pd.DataFrame(ROWS)
A.to_csv(OUT / "08_판독규칙_감사.csv", index=False, encoding="utf-8-sig")
log(f"저장 {len(A)}행 → 08_판독규칙_감사.csv")

log("")
V = A[A.분류 == "표기차이"]
log(f"=== 표기차이 {len(V)} 단계별 ===")
for (f, s), g in V.groupby(["필드", "단계"]):
    log(f"  {f:4s} {s:12s} {len(g):4d}건   조치가능 {g.조치가능.value_counts().to_dict()}")
log("")
log(f"=== 데이터쪽오류 {int((A.분류 == '데이터쪽오류').sum())} ===")
for (o, c), g in A[A.분류 == "데이터쪽오류"].groupby(["원인", "조치가능"]):
    log(f"  {o:14s} {c:16s} {len(g):3d}건")
log("")
log("=== 표본 ===")
for f in ("성분명", "농도"):
    for s in sorted(V[V.필드 == f].단계.unique()):
        g = V[(V.필드 == f) & (V.단계 == s)]
        log(f"[{f} · {s} · {len(g)}건]")
        for x in g.head(6).itertuples():
            log(f"   「{x.공유값[:64]}」 → {x.세부[:88]}")
            if isinstance(x.문서표기, str) and x.문서표기:
                log(f"      문서: {x.문서표기[:96]}")

# ------------------------------------------------------------- C. '일치' 앵커의 경계 품질
# 거절된 앵커만 보면 규칙의 반쪽만 보는 것이다. 받아들인 앵커 중에 더 긴 낱말 안에서
# 걸린 것이 있으면 그건 잘못 칠한 것이다 — 현행 `contains()` 는 숫자 앵커에만
# 경계검사를 붙이고 이름 앵커에는 안 붙인다
log("")
log("=== C. '일치' 판정 성분명 앵커의 낱말 경계 검사 ===")
HL = pd.read_csv(OUT / "05_원문대조표.csv")
OKN = HL[(HL.판정 == "일치") & (HL.필드 == "성분명") & HL.근거파일.notna()]
log(f"대상 {len(OKN)}앵커 · 문서 {OKN.근거파일.nunique()}건")
CROWS = []
for x in OKN.itertuples():
    doc = doctext(x.근거파일)
    if not doc:
        continue
    low2 = doc.lower()
    nd = norm(x.원문_문자열).lower()
    occ = occurrences(low2, nd)
    if not occ:
        CROWS.append({"Formulation_ID": x.Formulation_ID, "ing_idx": x.ing_idx,
                      "앵커": nd, "종류": "문서에없음", "문서낱말": "",
                      "근거파일": x.근거파일})
        continue
    if any(word_edge_ok(low2, i, len(nd)) for i in occ):
        continue                                   # 낱말 단위로 걸린 자리가 있다 → 정상
    i = occ[0]
    a, b = i, i + len(nd)
    while a > 0 and low2[a - 1].isalnum():
        a -= 1
    while b < len(low2) and low2[b].isalnum():
        b += 1
    CROWS.append({"Formulation_ID": x.Formulation_ID, "ing_idx": x.ing_idx,
                  "앵커": nd, "종류": "더긴낱말안에서만일치", "문서낱말": doc[a:b],
                  "근거파일": x.근거파일})
C = pd.DataFrame(CROWS)
if len(C):
    C.to_csv(OUT / "08b_일치앵커_경계검사.csv", index=False, encoding="utf-8-sig")
    log(f"의심 {len(C)}앵커 · 종류 {C.종류.value_counts().to_dict()}")
    for x in C.head(20).itertuples():
        log(f"   {x.Formulation_ID} #{x.ing_idx} 「{x.앵커[:34]}」 ⊂ 「{x.문서낱말[:44]}」")
else:
    log("의심 0앵커")

# ------------------------------------------------------------- D. HTML 사본 단어 쪼개짐
# `<mark>` 를 낱말 중간에 끼우면 사본의 텍스트가 갈라진다. 받는 쪽이 그 사본에서
# 값을 문자열 검색하면 못 찾는다 — 우리가 만든 사본 쪽 결함이다
log("")
log("=== D. HTML 사본에서 mark 삽입이 낱말을 가른 건수 ===")
FI = pd.read_csv(OUT / "03_근거파일_인벤토리.csv")
htmls = [p for p in FI.archive_path.dropna() if str(p).lower().endswith((".html", ".htm"))]
log(f"HTML 사본 {len(htmls)}건")
split_rows = []
for rel in htmls:
    a = doctext(rel, strip_mark=True)
    b = doctext(rel, strip_mark=False)
    if squash(a) == squash(b):
        continue
    # 원문에는 붙어 있는데 사본에서 갈라진 낱말을 찾아 본다
    wa = {w for w in re.split(r"[^0-9A-Za-z가-힣]+", a) if len(w) >= 6}
    wb = {w for w in re.split(r"[^0-9A-Za-z가-힣]+", b) if len(w) >= 6}
    lost = sorted(wa - wb)
    split_rows.append({"파일": rel, "갈라진낱말수": len(lost),
                       "예": " | ".join(lost[:5])})
D = pd.DataFrame(split_rows)
if len(D):
    D.to_csv(OUT / "08c_HTML사본_낱말분절.csv", index=False, encoding="utf-8-sig")
    log(f"낱말이 갈라진 HTML 사본 {len(D)}건 / {len(htmls)}건 · "
        f"갈라진 낱말 합계 {int(D.갈라진낱말수.sum())}개")
    for x in D.head(15).itertuples():
        log(f"   {Path(x.파일).name[:56]} · {x.갈라진낱말수}개 · {x.예[:70]}")
else:
    log("낱말이 갈라진 사본 0건")
log("끝")
