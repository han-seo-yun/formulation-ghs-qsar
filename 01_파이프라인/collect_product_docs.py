"""제품 문서가 안 붙어 있는 제형의 올바른 제품 문서를 다시 찾는다.

대상은 문서불일치 전수검사의 두 분류다 — `다른제품문서`(엉뚱한 제품 문서가 붙음)와
`대체근거문서`(제품 문서 대신 성분 단독 SDS 가 붙음). 원인은 과거 수집기가 제품
코드의 숫자·부분문자열로 문서를 골랐다는 것이다 — `Zoxamide` 에
`chemicalbook/msds/oxamide.pdf`, `Decis 1.0 Gel` 에 `1-decene.pdf`,
`SP5411` 에 `sigma/e5411` 이 붙었다.

**접근 가능한 경로를 실측해서 고른 순서다.**
  검색엔진은 쓸 수 없다 — DuckDuckGo HTML/lite 는 첫 질의 뒤 HTTP 202(90초 대기도
  안 풀림), Bing 은 외부링크 0, Brave 429, Mojeek 403, Startpage/searx 계열 0,
  Ecosia 403, Yep 403, Yandex 는 1회 성공 후 캡차, stract 404, rightdao 503.
  도메인 DB 도 막혀 있다 — cdms.net 로그인 필요, greenbook/agrian 은 JS 셸,
  chemicalsafety.com 은 API 키, CDPR labelque 는 폼 전용.
  그래서 실제로 쓸 수 있는 경로는 **우리 데이터에 이미 있는 URL** 뿐이다.

  1) 제형의 `재수집후보_URL` 을 직접 받는다 (URL 없는 제형은 바로 빈칸이다)
  2) HTML 이면 같은 호스트의 SDS·라벨 링크를 따라간다 (최대 6개)
  3) 여기서 아무것도 못 건지면 **웹아카이브(Wayback CDX)** 로 같은 URL 의 과거
     스냅샷을 찾아 원본 그대로(`id_`) 받는다. 링크가 6개월 넘게 지나 403·404 가 된
     경우를 위한 경로다 — 실측으로 살아 있는 유일한 공개 색인 API 다
     (availability API 는 429, Common Crawl 색인은 504 로 응답한다)
  4) **검증**: 제품명이 낱말 단위로 문서에 있고, 우리 성분(CAS 또는 이름)이 함께
     있고, SDS·라벨 형식 표지가 있어야 채택한다
  5) 하나라도 못 넘기면 **빈칸**으로 둔다. 사유를 적는다

값을 만들지 않는다 — 검증을 통과한 문서만 남기고, 못 찾으면 못 찾았다고 적는다.
전 대상을 다 돌면 결과표와 찾은 문서를 공유 폴더에도 옮긴다
(`10_제품문서_재수집.csv` · `10_재수집문서/`).

    python3 collect_product_docs.py [--limit N] [--ids ID,ID]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
import urllib.parse as up
from pathlib import Path

import fitz
import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_model as L                                            # noqa: E402

SHARE = L.ROOT / "07_화학연구원_공유_260918"
OUT = L.ROOT / "04_모델산출물" / "v8_재수집_260919"
DOCDIR = OUT / "문서"
log = L.make_logger(L.ROOT / "01_파이프라인" / "재수집.log")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
PAUSE = 1.2
MAX_BYTES = 14 * 1024 * 1024
MAX_FOLLOW = 6                  # HTML 한 장에서 따라갈 문서 링크 수
DASH = dict.fromkeys(map(ord, "‐‑‒–—―−－"), "-")

SDS_RE = re.compile(r"safety data sheet|material safety data|물질안전보건자료|"
                    r"fiche de données|sicherheitsdatenblatt|hoja de datos de seguridad", re.I)
S3_RE = re.compile(r"composition.{0,20}information on ingredients|"
                   r"information on ingredients|조성.{0,6}성분", re.I)
LABEL_RE = re.compile(r"\bEPA Reg(?:istration)?\.? No|active ingredient", re.I)
DOC_LINK_RE = re.compile(r"sds|msds|safety[-_ ]?data|label|스프레드|안전보건", re.I)


def norm(s) -> str:
    return "" if s is None or (isinstance(s, float) and pd.isna(s)) \
        else re.sub(r"\s+", " ", str(s).translate(DASH)).strip()


def squash(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", norm(s).lower())


def word_hit(hay_low: str, needle_low: str) -> bool:
    """낱말 경계까지 보는 포함검사. `oxamide` 가 `zoxamide` 안에서 걸리는 것을 막는다."""
    if not needle_low:
        return False
    i = hay_low.find(needle_low)
    while i >= 0:
        b = hay_low[i - 1] if i else " "
        a = hay_low[i + len(needle_low)] if i + len(needle_low) < len(hay_low) else " "
        if not b.isalnum() and not a.isalnum():
            return True
        i = hay_low.find(needle_low, i + 1)
    return False


SESS = requests.Session()
SESS.headers.update(HDR)


def fetch(url: str) -> tuple[bytes, str, str]:
    """(본문, Content-Type, 실패사유)."""
    try:
        r = SESS.get(url, timeout=30, stream=True, allow_redirects=True)
        if r.status_code != 200:
            return b"", "", f"HTTP {r.status_code}"
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > MAX_BYTES:
                return b"", "", "용량초과"
        return buf, r.headers.get("Content-Type", ""), ""
    except Exception as e:
        return b"", "", type(e).__name__


def text_of(buf: bytes, ctype: str, url: str) -> str:
    try:
        if buf[:5] == b"%PDF-" or "pdf" in ctype.lower() or url.lower().endswith(".pdf"):
            with fitz.open(stream=buf, filetype="pdf") as d:
                return norm(" ".join(p.get_text() for p in d))
        soup = BeautifulSoup(buf, "lxml")
        for t in soup(["script", "style"]):
            t.decompose()
        return norm(soup.get_text(" "))
    except Exception:
        return ""


def doc_links(buf: bytes, base: str) -> list[str]:
    """HTML 안에서 SDS·라벨로 보이는 같은 호스트 링크."""
    out: list[str] = []
    try:
        soup = BeautifulSoup(buf, "lxml")
    except Exception:
        return out
    host = up.urlparse(base).netloc
    for a in soup.select("a[href]"):
        h = up.urljoin(base, a["href"])
        if up.urlparse(h).netloc != host:
            continue
        txt = norm(a.get_text(" "))
        if not (DOC_LINK_RE.search(h) or DOC_LINK_RE.search(txt)):
            continue
        if h not in out:
            out.append(h)
        if len(out) >= MAX_FOLLOW:
            break
    return out


CDX = "http://web.archive.org/cdx/search/cdx"


def wayback_snaps(url: str, k: int = 2) -> tuple[list[str], str]:
    """그 URL 의 과거 스냅샷 주소 → (주소들, 사유).

    `id_` 를 붙여 아카이브가 끼워 넣는 배너·리라이트 없이 원본 바이트를 받는다.
    스냅샷이 없으면 빈 목록이다 — 없는 것을 있다고 적지 않는다."""
    try:
        r = SESS.get(CDX, timeout=25, params={
            "url": url, "output": "json", "limit": str(k * 3), "collapse": "digest",
            "fl": "timestamp,original,mimetype,statuscode"})
    except Exception as e:
        return [], f"아카이브 조회실패({type(e).__name__})"
    if r.status_code != 200:
        return [], f"아카이브 조회 HTTP {r.status_code}"
    try:
        rows = r.json()
    except Exception:
        return [], "아카이브 응답 형식 이상"
    out = []
    for row in rows[1:]:
        ts, orig = row[0], row[1]
        sc = row[3] if len(row) > 3 else ""
        if sc and not str(sc).startswith(("2", "3", "-")):
            continue
        out.append(f"http://web.archive.org/web/{ts}id_/{orig}")
        if len(out) >= k:
            break
    return out, "" if out else "아카이브에 스냅샷 없음"


def pname_forms(pname: str) -> list[str]:
    """제품명의 대조 후보. `Zoxamide(95_100%)` 처럼 농도 꼬리가 붙은 제품명이 있어서
    원형만으로 대조하면 올바른 문서도 떨어진다. 자르기만 하고 덧붙이지 않는다."""
    out = [pname]
    t = norm(re.sub(r"\([^)]*\)", " ", pname))
    t = norm(re.sub(r"[_%\d.\s]+$", "", t))
    if t and t != pname and len(squash(t)) >= 4:
        out.append(t)
    return out


def judge(txt: str, pname: str, cas: list[str], nms: list[str]) -> tuple[bool, int, bool, str]:
    """(제품명일치, 성분일치수, SDS·라벨형식, 사유)."""
    low = txt.lower()
    pn = any(word_hit(low, p.lower()) or (len(squash(p)) >= 5 and squash(p) in squash(txt))
             for p in pname_forms(pname))
    nhit = (sum(1 for c in cas if word_hit(low, c.lower()))
            + sum(1 for nm in nms if word_hit(low, nm.lower())))
    form = bool(SDS_RE.search(txt) or S3_RE.search(txt) or LABEL_RE.search(txt))
    why = []
    if not pn:
        why.append("제품명 없음")
    if nhit == 0:
        why.append("성분 일치 0")
    if not form:
        why.append("SDS·라벨 형식 아님")
    return pn, nhit, form, " · ".join(why)


def mirror(R: pd.DataFrame, TGT: pd.DataFrame) -> None:
    """결과표와 채택 문서를 공유 폴더에도 둔다.

    받는 쪽이 '이 제형은 우리가 찾아봤는데 없었다' 를 알아야 같은 일을 다시 하지
    않는다. 08_ 감사 csv 와 같은 자리이고 빌드가 지우지 않는 이름이다.
    **전 대상을 다 돈 표일 때만** 옮긴다 — 일부만 돈 표를 공유 폴더에 두면 아직
    안 돈 제형이 '못 찾음' 으로 읽힌다."""
    if set(R.Formulation_ID) < set(TGT.Formulation_ID.unique()):
        log(f"일부 제형({len(R)}/{TGT.Formulation_ID.nunique()})만 돈 표라 "
            f"공유 폴더에는 반영하지 않았다")
        return
    R.to_csv(SHARE / "10_제품문서_재수집.csv", index=False, encoding="utf-8-sig")
    d = SHARE / "10_재수집문서"
    if d.exists():
        shutil.rmtree(d)
    fnd = R[(R.판정 == "발견") & R.채택파일.notna()]
    miss = []
    if len(fnd):
        d.mkdir(parents=True, exist_ok=True)
        for x in fnd.itertuples():
            src = OUT / str(x.채택파일)
            if src.exists():
                shutil.copy2(src, d / src.name)
            else:
                miss.append(x.Formulation_ID)
    log(f"공유 폴더 반영 — 10_제품문서_재수집.csv {len(R)}행 · 문서 {len(fnd) - len(miss)}건"
        + (f" · 파일 없음 {miss}" if miss else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="")
    ap.add_argument("--mirror-only", action="store_true",
                    help="네트워크를 쓰지 않고 이미 만든 재수집결과.csv 와 채택 문서만 "
                         "공유 폴더로 옮긴다")
    ap.add_argument("--archive-only", action="store_true",
                    help="이전 판에서 아카이브가 503·타임아웃으로 답을 아예 안 준 제형만 "
                         "골라 아카이브 단계만 다시 돈다. 원URL 쪽 판정은 이전 판을 쓴다")
    a = ap.parse_args()

    MIS = pd.read_csv(SHARE / "07_문서불일치_전수검사.csv")
    # 두 분류 모두 '그 제품의 문서가 안 붙어 있다' 는 같은 상태다 — 다른제품문서는
    # 엉뚱한 제품 문서가 붙은 것, 대체근거문서는 제품 문서 대신 성분 단독 SDS 가
    # 붙은 것이다. 제품 문서를 찾는 일은 둘 다 같아서 같이 돈다
    TGT = MIS[MIS.분류.isin(["다른제품문서", "대체근거문서"])]
    CLS = TGT.groupby("Formulation_ID").분류.agg(lambda s: "·".join(sorted(set(s))))
    FORM = pd.read_excel(SHARE / "02_제품코드별_성분리스트.xlsx",
                         sheet_name="제형").set_index("Formulation_ID")
    ING = pd.read_excel(SHARE / "02_제품코드별_성분리스트.xlsx", sheet_name="성분")

    RES = OUT / "재수집결과.csv"
    PREV = pd.read_csv(RES) if RES.exists() else None
    PREVMAP = dict(zip(PREV.Formulation_ID, PREV.사유.fillna(""))) if PREV is not None else {}

    if a.mirror_only:
        if PREV is None:
            log("이전 결과 파일이 없다 — --mirror-only 를 쓸 수 없다")
            return
        mirror(PREV, TGT)
        log(f"판정 {PREV.판정.value_counts().to_dict()}")
        return

    ids = [x for x in a.ids.split(",") if x] or sorted(TGT.Formulation_ID.unique())
    if a.archive_only:
        # 아카이브가 살아 있는지와 무관하게 '스냅샷 없음' 이라고 적으면 없는 사실을
        # 적는 것이다. 서비스가 답을 안 준 제형만 다시 묻는다
        if PREV is None:
            log("이전 결과 파일이 없다 — --archive-only 를 쓸 수 없다")
            return
        pend = PREV[(PREV.판정 == "미발견") & PREV.사유.fillna("").str.contains(
            "아카이브 조회 HTTP|아카이브 조회실패|아카이브 응답 형식")]
        ids = [x for x in ids if x in set(pend.Formulation_ID)]
    if a.limit:
        ids = ids[:a.limit]
    log(f"대상 제형 {len(ids)}건 · 앵커 {len(TGT)}개 · 분류 "
        f"{TGT.분류.value_counts().to_dict()}")
    log("경로: 웹아카이브(CDX) 재조회만 → 3중 검증" if a.archive_only else
        "경로: 재수집후보_URL 직접 수신 → 같은 호스트 문서링크 추적 → 웹아카이브(CDX) → 3중 검증")
    DOCDIR.mkdir(parents=True, exist_ok=True)
    # 지난 판의 잔여 파일을 먼저 지운다. 이번 판에서 채택되지 않은 문서가
    # 폴더에 남아 있으면 결과표에 없는 파일이 되어 근거로 오인된다
    for fid in ids:
        for p in DOCDIR.glob(f"{fid}__재수집.*"):
            p.unlink()

    rows = []
    for k, fid in enumerate(ids, 1):
        pname = norm(FORM.at[fid, "product_name"])
        sub = ING[ING.Formulation_ID == fid]
        cas = [norm(x) for x in sub.ing_cas_best.dropna().unique() if norm(x)]
        nms = [norm(x) for x in sub.ing_name_best.dropna().unique()
               if len(squash(x)) >= 5]
        seed = [u for u in (norm(FORM.at[fid, "재수집후보_URL"]) or "").split("|")
                if u.strip().startswith("http")]
        seed = [u.strip() for u in seed]
        log(f"[{k}/{len(ids)}] {fid} · 「{pname}」 · 성분 CAS {len(cas)} 이름 {len(nms)} "
            f"· 후보URL {len(seed)}")

        rec = {"Formulation_ID": fid, "분류": CLS.get(fid, ""),
               "제품명": pname, "후보URL수": len(seed),
               "시도URL수": 0, "채택URL": "", "채택파일": "", "제품명일치": "",
               "성분일치수": 0, "문서형식": "", "판정": "미발견", "경로": "",
               "사유": "", "기존URL": " | ".join(seed)}
        if not seed:
            rec["사유"] = "재수집 후보 URL 이 없다"
            rows.append(rec)
            continue

        tried: set[str] = set()
        fails: list[str] = []

        def crawl(queue: list[str], follow: bool, cap: int):
            """큐를 훑어 검증을 통과한 최고점 문서를 고른다 → (점수, url, buf, nhit)."""
            best = None
            while queue:
                u = queue.pop(0)
                if u in tried or len(tried) >= cap:
                    continue
                tried.add(u)
                buf, ct, err = fetch(u)
                time.sleep(PAUSE)
                if not buf:
                    fails.append(f"{up.urlparse(u).netloc}: {err}")
                    continue
                is_pdf = buf[:5] == b"%PDF-" or "pdf" in ct.lower()
                if follow and not is_pdf:
                    for h in doc_links(buf, u):
                        if h not in tried:
                            queue.append(h)
                txt = text_of(buf, ct, u)
                if len(txt) < 400:
                    fails.append(f"{up.urlparse(u).netloc}: 본문 짧음({len(txt)}자)")
                    continue
                pn, nhit, form, why = judge(txt, pname, cas, nms)
                if pn and nhit >= 1 and form:
                    score = 2 + min(nhit, 5) + 1
                    if best is None or score > best[0]:
                        best = (score, u, buf, nhit)
                else:
                    fails.append(f"{up.urlparse(u).netloc}: {why}")
            return best

        best, 경로 = (None, "") if a.archive_only else \
            (crawl(list(seed), True, 3 + MAX_FOLLOW), "원URL")

        # 살아 있는 URL 로 못 건지면 웹아카이브 스냅샷을 본다. 링크가 죽었거나
        # 봇 차단으로 403 인 경우가 대부분이라 여기서 살아나는 건이 있다
        if best is None:
            snaps, why_wb = [], ""
            for u in seed:
                s, w = wayback_snaps(u)
                snaps += s
                if w:
                    why_wb = w
                time.sleep(0.4)
            if snaps:
                best = crawl(snaps, False, len(tried) + len(snaps))
                경로 = "웹아카이브"
                if best is None:
                    fails.append(f"아카이브 스냅샷 {len(snaps)}건도 검증 미통과")
            else:
                fails.append(why_wb or "아카이브에 스냅샷 없음")
        rec["시도URL수"] = len(tried)

        if best is None:
            why = ("검증 통과 문서 없음 — " + " | ".join(fails[:4])) if fails \
                else "검증 통과 문서 없음"
            # 아카이브만 다시 돈 판에서는 원URL 쪽 사유를 이전 판에서 물려받는다.
            # 안 물려받으면 '왜 못 찾았나' 가 아카이브 한 줄로 줄어 기록이 얇아진다
            rec["사유"] = f"{PREVMAP[fid]} · [아카이브 재조회] {why}" \
                if a.archive_only and PREVMAP.get(fid) else why
            log(f"    미발견 · {rec['사유'][:110]}")
            rows.append(rec)
            continue

        _, u, buf, nhit = best
        ext = ".pdf" if buf[:5] == b"%PDF-" else ".html"
        dst = DOCDIR / f"{fid}__재수집{ext}"
        dst.write_bytes(buf)
        rec.update({"채택URL": u, "채택파일": str(dst.relative_to(OUT)),
                    "제품명일치": "예", "성분일치수": nhit,
                    "문서형식": "SDS·라벨 표지 있음", "판정": "발견", "경로": 경로,
                    "사유": f"제품명 낱말일치 · 성분 {nhit}개 일치 · SDS·라벨 형식 ({경로})"})
        log(f"    발견({경로}) → {u[:110]}")
        rows.append(rec)

    R = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.archive_only and PREV is not None:
        # 이번에 돈 제형만 갈아 끼운다. 안 돈 제형의 이전 판정을 지우면 안 된다
        keep = PREV[~PREV.Formulation_ID.isin(R.Formulation_ID)]
        R = pd.concat([keep, R], ignore_index=True).sort_values(
            "Formulation_ID", ignore_index=True)
    R.to_csv(RES, index=False, encoding="utf-8-sig")
    mirror(R, TGT)
    log("")
    log(f"판정 {R.판정.value_counts().to_dict()}")
    for c, g in R.groupby("분류"):
        log(f"  {c}: {g.판정.value_counts().to_dict()}")
    log(f"경로 {R[R.판정 == '발견'].경로.value_counts().to_dict()}")
    log(f"저장 → {OUT / '재수집결과.csv'}")


if __name__ == "__main__":
    main()
