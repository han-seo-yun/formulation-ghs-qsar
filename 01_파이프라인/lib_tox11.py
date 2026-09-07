#!/usr/bin/env python3
"""SDS Section 11 자유서술 재파싱 (LIMITATIONS.md §F0.5).

배경
  `formulation_harness/_run_tox/final_merged_results.json` 에는 1,076 제품에 대해
  SDS Section 11 파라미터 **15종**이 수집돼 있으나,
  `apply_toxicity_results.py:build_extract_text()` 는 그 중 **신호어·H코드 2종만**
  독성코드 시트에 기입했다. 나머지 13종은 디스크에 있으면서 한 번도 쓰이지 않았다.
  이 모듈이 그 13종을 구조화한다. **웹 접근 불필요.**

설계 원칙 (이 파일의 존재 이유이므로 반드시 지킬 것)
  1. **결측과 음성을 구분한다.** "no data"/"자료 없음" 은 None(결측),
     "Not classified"/"별도 분류 없음" 은 NC(음성). 섞으면 §L1.4 의
     음성표본 부족을 잘못된 방식으로 메우게 된다.
  2. **정성 서술을 GHS 구분으로 승격하지 않는다.** "Severely irritating",
     "중등도 자극성" 은 구분을 특정하지 않는다 → `t11_qual_*`(0~3) 로만 남기고
     라벨로 쓰지 않는다.
  3. **EPA Tox Category 를 GHS 로 매핑하지 않는다** (§L1.3). 로마숫자 EPA 구분은
     `t11_epa_*` 로 분리 보관.
  4. **엔드포인트 교차오염을 막는다.** 감작성 필드에 적힌 "급성독성 Category 4" 가
     감작 구분으로 새어 들어가면 안 된다 → 절(clause) 단위로 엔드포인트를 판정하고
     엔드포인트별 허용 구분 토큰을 화이트리스트로 제한한다.
  5. **단위 없는 수치는 버린다.** 환산 추정치를 만들지 않는다(§L6 정책과 동일).
"""
import math
import re

# ─────────────────────────────────────────────── 엔드포인트별 허용 GHS 구분
ALLOWED = {
    "eye":  {"1", "2", "2A", "2B"},
    "skin": {"1", "1A", "1B", "1C", "2", "3"},
    "sens": {"1", "1A", "1B"},
}

# H코드 → 구분 (해당 엔드포인트 것만)
H2CAT = {
    "eye":  {"H318": "1", "H319": "2A", "H320": "2B"},
    "skin": {"H314": "1", "H315": "2", "H316": "3"},
    "sens": {"H317": "1"},
    "sens_resp": {"H334": "1"},
}
# EU DSD(구 지침) R-phrase → CLP 번역 (CLP Annex VII 번역표)
R2CAT = {
    "eye":  {"R41": "1", "R36": "2"},
    "skin": {"R35": "1A", "R34": "1B", "R38": "2"},
    "sens": {"R43": "1"},
}

# 절이 다른 엔드포인트를 말하고 있으면 그 절은 버린다
OTHER_EP = {
    "eye":  r"급성독성|acute\s*tox|경구|oral|흡입|inhal|호흡기|respirator|감작|sensiti"
            r"|발암|carcinogen|변이원|mutagen|생식|reproduc|흡인|aspirat|피부\s*부식|skin\s*corr",
    "skin": r"급성독성|acute\s*tox|경구|oral|흡입|inhal|호흡기|respirator|감작|sensiti"
            r"|발암|carcinogen|변이원|mutagen|생식|reproduc|흡인|aspirat|눈|eye|안구",
    "sens": r"급성독성|acute\s*tox|경구|oral|흡입\s*독성|발암|carcinogen|변이원|mutagen"
            r"|생식|reproduc|흡인|aspirat|눈|eye|피부\s*자극|skin\s*irrit|피부\s*부식",
}
# 절이 이 엔드포인트를 명시하면 위 배제규칙보다 우선
OWN_EP = {
    "eye":  r"눈|안구|eye|ocular|각막|cornea",
    "skin": r"피부|skin|dermal|경피",
    "sens": r"감작|과민|sensiti|allerg|알레르기",
}

