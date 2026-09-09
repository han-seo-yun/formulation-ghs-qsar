#!/usr/bin/env python3
"""동일 CAS · 상이 SMILES 그룹의 canonical 대표구조 선정 (분석 전용, 원본 불변).

배경
----
ingredient 시트에는 동일 CAS인데 SMILES 문자열이 서로 다른 그룹이 존재한다.
대부분은 진짜 다른 물질이 아니라 한쪽에만 입체화학(E/Z, R/S)이 표기된 경우다.
이를 기계적으로 구분하기 위해 RDKit InChIKey를 사용한다.

InChIKey 형식: ``XXXXXXXXXXXXXX-YYYYYYYYYY-Z``
  * 1블록(앞 14자) = 분자 골격(연결성)만 인코딩 → 골격 동일성 판정에 사용
  * 2블록(10자)   = 입체/동위원소/프로톤 레이어. ``UHFFFAOYSA`` 이면 입체정보 없음.

판정 규칙
--------
1) 그룹 내 모든 SMILES의 골격(1블록)이 동일 → 표기 차이일 뿐이므로 canonical 추천
   - 입체정보 보유(2블록 != UHFFFAOYSA) SMILES가 1개  → 그것을 추천
   - 2개 이상 → 중원자수 동일 확인 후 문자열이 더 긴 것을 추천
   - 0개(전부 입체정보 없음) → InChIKey가 완전 동일하면 표기만 다른 동일물질,
     임의로 첫 SMILES 추천 / InChIKey가 다르면 프로톤·전하 차이이므로 수동검토
2) 골격이 다름 → 진짜 구조 충돌. skeleton_conflict=True, 추천하지 않고 수동검토.

산출: 04_모델산출물/v4/smiles_canonicalization_map.csv
      (기존 xlsx/parquet/파이프라인 스크립트는 일절 수정하지 않는다)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")

ROOT = str(Path(__file__).resolve().parent.parent)
SRC = f"{ROOT}/04_모델산출물/input_dataset_v4.xlsx"
OUT = f"{ROOT}/04_모델산출물/v4/smiles_canonicalization_map.csv"

NO_STEREO_BLOCK = "UHFFFAOYSA"  # InChIKey 2블록이 이 값이면 입체정보 없음
SKELETON_LEN = 14


# --------------------------------------------------------------------------- #
# InChIKey 계산
# --------------------------------------------------------------------------- #
def inchikey_info(smiles: str):
    """(inchikey, skeleton, stereo_block, heavy_atom_count) 반환. 실패 시 None."""
    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return None
    try:
        key = Chem.MolToInchiKey(mol)
    except Exception:
        return None
    if not key or len(key) < SKELETON_LEN:
        return None
    parts = key.split("-")
    skeleton = parts[0][:SKELETON_LEN]
    stereo_block = parts[1] if len(parts) > 1 else ""
    return key, skeleton, stereo_block, mol.GetNumHeavyAtoms()


# --------------------------------------------------------------------------- #
# 그룹 1건 판정
# --------------------------------------------------------------------------- #
def decide(cas: str, smiles_list: list[str], failures: list[tuple[str, str]]):
    """한 CAS 그룹에 대해 (skeleton_conflict, recommended, reason) 결정."""
    parsed = []  # (smiles, key, skeleton, stereo_block, n_heavy)
    for smi in smiles_list:
        info = inchikey_info(smi)
        if info is None:
            failures.append((cas, smi))  # 파싱 실패는 로그만 남기고 건너뜀
            continue
        parsed.append((smi,) + info)

    if len(parsed) < 2:
        return (
            False,
            "",
            f"insufficient_parseable_smiles: RDKit 파싱 성공 {len(parsed)}건 "
            f"(전체 {len(smiles_list)}건) — 판정 불가, 수동검토",
        )

    skeletons = {p[2] for p in parsed}
    if len(skeletons) > 1:
        return (
            True,
            "",
            f"skeleton_conflict: InChIKey 골격 {len(skeletons)}종 상이 "
            f"({'|'.join(sorted(skeletons))}) — 진짜 다른 물질, 수동검토 필요",
        )

    # 골격 동일 → 입체정보 보유 SMILES 탐색
    stereo = [p for p in parsed if p[3] != NO_STEREO_BLOCK]

    if len(stereo) == 1:
        return (
            False,
            stereo[0][0],
            "same_skeleton_single_stereo: 골격 동일, 입체정보(InChIKey 2블록="
            f"{stereo[0][3]}) 보유 SMILES가 유일하여 채택",
        )

    if len(stereo) > 1:
        heavy = {p[4] for p in stereo}
        best = max(stereo, key=lambda p: len(p[0]))  # 표기가 가장 충실한 것
        if len(heavy) == 1:
            return (
                False,
                best[0],
                "same_skeleton_multi_stereo: 골격·중원자수"
                f"({heavy.pop()}) 동일한 입체표기 SMILES {len(stereo)}건 중 "
                "문자열이 가장 긴(입체표기가 가장 완전한) 것 채택",
            )
        return (
            False,
            best[0],
            "same_skeleton_multi_stereo_atomcount_mismatch: 입체표기 SMILES "
            f"{len(stereo)}건의 중원자수가 상이({sorted(heavy)}) — 문자열이 "
            "가장 긴 것을 잠정 채택, 검토 권장",
        )

    # 입체정보 보유 SMILES 없음
    if len({p[1] for p in parsed}) == 1:
        best = max(parsed, key=lambda p: len(p[0]))
        return (
            False,
            best[0],
            "same_inchikey_notation_only: InChIKey 완전 동일(입체정보 없음) — "
            "SMILES 표기 차이일 뿐이므로 임의 대표(문자열 최장) 채택",
        )
    return (
        False,
        "",
        "same_skeleton_no_stereo_layer_differs: 골격 동일하나 입체정보 없이 "
        "InChIKey 2/3블록이 상이(전하·프로톤화 차이 추정) — 수동검토 필요",
    )


# --------------------------------------------------------------------------- #
def main() -> None:
    ing = pd.read_excel(SRC, sheet_name="ingredient")
    print(f"[로드] ingredient 시트 {len(ing)}행")

    df = ing[ing["smiles"].notna() & ing["cas"].notna()].copy()
    df["cas"] = df["cas"].astype(str).str.strip()
    df["smiles"] = df["smiles"].astype(str).str.strip()
    print(f"[대상] smiles·cas 동시 보유 {len(df)}행, 고유 CAS {df['cas'].nunique()}개")

    nuniq = df.groupby("cas")["smiles"].nunique()
    multi = sorted(nuniq[nuniq > 1].index)
    print(f"[스캔] distinct smiles >= 2인 CAS: {len(multi)}개")

    row_counts = df["cas"].value_counts()
    failures: list[tuple[str, str]] = []
    records = []

    for cas in multi:
        smiles_list = sorted(df.loc[df["cas"] == cas, "smiles"].unique())
        conflict, rec, reason = decide(cas, smiles_list, failures)
        records.append(
            {
                "cas": cas,
                "n_distinct_smiles": len(smiles_list),
                "skeleton_conflict": conflict,
                "all_smiles": "|".join(smiles_list),
                "recommended_smiles": rec,
                "recommendation_reason": reason,
                "affected_row_count": int(row_counts[cas]),
            }
        )

    out = pd.DataFrame(records, columns=[
        "cas", "n_distinct_smiles", "skeleton_conflict", "all_smiles",
        "recommended_smiles", "recommendation_reason", "affected_row_count",
    ])
    out.to_csv(OUT, index=False, encoding="utf-8-sig")

    # --- 로그 ---
    if failures:
        print(f"\n[RDKit 파싱 실패] {len(failures)}건 (건너뜀)")
        for cas, smi in failures:
            print(f"  cas={cas}  smiles={smi}")

    resolved = out[(~out["skeleton_conflict"]) & (out["recommended_smiles"] != "")]
    conflicts = out[out["skeleton_conflict"]]
    manual = out[(~out["skeleton_conflict"]) & (out["recommended_smiles"] == "")]

    print("\n[요약]")
    print(f"  다중 SMILES CAS            : {len(out)}개 / {int(out['affected_row_count'].sum())}행")
    print(f"  canonical 추천 완료        : {len(resolved)}개 CAS / {int(resolved['affected_row_count'].sum())}행")
    print(f"  skeleton_conflict(수동검토): {len(conflicts)}개 CAS / {int(conflicts['affected_row_count'].sum())}행")
    print(f"  기타 수동검토              : {len(manual)}개 CAS / {int(manual['affected_row_count'].sum())}행")
    print("\n[추천 근거 분포]")
    print(out["recommendation_reason"].str.split(":").str[0].value_counts().to_string())
    if len(conflicts):
        print("\n[골격 충돌 상세]")
        print(conflicts[["cas", "n_distinct_smiles", "all_smiles", "affected_row_count"]].to_string(index=False))
    if len(manual):
        print("\n[기타 수동검토 상세]")
        print(manual[["cas", "n_distinct_smiles", "all_smiles", "affected_row_count"]].to_string(index=False))

    print(f"\n저장: {OUT}  ({len(out)}행)")


if __name__ == "__main__":
    main()
