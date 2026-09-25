#!/usr/bin/env python3
"""2차 배정 시트의 자유서술 '추출정보'를 모델 입력용 구조화 컬럼으로 파싱한다.

시트별 포맷(실측):
  성분     "Xylene | 1330-20-7 | 60-100%"
  제형코드  "OD"  /  "EC"  /  "SL-미분류" 처럼 접미사가 붙는 경우도 있음
  독성코드  "Danger / H226(가연성...), H304(...), ..."
  특성     "Clear liquid (Appearance) | -48 C (Melting point) | 0.93 kPa at room (Vapor pressure)"

원칙
  - 파싱 실패는 조용히 0/NaN 으로 만들지 않고 *_parse_ok=False 로 남긴다.
  - 단위는 컬럼별 정규단위 1개로 환산하고, 컬럼명에 그 단위를 박아 넣는다.
  - 범위값('106-112 C')은 대표값(중앙)과 lo/hi 를 함께 남긴다 — 불확실성 자체가 피처다.
"""
import math
import re

# ---------------------------------------------------------------- 공통 수치 파싱

_NUM = r"[-+]?\d+(?:\.\d+)?(?:\s*[xX×]\s*10\s*\^?\s*[-+]?\d+|[eE][-+]?\d+)?"


def _txt(v):
    """pandas NaN / None / 숫자 등 무엇이 와도 안전하게 문자열로. 빈 값은 ""."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none") else s


def _to_float(tok):
    """'5.2x10-20', '1.2e-3', '-48' 을 float 으로. 실패 시 None."""
    t = tok.strip().replace(" ", "").replace("×", "x")
    m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)[xX]10\^?([-+]?\d+)", t)
    if m:
        try:
            return float(m.group(1)) * 10 ** int(m.group(2))
        except (ValueError, OverflowError):
            return None
    try:
        return float(t)
    except ValueError:
        return None


def num_range(text):
    """텍스트에서 (lo, hi, modifier) 추출. 단일값이면 lo==hi.

    modifier: '' | '<' | '>' | '~' | 'range'
    """
    t = text.strip()
    mod = ""
    if re.search(r"^\s*(?:<|less than|below|최대|이하)", t, re.I):
        mod = "<"
    elif re.search(r"^\s*(?:>|greater than|above|최소|이상)", t, re.I):
        mod = ">"
    elif re.search(r"^\s*(?:~|ca\.?|approx|about|약)", t, re.I):
        mod = "~"
    # 범위: '106-112', '106 to 112', '106~112'  (지수표기의 '-' 와 헷갈리지 않게 공백/구분자 요구)
    rng = re.search(rf"({_NUM})\s*(?:-\s+|--|~|\bto\b|–|—)\s*({_NUM})", t)
    if rng:
        a, b = _to_float(rng.group(1)), _to_float(rng.group(2))
        if a is not None and b is not None:
            return (min(a, b), max(a, b), "range")
    # '106-112 C' 처럼 공백 없는 하이픈 범위. 앞 토큰이 지수표기가 아닐 때만.
    rng2 = re.search(r"(?<![eExX^\d.])(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)(?![\d])", t)
    if rng2:
        a, b = float(rng2.group(1)), float(rng2.group(2))
        return (min(a, b), max(a, b), "range")
    one = re.search(_NUM, t)
    if not one:
        return (None, None, mod)
    v = _to_float(one.group(0))
    return (v, v, mod)


def _unit_factor(text, table, default=1.0):
    """단위 정규식 표에서 첫 매치의 환산계수. 매치 없으면 default."""
    for pat, fac in table:
        if re.search(pat, text, re.I):
            return fac
    return default


# ---------------------------------------------------------------- 성분 시트

PCT_KIND = ("range", "max", "min", "approx", "exact")


def parse_ingredient_extract(ext):
    """'name | CAS | pct' → dict."""
    out = {"ing2_name": None, "ing2_cas": None, "ing2_pct_text": None,
           "ing2_pct_value": None, "ing2_pct_lo": None, "ing2_pct_hi": None,
           "ing2_pct_kind": None, "ing2_parse_ok": False}
    ext = _txt(ext)
    if not ext:
        return out
    parts = [p.strip() for p in ext.split("|")]
    out["ing2_name"] = parts[0] or None
    # CAS 는 2번째 칸이 기본이나, 칸 순서가 흔들린 행이 있어 정규식으로도 훑는다.
    cas = None
    for p in parts[1:3]:
        m = re.search(r"\b(\d{2,7}-\d{2}-\d)\b", p)
        if m:
            cas = m.group(1)
            break
    if cas is None:
        m = re.search(r"\b(\d{2,7}-\d{2}-\d)\b", ext)
        cas = m.group(1) if m else None
    out["ing2_cas"] = cas
    pct = parts[2] if len(parts) > 2 else ""
    if not pct and len(parts) > 1 and "%" in parts[1]:
        pct = parts[1]
    if pct:
        out["ing2_pct_text"] = pct
        lo, hi, mod = num_range(pct)
        if lo is not None:
            out["ing2_pct_lo"], out["ing2_pct_hi"] = lo, hi
            out["ing2_pct_value"] = (lo + hi) / 2
            out["ing2_pct_kind"] = ("range" if mod == "range" else
                                    {"<": "max", ">": "min", "~": "approx"}.get(mod, "exact"))
    out["ing2_parse_ok"] = bool(out["ing2_name"])
    return out


# ---------------------------------------------------------------- 독성코드 시트

H_EYE = {"H318": "1", "H319": "2A", "H320": "2B"}
H_SKIN = {"H314": "1", "H315": "2", "H316": "3"}
H_SENS_SKIN = {"H317": "1"}
H_SENS_RESP = {"H334": "1"}
DANGER_H = {"H314", "H318", "H304", "H330", "H310", "H300", "H340", "H350",
            "H360", "H370", "H301", "H311", "H331", "H372"}

# 부가 엔드포인트: 타깃은 아니지만 독성 프로파일 피처로 쓴다.
H_GROUPS = {
    "acute_oral": {"H300": 1, "H301": 2, "H302": 3, "H303": 4},
    "acute_dermal": {"H310": 1, "H311": 2, "H312": 3, "H313": 4},
    "acute_inhal": {"H330": 1, "H331": 2, "H332": 3, "H333": 4},
    "stot_se": {"H370": 1, "H371": 2, "H335": 3, "H336": 3},
    "stot_re": {"H372": 1, "H373": 2},
    "carc": {"H350": 1, "H351": 2},
    "muta": {"H340": 1, "H341": 2},
    "repr": {"H360": 1, "H361": 2, "H362": 3},
    "aspiration": {"H304": 1, "H305": 2},
    "flammable": {"H224": 1, "H225": 2, "H226": 3, "H227": 4,
                  "H228": 1, "H250": 1, "H251": 1, "H252": 1},
    "oxidizer": {"H270": 1, "H271": 1, "H272": 2},
    "corrosive_metal": {"H290": 1},
    "aquatic": {"H400": 1, "H410": 1, "H411": 2, "H412": 3, "H413": 4},
    "resp_irrit": {"H335": 1},
    "ozone": {"H420": 1},
}

_PRE_GHS = re.compile(r"pre-?GHS|구형|비-?GHS|GHS 이전|EPA 라벨|EPA label|FIFRA|"
                      r"명시 없음|None stated|not stated|OSHA HCS 2012 이전|"
                      r"HCS 2012 이전 형식|GHS 형식 아님|GHS 미표기|"
                      r"GHS 신호어 없음|구 MSDS|구 EU 분류|서술형", re.I)
# 문서가 '분류 대상 아님'을 명시한 경우. H코드 부재와 달리 이것은 *음성 라벨*이므로
# 결측이 아니라 NC(Not Classified)로 취급해야 한다 — 불균형 데이터의 음성 표본 확보.
_NOT_CLASSIFIED = re.compile(
    r"not classified|미분류|분류되지\s*않|분류\s*대상\s*아님|"
    r"not a hazardous (?:substance|material|mixture)|유해물질/?혼합물\s*아님|"
    r"non-?hazardous|유해성\s*분류\s*없음|해당\s*없음\s*\(분류|"
    r"GHS 분류 없음|H코드 없음|중요 유해성 없음", re.I)


def parse_tox_extract(ext):
    """'Danger / H226(...), H319(...)' → 신호어·H코드 집합·엔드포인트별 구분."""
    out = {"tox2_signal_word": None, "tox2_signal_scheme": None,
           "tox2_h_codes": None, "tox2_n_h": 0, "tox2_pre_ghs": False,
           "tox2_not_classified": False,
           "tox2_eye_ghs": None, "tox2_skin_ghs": None, "tox2_sens_ghs": None,
           "tox2_sens_resp_ghs": None, "tox2_parse_ok": False}
    for g in H_GROUPS:
        out[f"tox2_{g}_cat"] = None
    ext = _txt(ext)
    if not ext:
        return out
    e = ext
    hs = sorted(set(re.findall(r"\bH\d{3}\b", e)))
    out["tox2_h_codes"] = ";".join(hs) or None
    out["tox2_n_h"] = len(hs)
    out["tox2_pre_ghs"] = bool(_PRE_GHS.search(e))

    if re.search(r"\bDanger\b|위험", e, re.I):
        out["tox2_signal_word"], out["tox2_signal_scheme"] = "Danger", "GHS"
    elif re.search(r"\bWarning\b|경고", e, re.I):
        out["tox2_signal_word"], out["tox2_signal_scheme"] = "Warning", "GHS"
    elif re.search(r"\bCaution\b|주의", e, re.I):
        # CAUTION 은 GHS 신호어가 아니라 EPA FIFRA 4단 신호어. EPA cat III/IV 를 시사한다.
        out["tox2_signal_word"], out["tox2_signal_scheme"] = "Caution", "EPA_FIFRA"

    def pick(table):
        hit = [table[h] for h in hs if h in table]
        return sorted(hit, key=lambda x: (x != "1", x))[0] if hit else None

    out["tox2_eye_ghs"] = pick(H_EYE)
    out["tox2_skin_ghs"] = pick(H_SKIN)
    out["tox2_sens_ghs"] = pick(H_SENS_SKIN)
    out["tox2_sens_resp_ghs"] = pick(H_SENS_RESP)
    for g, table in H_GROUPS.items():
        hit = [table[h] for h in hs if h in table]
        out[f"tox2_{g}_cat"] = min(hit) if hit else None

    # '분류 대상 아님'이 명시되고 H코드가 하나도 없으면 세 엔드포인트 모두 음성(NC).
    # H코드가 일부 있으면 그 문구는 다른 엔드포인트에 대한 것이므로 전역 NC 로 보지 않는다.
    if _NOT_CLASSIFIED.search(e) and not hs:
        out["tox2_not_classified"] = True
        if not out["tox2_pre_ghs"]:
            out["tox2_eye_ghs"] = out["tox2_skin_ghs"] = out["tox2_sens_ghs"] = "NC"
    out["tox2_parse_ok"] = bool(hs or out["tox2_signal_word"] or out["tox2_pre_ghs"]
                                or out["tox2_not_classified"])
    return out


# ---------------------------------------------------------------- 특성 시트

TEMP_C = [(r"°?\s*F\b|deg\.?\s*F\b|Fahrenheit", "F"), (r"\bK\b|Kelvin", "K")]
PRESS_PA = [(r"\bkPa\b", 1000.0), (r"\bhPa\b", 100.0), (r"\bmbar\b", 100.0),
            (r"\bbar\b", 1e5), (r"\bMPa\b", 1e6),
            (r"\bmm\s*Hg\b|\bmmhg\b|\btorr\b", 133.322),
            (r"\batm\b", 101325.0), (r"\bpsi\b|\blb/in", 6894.76),
            (r"\bPa\b", 1.0)]
DENS_GCM3 = [(r"lbs?\s*/\s*(?:cu\s*ft|ft\s*3|ft³)", 0.0160185),
             (r"lbs?\s*/\s*(?:gal|gallon)", 0.119826),
             (r"kg\s*/\s*m\s*3|kg\s*/\s*m³", 0.001),
             (r"(?<![kK])g\s*/\s*[lL]\b|(?<![kK])g\s*/\s*dm3", 0.001),
             (r"kg\s*/\s*[lL]\b|g\s*/\s*(?:cm3|cm³|mL|ml|cc)", 1.0)]
SOL_MGL = [(r"\bmg\s*/\s*[lL]\b|\bppm\b|\bmg\s*/\s*kg\b", 1.0),
           (r"\bg\s*/\s*[lL]\b", 1000.0), (r"\bmg\s*/\s*m[lL]\b", 1000.0),
           (r"\bg\s*/\s*100\s*m[lL]\b|\bg\s*/\s*100\s*g\b", 10000.0),
           (r"\bg\s*/\s*m[lL]\b", 1e6), (r"\bwt\s*%|\b%\s*\(?w/w|\b%(?!\s*w/v)", 10000.0),
           (r"\bug\s*/\s*[lL]\b|\bµg\s*/\s*[lL]\b|\bppb\b", 0.001)]
VISC_CP = [(r"\bcP\b|\bmPa\s*[·.*]?\s*s\b|\bcps\b", 1.0),
           (r"\bPa\s*[·.*]?\s*s\b", 1000.0), (r"\bP\b(?!a)", 100.0)]

SOL_QUAL = [(r"\bmiscible\b|완전\s*혼화|freely soluble", "miscible"),
            (r"\b(?:practically\s+)?insoluble\b|불용", "insoluble"),
            (r"\bslightly\s+soluble\b|\bsparingly\b|난용", "slightly_soluble"),
            (r"\bsoluble\b|가용", "soluble"),
            (r"\bdispersible\b|\bemulsifiable\b", "dispersible")]

STATE_PAT = [(r"\baerosol\b|\bspray\b", "aerosol"), (r"\bgas(?:eous)?\b", "gas"),
             (r"\bgel\b", "gel"), (r"\bpaste\b", "paste"),
             (r"\bfoam\b", "foam"),
             (r"\bwax\b|\bwaxy\b", "wax"),
             (r"\bpowder\b|\bdust\b|\bflour\b|분말", "powder"),
             (r"\bgranul\w*\b|\bpellet\b|\bprill\b|\bbead\b|과립", "granule"),
             (r"\bflake\b|\bchip\b", "flake"),
             (r"\bcrystal\w*\b|결정", "crystalline_solid"),
             (r"\bsolid\b|고체", "solid"),
             (r"\bemulsion\b|\bsuspension\b|\bslurry\b|\bdispersion\b", "dispersion"),
             (r"\bliquid\b|\bsolution\b|\bfluid\b|\boil\b|액체", "liquid"),
             (r"\bsemi-?solid\b", "semisolid")]

# 시트 실측 파라미터 어휘 17종 → 정규 컬럼 접두어
PARAM_MAP = {
    "water solubility": "solubility", "appearance": "appearance",
    "melting point": "mp", "vapor pressure": "vp", "relative density": "density",
    "odor": "odor", "color": "color", "partition coefficient": "logkow",
    "ph": "ph", "flash point": "fp", "boiling point": "bp",
    "viscosity": "viscosity", "flammability": "flammability",
    "auto-ignition temperature": "autoignition",
    "decomposition temperature": "decomp", "vapor density": "vapdens",
    "evaporation rate": "evap",
    # 동의어 방어
    "specific gravity": "density", "density": "density",
    "melting/freezing point": "mp", "initial boiling point": "bp",
    "solubility": "solubility", "log kow": "logkow",
    "physical state": "appearance", "autoignition temperature": "autoignition",
}
NUMERIC_PARAMS = {"mp", "bp", "fp", "autoignition", "decomp", "vp", "density",
                  "solubility", "logkow", "ph", "viscosity", "vapdens", "evap"}
TEXT_PARAMS = {"appearance", "odor", "color", "flammability"}

# 물리적으로 불가능한 값은 버린다(pc2_suspect 에 흔적을 남김). 이상하지만 가능한 값은
# 남기고 플래그만 세운다 — 파서 실수와 실제 특이물질을 구별할 수 없기 때문.
HARD_RANGE = {"ph": (0, 14), "density": (5e-4, 25), "solubility": (0, 1.1e6),
              "logkow": (-12, 14), "mp": (-273, 4000), "bp": (-273, 6000),
              "fp": (-200, 1000), "autoignition": (-100, 2000),
              "decomp": (-100, 2000), "vp": (0, 1e8), "viscosity": (0, 1e7)}
SOFT_RANGE = {"density": (0.3, 3.0), "ph": (0.5, 13.5), "logkow": (-5, 10)}


def _temp_to_c(lo, hi, raw):
    scale = _unit_factor(raw, TEMP_C, "C")
    if scale == "F":
        f = lambda v: None if v is None else (v - 32) * 5.0 / 9.0
    elif scale == "K":
        f = lambda v: None if v is None else v - 273.15
    else:
        f = lambda v: v
    return f(lo), f(hi)


def parse_physchem_extract(ext):
    """'값 (Parameter) | 값 (Parameter) ...' → 파라미터별 정규화 컬럼."""
    out = {"pc2_n_params": 0, "pc2_n_items": 0, "pc2_parse_ok": False,
           "pc2_physical_state": None, "pc2_solubility_qual": None,
           "pc2_ph_lo": None, "pc2_ph_hi": None, "pc2_strong_acid": None,
           "pc2_strong_base": None, "pc2_unparsed": None, "pc2_suspect": None}
    for p in NUMERIC_PARAMS:
        out[f"pc2_{p}"] = None
        out[f"pc2_{p}_mod"] = None
    for p in TEXT_PARAMS:
        out[f"pc2_{p}"] = None
    ext = _txt(ext)
    if not ext:
        return out

    items = [x.strip() for x in ext.split("|") if x.strip()]
    out["pc2_n_items"] = len(items)
    unparsed, suspect = [], []
    for it in items:
        m = re.search(r"\(([^()]+)\)\s*$", it)
        if not m:
            unparsed.append(it[:40])
            continue
        key = PARAM_MAP.get(m.group(1).strip().lower())
        if key is None:
            unparsed.append(it[:40])
            continue
        out["pc2_n_params"] += 1
        val = it[:m.start()].strip()
        if key in TEXT_PARAMS:
            out[f"pc2_{key}"] = val[:120] or None
            continue
        lo, hi, mod = num_range(val)
        if key in ("mp", "bp", "fp", "autoignition", "decomp"):
            lo, hi = _temp_to_c(lo, hi, val)
        elif key == "vp":
            fac = _unit_factor(val, PRESS_PA, None)
            if fac is None:      # 단위 미기재는 신뢰할 수 없다 — 버리고 unparsed 로
                unparsed.append(f"vp:unit? {val[:30]}")
                lo = hi = None
            else:
                lo = None if lo is None else lo * fac
                hi = None if hi is None else hi * fac
        elif key == "density":
            fac = _unit_factor(val, DENS_GCM3, 1.0)   # 무단위는 관례상 g/cm3(비중)
            lo = None if lo is None else lo * fac
            hi = None if hi is None else hi * fac
        elif key == "solubility":
            for pat, lab in SOL_QUAL:
                if re.search(pat, val, re.I):
                    out["pc2_solubility_qual"] = lab
                    break
            fac = _unit_factor(val, SOL_MGL, None)
            if fac is None:
                lo = hi = None
            else:
                lo = None if lo is None else lo * fac
                hi = None if hi is None else hi * fac
        elif key == "viscosity":
            fac = _unit_factor(val, VISC_CP, None)
            if fac is None:      # cSt(동점도)는 밀도 없이 cP 로 못 바꾼다
                lo = hi = None
                if re.search(r"\bcSt\b|mm2\s*/\s*s", val, re.I):
                    unparsed.append(f"viscosity:cSt {val[:24]}")
            else:
                lo = None if lo is None else lo * fac
                hi = None if hi is None else hi * fac
        elif key == "logkow":
            # 'log Kow 0.05' / 'log Pow = 3.4' / '4.5 (log Kow)'
            mm = re.search(rf"(?:log\s*[KP]ow|log\s*P(?:ow)?|logP)\s*[:=]?\s*({_NUM})",
                           val, re.I)
            if mm:
                lo = hi = _to_float(mm.group(1))

        # 범위 sanity. 불가능값은 폐기, 특이값은 플래그.
        if lo is not None and key in HARD_RANGE:
            a, b = HARD_RANGE[key]
            if not (a <= lo <= b) or (hi is not None and not (a <= hi <= b)):
                unparsed.append(f"{key}:범위이탈 {lo:g} '{val[:22]}'")
                lo = hi = None
        if lo is not None and key in SOFT_RANGE:
            a, b = SOFT_RANGE[key]
            if not (a <= lo <= b):
                suspect.append(f"{key}={lo:g}")

        if key == "ph":
            out["pc2_ph_lo"], out["pc2_ph_hi"] = lo, hi
        if lo is not None:
            rep = (lo + hi) / 2 if hi is not None else lo
            out[f"pc2_{key}"] = rep
            out[f"pc2_{key}_mod"] = mod or None

    app = " ".join(str(out.get(f"pc2_{k}") or "") for k in ("appearance", "flammability"))
    for pat, lab in STATE_PAT:
        if re.search(pat, app, re.I):
            out["pc2_physical_state"] = lab
            break
    if out["pc2_ph_lo"] is not None:
        # docx 표1: GHS 가산성 접근이 적용되지 않는 예외 게이트
        out["pc2_strong_acid"] = bool(out["pc2_ph_lo"] <= 2)
        out["pc2_strong_base"] = bool(out["pc2_ph_hi"] >= 11.5)
    out["pc2_unparsed"] = "; ".join(unparsed)[:200] or None
    out["pc2_suspect"] = "; ".join(suspect)[:120] or None
    out["pc2_parse_ok"] = out["pc2_n_params"] > 0
    return out


# ---------------------------------------------------------------- 제형코드 시트

def parse_form_extract(ext, cipac):
    out = {"form2_code": None, "form2_code_base": None, "form2_cipac_ok": None,
           "form2_sb": False}
    ext = _txt(ext)
    if not ext:
        return out
    e = ext.upper()
    out["form2_code"] = e[:24]
    base = re.split(r"[-/\s(]", e)[0].strip()
    out["form2_code_base"] = base or None
    out["form2_cipac_ok"] = base in cipac if base else None
    out["form2_sb"] = e.endswith("SB") and base != "SB"
    return out


# ---------------------------------------------------------------- GHS CT 가산식

CAT1 = {"1", "1A", "1B", "1C"}
CAT2 = {"2", "2A", "2B"}


def ct_predict(pairs, endpoint):
    """GHS 가산성 접근(Concentration Threshold)으로 혼합물 구분을 예측한다.

    pairs = [(성분 GHS 구분, 성분 농도%), ...]
    반환 dict: cat(예측구분) · s1/s2/s3(구분별 농도합) · add(10*s1+s2) · n_known(분류 보유 성분수)

    근거: GHS 개정판 3.2.3 표(피부), 3.3.3 표(눈), 3.4.3(감작성).
      눈   : ΣCat1 ≥ 3% → 1 · 1%≤ΣCat1 <3% → 2 · ΣCat2 ≥ 10% → 2 · 10ΣCat1+ΣCat2 ≥ 10% → 2
      피부 : ΣCat1 ≥ 5% → 1 · 1%≤ΣCat1 <5% → 2 · ΣCat2 ≥ 10% → 2 · 10ΣCat1+ΣCat2 ≥ 10% → 2
             · ΣCat3 ≥ 20% → 3
      감작 : ΣCat1 ≥ 1.0%(고체/액체) → 1 · Cat1A 성분이 ≥ 0.1% → 1
    눈 Cat2 는 가산식으로 2A/2B 를 구별할 수 없어 보수적으로 '2A' 로 둔다.
    """
    s1 = s2 = s3 = 0.0
    s1a = 0.0
    n = 0
    for cat, pct in pairs:
        if cat is None or pct is None:
            continue
        c = str(cat).strip().upper()
        if c in ("NOT CLASSIFIED", "NC", "-", ""):
            n += 1          # '분류 안 됨'도 정보다 — 분모에 포함, 합계엔 0
            continue
        if c in CAT1:
            s1 += float(pct)
            if c == "1A":
                s1a += float(pct)
        elif c in CAT2:
            s2 += float(pct)
        elif c == "3":
            s3 += float(pct)
        else:
            continue
        n += 1
    if n == 0:
        return {"cat": None, "s1": None, "s2": None, "s3": None,
                "add": None, "n_known": 0}
    add = 10 * s1 + s2
    if endpoint == "eye":
        cat = "1" if s1 >= 3 else ("2A" if (s1 >= 1 or s2 >= 10 or add >= 10) else "NC")
    elif endpoint == "skin":
        cat = ("1" if s1 >= 5 else
               "2" if (s1 >= 1 or s2 >= 10 or add >= 10) else
               "3" if s3 >= 20 else "NC")
    else:
        cat = "1" if (s1 >= 1.0 or s1a >= 0.1) else "NC"
    return {"cat": cat, "s1": s1, "s2": s2, "s3": s3, "add": add, "n_known": n}