NEG_STRONG = (r"not\s*classif|non[-\s]*classif|미분류|별도\s*분류\s*없|분류\s*대상\s*아"
              r"|분류\s*기준\s*(미충족|불충족|을?\s*충족하지)|criteria\s*not\s*met"
              r"|does\s*not\s*(cause|meet)|not\s*a\s*sensiti[sz]er|non[-\s]*sensiti"
              r"|not\s*(irritating|irritant|corrosive)|non[-\s]*irritat"
              r"|no\s*(eye|skin|dermal|ocular)\s*irritat|자극\s*(성\s*)?없|해당\s*없"
              r"|negative|음성|비자극|not\s*sensiti")
NO_DATA = (r"no\s*data|not\s*(determined|available|indicated|reported|tested|specified)"
           r"|자료\s*없|자료없|정보\s*없|정보없|데이터\s*없|미기재|미제공|unknown|n/?a\b"
           r"|시험\s*(자료|결과)\s*없|no\s*information")

QUAL = [(r"부식|corrosiv|serious\s*(eye\s*)?damage|심한\s*(눈\s*)?(손상|자극)"
         r"|severe|severely|극심", 3),
        (r"중등도|moderate", 2),
        (r"경미|mild|minor|slight|약한|weak", 1),
        (r"irritat|자극", 2)]     # 수식어 없는 '자극성' → 중간값. 반드시 마지막 순위

# 값이 제품 전체가 아니라 **성분별로 나열**된 정황. 성분값을 제품 라벨로 승격하면
# 하네스의 성분일치 게이팅을 우회하는 것이 되므로 라벨에서 제외한다.
_COMP_LIST = re.compile(r"[A-Za-z가-힣][A-Za-z가-힣\-\s()]{3,}\s*[:：]\s*"
                        r"(?:cat|구분|category|자료|미분류|no\s|\d|>|<|H\d)", re.I)
_PRODUCT_HINT = re.compile(r"제품|product|ATE\s*mix|ATEmix|\bATE\b|혼합물|formulation", re.I)

_CAT_RE = re.compile(
    r"(?:cat(?:egory|\.)?|구분|category)\s*[:\-]?\s*(1[ABC]?|2[AB]?|3|4|5)\b"
    r"|(?:eye\s*(?:dam|irrit)\.?|skin\s*(?:corr|irrit)\.?|(?:skin|resp)\.?\s*sens\.?)"
    r"\s*(1[ABC]?|2[AB]?|3)\b", re.I)
_EPA_RE = re.compile(r"EPA[^.;]{0,30}?(?:tox(?:icity)?\s*)?(?:cat(?:egory)?\.?)\s*"
                     r"(I{1,3}V?|IV|1|2|3|4)\b", re.I)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _clauses(text):
    # `/` 로는 절대 쪼개지 않는다 — mg/kg, mg/L, R36/37/38 이 파괴된다.
    return [c for c in re.split(r"[;；\n]", str(text)) if c and c.strip()]


def _cats_in(clause, ep):
    """절 안에서 이 엔드포인트의 GHS 구분 후보를 뽑는다."""
    out = []
    for m in _CAT_RE.finditer(clause):
        tok = (m.group(1) or m.group(2) or "").upper()
        if tok in ALLOWED[ep]:
            out.append(tok)
    return out


def _endpoint_cat(text, ep):
    """(cat, basis, weak, ambig) — basis: ghs_cat / h_code / dsd_r / nc / None"""
    if not text:
        return None, None, 0, 0
    t = str(text).strip()
    if not t:
        return None, None, 0, 0
    pos, basis = [], None
    has_neg = has_nodata = False
    for cl in _clauses(t):
        own = re.search(OWN_EP[ep], cl, re.I)
        other = re.search(OTHER_EP[ep], cl, re.I)
        if other and not own:
            continue                                   # 다른 엔드포인트를 말하는 절
        is_epa = re.search(r"\bEPA\b", cl, re.I)
        # ① H코드
        for h, c in H2CAT[ep].items():
            if re.search(rf"\b{h}\b", cl, re.I):
                pos.append((c, "h_code"))
        # ② 명시적 GHS 구분 (EPA 구분 문장은 제외)
        if not is_epa:
            for c in _cats_in(cl, ep):
                pos.append((c, "ghs_cat"))
        # ③ 구 EU R-phrase
        for r_, c in R2CAT[ep].items():
            if re.search(rf"\b{r_}\b", cl, re.I):
                pos.append((c, "dsd_r"))
        if re.search(NEG_STRONG, cl, re.I):
            has_neg = True
        if re.search(NO_DATA, cl, re.I):
            has_nodata = True

    if pos:
        # 여러 후보가 나오면 가장 심한 구분을 취한다(GHS 분류 관행: 최악값 채택)
        rank = {"1": 5, "1A": 5, "1B": 5, "1C": 5, "2": 3, "2A": 3, "2B": 2, "3": 1}
        prio = {"ghs_cat": 3, "h_code": 2, "dsd_r": 1}
        pos.sort(key=lambda x: (rank.get(x[0], 0), prio[x[1]]), reverse=True)
        cat, basis = pos[0]
        return cat, basis, 0, int(has_neg or len({c for c, _ in pos}) > 1)
    if has_neg:
        # 음성이지만 근거가 "자료 없음"이면 약한 음성
        return "NC", "nc", int(has_nodata), 0
    return None, None, 0, 0


