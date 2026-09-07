#!/usr/bin/env python3
"""6/30 감사 워크북 → v4 병합부(build_input_v4.py L301-307) 위치조인 결함 실증.

산출: 04_모델산출물/v4_fixed/june_merge_defect_evidence.csv

판정 논리
  1. 세 파이프리스트(이름/CAS/SMILES)의 길이 불일치 여부 — 위치조인 전제 붕괴의 직접 증거.
  2. 이름 토큰이 화학물질명인가 — "Content (W/W)" 같은 SDS 표 헤더 조각 검출.
  3. 참조 CAS↔이름↔SMILES 사전(위치조인에 의존하지 않는 출처만)과 대조해
     v4가 부여한 CAS/SMILES가 화학적으로 그 이름의 것인지 확인.
     참조 출처: (a) june 워크북 중 이름/CAS 길이가 정확히 같은 행,
               (b) v1 ingredient 시트의 (이름, CAS, SMILES) 기존값,
               (c) v4/smiles_canonicalization_map.csv (CAS→대표 SMILES).
  4. 오프바이원 판정: 부여된 CAS가 같은 제형 리스트 내 다른 위치(i±k) 이름의 참조 CAS와 일치.
"""
import os
import re
import sys
from collections import defaultdict

import pandas as pd
from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")

BASE = "/Users/hanseoyun/Desktop/260830"
JUNE = (f"{BASE}/formulation_audit_team_share_highlighted_only_20260630/"
        "formulation_ingredients_master_audited.xlsx")
RECON = f"{BASE}/04_모델산출물/v4/june_audit_reconciliation.csv"
V1 = f"{BASE}/03_입력데이터/input_dataset.xlsx"
CANON = f"{BASE}/04_모델산출물/v4/smiles_canonicalization_map.csv"
V4XLSX = f"{BASE}/04_모델산출물/input_dataset_v4.xlsx"
OUTDIR = f"{BASE}/04_모델산출물/v4_fixed"
os.makedirs(OUTDIR, exist_ok=True)

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def nrm_name(x):
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


def cas_checkdigit_ok(c):
    if not c or not CAS_RE.match(c):
        return False
    digits = c.replace("-", "")
    body, chk = digits[:-1], int(digits[-1])
    s = sum(int(d) * (i + 1) for i, d in enumerate(reversed(body)))
    return s % 10 == chk


# --- 비화학 토큰 판별 --------------------------------------------------------
NONCHEM_PAT = [
    r"^\s*$",
    r"content", r"\bw\s*/\s*w\b", r"\bw\s*/\s*v\b", r"concentration",
    r"^\s*%\s*$", r"percent(age)?$", r"^\s*[\d.,\s%<>=~\-–]+$",
    r"^cas\b", r"^ec\b", r"^ec[\s_-]?index", r"^index\s*no",
    r"^name\s*[:：]", r"^ingredient(s)?\s*$", r"^component(s)?\s*$",
    r"^chemical\s+name", r"^classification", r"^hazard", r"^weight",
    r"^amount", r"^substance\s*$", r"^identifier", r"^reach",
    r"^ppm$", r"^mg/kg$", r"^g/l$", r"^wt\b", r"^n/?a$", r"^none$",
    r"^other\s+ingredient", r"^remainder", r"^balance$",
    r"^trade\s+secret", r"^proprietary\s*$",
    r"^total$", r"^sum$", r"^range$", r"^number$", r"^no\.$",
]
NONCHEM_RE = [re.compile(p, re.I) for p in NONCHEM_PAT]


def is_nonchemical_token(tok):
    """SDS 표 헤더/단위/숫자 조각이면 True. 화학물질명으로 볼 수 없는 토큰."""
    t = str(tok or "").strip()
    if not t:
        return True, "empty"
    for rx in NONCHEM_RE:
        if rx.search(t):
            return True, f"matches:{rx.pattern}"
    # 알파벳 3자 미만이면 물질명으로 인정하지 않는다.
    if len(re.sub(r"[^A-Za-z]", "", t)) < 3:
        return True, "too_few_letters"
    return False, ""


def split_pipe(s):
    if pd.isna(s):
        return []
    return [x.strip() for x in str(s).split("|")]


def canon(smi):
    if not smi:
        return None
    try:
        m = Chem.MolFromSmiles(str(smi))
        return Chem.MolToSmiles(m) if m is not None else None
    except Exception:                                            # noqa: BLE001
        return None


def skeleton(smi):
    """입체·전하 무시 골격 비교용 키 (동일물질 이표기 허용)."""
    c = canon(smi)
    if not c:
        return None
    return re.sub(r"[@/\\]", "", c)


