#!/usr/bin/env python3
r"""성분 GHS 구분 조사 — PubChem 경유 CLP Annex VI · ECHA C&L. 산출: v8_성분조사/

## 왜 필요한가

가산식(`lib_model.ct_predict`)은 성분의 **구분과 농도가 둘 다** 있어야 계산에 넣는다
(`lib_model.py:439`). 실측 결과 성분행 5287 중 계산에 들어가는 것은 926(17.5%)뿐이고,
**농도는 있는데 구분만 없는 행이 2240** 이다. 그중 1167 은 기존 독립조사(A2)가 메우고
1073 행이 미조사다. 이를 채우면 평균 성분커버리지가 0.413 → 0.607 로 오르고 커버리지
≥0.75 제형이 356 → 739 개가 된다(그 구간에서 규칙 단독 MCC 가 eye 0.516 / skin 0.379).

**0.607 이 천장이다** — 농도 결측 2121 성분행은 이 조사로 해결되지 않는다.

## 출처 계층 — 추측이 아니라 `SourceName` 으로 식별한다

PUG-View 응답의 `Record.Reference[]` 는 각 `ReferenceNumber` 에 `SourceName` 을 준다.
`GHS Classification` 절의 각 정보블록은 `ReferenceNumber` 를 달고 있으므로 **어느 기관
값인지 행 단위로 확정된다**. 실측(CID 17520, BIT)에서 refno 134=CLP 규정,
23=ECHA, 129/128=NITE-CMC, 44=HCIS, 45=HSDB 로 갈렸다.

| tier | SourceName | 성격 |
|---|---|---|
| 1 | `Regulation (EC) No 1272/2008 …` | **CLP Annex VI 조화분류.** 법적 구속력 |
| 2 | `European Chemicals Agency (ECHA)` | C&L 신고 집계. 합의율(%)이 붙는다 |
| — | NITE-CMC · HCIS · HSDB 등 | 교차검증만. 값을 고르는 데 쓰지 않는다 |

tier1 이 있으면 tier1 만 쓴다. 없으면 tier2 를 합의율 하한과 함께 쓴다.

## H 코드를 1차 신호로 쓰는 이유

같은 절이 `Hazard Classes and Categories`(CLP 약어)와 H 코드를 모두 주는데 H 코드를
쓴다. (1) 약어는 CLP 어휘라 `Eye Irrit. 2` 로만 오고 **2A/2B 를 구별하지 못한다**.
H319/H320 은 구별한다. (2) 약어 → 구분 매핑에 표기 변이가 많다. (3) 같은 요청 하나로
합의율까지 온다.

## 앞판의 실측된 오류 — 여기서 고친 것

1. **`(> 99.9%)` 를 놓쳤다.** 정규식이 `([\d.]+)%` 만 받아 `>`·`<` 접두가 붙은 항목을
   버렸다. BIT 에서 `Eye Dam. 1 (> 99.9%)` 가 통째로 누락되고 `Skin Irrit. 2 (99.8%)`
   만 잡혀, **눈은 결측 피부는 확보**라는 엉뚱한 결과가 나왔다.
2. **PubChem 은 과부하를 HTTP 200 + `{"Fault": {...}}` 본문으로도 돌려준다.** 앞판은
   이것을 정상 응답으로 보고 **캐시에 저장**해서 영구히 "데이터 없음" 으로 읽었다.
   실측으로 확인했다. 이제 `Fault` 는 실패로 처리하고 캐시하지 않는다.
3. **조회 실패와 데이터 부재를 구별하지 못했다.** 앞판은 503 으로 못 받은 tebuconazole
   을 `GHS 절 없음 — 결측 유지` 로 적었다. 둘 다 결측이라 값은 안 틀리지만 **왜 비었는지
   를 틀리게 기록**했고, 재조회 대상을 식별할 수 없게 만든다. 이제 `조회실패` 로 갈라
   적고, **실패한 행에서는 NC 를 유도하지 않는다**.
4. **`명시적_NC` 판정이 틀렸다.** `not classified` 를 찾았는데 실제 문구는
   `This chemical does not meet GHS hazard criteria for < 0.1% (1 of 2589) of reports`
   다. 이는 "신고 중 0.1% 가 무해라고 했다" 는 뜻이고 물질이 NC 라는 뜻이 아니다.
   비율 문구를 NC 근거로 읽으면 값을 만드는 것이다. 이 판정을 없앴다.
5. **Annex VI 부재를 NC 로 읽었다.** 가장 심각했다. Annex VI 는 완전한 분류표가 아니라
   EU 가 조화한 엔드포인트만 싣는 **부분 목록**이다. tebuconazole(제형 74 개에 등장,
   최다 빈출)의 Annex VI 항목에는 눈 코드가 없어 앞판이 `NC` 로 확정했는데, 다른
   출처들은 H320(눈 2B)을 준다. 지금은 tier1 을 **양성 확정에만** 쓰고 부재는 tier2
   로 내려보낸다. NC 는 신고자가 전 엔드포인트를 올리는 tier2 에서만 유도한다.
6. **이름 필드에 농도 범위가 섞여 있다.** `'2-Benzisothiazol-3(2H)-one >= 0.005 - <='`
   같은 값이라 이름 조회가 통째로 실패한다. `clean_name` 으로 꼬리만 떼어낸다.
7. **같은 출처의 정보블록을 병합해 JSON 순서가 판정을 결정했다.** 가장 심각했다.
   ECHA C&L 은 한 물질에 `ReferenceNumber` 가 다른 신고 집계 블록을 여러 개 준다
   (실측 92 종). 앞판은 `out[tier]["codes"].update(blk["codes"])` 로 합쳐 **마지막
   블록이 이기게** 만들었다. 멘톨(CID 1254)의 눈 H319 는 블록별로 82.9% · 13.3% ·
   43% 인데, 어느 것이 남는지가 `2A` 냐 결측이냐를 갈랐다 — 응답 순서에 판정을 맡긴
   것이고, 탈락한 의견은 `*_후보` 에도 남지 않았다. 지금은 블록을 보존하고 블록끼리
   결론이 갈리면 **결측**으로 둔다(실측 눈 12 종 · 피부 7 종).
8. **`1 of 1 companies` 를 분모 9127 짜리 신고와 같은 무게로 셌다.** 미분류율도 같은
   `update` 경로에서 마지막 값이 이겨, 이산화규소(CID 24261)에서 `1 of 1`(100%)이
   `7724 of 9127`(84.6%)을 덮었다. 이제 블록별로 분모를 파싱해 `NC_MIN_N` 미만 블록은
   표에서 빼고 증거로만 남긴다.
9. **CAS 로 찾으면 동일성이 확정된 줄 알았다.** PubChem 의 `/compound/name/` 은 CAS 를
   그저 동의어로 취급하므로 UVCB·정유는 **구성 성분 하나**로 좁혀진다. `9000-24-2`
   갈바넘 오일 → CID 131750174, `458-37-7` 쿠르쿠마 오일 → CID 969516 쿠르쿠민,
   `39464-70-5` → CID 62906 2-페녹시에탄올. 동의어 대조로는 못 잡는다(세 건 모두
   통과한다) — 이름이 혼합물을 가리키면 보류하는 `MIXTURE` 규칙으로 막는다
   (실측 94 종 보류, 그중 값이 실제로 있던 것은 6 종 · 8 성분행).
10. **임포트만으로 로그 파일이 지워졌다.** `L.make_logger` 가 `open(path, "w")` 라
   모듈 최상위에서 부르면 임포트 시점에 기존 로그가 0 바이트가 된다. 실제로 검수
   도구가 `import collect_ing_ghs` 만 하고 앞선 조사 로그를 날렸다. 로그는 `main()`
   이 처음 쓸 때 연다.

## 값을 만들지 않는다

- tier1·tier2 가 모두 없으면 결측. 이름 유사도나 유사물질로 추정하지 않는다.
- tier2 합의율이 `AGREE_MIN` 미만이면 결측.
- **조회 실패는 결측이고 NC 가 아니다.** `--재시도실패분` 으로 다시 돌린다.
- `NC` 는 tier 블록이 존재하고 **다른 유해성 코드를 하나 이상 신고했는데** 해당
  엔드포인트 코드가 없을 때만 적는다. 그 물질은 분류 평가를 받았고 이 엔드포인트에
  해당하지 않았다는 뜻이다. tier2 의 NC 에는 한계가 있다 — ECHA 집계는 **10% 초과
  코드만 표시**하므로(응답에 명시) 10% 이하로 신고된 유해성은 보이지 않는다. 그래서
  `nc_출처tier` 를 남겨 하류에서 tier2 유래 NC 만 배제할 수 있게 한다.
- 응답 원문을 캐시에 남긴다. 재실행이 무료이고 검수자가 같은 입력을 다시 본다.

## 쓰는 법

    python3 collect_ing_ghs.py                # 전량
    python3 collect_ing_ghs.py --limit 8      # 스모크
    python3 collect_ing_ghs.py --재시도실패분   # 실패분만 다시
    python3 collect_ing_ghs.py --no-net       # 캐시만으로 재파싱(파서 수정 검증용)

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx. 기존 산출물·`05_원본보관_*`
은 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

import lib_model as L

OUT = L.ROOT / "04_모델산출물" / "v8_성분조사"
CACHE = OUT / "캐시"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
_LOG = None


def log(m):
    """로그 파일을 **`main()` 이 부를 때** 연다.

    `L.make_logger` 는 `open(path, "w")` 라서 모듈을 임포트하는 순간 기존 로그를
    0 바이트로 지운다. 실제로 검수 도구가 `import collect_ing_ghs` 만 하고도 앞선
    조사 로그를 통째로 날렸다(복구 불가 — 추적 대상이 아닌 파일이다). 임포트는
    부작용이 없어야 한다.
    """
    global _LOG
    if _LOG is None:
        _LOG = L.make_logger(OUT / "조사.log")
    _LOG(m)

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest"
UA = "crop-protection-ghs-research/1.0 (academic)"
# PUG-View 는 PUG 보다 훨씬 쉽게 ServerBusy 를 낸다. 실측에서 0.25s 간격은 거의 매
# 요청이 503 이었다. 권고는 5 req/s 지만 실제 여력에 맞춰 크게 물러선다.
SLEEP_VIEW = 1.2
SLEEP_PUG = 0.3
RETRY = 6
AGREE_MIN = 50.0          # tier2 최다 신고 구분의 하한(%). 과반
# 다른 **심각도**의 구분이 이 비율 이상 신고되면 규제적 이견으로 보아 판정을 보류한다.
# 합의율 하한만 올리지 않는 이유: NC 는 무코드·명시 경로로 들어와 합의율 심사를 받지
# 않으므로, 하한을 올리면 양성만 골라 탈락시켜 가산합이 계통적으로 낮아진다(A2 증강
# 비대칭과 방향만 반대인 같은 오염). 경합 자체를 조건으로 삼으면 대칭이 유지된다.
# 실측 근거: CID 162217 은 H318(구분1) 43.8% 대 H319(구분2A) 50.3% 로 갈리는데,
# 가산식에서 구분1 은 구분2 의 10 배 가중이다(`add = 10*s1 + s2`). 근소다수로 확정하면
# 43.8% 의 반대 의견이 기록 없이 사라진다.
CONTEST_MIN = 20.0
# 명시 미분류 블록이 한 표를 얻는 데 필요한 최소 분모(신고 기업 수). ECHA 는 분모가
# 1 인 블록도 `Reported as not meeting GHS hazard criteria by 1 of 1 companies` 로
# 100% 로 준다(실측 CID 1254·24261). 이것을 분모 9127 블록과 같은 한 표로 세면 신고
# 1 건이 판정을 뒤집는다. 하한 미달 블록은 증거(`*_후보`)로만 남기고 표에서 뺀다.
NC_MIN_N = 10.0

FAIL = "__조회실패__"      # 조회 실패 표식. 데이터 부재(None)와 구별한다
TODAY = datetime.date.today().isoformat()   # 값마다 조회일자를 남긴다(추적성)

TIER1 = "Regulation (EC) No 1272/2008"          # CLP Annex VI 조화분류
TIER2 = "European Chemicals Agency (ECHA)"      # C&L 신고 집계

# 채울 수 없는 토큰. 화학 실체가 아니라 SDS 의 잔여·총칭 표기다.
# 단어 경계를 준다 — `other` 를 부분일치로 두면 화합물명에 걸린다.
NONCHEM = re.compile(
    r"(?:^|[\s,/()-])(?:other|inert|proprietary|confidential|balance|remainder|"
    r"n/?a)(?:$|[\s,/()-])|trade\s*secret|ingredients?\s*$|"
    r"not\s*(?:listed|disclosed|available)|기타|비활성|영업비밀", re.I)

# 추출 잔여물. 성분명이 아니라 SDS 본문 문장·노출한계표·제품명이 키 자리에 들어온
# 것이다. 실측 예: 'Xylene () 100 ppm TWA 100 ppm TWA; 150 …',
# 'This product does not contain candidate substances …', '15-5-10 FERTILIZER
# PLUS RONSTAR', 'PARAFFINS) ambientlevelsto(cid:3),hypoxia…'.
# **실측: 이 규칙에 걸리는 68 종 가운데 조회 성공은 0 종**이므로 커버리지 손실 없이
# 뺀다. 임계(어절 6·길이 48)도 그 실측으로 정했다.
JUNK = re.compile(r"ppm|\bTWA\b|\(cid:|성분\s*아님|조성표|정보\s*없음|[0-9]\s*%|"
                  r"\.{4,}|\bIARC\b|\bthis\s+product\b|\bshall\b|\bthe\s|\bbe\s",
                  re.I)


def junk_key(s):
    """문장·표 조각 판정. 정규식 + 어절수·길이."""
    t = s.astype(str)
    return (t.str.contains(JUNK, na=True) | (t.str.split().str.len() >= 6)
            | (t.str.len() > 48))


# **단일 화합물이 아님을 이름이 스스로 밝히는 경우.** UVCB·정유·추출물·에톡실레이트
# 계열은 조성이 가변인 혼합물이라 CID 하나로 대표될 수 없다. 그런데 PubChem 은 이들의
# CAS 를 **구성 성분 하나의 동의어로** 등재해 두어서, CAS 로 조회하면 그 단일 화합물이
# 돌아오고 동의어 대조도 통과한다. 실측 오염 3 건이 전부 이 경로였다:
#   `9000-24-2` Galbanum oil(UVCB) → CID 131750174 → 눈 NC · 피부 NC
#   `458-37-7`  Curcuma longa Root Oil → CID 969516 쿠르쿠민 → 눈 2A
#   `39464-70-5` Phenol ethoxylate phosphate ester → CID 62906 2-페녹시에탄올 → 눈 2A
# 동의어 일치는 이것을 걸러내지 못하므로(세 건 모두 `CAS_동의어일치`) 이름 쪽에서
# 막는다. 단일 화합물 동일성 주장과 이름이 모순되면 값을 확정하지 않는다.
MIXTURE = re.compile(
    r"\bUVCB\b|\bessential\s+oil\b|\boil\b(?!\s*[-–]?\s*soluble)|\bextract\b|"
    r"\bdistillate\b|\bresin\b|\boleoresin\b|\bethoxylat|\bpropoxylat|"
    r"\balkoxylat|\bpolyethylene\s+glycol\b|\bPEG[-\s]?\d|\bpolysorbate\b|"
    r"\bnaphtha\b|\bpetroleum\b|\bkerosene\b|\bparaffin\b|\bwax\b|\btar\b|"
    r"\bclay\b|\bbentonite\b|\bkaolin\b|\battapulgite\b|\bsilicate\b|"
    r"\bhydrotreated\b|\bsolvent\s+naphtha\b|\bmixture\b|\bblend\b|"
    r"\bethoxylated\b|\bcondensate\b|\bcopolymer\b|\bpolymer\b",
    re.I)

# H 코드 → 구분. eye 는 H319/H320 이 있어 2A/2B 가 구별된다(CLP 약어로는 불가).
#
# **눈에 H314 를 넣는다.** CLP Annex I 3.3.2.2 — 피부 부식성(Skin Corr. 1)으로
# 분류된 물질은 **심한 눈 손상(Eye Dam. 1)을 일으키는 것으로 본다**. 그래서 강산·
# 강염기는 H314 만 달고 H318 을 따로 달지 않는다. 앞판은 눈 코드만 찾다가 이것을
# 전부 놓쳐 **조화분류에 H314 가 있는 물질을 눈 NC(무해)로 확정했다** — 실측:
# 질산(CID 944) tier1 H314 → 눈 NC, 암모니아(CID 222) tier1 H314 → 눈 NC,
# 황산도 같다. 기존 성분 MSDS 선언값은 이들을 눈 구분 1 로 적어 놓았고(검수 M2
# '`MSDS 가 높음`' 9 건의 정체가 대부분 이것이다), MSDS 쪽이 맞았다.
H_MAP = {"eye": {"H318": "1", "H319": "2A", "H320": "2B", "H314": "1"},
         "skin": {"H314": "1", "H315": "2", "H316": "3"}}
SEV = {"eye": {"1": 3, "2A": 2, "2": 2, "2B": 1, "NC": 0},
       "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 3, "3": 2, "NC": 0}}
# 'H318 (> 99.9%): ...' / 'H315: ...' 둘 다 받는다. `>`·`<` 접두를 허용하는 것이
# 앞판이 Eye Dam. 1 을 놓친 지점이다.
H_RE = re.compile(r"\b(H\d{3})\b(?:\s*\(\s*[<>]?\s*([\d.]+)\s*%\s*\))?")


def _cache_path(kind: str, key: str):
    h = hashlib.sha1(f"{kind}:{key}".encode()).hexdigest()[:24]
    return CACHE / f"{kind}_{h}.json"


def fetch(url: str, kind: str, key: str, use_net: bool, sleep: float):
    """캐시 우선 GET. 성공 응답만 캐시한다. 실패는 FAIL 을 돌려준다.

    실패를 캐시하지 않으므로 재실행하면 실패분만 다시 조회된다. 404 는 '없음이 확정'
    이라 `null` 로 캐시해 재조회를 막는다.
    """
    p = _cache_path(kind, key)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            p.unlink()                              # 손상 캐시는 버린다
    if not use_net:
        return FAIL
    last = None
    for i in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode("utf-8"))
            # 과부하를 HTTP 200 + Fault 본문으로 돌려주는 경로. 실측 확인했다.
            # 이것을 캐시하면 영구히 '데이터 없음' 이 된다.
            if isinstance(d, dict) and "Fault" in d:
                last = "Fault:" + str(d["Fault"].get("Code"))
                time.sleep(2.5 * (i + 1))
                continue
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            time.sleep(sleep)
            return d
        except urllib.error.HTTPError as e:
            if e.code == 404:                       # 없는 것이 확정된 경우
                p.write_text("null", encoding="utf-8")
                time.sleep(sleep)
                return None
            last = f"HTTP {e.code}"
            time.sleep(2.5 * (i + 1))
        except Exception as e:                      # noqa: BLE001
            last = repr(e)[:80]
            time.sleep(2.0 * (i + 1))
    log(f"    ! 조회실패 {kind}:{key} — {last}")
    return FAIL


# `ing_name_best` 에는 농도 범위가 섞여 들어와 있다. 실측 예:
# 'propane-,2-diol propane-1,2-diol >= 5 - < 10', '2-Benzisothiazol-3(2H)-one
# >= 0.005 - <='. 이 상태로 조회하면 이름 fallback 이 통째로 실패한다.
# 잘라내기만 한다 — 이름을 새로 만들지 않는다.
NAME_JUNK = re.compile(r"\s*(?:[<>]=?|~|≥|≤)\s*[\d.]+.*$|\s*\d+(?:\.\d+)?\s*[-–]\s*"
                       r"\d+(?:\.\d+)?\s*%?\s*$|\s*\(\s*\)\s*$")


def clean_name(s):
    """조회용 이름 정리. 농도 범위 꼬리를 떼고 공백을 정규화한다."""
    if not s or str(s).strip().lower() in ("nan", "none", ""):
        return None
    t = re.sub(r"\s+", " ", str(s)).strip()
    for _ in range(3):                      # '>= 5 - < 10' 처럼 겹친 꼬리
        t2 = NAME_JUNK.sub("", t).strip(" ,;-")
        if t2 == t:
            break
        t = t2
    return t or None


def cid_of(cas, name, use_net):
    """CAS 우선, 이름 차선. 어느 쪽으로 찾았는지와 후보 수를 함께 돌려준다.

    PubChem 의 `/compound/name/` 은 CAS 번호도 동의어로 받는다. 별도 `/cas/` 종점은
    없다. 후보가 여럿이면 첫 CID 를 쓰되 그 개수를 남겨 검수에서 걸러낼 수 있게 한다.
    """
    for kind, q in (("cid_cas", cas), ("cid_name", clean_name(name))):
        if not q or str(q).strip().lower() in ("nan", "none", ""):
            continue
        q = str(q).strip()
        d = fetch(f"{BASE}/pug/compound/name/"
                  f"{urllib.parse.quote(q, safe='')}/cids/JSON",
                  kind, q, use_net, SLEEP_PUG)
        if d is FAIL:
            return FAIL, None, q, None
        cids = (d or {}).get("IdentifierList", {}).get("CID") or []
        if cids:
            return (int(cids[0]), "CAS" if kind == "cid_cas" else "이름", q,
                    cids)
    return None, None, None, None


def _norm(s):
    """이름 대조용 정규화. 대소문자·공백·하이픈·괄호만 지운다 — 글자는 안 건드린다."""
    return re.sub(r"[\s\-–—_,'\"()\[\]]+", "", str(s)).lower()


def 이름동일성(cid: int, q: str, use_net: bool):
    """이름 경로로 찾은 CID 가 정말 그 이름의 물질인지 동의어 목록으로 확인한다.

    이름 경로를 전부 보류하면 `Citronellol`·`Cyclohexanone` 처럼 이름이 유일한
    물질까지 함께 잃는다(실측 58 종). 반대로 전부 받으면 클래스명 `Glycol ether`
    나 잘린 이름 `Propylene glycol monomethyl` 이 임의의 한 멤버로 좁혀진다.
    **질의한 이름이 그 CID 의 동의어에 정확히 있는지**로 두 경우를 가른다 —
    추정이 아니라 PubChem 자신의 대조표를 쓴다.

    **이 검사는 필요조건이고 충분조건이 아니다.** 실측 확인: `Glycol ether` 는
    CID 8117(2-에톡시에탄올)의 동의어로 실제 등재돼 있고 `Propylene glycol
    monomethyl` 도 CID 11008049 의 동의어다 — 둘 다 이 검사를 통과한다. PubChem
    동의어 목록에는 클래스명·상품명이 섞여 있다. 그래서 통과를 '동일성 확정' 이
    아니라 **등급**으로만 기록하고, 이름 경로 값이 실제로 덜 맞는지는 `--대조군`
    으로 재서 판단한다(검수 C1).

    반환: (동의어 일치 여부, 사유). 조회 실패는 일치로 보지 않는다.
    """
    d = fetch(f"{BASE}/pug/compound/cid/{cid}/synonyms/JSON",
              "syn", str(cid), use_net, SLEEP_PUG)
    if d is FAIL:
        return False, "동의어조회실패"
    syn = (((d or {}).get("InformationList", {}).get("Information") or [{}])[0]
           .get("Synonym") or [])
    if not syn:
        return False, "동의어없음"
    return ((True, None) if _norm(q) in {_norm(s) for s in syn}
            else (False, "이름_동의어불일치"))


def hazard_blocks(cid: int, use_net: bool):
    """GHS Classification 절을 출처(tier)별 H 코드 집합으로 갈라 돌려준다.

    반환: {tier이름: {"src": SourceName, "codes": {H코드: 합의율 or None}}}
    """
    d = fetch(f"{BASE}/pug_view/data/compound/{cid}/JSON"
              f"?heading=GHS+Classification", "ghs", str(cid), use_net,
              SLEEP_VIEW)
    if d is FAIL:
        return FAIL
    if not d:
        return {}
    rec = d.get("Record", {})
    src = {r.get("ReferenceNumber"): (r.get("SourceName") or "")
           for r in rec.get("Reference", [])}
    per: dict[int, dict] = {}

    def walk(node):
        for s in node.get("Section", []):
            for inf in s.get("Information", []):
                if (inf.get("Name") or "") != "GHS Hazard Statements":
                    continue
                ref = inf.get("ReferenceNumber")
                slot = per.setdefault(ref, {"ref": ref, "src": src.get(ref, ""),
                                           "codes": {}, "미분류율": None,
                                           "미분류분모": None})
                for x in inf.get("Value", {}).get("StringWithMarkup", []):
                    s_ = x.get("String") or ""
                    for code, pct in H_RE.findall(s_):
                        p = float(pct) if pct else None
                        # 같은 블록에 같은 코드가 두 번 오면 **덮지 않는다**. 앞판은
                        # 마지막 값이 이겨서 실측 38 건이 조용히 한 값으로 줄었다.
                        prev = slot["codes"].get(code)
                        slot["codes"][code] = (p if prev is None else
                                               max(prev, p) if p is not None
                                               else prev)
                    # ECHA 는 무해 신고가 지배적일 때 H 코드 대신 'Not Classified'
                    # 문자열과 신고 비율을 준다. 실측(CID 1030, 프로필렌글리콜):
                    # 'Not Classified' + 'by 6762 of 6899 companies' = 98%.
                    # H 코드만 찾으면 근거가 가장 강한 NC 를 통째로 놓친다.
                    if re.search(r"not\s+classified", s_, re.I):
                        slot["미분류율"] = slot["미분류율"] or 0.0
                    m = re.search(r"not\s+meeting\s+GHS\s+hazard\s+criteria\s+by"
                                  r"\s+([\d,]+)\s+of\s+([\d,]+)", s_, re.I)
                    if m:
                        a_ = float(m.group(1).replace(",", ""))
                        b_ = float(m.group(2).replace(",", ""))
                        if b_:
                            slot["미분류율"] = 100.0 * a_ / b_
                            slot["미분류분모"] = b_
            walk(s)

    walk(rec)
    out = {}
    for ref, blk in per.items():
        s = blk["src"]
        tier = ("tier1_AnnexVI" if s.startswith(TIER1) else
                "tier2_ECHA_CL" if s.startswith(TIER2) else f"참고_{s[:40]}")
        # **블록을 병합하지 않는다.** 같은 `SourceName` 으로 여러 `ReferenceNumber`
        # 가 오는데(실측 92 종) 앞판은 `codes.update()` 로 합쳐 **JSON 순서상 마지막
        # 블록이 이기게** 만들었다. 멘톨(CID 1254)의 눈 H319 는 블록별로 82.9% ·
        # 13.3% · 43% 이고, 어느 것이 남는지가 판정을 2A 냐 결측이냐로 가른다 —
        # 순서에 판정을 맡긴 것이다. 이제 블록을 리스트로 보존하고 `decide` 가
        # 블록 간 이견을 이견으로 처리한다. `codes`·`미분류율` 은 표시·교차검증용
        # 요약이며 판정에 쓰지 않는다.
        o = out.setdefault(tier, {"src": s, "codes": {}, "미분류율": None,
                                  "미분류분모": None, "blocks": []})
        o["blocks"].append(blk)
        for c, p in blk["codes"].items():           # 표시용 요약 = 코드별 최대 %
            q = o["codes"].get(c)
            o["codes"][c] = (p if q is None else
                             max(q, p) if p is not None else q)
        # 요약 미분류율은 **분모가 가장 큰** 블록에서 취한다. 앞판은 마지막 블록을
        # 취해 `1 of 1 companies` 가 `7724 of 9127` 을 이겼다(실측 CID 24261)
        if blk["미분류율"] is not None and (
                o["미분류율"] is None
                or (blk["미분류분모"] or 0) >= (o["미분류분모"] or 0)):
            o["미분류율"], o["미분류분모"] = blk["미분류율"], blk["미분류분모"]
    return out


def decide(ep: str, blocks: dict):
    """한 엔드포인트의 구분을 정한다. 반환 (구분, 근거, 출처tier, 합의율).

    **tier1 은 양성 확정에만 쓰고 부재를 NC 로 읽지 않는다.** Annex VI 는 완전한
    분류 목록이 아니라 EU 가 조화한 엔드포인트만 싣는 부분 목록이다. 눈 항목이
    없다는 것은 '눈 자극이 없다' 가 아니라 '눈은 조화 대상이 아니었다' 는 뜻이다.
    실측으로 확인했다 — tebuconazole(제형 74 개에 등장)의 Annex VI 항목에는 눈
    코드가 없지만 다른 출처는 H320(눈 2B)을 준다. 부재를 NC 로 읽으면 최다 빈출
    성분을 무해로 확정하게 된다.

    tier2(ECHA C&L 신고)는 신고자가 자기 물질의 전 엔드포인트를 분류해 올리므로
    부재가 정보를 갖는다. 그래서 NC 는 tier2 에서만 유도한다.
    """
    table = H_MAP[ep]
    b1 = blocks.get("tier1_AnnexVI")
    b2 = blocks.get("tier2_ECHA_CL")
    if b1 and b1["codes"]:
        # **tier1 도 블록별로 판정한다.** 이 모듈은 `codes`(코드별 블록간 MAX 병합
        # 요약)를 "표시·교차검증용이며 판정에 쓰지 않는다" 고 스스로 적어 놨는데
        # (`hazard_blocks` 주석), tier1 분기만 그 요약을 쓰고 있었다. tier2 는 이미
        # 블록별 판정으로 고쳤다. 여기서도 같게 맞춘다.
        #
        # **왜 중요한가 — 두 오염을 갈라야 한다.** PubChem 은 한 CID 아래 조화분류
        # 여러 항목(모체·염·유도체)을 싣고, 그것이 **여러 블록**으로 오기도 하고
        # **한 블록으로 합쳐**지기도 한다. 병합 요약만 보면 둘을 구별할 수 없다.
        #   · 블록끼리 결론이 다르다 → 여러 항목이 섞였다. 어느 것이 이 물질 것인지
        #     정할 근거가 없으므로 **보류(결측)**. 실측: 이소프로판올(CID 3776)은
        #     blk0 {H225,H319,H336}(→눈 2A) · blk1 {H318,…}(→눈 1) 로 갈린다.
        #     앞판은 병합해 눈 구분 1 로 확정했고, 그 값이 제형 2 개에 들어갔다.
        #     이소프로판올의 조화분류는 Eye Irrit. 2 다.
        #   · 한 블록 안에 심각도 다른 코드가 있다 → 이것은 **정당할 수 있다**.
        #     Annex VI 는 특정농도한계(SCL)로 한 항목에 두 구분을 싣는다(예:
        #     과산화수소 = Skin Corr. 1A + Skin Irrit. 2). 그래서 블록 안에서는
        #     보류하지 않고 **최고 심각도**를 택한다. 앞판의 R1 은 이 경우까지
        #     결측으로 버렸을 것이다 — 현재 캐시 529 CID 에 해당 사례가 없어
        #     드러나지 않았을 뿐이다.
        blk1 = b1.get("blocks") or [b1]
        판정, 근거들 = {}, []
        for blk in blk1:
            hit = [c for c in (blk.get("codes") or {}) if c in table]
            # **눈은 직접 코드가 있으면 H314 추론을 쓰지 않는다.** H318/H319/H320 은
            # 눈을 직접 분류한 코드이고, H314→눈 구분 1 은 Annex I 3.3.2.2 로
            # **유도한** 값이다. 직접 분류가 있는데 유도값을 섞으면 아래 오염 검사가
            # 엉뚱하게 유도값을 놓고 판단한다 — 2-페닐페놀은 조화분류가 Eye Dam.
            # 1(H318)인데 같은 블록의 H314 가 승자로 뽑혀 보류될 뻔했다
            직접 = [c for c in hit if not (ep == "eye" and c == "H314")]
            if 직접:
                hit = 직접
            if not hit:
                continue                      # 이 엔드포인트를 안 실은 블록
            c = max(hit, key=lambda x: SEV[ep][table[x]])
            판정.setdefault(table[c], c)
            근거들.append(c)
        if len(판정) > 1:
            return (None,
                    f"tier1 블록간 결론 불일치({' vs '.join(f'{v}({k})' for v, k in 판정.items())}) "
                    f"— 한 CID 에 여러 조화분류 항목이 실렸다 — 결측",
                    "tier1_AnnexVI", None)
        if 판정:
            v, c = next(iter(판정.items()))
            # **R2 교차 증거 — 피부 부식(H314) 에만** 적용한다. tier1 이 H314 를
            # 주는데 tier2 신고자가 H314 를 **한 명도** 올리지 않고 과반이
            # H315(자극 — H314 와 양립 불가)를 올렸다면 그 H314 는 이 물질 것이
            # 아니다. 실측: 2-페닐페놀(CID 7017) 단일 블록의 H314 가 피부 구분 1 을
            # 만드는데 ECHA 신고자 100% 가 H315 를 올린다(조화분류는 Skin Irrit. 2).
            # 다른 코드로 넓히지 않는다 — 넓히면 같은 물질의 눈처럼 **조화분류
            # (H318)를 신고(H319 97.3%)로 뒤집는** 일이 생기고, 그것은 조화분류의
            # 취지에 반한다.
            t2c = (b2 or {}).get("codes") or {}
            if (c == "H314" and "H314" not in t2c
                    and (t2c.get("H315") or 0) >= AGREE_MIN):
                return (None,
                        f"tier1 H314(부식)을 tier2 신고자가 아무도 올리지 않고 "
                        f"{t2c['H315']}% 가 양립 불가한 H315(자극)를 올린다 — "
                        f"합쳐진 블록 의심 — 결측", "tier1_AnnexVI", None)
            return v, f"tier1_AnnexVI_H코드({c})", "tier1_AnnexVI", None
        # 부재 → NC 로 읽지 않고 tier2 로 내려간다
    if b2 and b2.get("blocks"):
        # **블록마다 따로 판정하고 결론이 갈리면 결측으로 둔다.** ECHA C&L 은 같은
        # 물질에 여러 신고 집계 블록을 주고(분모가 각각 다르다) 블록끼리 결론이
        # 어긋나는 일이 실제로 있다 — 이산화규소(CID 24261)는 눈 H319 를 한 블록은
        # 56.3%, 다른 블록은 16.7% 로 신고하고 또 다른 블록들은 명시 미분류를 준다.
        # 어느 하나를 골라 확정하면 나머지 신고를 근거 없이 버리는 것이므로, 이견은
        # 이견으로 기록하고 값을 만들지 않는다.
        표 = []                     # (블록 판정, 합의율, 설명)
        유해신고 = False             # 이 엔드포인트의 유해 코드를 올린 블록이 있는가
        for blk in b2["blocks"]:
            hit = {c: p for c, p in blk["codes"].items() if c in table}
            유해신고 |= bool(hit)
            if hit:
                top = max(hit, key=lambda c: (hit[c] or 0))
                p_ = hit[top]
                if (p_ or 0) >= AGREE_MIN:
                    # 같은 블록 안 다른 심각도의 이견
                    경합 = [c for c in hit if c != top
                          and SEV[ep][table[c]] != SEV[ep][table[top]]
                          and (hit[c] or 0) >= CONTEST_MIN]
                    if 경합:
                        d_ = ", ".join(f"{c} {hit[c]}%" for c in [top] + 경합)
                        표.append((None, p_, f"블록내경합({d_})"))
                    else:
                        표.append((table[top], p_, f"H코드({top}@{p_}%)"))
                else:
                    # 과반 미달은 **표를 주지 않는다**(결측 쪽). 이 블록의 신고자
                    # 다수가 분류하지 않았으니 `NC` 로 읽자는 안을 실제로 넣어서
                    # 재봤고, **정확도가 떨어졌다** — MSDS 유래 정답 대조에서 눈
                    # 일치율 93.4% → 87.6%, 불일치가 과소 10 대 과대 1 로 한쪽으로
                    # 쏠렸다(검수 C1). ECHA 가 10% 초과 코드만 표시하는 탓에 '과반이
                    # 아니다' 와 '무해다' 사이 간격을 메울 근거가 없다. 값을 만들지
                    # 않는다는 규약대로 결측으로 둔다
                    표.append((None, p_, f"미달({top}@{p_}%)"))
            elif blk["codes"]:
                # 다른 유해성은 올렸는데 이 엔드포인트는 없다 → 평가받고 미해당
                표.append(("NC", None, "무코드"))
            elif blk["미분류율"] is not None:
                r_, n_ = blk["미분류율"], blk["미분류분모"]
                if (n_ or 0) < NC_MIN_N:
                    # `1 of 1 companies` 짜리 블록을 분모 9127 블록과 동등한 한 표로
                    # 세면 표본 1 개가 판정을 뒤집는다. 증거로만 남긴다
                    표.append((None, r_, f"미분류@{r_:.0f}%·분모{n_ or 0:g}<{NC_MIN_N:g}"))
                elif r_ >= AGREE_MIN:
                    표.append(("NC", r_, f"명시미분류@{r_:.1f}%(n={n_:g})"))
                else:
                    표.append((None, r_, f"미분류율{r_:.1f}%<{AGREE_MIN}%"))
        확정 = {v for v, _, _ in 표 if v is not None}
        설명 = " | ".join(f"{i+1}:{s}" for i, (_, _, s) in enumerate(표))
        if len(확정) > 1:
            return (None, f"tier2 블록간 결론 불일치({설명}) — 결측",
                    "tier2_ECHA_CL", None)
        # **유해 신고가 있는데 `NC` 로 확정하지 않는다.** 앞판은 유해 코드를 올린
        # 블록이 등급을 못 정해 표를 안 주면, 남은 '무코드' 블록 하나가 단독으로
        # **무해를 확정**했다. 실측: 라우릴황산나트륨(CID 3423265)은 한 블록이 눈
        # H318 52.3% · H319 39.8% — 신고자 92% 가 눈에 유해하다는데 1 이냐 2A 냐가
        # 갈려 표가 버려지고, `H302` 만 있는 다른 블록의 '무코드' 가 NC 를 확정했다.
        # 등급을 모르는 것과 무해한 것은 다르다. 결측으로 둔다
        if 확정 == {"NC"} and 유해신고:
            return (None,
                    f"tier2 유해 신고 있음 — NC 확정 거부({설명}) — 결측",
                    "tier2_ECHA_CL", None)
        if len(확정) == 1:
            v = 확정.pop()
            # 합의율은 그 결론을 낸 블록들의 최대값. 값을 평균해 만들지 않는다
            ps = [p for c, p, _ in 표 if c == v and p is not None]
            if v != "NC":
                근거 = f"tier2_ECHA_CL_H코드({설명})"
            elif any("명시미분류" in s for c, _, s in 표 if c == "NC"):
                근거 = "NC_tier2_명시_미분류"
            else:
                근거 = "NC_tier2_ECHA_CL_무코드"
            return v, 근거, "tier2_ECHA_CL", (max(ps) if ps else None)
        if 표:
            return (None, f"tier2 확정 블록 없음({설명}) — 결측",
                    "tier2_ECHA_CL", None)
    if b1 and b1["codes"]:
        return (None, "tier1 에 해당 엔드포인트 없음 · tier2 부재 — 결측",
                "tier1_AnnexVI", None)
    return None, "규제출처(AnnexVI·ECHA) 없음 — 결측", None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="상위 N 종만")
    ap.add_argument("--no-net", action="store_true", help="캐시만 사용")
    ap.add_argument("--재시도실패분", action="store_true",
                    help="이전 결과에서 조회실패인 종만 다시 조회")
    ap.add_argument("--대조군", type=int, default=0, metavar="N",
                    help="검수용. 이미 구분값을 가진 성분 상위 N 종을 같은 경로로 "
                         "조사해 기존값과 나란히 저장한다(성분GHS_대조조사.csv). "
                         "조사 경로가 기존 데이터를 재현하는지 재는 유일한 객관 측정")
    a = ap.parse_args()
    use_net = not a.no_net

    log("=== 성분 GHS 구분 조사 (CLP Annex VI → ECHA C&L) ===")
    log(f"tier1 '{TIER1[:34]}…' → tier2 '{TIER2}' (합의율 ≥{AGREE_MIN}%) "
        f"· 네트워크 {'사용' if use_net else '미사용'}")
    data = L.FormulationData(log)
    I = data.ING

    def has(s):
        return s.notna() & (~s.astype(str).str.strip().str.lower()
                            .isin(["nan", "none", ""]))

    # 조사 대상 = 농도가 있고, 눈·피부 중 **하나라도** 구분이 없는 성분행.
    # 두 엔드포인트를 AND 로 걸면 한쪽만 채워진 성분이 대상에서 빠진다.
    pct = has(I["ing_pct_best"])
    부족 = pd.Series(False, index=I.index)
    보유 = pd.Series(True, index=I.index)
    for ep in ("eye", "skin"):
        있 = has(I[f"ing_ghs_{ep}"]) | has(I[f"ing_ghs_indep_{ep}_cat"])
        부족 |= ~있
        보유 &= 있
    if a.대조군:
        # 검수용 대조군 — 이미 두 엔드포인트 값을 다 가진 성분을 같은 경로로 조사한다
        T = I[pct & 보유].copy()
        log(f"[대조군 모드] 기존값 보유 성분행 {len(T)} 에서 상위 {a.대조군}종 조사")
    else:
        T = I[pct & 부족].copy()
    T["키"] = T["ing_cas_best"].where(has(T["ing_cas_best"]),
                                     T["ing_name_best"])
    T = T[T["키"].notna()]
    비화학 = T["키"].astype(str).str.contains(NONCHEM, na=True)
    문장 = junk_key(T["키"]) & ~비화학
    제외 = 비화학 | 문장
    log(f"조사 대상 성분행 {len(T)} → 고유 {T['키'].nunique()}종 "
        f"(비화학 토큰 {int(비화학.sum())}행 / {T[비화학]['키'].nunique()}종, "
        f"문장·표 조각 {int(문장.sum())}행 / {T[문장]['키'].nunique()}종 제외)")
    # 두 제외 목록을 따로 남긴다 — 근거가 다르므로 검수에서 따로 볼 수 있어야 한다.
    # **대조군 실행은 파일명을 갈라 쓴다.** 대조군은 기존값 보유 성분(깨끗한 CAS)만
    # 보므로 제외가 0 종이고, 본조사 뒤에 대조군을 돌리면 실측 38·68 종이 적힌
    # 목록이 **헤더만 남은 파일로 덮였다**. 요약 JSON 도 같은 사고를 당했다
    꼬리 = "_대조군" if a.대조군 else ""
    for 이름, m in ((f"제외_비화학토큰{꼬리}.csv", 비화학),
                   (f"제외_문장표조각{꼬리}.csv", 문장)):
        (T[m].groupby("키").agg(성분행수=("Formulation_ID", "size"))
         .sort_values("성분행수", ascending=False)
         .to_csv(OUT / 이름, encoding="utf-8-sig"))

    for ep in ("eye", "skin"):
        # 기존값 = A0 구분 우선, 없으면 A2 독립조사 구분
        T[f"기존_{ep}"] = T[f"ing_ghs_{ep}"].where(
            has(T[f"ing_ghs_{ep}"]), T[f"ing_ghs_indep_{ep}_cat"])
        # 정답의 출처를 함께 남긴다. `indep` 는 annex_vi·echa_cl 유래이므로 이 조사와
        # **같은 출처**다 — 그 부분 대조는 정확도 검정이 아니라 순환(재현성 확인)이다.
        # 독립 검정이 되는 것은 성분 MSDS 유래(`ing_ghs_*`) 쪽뿐이다.
        T[f"기존_{ep}_출처"] = np.where(
            has(T[f"ing_ghs_{ep}"]), "MSDS유래",
            np.where(has(T[f"ing_ghs_indep_{ep}_cat"]), "독립조사유래", None))
        # 정답의 tier 도 남긴다. `annex_vi` 유래 `NC` 는 **부재를 NC 로 읽던 앞판의
        # 결함**(모듈 독스트링 5번)에서 나온 값이므로 정답으로 쓸 수 없다. 그것과의
        # 일치는 재현성이 아니라 같은 오류의 되풀이다 — 검수에서 갈라내게 한다
        T[f"기존_{ep}_tier"] = T[f"ing_ghs_indep_{ep}_tier"].where(
            ~has(T[f"ing_ghs_{ep}"]))
    G = (T[~제외].groupby("키")
         .agg(성분행수=("Formulation_ID", "size"),
              제형수=("Formulation_ID", "nunique"),
              cas_후보수=("ing_cas_best", "nunique"),
              이름_후보수=("ing_name_best", "nunique"),
              cas=("ing_cas_best", "first"), 이름=("ing_name_best", "first"),
              기존_eye=("기존_eye", "first"), 기존_skin=("기존_skin", "first"),
              기존_eye_출처=("기존_eye_출처", "first"),
              기존_skin_출처=("기존_skin_출처", "first"),
              기존_eye_tier=("기존_eye_tier", "first"),
              기존_skin_tier=("기존_skin_tier", "first"),
              기존_eye_후보수=("기존_eye", "nunique"),
              기존_skin_후보수=("기존_skin", "nunique"))
         .sort_values("성분행수", ascending=False))
    prev = OUT / ("성분GHS_대조조사.csv" if a.대조군 else "성분GHS_조사결과.csv")
    if a.재시도실패분 and prev.exists():
        P = pd.read_csv(prev)
        # CID없음(PubChem 미등재)은 재시도 대상이 아니다. 실패만 다시 돈다
        재 = set(P.loc[P["조회상태"].astype(str).str.contains("실패"),
                      "키"].astype(str))
        G = G[G.index.astype(str).isin(재)]
        log(f"재시도 대상 {len(G)}종 (이전 실패분)")
    if a.대조군:
        # **조회 경로별로 층화 추출한다.** 성분행수 상위 N 종을 그냥 자르면 CAS 를
        # 가진 성분이 앞을 채워 이름 경로가 실측 4 종밖에 안 들어왔고, 검수의
        # `len(g) < 10` 하한에 걸려 **이름 경로 정확도를 재려고 만든 대조군이 그것을
        # 재지 못했다**. 경로는 키가 CAS 인지 이름인지로 조회 전에 알 수 있다.
        cas키 = G["cas"].notna() & (G["cas"].astype(str).str.strip()
                                   .str.lower() != "nan")
        절반 = a.대조군 // 2
        이름분 = G[~cas키].head(a.대조군 - 절반)
        cas분 = G[cas키].head(a.대조군 - len(이름분))
        G = pd.concat([cas분, 이름분])
        log(f"[대조군 층화] CAS 키 {len(cas분)}종 + 이름 키 {len(이름분)}종 "
            f"= {len(G)}종 (경로별 정확도를 각각 재려면 층마다 표본이 필요하다)")
    elif a.limit:
        G = G.head(a.limit)
    log(f"조사 착수 {len(G)}종 (성분행 {int(G['성분행수'].sum())})")

    RES = []
    for i, (key, r) in enumerate(G.iterrows(), 1):
        cas = r["cas"] if pd.notna(r["cas"]) else None
        name = r["이름"] if pd.notna(r["이름"]) else None
        cid, via, q, cids = cid_of(cas, name, use_net)
        # 물질 동일성이 확정되지 않은 경로. 값을 자동 확정하지 않는다.
        # 실측 오염 2 건: (1) CAS 1322-93-6(나트륨염)이 후보 2 개를 돌려주고 첫
        # CID 는 **유리 술폰산**이다 — 산과 그 염은 눈·피부 등급이 통상 한 단계
        # 이상 다르다. (2) 이름 `Glycol ether`(클래스명)가 임의의 한 멤버
        # (디에틸렌글리콜)로 좁혀지고, 잘린 이름 `Propylene glycol monomethyl`
        # 은 전혀 다른 에스터로 해석된다.
        보류, 등급 = None, via
        # 이름이 혼합물·UVCB 를 가리키면 CID 하나로 대표할 수 없다. CAS 가 맞아도
        # 마찬가지다 — PubChem 이 그 CAS 를 구성 성분 하나의 동의어로 등재해 둔다
        혼합 = bool(name and MIXTURE.search(str(name)))
        if 혼합:
            보류, 등급 = "혼합물명", "보류_혼합물명"
        elif isinstance(cids, list) and len(cids) > 1:
            # 염과 유리산은 눈·피부 등급이 통상 한 단계 이상 다르다. 후보가 여럿일
            # 때 첫 CID 를 값으로 확정하지 않는다 — 실측 5 종/8 성분행뿐이다.
            보류, 등급 = f"다중CID{len(cids)}", "보류_다중CID"
        elif via == "이름" and cid not in (None, FAIL):
            일치, 사유 = 이름동일성(cid, q, use_net)
            if 일치:
                등급 = "이름_동의어일치"
            else:
                보류, 등급 = 사유, f"보류_{사유}"
        elif via == "CAS" and cid not in (None, FAIL):
            # **CAS 경로도 검증한다.** 앞판은 CAS 로 찾았으면 동일성이 확정된 것처럼
            # `"CAS"` 등급을 붙였는데, PubChem 의 `/compound/name/` 은 CAS 를 그저
            # 동의어로 취급하므로 UVCB·천연물은 임의의 성분 화합물로 좁혀진다.
            # 실측 오염: `9000-24-2`(갈바넘 오일, UVCB) · `458-37-7`(쿠르쿠마 오일)
            # → CID 969516 쿠르쿠민 · `39464-70-5`(페놀 에톡실레이트 인산 에스터)
            # → CID 62906 2-페녹시에탄올. 같은 대조표(동의어)로 되짚어 확인한다.
            일치, 사유 = 이름동일성(cid, q, use_net)
            if 일치:
                등급 = "CAS_동의어일치"
            elif 사유 in ("동의어조회실패", "동의어없음"):
                # **검증 조회가 실패한 것을 불일치로 읽지 않는다.** 그렇게 하면
                # `--no-net` 재파싱이 이미 확보한 CAS 경로 값을 전부 보류로 만든다.
                # 검증을 못 했다는 사실만 등급에 남기고 값은 건드리지 않는다
                등급 = "CAS_미검증"
            else:
                보류, 등급 = f"CAS_{사유}", f"보류_CAS_{사유}"
        rec = {"키": key, "cas": cas, "이름": name,
               "성분행수": int(r["성분행수"]), "제형수": int(r["제형수"]),
               "cas_후보수": int(r["cas_후보수"]),
               "이름_후보수": int(r["이름_후보수"]),
               "조회경로": via, "조회질의": q,
               "CID후보수": len(cids) if isinstance(cids, list) else None,
               "CID후보전체": ("|".join(map(str, cids[:8]))
                          if isinstance(cids, list) else None),
               # 등급은 값을 막지 않는다. 이름 경로 값을 통째로 뺄지는 `--대조군`
               # 정확도 실측으로 정한다 — 지금 임의로 자르지 않는다
               "동일성등급": 등급, "동일성보류": 보류,
               # 판정일자와 원문취득일자를 가른다. 캐시에서 읽은 값에 오늘 날짜를
               # 붙이면 실제로는 며칠 전 응답인데 오늘 조회한 것처럼 기록된다
               "판정일자": TODAY, "원문취득일자": None}
        if a.대조군:
            # 검수에서 조사값과 맞춰 볼 기존값. 키 안에서 값이 갈리면 후보수로 드러난다
            for ep in ("eye", "skin"):
                rec[f"기존_{ep}"] = (r[f"기존_{ep}"]
                                   if pd.notna(r[f"기존_{ep}"]) else None)
                rec[f"기존_{ep}_후보수"] = int(r[f"기존_{ep}_후보수"])
                rec[f"기존_{ep}_출처"] = r[f"기존_{ep}_출처"]
                rec[f"기존_{ep}_tier"] = (r[f"기존_{ep}_tier"]
                                        if pd.notna(r[f"기존_{ep}_tier"])
                                        else None)
        blocks = None
        if cid is FAIL:
            rec.update(cid=None, 조회상태="CID조회실패")
        elif cid is None:
            rec.update(cid=None, 조회상태="CID없음")
        else:
            blocks = hazard_blocks(cid, use_net)
            rec["cid"] = cid
            rec["출처URL"] = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
            # 원문 응답으로 되짚어갈 경로. 판정 근거를 재확인할 수 있게 남긴다
            cp = _cache_path("ghs", str(cid))
            rec["캐시파일"] = cp.name
            # 원문을 실제로 받은 날. 캐시 파일의 수정시각이 그 시점이다
            rec["원문취득일자"] = (
                datetime.date.fromtimestamp(cp.stat().st_mtime).isoformat()
                if cp.exists() else None)
            rec["조회상태"] = "GHS조회실패" if blocks is FAIL else "성공"
        for ep in ("eye", "skin"):
            if not isinstance(blocks, dict):
                # 조회 실패·CID 부재에서는 NC 를 유도하지 않는다
                rec[f"{ep}_구분"] = None
                rec[f"{ep}_근거"] = rec["조회상태"] + " — 결측 유지"
                rec[f"{ep}_출처tier"] = rec[f"{ep}_합의율"] = None
                rec[f"{ep}_교차불일치"] = rec[f"{ep}_참고출처값"] = None
                continue
            cat, 근거, tier, pctv = decide(ep, blocks)
            # 신고된 후보 전체를 원문 비율과 함께 남긴다 — 판정의 직접 증거이고,
            # 탈락한 반대 의견이 기록 없이 사라지지 않게 한다
            # 블록별로 남긴다. 병합 요약만 남기면 탈락한 반대 의견이 증거에서조차
            # 사라진다(멘톨 눈 82.9/13.3/43% 가 한 값으로 보였던 원인)
            증거 = []
            for t, blk in blocks.items():
                짧은 = t.replace("tier1_", "").replace("tier2_", "")
                for j, sub in enumerate(blk.get("blocks") or [blk]):
                    태 = f"{짧은}#{sub.get('ref', j)}"
                    for c, p in sub["codes"].items():
                        if c in H_MAP[ep]:
                            증거.append(f"{태}:{c}"
                                      + (f"@{p}%" if p is not None else ""))
                    r_ = sub.get("미분류율")
                    if r_ is not None:
                        n_ = sub.get("미분류분모")
                        # 율 0.0 은 'Not Classified 문구는 있는데 N of M 을 못 읽었다'
                        # 는 뜻이다. 0% 로 인쇄하면 '아무도 무해라 안 했다' 로 읽힌다
                        증거.append(f"{태}:미분류"
                                    + (f"@{r_:.1f}%(n={n_:g})" if n_
                                       else "@비율미상"))
            rec[f"{ep}_후보"] = "|".join(증거) or None
            if 보류 and cat is not None:
                # 물질 동일성이 미확정이면 값을 확정하지 않고 후보로만 남긴다
                rec[f"{ep}_보류값"] = cat
                rec[f"{ep}_구분"] = None
                rec[f"{ep}_근거"] = f"{보류} — 물질 동일성 미확정, 결측 유지"
                rec[f"{ep}_출처tier"] = tier
                rec[f"{ep}_합의율"] = pctv
                rec[f"{ep}_교차불일치"] = rec[f"{ep}_참고출처값"] = None
                continue
            rec[f"{ep}_구분"] = cat
            rec[f"{ep}_근거"] = 근거
            rec[f"{ep}_출처tier"] = tier
            rec[f"{ep}_합의율"] = pctv
            # 교차검증 — 값 선택에는 쓰지 않고 어긋남만 센다
            참고 = []
            for t, blk in blocks.items():
                if t.startswith("참고_") and blk["codes"]:
                    h = [H_MAP[ep][c] for c in blk["codes"] if c in H_MAP[ep]]
                    참고.append(max(h, key=lambda x: SEV[ep][x]) if h else "NC")
            rec[f"{ep}_참고출처값"] = "|".join(참고) or None
            rec[f"{ep}_교차불일치"] = (
                int(any(SEV[ep][v] != SEV[ep][cat] for v in 참고))
                if (참고 and cat) else None)
        RES.append(rec)
        if i % 25 == 0 or i == len(G):
            ok = sum(1 for x in RES if x.get("eye_구분") or x.get("skin_구분"))
            f_ = sum(1 for x in RES if "실패" in str(x.get("조회상태")))
            log(f"  [{i}/{len(G)}] 값확보 {ok}종 · 실패 {f_}종")

    R = pd.DataFrame(RES)
    if prev.exists() and (a.재시도실패분 or a.limit or a.대조군) and len(R):
        # 재시도·부분 실행은 이전 결과를 덮지 않고 키 기준으로 갱신한다
        P = pd.read_csv(prev)
        R = pd.concat([P[~P["키"].astype(str).isin(R["키"].astype(str))], R],
                      ignore_index=True)
    실패수 = int(R["조회상태"].astype(str).str.contains("실패").sum())
    저장 = prev
    if not use_net and 실패수:
        # 캐시 없는 키는 `--no-net` 에서 전부 조회실패가 된다. 그 상태로 정본을
        # 덮으면 이미 확보한 값이 결측으로 바뀐다. 별도 파일로 뺀다.
        저장 = prev.with_name(prev.stem + "_재파싱.csv")
        log(f"  ! --no-net 인데 캐시 미보유 {실패수}종 — 정본을 덮지 않고 "
            f"{저장.name} 로 저장한다")
    R.to_csv(저장, index=False, encoding="utf-8-sig")

    log("--- 조사 요약 ---")
    S = {"조사종수": int(len(R)),
         "조회상태": R["조회상태"].value_counts().astype(int).to_dict(),
         "동일성등급": R["동일성등급"].value_counts(dropna=False)
         .astype(int).to_dict()}
    for ep in ("eye", "skin"):
        확보 = R[f"{ep}_구분"].notna()
        t = R.loc[확보, f"{ep}_출처tier"].value_counts().astype(int).to_dict()
        S[f"{ep}_구분확보_종"] = int(확보.sum())
        S[f"{ep}_구분확보_성분행"] = int(R.loc[확보, "성분행수"].sum())
        S[f"{ep}_출처tier별"] = t
        S[f"{ep}_NC_종"] = int((R[f"{ep}_구분"] == "NC").sum())
        S[f"{ep}_NC_tier2유래"] = int(
            (R[f"{ep}_근거"] == "NC_tier2_ECHA_CL_무코드").sum())
        mm = R[f"{ep}_교차불일치"]
        S[f"{ep}_교차검증"] = {"대상": int(mm.notna().sum()),
                           "불일치": int((mm == 1).sum())}
        S[f"{ep}_값분포"] = (R[f"{ep}_구분"].value_counts(dropna=False)
                          .astype(int).to_dict())
        log(f"  {ep:4} 구분확보 {S[f'{ep}_구분확보_종']:4}종 / 성분행 "
            f"{S[f'{ep}_구분확보_성분행']:5} · tier {t} · "
            f"NC {S[f'{ep}_NC_종']}(tier2유래 {S[f'{ep}_NC_tier2유래']}) · "
            f"교차불일치 {S[f'{ep}_교차검증']['불일치']}/"
            f"{S[f'{ep}_교차검증']['대상']}")
    # 재조회로 해결되는 것(실패)과 그렇지 않은 것(PubChem 에 없음)을 갈라 센다.
    # UVCB·석유 유분·점토처럼 단일 화합물이 아닌 물질은 재시도해도 CID 가 없다.
    재시도 = 실패수
    없음 = int((R["조회상태"] == "CID없음").sum())
    if 재시도:
        log(f"  ! 조회실패 {재시도}종 — `--재시도실패분` 으로 다시 돌려야 한다")
    if 없음:
        log(f"    PubChem 미등재 {없음}종 — 재시도로 해결되지 않는다"
            f"(UVCB·석유유분·점토 등 단일 화합물이 아닌 물질)")
    S["재조회필요"] = 재시도
    S["PubChem미등재"] = 없음

    요약파일 = OUT / ("조사요약_대조군.json" if a.대조군 else "조사요약.json")
    with open(요약파일, "w", encoding="utf-8") as f:
        json.dump({
            "출처정책": {
                "tier1": f"{TIER1} — CLP Annex VI 조화분류. 있으면 이것만 쓴다",
                "tier2": f"{TIER2} — C&L 신고 집계. 합의율 ≥{AGREE_MIN}% 만 쓴다",
                "참고": "NITE-CMC·HCIS·HSDB 등. 값 선택에 쓰지 않고 교차검증만",
                "식별방법": "`Record.Reference[].SourceName` 을 각 정보블록의 "
                        "`ReferenceNumber` 로 대조한다. 퍼센트 유무 같은 "
                        "간접 신호로 출처를 추정하지 않는다.",
                "H코드를_쓰는_이유": "CLP 약어는 `Eye Irrit. 2` 로만 와서 2A/2B 를 "
                              "구별하지 못한다. H319/H320 은 구별한다.",
                "NC_판정": "tier 블록이 다른 유해성 코드를 신고했는데 해당 엔드포인트 "
                        "코드가 없을 때만 NC. 조회 실패에서는 NC 를 유도하지 않는다.",
            },
            "요약": S,
            "한계": [
                "tier2(ECHA C&L)는 기업 자기분류 신고 집계다. tier1 조화분류와 다를 수 "
                "있으므로 `*_출처tier` 로 갈라 기록했다.",
                "ECHA 집계는 응답에 명시된 대로 **10% 초과 코드만 표시**한다. 따라서 "
                "tier2 유래 NC 는 '10% 이하로 신고된 유해성이 없다'를 확인하지 못한다. "
                "`*_NC_tier2유래` 수치를 공시하고 하류에서 배제 가능하게 두었다.",
                "조회 실패와 데이터 부재를 `조회상태` 로 구별한다. 실패분이 남아 있으면 "
                "커버리지 개선폭을 과소평가한 상태다.",
                "농도 결측 2121 성분행은 이 조사로 해결되지 않는다. 성분커버리지 "
                "천장은 약 0.607 이다.",
                "CID 후보가 여럿인 경우 첫 CID 를 썼다. `CID후보수`·`cas_후보수`·"
                "`이름_후보수` 로 검수에서 걸러낼 수 있게 남겼다.",
            ],
        }, f, ensure_ascii=False, indent=2, default=str)
    log(f"완료 → {OUT.relative_to(L.ROOT)}/{저장.name} · {요약파일.name}")
    log("주의 — 학습에 반영하기 전에 검수(자동 정합 대조)를 통과해야 한다.")


if __name__ == "__main__":
    main()