def _qual(text, ep):
    if not text:
        return None
    for cl in _clauses(str(text)):
        own = re.search(OWN_EP[ep], cl, re.I)
        if re.search(OTHER_EP[ep], cl, re.I) and not own:
            continue
        if re.search(NEG_STRONG, cl, re.I):
            return 0
        for pat, v in QUAL:
            if re.search(pat, cl, re.I):
                return v
    return None


def _epa_cat(text):
    if not text:
        return None
    m = _EPA_RE.search(str(text))
    if not m:
        return None
    tok = m.group(1).upper()
    return _ROMAN.get(tok, int(tok) if tok.isdigit() else None)


# ─────────────────────────────────────────────── LD50 / LC50 수치 파싱
_NUMU = re.compile(
    r"(?P<op>>=|<=|[><≥≤]|~|about|approx\.?|약)?\s*"
    r"(?P<val>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mg\s*/\s*kg|g\s*/\s*kg|µg\s*/\s*kg|ug\s*/\s*kg"
    r"|mg\s*/\s*l|mg\s*/\s*m3|mg\s*/\s*m³|g\s*/\s*m3|ppm|µl\s*/\s*kg|ul\s*/\s*kg"
    r"|ml\s*/\s*kg)", re.I)
# 급성독성 H코드 → GHS 구분 (H300 Cat1·2, H301 Cat3, H302 Cat4, H303 Cat5)
_H_ACUTE_FIX = {"H300": 1, "H301": 3, "H302": 4, "H303": 5,
                "H310": 1, "H311": 3, "H312": 4, "H313": 5,
                "H330": 2, "H331": 3, "H332": 4, "H333": 5}
# 값 → GHS 급성독성 구분 (GHS 3.1.1 표, 경구/경피 mg/kg · 흡입 증기 mg/L)
CUT_ORAL = [(5, 1), (50, 2), (300, 3), (2000, 4), (5000, 5)]
CUT_DERM = [(50, 1), (200, 2), (1000, 3), (2000, 4), (5000, 5)]
CUT_INH = [(0.5, 1), (2.0, 2), (10.0, 3), (20.0, 4)]           # mg/L, 분진/미스트 아님 주의


def _to_mgkg(val, unit):
    u = re.sub(r"\s+", "", unit).lower()
    if u == "mg/kg":
        return val
    if u == "g/kg":
        return val * 1000
    if u in ("µg/kg", "ug/kg"):
        return val / 1000
    return None                       # µL/kg·mL/kg 는 밀도 없이 환산 불가 → 버린다


def _to_mgL(val, unit):
    u = re.sub(r"\s+", "", unit).lower()
    if u == "mg/l":
        return val
    if u in ("mg/m3", "mg/m³"):
        return val / 1000
    if u == "g/m3":
        return val
    return None                       # ppm 은 분자량 없이 환산 불가 → 버린다