# ================================================================ 참조 사전
def build_reference():
    """위치조인에 의존하지 않는 (이름↔CAS), (CAS→SMILES) 참조 사전."""
    name2cas = defaultdict(set)
    cas2name = defaultdict(set)
    cas2smi = defaultdict(set)

    june = pd.read_excel(JUNE, sheet_name="Formulations")
    n_safe_rows = 0
    for r in june.itertuples(index=False):
        nm = split_pipe(r.Formulation_Ingredients)
        cs = split_pipe(r.Formulation_Ingredients_CAS)
        sm = split_pipe(r.Formulation_Ingredients_SMILES)
        if len(nm) == len(cs) and len(nm) > 0:
            n_safe_rows += 1
            for a, b in zip(nm, cs):
                k, c = nrm_name(a), b.strip()
                if k and CAS_RE.match(c) and not is_nonchemical_token(a)[0]:
                    name2cas[k].add(c)
                    cas2name[c].add(k)
        if len(nm) == len(cs) == len(sm) and len(nm) > 0:
            for a, b, s in zip(nm, cs, sm):
                if CAS_RE.match(b.strip()) and canon(s):
                    cas2smi[b.strip()].add(canon(s))

    v1 = pd.read_excel(V1, sheet_name="ingredient")
    for r in v1.itertuples(index=False):
        k = nrm_name(r.ingredient_name)
        c = str(r.cas).strip() if pd.notna(r.cas) else ""
        s = str(r.smiles).strip() if pd.notna(r.smiles) else ""
        if k and CAS_RE.match(c):
            name2cas[k].add(c)
            cas2name[c].add(k)
        if CAS_RE.match(c) and canon(s):
            cas2smi[c].add(canon(s))

    if os.path.exists(CANON):
        cm = pd.read_csv(CANON)
        for r in cm.itertuples(index=False):
            c = str(r.cas).strip()
            s = canon(r.recommended_smiles)
            if CAS_RE.match(c) and s:
                cas2smi[c].add(s)
    print(f"[ref] june 안전행(이름/CAS 길이일치) {n_safe_rows}/{len(june)} · "
          f"name2cas {len(name2cas)} · cas2name {len(cas2name)} · cas2smi {len(cas2smi)}")
    return name2cas, cas2name, cas2smi


