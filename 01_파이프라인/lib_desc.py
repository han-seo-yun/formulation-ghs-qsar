#!/usr/bin/env python3
"""성분(ingredient) 단위 디스크립터 계산 — 성분/제형 2층 구조의 아래층.

이 모듈은 SMILES 하나에서 나오는 것만 계산한다(제형 집계는 build_input_v2.py 담당).
  1) RDKit 2D 물리화학 디스크립터 (연속형, 해석가능)
  2) 지문: Morgan/ECFP4 2048bit + MACCS 167bit
  3) 도메인 파생: Potts-Guy 피부투과계수, 이온성, 계면활성제 전하클래스, 역할 추정
"""
import math
import re

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem, Crippen, Descriptors, MACCSkeys, rdMolDescriptors
from rdkit.Chem import rdFingerprintGenerator

RDLogger.DisableLog("rdApp.*")

# ---------------------------------------------------------------- RDKit 디스크립터

# 눈/피부 자극성에 기전적으로 연결되는 것 위주로 고른 연속형 디스크립터.
#   - 크기/투과: MolWt, TPSA, LabuteASA, HeavyAtomCount
#   - 소수성: MolLogP (각질층 분배), MolMR
#   - 수소결합: HBD/HBA (단백질 변성 성향)
#   - 형태/유연성: RotB, FractionCSP3, RingCount, 방향족성
#   - 전자적: 부분전하 극단값 (친전자성 → 단백질 부가체 → 감작성)
DESC_FUNCS = {
    "MolWt": Descriptors.MolWt,
    "ExactMolWt": Descriptors.ExactMolWt,
    "MolLogP": Crippen.MolLogP,
    "MolMR": Crippen.MolMR,
    "TPSA": Descriptors.TPSA,
    "NumHDonors": Descriptors.NumHDonors,
    "NumHAcceptors": Descriptors.NumHAcceptors,
    "NumRotatableBonds": Descriptors.NumRotatableBonds,
    "NumAromaticRings": Descriptors.NumAromaticRings,
    "NumAliphaticRings": Descriptors.NumAliphaticRings,
    "RingCount": Descriptors.RingCount,
    "FractionCSP3": Descriptors.FractionCSP3,
    "HeavyAtomCount": Descriptors.HeavyAtomCount,
    "NumHeteroatoms": Descriptors.NumHeteroatoms,
    "NOCount": Descriptors.NOCount,
    "NHOHCount": Descriptors.NHOHCount,
    "NumSaturatedRings": Descriptors.NumSaturatedRings,
    "LabuteASA": Descriptors.LabuteASA,
    "BalabanJ": Descriptors.BalabanJ,
    "BertzCT": Descriptors.BertzCT,
    "Chi0v": Descriptors.Chi0v,
    "Chi1v": Descriptors.Chi1v,
    "Kappa1": Descriptors.Kappa1,
    "Kappa2": Descriptors.Kappa2,
    "Kappa3": Descriptors.Kappa3,
    "HallKierAlpha": Descriptors.HallKierAlpha,
    "MaxPartialCharge": Descriptors.MaxPartialCharge,
    "MinPartialCharge": Descriptors.MinPartialCharge,
    "MaxAbsPartialCharge": Descriptors.MaxAbsPartialCharge,
    "NumValenceElectrons": Descriptors.NumValenceElectrons,
    "qed": Descriptors.qed,
}
DESC_NAMES = list(DESC_FUNCS)

# ---------------------------------------------------------------- SMARTS 패턴