def _parse_dose(text, kind):
    """(val, censored, n_vals, multi, unparsed).

    채택 규칙: **제품/ATE 로 명시된 값이 있으면 그것을 쓴다.** 없으면 최솟값(=가장
    독한 값). 성분별 값이 나열된 텍스트에서 무조건 최솟값을 쓰면 제품이 아니라
    가장 독한 원료의 LD50 을 제품값으로 오기재하게 된다(실제 사례 확인됨:
    "제품 ATE 465 mg/kg; 활성성분 beta-Cyfluthrin 11 mg/kg" → 465 를 써야 한다).
    """
    if not text:
        return None, None, 0, 0, 0
    t = str(text)
    conv = _to_mgkg if kind in ("oral", "dermal") else _to_mgL
    lo, hi = (0.01, 1e6) if kind in ("oral", "dermal") else (1e-5, 1e4)
    vals, unparsed = [], 0
    for cl in _clauses(t):
        prod = bool(_PRODUCT_HINT.search(cl))
        for m in _NUMU.finditer(cl):
            try:
                v = float(m.group("val").replace(",", ""))
            except ValueError:
                continue
            c = conv(v, m.group("unit"))
            if c is None or not (lo <= c <= hi):
                unparsed += 1
                continue
            op = (m.group("op") or "").strip()
            vals.append((c, 1 if op in (">", ">=", "≥") else 0, prod))
    if not vals:
        return None, None, 0, 0, unparsed
    prod_vals = [v for v in vals if v[2]]
    pick = min(prod_vals or vals, key=lambda x: x[0])
    multi = 1 if (len(vals) > 1 and _COMP_LIST.search(t)) else 0
    return pick[0], pick[1], len(vals), multi, unparsed


def _acute_ord(val, censored, cuts, text, hmap):
    """급성독성 구분(1~5, NC=0 아님 → 6=NC). 값 우선, 없으면 H코드."""
    if val is not None:
        if censored:
            # '>X' 는 X 초과라는 뜻 → X 가 최상단 컷오프 이상이면 NC 로 확정 가능
            if val >= cuts[-1][0]:
                return 6
            for c, k in cuts:
                if val < c:
                    return None          # 구분 특정 불가 (하한만 아는 상태)
            return 6
        for c, k in cuts:
            if val <= c:
                return k
        return 6
    if text:
        for h, k in _H_ACUTE_FIX.items():
            if re.search(rf"\b{h}\b", str(text), re.I):
                return k
    return None


# ─────────────────────────────────────────────── 기타 엔드포인트 (프로파일 피처)
PROF = {
    "carc": ("carcinogenicity", {"1": 3, "1A": 3, "1B": 3, "2": 2}),
    "muta": ("germ_cell_mutagenicity", {"1": 3, "1A": 3, "1B": 3, "2": 2}),
    "repr": ("reproductive_toxicity", {"1": 3, "1A": 3, "1B": 3, "2": 2}),
    "stot_se": ("stot_single_exposure", {"1": 3, "2": 2, "3": 1}),
    "stot_re": ("stot_repeated_exposure", {"1": 3, "2": 2}),
    "aspir": ("aspiration_hazard", {"1": 2, "2": 1}),
}


def _prof_ord(text, mapping):
    if not text:
        return None
    t = str(text)
    if re.search(NEG_STRONG, t, re.I) and not _CAT_RE.search(t):
        return 0
    best = None
    for m in _CAT_RE.finditer(t):
        tok = (m.group(1) or m.group(2) or "").upper()
        if tok in mapping:
            best = max(best or 0, mapping[tok])
    if best is not None:
        return best
    if re.search(NO_DATA, t, re.I):
        return None
    return None