def main():
    name2cas, cas2name, cas2smi = build_reference()

    recon = pd.read_csv(RECON)
    targets = set(recon.loc[recon["recommendation"] == "RECOVER_CAS_HIGH_CONFIDENCE",
                            "Formulation_ID"])
    june = pd.read_excel(JUNE, sheet_name="Formulations")
    jt = june[june["Formulation_ID"].isin(targets)]

    # v4 산출물에서 실제로 병합 영향을 받은 (fid, nrm_name) 집합
    v4_used = set()
    v4ing = pd.read_excel(V4XLSX, sheet_name="ingredient")
    for r in v4ing[v4ing["june_audit_recovered"] == True].itertuples(index=False):  # noqa: E712
        v4_used.add((r.Formulation_ID, nrm_name(r.ing_name_best)))
    print(f"[v4] june_audit_recovered 행 {len(v4ing[v4ing['june_audit_recovered'] == True])} "  # noqa: E712
          f"· 고유 (fid,name) {len(v4_used)}")

    rows = []
    n_len_mismatch = 0
    for r in jt.itertuples(index=False):
        fid = r.Formulation_ID
        names = split_pipe(r.Formulation_Ingredients)
        cass = split_pipe(r.Formulation_Ingredients_CAS)
        smis = split_pipe(r.Formulation_Ingredients_SMILES)
        lm = (len(names) == len(cass) == len(smis)) and len(names) > 0
        if not lm:
            n_len_mismatch += 1
        # v4 위치조인 재현
        for i in range(max(len(names), len(cass), len(smis))):
            raw = names[i] if i < len(names) else None
            nm = nrm_name(raw) if raw is not None else None
            if not nm:
                continue
            a_cas = cass[i] if i < len(cass) and cass[i] else None
            a_smi = smis[i] if i < len(smis) and smis[i] else None
            nonchem, nonchem_why = is_nonchemical_token(raw)
            ref_cas = sorted(name2cas.get(nm, []))
            ref_name_of_acas = sorted(cas2name.get(a_cas, [])) if a_cas else []
            ref_smi_true = set()
            for c in ref_cas:
                ref_smi_true |= cas2smi.get(c, set())
            a_smi_c = canon(a_smi)
            smi_valid = a_smi_c is not None if a_smi else None

            # ---- 판정
            verdict, detail = "UNVERIFIABLE", ""
            if nonchem and (a_cas or a_smi):
                verdict = "NONCHEM_TOKEN_GOT_STRUCTURE"
                detail = nonchem_why
            elif a_cas and ref_cas:
                if a_cas in ref_cas:
                    verdict, detail = "CAS_OK", "assigned CAS in reference set"
                else:
                    verdict = "CAS_WRONG"
                    # 오프바이원/시프트 탐지
                    shift = None
                    for k in range(1, min(6, len(names))):
                        for d in (-k, k):
                            j = i + d
                            if 0 <= j < len(names):
                                if a_cas in name2cas.get(nrm_name(names[j]), set()):
                                    shift = d
                                    break
                        if shift is not None:
                            break
                    if shift is not None:
                        verdict = "CAS_WRONG_SHIFT"
                        detail = (f"assigned CAS belongs to list index {i + shift} "
                                  f"('{names[i + shift]}') → shift={shift:+d}")
                    else:
                        detail = (f"assigned CAS maps to {ref_name_of_acas[:2]}"
                                  if ref_name_of_acas else "assigned CAS unknown in reference")
            elif a_cas and not ref_cas:
                verdict, detail = "UNVERIFIABLE", "name absent from reference name2cas"

            # SMILES 정합성 (참조가 있을 때만)
            smi_match = None
            if a_smi_c and ref_smi_true:
                ks = {skeleton(s) for s in ref_smi_true}
                smi_match = skeleton(a_smi_c) in ks
                if smi_match is False and verdict in ("CAS_OK", "UNVERIFIABLE"):
                    verdict = "SMILES_WRONG"
                    detail = (detail + " | " if detail else "") + \
                        "assigned SMILES skeleton != reference SMILES for this name"

            rows.append({
                "Formulation_ID": fid, "pipe_index": i, "name_token": raw,
                "name_norm": nm,
                "len_names": len(names), "len_cas": len(cass), "len_smiles": len(smis),
                "length_match": lm,
                "assigned_cas_v4": a_cas, "assigned_smiles_v4": a_smi,
                "assigned_cas_format_ok": bool(a_cas and CAS_RE.match(a_cas)),
                "assigned_cas_checkdigit_ok": cas_checkdigit_ok(a_cas) if a_cas else None,
                "assigned_smiles_parses": smi_valid,
                "name_is_nonchemical": nonchem,
                "nonchemical_reason": nonchem_why,
                "reference_cas_for_name": "|".join(ref_cas),
                "reference_names_for_assigned_cas": "|".join(ref_name_of_acas),
                "reference_smiles_for_name": "|".join(sorted(ref_smi_true))[:400],
                "smiles_matches_reference": smi_match,
                "verdict": verdict, "detail": detail,
                "propagated_to_v4": (fid, nm) in v4_used,
            })

    ev = pd.DataFrame(rows)
    out = f"{OUTDIR}/june_merge_defect_evidence.csv"
    ev.to_csv(out, index=False)

    print(f"\n=== 결함 실증 요약 ===")
    print(f"대상 제형(RECOVER_CAS_HIGH_CONFIDENCE) : {len(jt)}")
    print(f"세 리스트 길이 불일치 제형             : {n_len_mismatch} "
          f"({100*n_len_mismatch/len(jt):.1f}%)")
    print(f"위치조인이 만든 (fid,name) 항목        : {len(ev)}")
    print(f"\n판정 분포:\n{ev['verdict'].value_counts().to_string()}")
    ver = ev[ev["verdict"].isin(["CAS_OK", "CAS_WRONG", "CAS_WRONG_SHIFT"])]
    print(f"\n교차검증 가능(CAS 참조 존재) 항목      : {len(ver)}")
    print(f"  그중 CAS 오배정                      : "
          f"{int((ver['verdict'] != 'CAS_OK').sum())} "
          f"({100*(ver['verdict'] != 'CAS_OK').mean():.1f}%)")
    print(f"  그중 오프바이원/시프트로 설명됨       : "
          f"{int((ver['verdict'] == 'CAS_WRONG_SHIFT').sum())}")
    print(f"비화학 토큰에 구조/CAS 부착            : "
          f"{int((ev['verdict'] == 'NONCHEM_TOKEN_GOT_STRUCTURE').sum())}")
    print(f"v4 산출물로 실제 전파된 항목           : {int(ev['propagated_to_v4'].sum())}")
    bad = ev[ev["verdict"].isin(["CAS_WRONG", "CAS_WRONG_SHIFT",
                                 "NONCHEM_TOKEN_GOT_STRUCTURE", "SMILES_WRONG"])]
    print(f"오염 판정 항목 중 v4 전파분             : "
          f"{int(bad['propagated_to_v4'].sum())} / {len(bad)}")
    print(f"\n→ {out}")

    # 시프트 사례 예시 출력
    sh = ev[ev["verdict"] == "CAS_WRONG_SHIFT"]
    if len(sh):
        print("\n오프바이원 사례(최대 10):")
        for r in sh.head(10).itertuples(index=False):
            print(f"  {r.Formulation_ID} [{r.pipe_index}] '{r.name_token}' "
                  f"← {r.assigned_cas_v4} ({r.detail})")
    nc = ev[ev["verdict"] == "NONCHEM_TOKEN_GOT_STRUCTURE"]
    if len(nc):
        print("\n비화학 토큰 사례(최대 15):")
        for r in nc.head(15).itertuples(index=False):
            print(f"  {r.Formulation_ID} [{r.pipe_index}] '{r.name_token}' "
                  f"← CAS={r.assigned_cas_v4} SMILES={str(r.assigned_smiles_v4)[:40]}")
    return ev


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