SMARTS = {
    # 계면활성제 머리기 — 각질층 지질 교란·단백질 변성의 주된 원인. 눈/피부 자극의 핵심 축.
    "sulfate": "[OX2][SX4](=O)(=O)[OX1H0-,OX2H1]",
    "sulfonate": "[#6][SX4](=O)(=O)[OX1H0-,OX2H1]",
    "carboxylate": "[CX3](=O)[OX1H0-]",
    "carboxylic_acid": "[CX3](=O)[OX2H1]",
    "phosphate_ester": "[OX2]P(=O)([OX2,OX1-])[OX2,OX1-]",
    "quat_ammonium": "[NX4+]",
    "amine_prim": "[NX3;H2;!$(NC=O)]",
    "amine_tert": "[NX3;H0;!$(NC=O);!$(N[a])]",
    "peo_chain": "[OX2][CH2][CH2][OX2][CH2][CH2][OX2]",   # 폴리에톡실레이트(비이온)
    "betaine": "[NX4+][CH2][CX3](=O)[OX1-]",
    # 친전자성/감작성 구조경보 (OECD 피부감작 AOP 의 Michael acceptor·SNAr 계열)
    "michael_acceptor": "[CX3]=[CX3][CX3]=[OX1]",
    "aldehyde": "[CX3H1](=O)[#6]",
    "epoxide": "[OX2r3]1[#6r3][#6r3]1",
    "isothiocyanate": "[NX2]=[CX2]=[SX1]",
    "acyl_halide": "[CX3](=[OX1])[F,Cl,Br,I]",
    "quinone": "O=C1C=CC(=O)C=C1",
    "nitro_aromatic": "[a][NX3](=O)=O",
    "halo_aromatic": "[a][F,Cl,Br,I]",
    "thiol": "[#16X2H]",
    "phenol": "[OX2H][a]",
    "anhydride": "[CX3](=O)[OX2][CX3](=O)",
    "sulfonyl_halide": "[SX4](=O)(=O)[F,Cl,Br,I]",
    # 강산/강염기 잔기 — pH 예외 게이트의 구조적 대리지표
    "strong_acid_grp": "[SX4](=O)(=O)([OX2H1])",
    "alkoxide_hydroxide": "[OX1-,OH-]",
    "long_alkyl": "[CH2][CH2][CH2][CH2][CH2][CH2][CH2][CH2]",   # C8+ 사슬
}
_COMPILED = {k: Chem.MolFromSmarts(v) for k, v in SMARTS.items()}
_BAD = [k for k, v in _COMPILED.items() if v is None]
if _BAD:
    raise RuntimeError(f"SMARTS 컴파일 실패: {_BAD}")

METALS = set("Li Be Na Mg Al K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Rb Sr Y Zr Nb Mo "
             "Ag Cd Sn Sb Ba La W Pt Au Hg Tl Pb Bi".split())

_MFPGEN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def potts_guy_logkp(mw, logp):
    """Potts-Guy: log Kp(cm/h) = -2.7 + 0.71·logKow - 0.0061·MW.

    피부 투과 플럭스의 1차 근사. 제형 수준에서 '얼마나 각질층을 통과하는가'는
    피부 자극 강도와 직접 연결되므로 성분 단위 피처로 넣는다.
    """
    if mw is None or logp is None:
        return None
    return -2.7 + 0.71 * logp - 0.0061 * mw


def surfactant_class(hits, mol):
    """전하 기준 계면활성제 분류. 사슬길이 조건(C8+)을 함께 요구해 단순 이온과 구별."""
    if not hits["long_alkyl"] and not hits["peo_chain"]:
        return "none"
    anionic = any(hits[k] for k in ("sulfate", "sulfonate", "phosphate_ester")) or \
        (hits["carboxylate"] and hits["long_alkyl"])
    cationic = bool(hits["quat_ammonium"])
    if hits["betaine"] or (anionic and cationic):
        return "amphoteric"
    if cationic:
        return "cationic"
    if anionic:
        return "anionic"
    if hits["peo_chain"]:
        return "nonionic"
    return "none"


def desc_for_smiles(smi):
    """SMILES → (디스크립터 dict, morgan bit vector, maccs bit vector). 실패 시 (None,None,None)."""
    if not smi or not str(smi).strip():
        return None, None, None
    mol = Chem.MolFromSmiles(str(smi).strip())
    if mol is None:
        return None, None, None
    d = {}
    for name, fn in DESC_FUNCS.items():
        try:
            v = fn(mol)
            d[name] = None if (v is None or (isinstance(v, float) and not math.isfinite(v))) else float(v)
        except Exception:
            d[name] = None

    hits = {k: len(mol.GetSubstructMatches(p)) for k, p in _COMPILED.items()}
    for k, v in hits.items():
        d[f"has_{k}"] = int(v > 0)
    d["n_structural_alerts"] = sum(
        1 for k in ("michael_acceptor", "aldehyde", "epoxide", "isothiocyanate",
                    "acyl_halide", "quinone", "nitro_aromatic", "anhydride",
                    "sulfonyl_halide", "thiol") if hits[k])
    d["formal_charge"] = int(Chem.GetFormalCharge(mol))
    d["n_pos_atoms"] = sum(1 for a in mol.GetAtoms() if a.GetFormalCharge() > 0)
    d["n_neg_atoms"] = sum(1 for a in mol.GetAtoms() if a.GetFormalCharge() < 0)
    d["is_ionic"] = int(d["n_pos_atoms"] + d["n_neg_atoms"] > 0)
    d["n_fragments"] = len(Chem.GetMolFrags(mol))
    d["has_metal"] = int(any(a.GetSymbol() in METALS for a in mol.GetAtoms()))
    d["surfactant_class"] = surfactant_class(hits, mol)
    d["logKp_pottsguy"] = potts_guy_logkp(d.get("MolWt"), d.get("MolLogP"))
    d["max_alkyl_chain"] = _longest_alkyl(mol)

    fp_m = np.zeros(2048, dtype=np.uint8)
    fp_k = np.zeros(167, dtype=np.uint8)
    try:
        arr = _MFPGEN.GetFingerprintAsNumPy(mol)
        fp_m = arr.astype(np.uint8)
    except Exception:
        pass
    try:
        mk = MACCSkeys.GenMACCSKeys(mol)
        for i in mk.GetOnBits():
            fp_k[i] = 1
    except Exception:
        pass
    return d, fp_m, fp_k