def parse_tox11(tox, resolution=None, ingredients_match=None):
    """final_merged_results.json 의 field_values.toxicity → t11_* 컬럼 dict.

    게이팅: 하네스와 동일한 규칙을 유지한다 — 성분 일치가 확인되지 않은 레코드는
    라벨로 쓰지 않는다(`t11_gated=0`). 수치 피처도 같이 막는다(같은 문서 출처).
    """
    out = {"t11_present": 0, "t11_gated": 0}
    if not isinstance(tox, dict) or not tox:
        return out
    out["t11_present"] = 1
    gated = (resolution in ("found", "no_code_in_sds")) and bool(ingredients_match)
    out["t11_gated"] = int(gated)
    if not gated:
        return out

    for ep, key in (("eye", "eye_damage_irritation"),
                    ("skin", "skin_corrosion_irritation"),
                    ("sens", "respiratory_skin_sensitization")):
        txt = tox.get(key)
        cat, basis, weak, ambig = _endpoint_cat(txt, ep)
        # 성분별 나열 텍스트에서 뽑은 구분은 제품 라벨이 아니다 → 라벨 승격 금지
        comp = int(bool(txt) and bool(_COMP_LIST.search(str(txt)))
                   and not _PRODUCT_HINT.search(str(txt)))
        out[f"t11_{ep}_cat"] = cat
        out[f"t11_{ep}_basis"] = basis
        out[f"t11_{ep}_weak_nc"] = weak
        out[f"t11_{ep}_ambig"] = ambig
        out[f"t11_{ep}_comp"] = comp
        # ★ 빌더가 라벨로 쓰는 컬럼. 성분나열 유래는 제외한다.
        out[f"t11_{ep}_label"] = None if comp else cat
        out[f"t11_qual_{ep}"] = _qual(txt, ep)
        out[f"t11_epa_{ep}"] = _epa_cat(txt) if ep != "sens" else None
        out[f"t11_{ep}_raw"] = (str(txt).strip() or None) if txt else None
    # 호흡기 감작은 별도 엔드포인트
    st = tox.get("respiratory_skin_sensitization")
    out["t11_sens_resp_cat"] = "1" if (st and re.search(r"\bH334\b", str(st), re.I)) else None

    for kind, key, cuts in (("oral", "acute_oral_toxicity_ld50", CUT_ORAL),
                            ("dermal", "acute_dermal_toxicity_ld50", CUT_DERM),
                            ("inhal", "acute_inhalation_toxicity_lc50", CUT_INH)):
        txt = tox.get(key)
        v, cen, n, multi, unp = _parse_dose(txt, kind)
        pre = f"t11_{'ld50' if kind != 'inhal' else 'lc50'}_{kind}"
        out[pre] = v
        out[f"{pre}_log"] = math.log10(v) if v and v > 0 else None
        out[f"{pre}_censored"] = cen
        out[f"{pre}_n"] = n
        out[f"{pre}_multi"] = multi
        out[f"{pre}_unparsed"] = unp
        out[f"t11_acute_{kind}_ord"] = _acute_ord(v, cen, cuts, txt, _H_ACUTE_FIX)

    for nm, (key, mapping) in PROF.items():
        out[f"t11_{nm}_ord"] = _prof_ord(tox.get(key), mapping)

    pic = str(tox.get("ghs_pictograms") or "")
    out["t11_n_pictograms"] = len(set(re.findall(r"GHS0?(\d)", pic))) or None
    out["t11_pic_corrosion"] = 1 if re.search(r"GHS0?5", pic) else (0 if pic else None)
    out["t11_pic_exclam"] = 1 if re.search(r"GHS0?7", pic) else (0 if pic else None)
    out["t11_pic_health"] = 1 if re.search(r"GHS0?8", pic) else (0 if pic else None)
    return out


# 라벨 유도 컬럼(누출원) — build_input_v2.py 의 LABEL_SOURCE 에 합칠 집합
T11_LABEL_SOURCE = {
    "t11_eye_cat", "t11_skin_cat", "t11_sens_cat", "t11_sens_resp_cat",
    "t11_eye_label", "t11_skin_label", "t11_sens_label",
    "t11_eye_comp", "t11_skin_comp", "t11_sens_comp",
    "t11_eye_basis", "t11_skin_basis", "t11_sens_basis",
    "t11_eye_raw", "t11_skin_raw", "t11_sens_raw",
    "t11_qual_eye", "t11_qual_skin", "t11_qual_sens",
    "t11_epa_eye", "t11_epa_skin",
    "t11_eye_weak_nc", "t11_skin_weak_nc", "t11_sens_weak_nc",
    "t11_eye_ambig", "t11_skin_ambig", "t11_sens_ambig",
    # 그림문자 GHS05(부식)·GHS07(경고)는 눈/피부 구분을 직접 인코딩한다 → 피처 금지
    "t11_n_pictograms", "t11_pic_corrosion", "t11_pic_exclam", "t11_pic_health",
}
# 피처로 쓰지만 라벨과 **같은 SDS 문서**에서 나온 열 (동일문서 상관 위험 → ablation 대상)
T11_CODERIVED = {
    "t11_ld50_oral", "t11_ld50_oral_log", "t11_ld50_oral_censored", "t11_ld50_oral_n",
    "t11_ld50_oral_multi", "t11_ld50_oral_unparsed", "t11_acute_oral_ord",
    "t11_ld50_dermal", "t11_ld50_dermal_log", "t11_ld50_dermal_censored",
    "t11_ld50_dermal_n", "t11_ld50_dermal_multi", "t11_ld50_dermal_unparsed",
    "t11_acute_dermal_ord",
    "t11_lc50_inhal", "t11_lc50_inhal_log", "t11_lc50_inhal_censored",
    "t11_lc50_inhal_n", "t11_lc50_inhal_multi", "t11_lc50_inhal_unparsed",
    "t11_acute_inhal_ord",
    "t11_carc_ord", "t11_muta_ord", "t11_repr_ord",
    "t11_stot_se_ord", "t11_stot_re_ord", "t11_aspir_ord",
}