def _longest_alkyl(mol):
    """가장 긴 비고리·비방향족 sp3 탄소 연쇄 길이. 계면활성제 소수부 크기의 대리지표."""
    idx = [a.GetIdx() for a in mol.GetAtoms()
           if a.GetSymbol() == "C" and not a.GetIsAromatic() and not a.IsInRing()]
    if not idx:
        return 0
    s = set(idx)
    best = 0
    for start in idx:                       # 사슬은 짧으므로 각 노드에서 DFS 로 최장경로
        stack = [(start, {start}, 1)]
        while stack:
            cur, seen, ln = stack.pop()
            best = max(best, ln)
            for nb in mol.GetAtomWithIdx(cur).GetNeighbors():
                j = nb.GetIdx()
                if j in s and j not in seen:
                    stack.append((j, seen | {j}, ln + 1))
    return best


# ---------------------------------------------------------------- 역할 추정

ROLE_RULES = [
    ("water", r"^\s*water\b|^\s*aqua\b|증류수|정제수|^\s*deionized water|^h2o$"),
    ("surfactant", r"surfactant|sulfonate|sulphonate|sulfate|sulphate|ethoxylat|"
                   r"polysorbate|tween|span\b|nonoxynol|betaine|quaternium|"
                   r"lauryl|laureth|ceteth|oleth|steareth|POE\b|polyoxyethylene|"
                   r"alkylbenzene|emulsifier|wetting agent|dispersant|나트륨.*설페이트"),
    ("solvent", r"xylene|toluene|benzene(?!sulfon)|naphtha|kerosene|mineral spirit|"
                r"ethanol|methanol|isopropanol|propylene glycol|ethylene glycol|"
                r"glycol ether|acetone|methyl ethyl ketone|dimethyl ?sulfoxide|"
                r"n-methyl-?2-?pyrrolidone|cyclohexanone|solvent|petroleum distillate|"
                r"aromatic hydrocarbon|butanol|dipropylene"),
    ("synergist", r"piperonyl butoxide|MGK[- ]?264|bicycloheptene dicarboximide|"
                  r"sesamex|synergist"),
    ("carrier_inert", r"silica|silicon dioxide|kaolin|clay|attapulgite|talc|"
                      r"calcium carbonate|limestone|diatomaceous|bentonite|"
                      r"cellulose|starch|dextrin|sugar|sucrose|urea|"
                      r"sodium chloride|inert ingredient|filler|carrier"),
    ("preservative", r"paraben|benzoate|sorbate|isothiazolinon|formaldehyde releas|"
                     r"phenoxyethanol|preservative"),
    ("thickener", r"xanthan|carbomer|guar|carrageenan|hydroxyethyl cellulose|"
                  r"thickener|polyacrylate"),
    ("propellant", r"propane|butane|isobutane|dimethyl ether|HFC|hydrofluoro|"
                   r"propellant|carbon dioxide"),
    ("ph_adjuster", r"sodium hydroxide|potassium hydroxide|citric acid|"
                    r"phosphoric acid|sulfuric acid|hydrochloric acid|"
                    r"triethanolamine|monoethanolamine|buffer|pH adjust"),
    ("fragrance_dye", r"fragrance|perfume|parfum|dye\b|pigment|colorant|"
                      r"CI \d{5}|FD&C|limonene|linalool"),
]
_ROLE_RE = [(r, re.compile(p, re.I)) for r, p in ROLE_RULES]


def guess_role(name, is_active_flag=None):
    """성분명 기반 역할 추정. 명시된 active 플래그가 있으면 그것을 우선한다."""
    if is_active_flag:
        return "active"
    n = str(name or "")
    for role, rx in _ROLE_RE:
        if rx.search(n):
            return role
    return "unknown"