# --------------------------------------------------------------------------
# y_conflict_rule / y_preferred 재현 (축 C-2, 04_모델산출물/v3 산출 당시 파이프라인
# 코드에 존재하지 않고 결과값만 남아있던 로직 — input_dataset_v3.xlsx formulation
# 시트의 n_y_conflict>0인 99건 전체(y_conflict_rule·y_preferred 둘 다) 대비 역설계 후
# 100% 재현 검증됨. 알려진 전제: `_cas_tokens`는 '|'/',' 로 분리한 각 토큰이 CAS
# 정규식과 정확히 일치할 때만 인정한다 — "82657-04-3 and 83322-02-5" 같은 연결
# 토큰은 원본 큐레이션에서도 무효 처리되어 통째로 버려졌으므로, 토큰 내부에서
# CAS를 추가로 뽑아내면(re.findall) 오히려 재현율이 떨어진다(99→96/99 확인됨).
# --------------------------------------------------------------------------
_CAS_TOKEN_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _cas_tokens(text):
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return set()
    out = set()
    for part in re.split(r"[|,]", str(text)):
        tok = part.strip()
        if _CAS_TOKEN_RE.match(tok):
            out.add(tok)
    return out


def _alnum(text):
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return ""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def resolve_y_conflict(ice_cas, sds_comp_cas, product_name, doc_product_name):
    """`y_conflict_rule`/`y_preferred`/`y_conflict_evidence`를 결정론적으로 산출.

    ICE(NTP 실측 매칭) 활성성분 CAS 집합과 SDS Section 3 조성 CAS 집합을 비교해
    5개 카테고리 중 하나로 분류한다:

    - no_composition_data : 둘 중 하나라도 유효 CAS가 없음               → 선호=ntp
    - composition_mismatch: 교집합이 공집합 (활성성분이 SDS 조성에 전혀 없음) → 선호=미정
    - partial_composition : 교집합이 있으나 ICE 활성 전부를 포함하지 않음   → 선호=ntp
    - full overlap 시 product_name과 tox_doc_product(SDS 문서 자체가
      기재한 제품명)의 영숫자 정규화 결과가 서로 포함관계이면
        → product_identical            → 선호=sds
      아니면
        → same_actives_diff_formulation → 선호=ntp

    반환: (rule: str, preferred: str|None, evidence: str)
    """
    ice_set = _cas_tokens(ice_cas)
    sds_set = _cas_tokens(sds_comp_cas)

    if not ice_set or not sds_set:
        return "no_composition_data", "ntp", "CAS 없음 — 조성 비교 불가"

    overlap = ice_set & sds_set
    if not overlap:
        return ("composition_mismatch", None,
                f"ICE활성 {sorted(ice_set)} ∉ SDS성분 {sorted(sds_set)}")

    if overlap != ice_set:
        name_ok = bool(_alnum(product_name)) and bool(_alnum(doc_product_name)) and (
            _alnum(product_name) in _alnum(doc_product_name)
            or _alnum(doc_product_name) in _alnum(product_name)
        )
        note = "제품명 일치" if name_ok else "제품명 불일치"
        return "partial_composition", "ntp", f"활성성분 부분일치 · {note}"

    name_ok = bool(_alnum(product_name)) and bool(_alnum(doc_product_name)) and (
        _alnum(product_name) in _alnum(doc_product_name)
        or _alnum(doc_product_name) in _alnum(product_name)
    )
    if name_ok:
        return "product_identical", "sds", "제품명 일치 · 활성성분 조성 일치"
    return "same_actives_diff_formulation", "ntp", "활성성분 일치 · 제품명 불일치"
