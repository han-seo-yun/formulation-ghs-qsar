"""점검본(`07_화학연구원_공유_260918/`)을 **6월 팀 공유본과 같은 배포 형식**으로 바꾼다.

기준 형식은 `formulation_audit_team_share_highlighted_only_20260630/` 이다. 실측한
그 형식의 특징 네 가지를 그대로 따른다.

  1. 워크북 하나(`formulation_ingredients_master_audited.xlsx`) · **제형 1행**.
     성분은 행이 아니라 `Formulation_Ingredients` 계열 열에 `" | "` 로 묶여 들어간다
  2. 근거 문서는 제형 폴더 없이 **평평하게** 두고 이름에 원본 해시를 쓴다 —
     `ingredient_source_audit/highlighted/` 와
     `ingredient_source_audit/ordered_option_runs/highlighted_sources/`
  3. 행과 파일의 연결은 `Audit_Highlighted_Source_File`(제품 단위) ·
     `Supplemental_Highlighted_Source_File`(대체 근거) 두 열의 **패키지 기준 상대경로**
  4. 값은 원값이다 — 판정·검토 열은 넣지 않는다

**제외한 것.** 검증 csv(우리 것 9개, 6월 판의 `source_manifest.csv` ·
`artifacts/share_packages/*.inventory|review_queue|summary`)와 설명 시트
(6월 판의 `Summary`·`Audit_Summary`, 우리 `요약`)는 넣지 않는다. md 도 없다.

**6월 판과 다르게 한 곳 하나.** 6월 판은 성분 목록을 묶을 때 빈 값을 버려서
성분 8개인데 CAS 가 7개, 농도가 2개로 **자리가 안 맞는다**. 여기서는 빈 자리를
빈 칸으로 남겨 모든 목록의 칸 수가 `Ingredient_Count` 와 같다 — 그래야 이름·CAS·농도를
자리로 맞출 수 있다.

    python3 build_share_audit_format.py
"""
from __future__ import annotations

import shutil
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_model as L                                            # noqa: E402
from build_share_dist import relabel                             # noqa: E402

SRC = L.ROOT / "07_화학연구원_공유_260918"
REF = L.ROOT / "formulation_audit_team_share_highlighted_only_20260630"
OUT = L.ROOT / "09_화학연구원_배포_감사형식_260920"
XLSX = "formulation_ingredients_master_audited.xlsx"
HL = "ingredient_source_audit/highlighted"
SUP = "ingredient_source_audit/ordered_option_runs/highlighted_sources"
SEP = " | "
log = L.make_logger(L.ROOT / "01_파이프라인" / "배포본_감사형식.log")

# 성분 열 → 워크북에서 쓸 이름. 6월 판의 `Formulation_Ingredients*` 관례를 따른다
ING_MAP = {
    "ing_name_best": "Formulation_Ingredients",
    "ing_cas_best": "Formulation_Ingredients_CAS",
    "ing_pct_best": "Formulation_Ingredients_Pct",
    "ing_pct_kind_best": "Formulation_Ingredients_Pct_Kind",
    "ing_role_final": "Formulation_Ingredients_Role",
    "ing_ghs_eye": "Formulation_Ingredients_GHS_Eye",
    "ing_ghs_skin": "Formulation_Ingredients_GHS_Skin",
    "ing_ghs_sens": "Formulation_Ingredients_GHS_Sens",
    "ing_ghs_indep_eye_cat": "Formulation_Ingredients_GHS_Indep_Eye_Cat",
    "ing_ghs_indep_eye_tier": "Formulation_Ingredients_GHS_Indep_Eye_Tier",
    "ing_ghs_indep_skin_cat": "Formulation_Ingredients_GHS_Indep_Skin_Cat",
    "ing_ghs_indep_skin_tier": "Formulation_Ingredients_GHS_Indep_Skin_Tier",
    "ing_ghs_indep_sens_cat": "Formulation_Ingredients_GHS_Indep_Sens_Cat",
    "ing_ghs_indep_sens_tier": "Formulation_Ingredients_GHS_Indep_Sens_Tier",
    "ing_src": "Ingredient_Source",
}
FORM_MAP = {
    "product_name": "Formulation_Name",
    "formulation_type_ko": "Formulation_Type",
    "y_eye": "Formulation_GHS_Eye",
    "y_skin": "Formulation_GHS_Skin",
    "y_sens": "Formulation_GHS_Sensitization",
    "ph_best": "Formulation_pH",
}
COLS = (["Formulation_ID", "Formulation_Name", "Formulation_Type"]
        + [v for k, v in ING_MAP.items() if k != "ing_src"]
        + ["Ingredient_Source", "Ingredient_Count",
           "Formulation_GHS_Eye", "Formulation_GHS_Skin",
           "Formulation_GHS_Sensitization", "Formulation_pH",
           "Audit_Highlighted_Source_File", "Supplemental_Highlighted_Source_File"])


def cell(v) -> str:
    """빈 값은 빈 칸으로 — 자리를 지운다면 목록끼리 맞춰볼 수 없다."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def copy_docs(INV: pd.DataFrame) -> tuple[dict, dict]:
    """점검본 문서를 6월 형식 경로·이름으로 옮기고 제형별 경로 목록을 돌려준다.

    이름은 6월 판의 원본 이름을 그대로 쓰되 제형 코드를 덧붙인다. 한 문서를 여러
    제형이 쓰는 경우(원본 490개 → 사본 703개)에 6월 이름만으로는 평평한 폴더에서
    부딧치고, 우리 사본은 **제형마다 따로 칠해서 내용도 다르다**. 그래서 제형 코드가
    이름에 있어야 한다."""
    for d in (HL, SUP):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    aud: dict[str, list[str]] = {}
    sup: dict[str, list[str]] = {}
    for r in INV.itertuples():
        src = SRC / str(r.archive_path)
        if not src.exists():
            raise SystemExit(f"점검본에 없는 문서: {r.archive_path}")
        orig = Path(str(r.원경로))
        into, box = (HL, aud) if r.kind == "제품단위_하이라이트" else (SUP, sup)
        want = HL if box is aud else SUP
        if orig.parent.as_posix() != want:
            raise SystemExit(f"{r.kind} 의 원경로가 {want} 가 아니다: {r.원경로}")
        stem = orig.stem[:-len("_highlighted")] \
            if orig.stem.endswith("_highlighted") else orig.stem
        rel = f"{into}/{stem}__{r.Formulation_ID}_highlighted{src.suffix}"
        shutil.copy2(src, OUT / rel)
        box.setdefault(r.Formulation_ID, []).append(rel)
    log(f"{HL}/ — {len(list((OUT / HL).iterdir())):,}건 · 제형 {len(aud):,}개")
    log(f"{SUP}/ — {len(list((OUT / SUP).iterdir())):,}건 · 제형 {len(sup):,}개")
    return aud, sup


def main() -> None:
    BOOK = SRC / "02_제품코드별_성분리스트.xlsx"
    FORM = pd.read_excel(BOOK, sheet_name="제형")
    ING = pd.read_excel(BOOK, sheet_name="성분")
    INV = pd.read_csv(SRC / "03_근거파일_인벤토리.csv")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    aud, sup = copy_docs(INV)
    n_pdf, n_annot = relabel(OUT, ING)
    log(f"정규화 주석 문구 교체 — 문서 {n_pdf}건 · 주석 {n_annot}개")

    # 제형 1행으로 접는다. 성분 순서는 ing_idx 그대로다
    ING = ING.sort_values(["Formulation_ID", "ing_idx"])
    g = ING.groupby("Formulation_ID", sort=False)
    joined = {ING_MAP[c]: g[c].apply(lambda s: SEP.join(cell(v) for v in s))
              for c in ING_MAP}
    W = pd.DataFrame({"Formulation_ID": FORM.Formulation_ID})
    for src, dst in FORM_MAP.items():
        W[dst] = FORM[src].map(cell).values
    for dst, s in joined.items():
        W[dst] = W.Formulation_ID.map(s).fillna("")
    W["Ingredient_Count"] = W.Formulation_ID.map(g.size()).fillna(0).astype(int)
    W["Audit_Highlighted_Source_File"] = W.Formulation_ID.map(
        lambda f: SEP.join(sorted(aud.get(f, []))))
    W["Supplemental_Highlighted_Source_File"] = W.Formulation_ID.map(
        lambda f: SEP.join(sorted(sup.get(f, []))))
    W = W[COLS]
    with pd.ExcelWriter(OUT / XLSX, engine="openpyxl") as w:
        W.to_excel(w, sheet_name="Formulations", index=False)

    # 검사
    if len(W) != len(FORM):
        raise SystemExit(f"제형 행수가 어긋난다 {len(W)} != {len(FORM)}")
    bad = sorted({p.suffix.lower() for p in OUT.rglob("*") if p.is_file()}
                 - {".pdf", ".html", ".htm", ".xlsx"})
    if bad:
        raise SystemExit(f"있어서는 안 되는 확장자: {bad}")
    if pd.ExcelFile(OUT / XLSX).sheet_names != ["Formulations"]:
        raise SystemExit("시트가 Formulations 하나가 아니다")
    # 목록 열의 칸 수가 모두 Ingredient_Count 와 같아야 자리를 맞춰볼 수 있다.
    # 빈 칸도 자리를 차지하므로 구분자 수로 센다 — 값이 다 비어 빈 문자열이 된 제형이
    # 70건 있는데(성분 1개, 이름 결측), 그건 성분이 없는 것과 다르다
    for c in ING_MAP.values():
        n = W[c].map(lambda s: s.count(SEP) + 1)
        off = W[(n != W.Ingredient_Count) & (W.Ingredient_Count > 0)]
        if len(off):
            raise SystemExit(f"{c} 칸 수가 성분 수와 다르다 {len(off)}행: "
                             f"{off.Formulation_ID.head(3).tolist()}")
    files = {q for col in ("Audit_Highlighted_Source_File",
                           "Supplemental_Highlighted_Source_File")
             for v in W[col] if v for q in v.split(SEP)}
    miss = [q for q in sorted(files) if not (OUT / q).exists()]
    if miss:
        raise SystemExit(f"연결된 경로가 안 열린다 {len(miss)}건: {miss[:3]}")
    ondisk = {p.relative_to(OUT).as_posix() for p in OUT.rglob("*")
              if p.is_file() and p.suffix.lower() != ".xlsx"}
    if ondisk != files:
        raise SystemExit(f"워크북에서 가리키지 않는 문서 {len(ondisk - files)}건 · "
                         f"없는 문서를 가리킴 {len(files - ondisk)}건")
    # 6월 판이 실제로 쓰던 열 이름을 그대로 쓰고 있는지 (형식이 틀어지면 알 수 있게)
    ref = pd.read_excel(REF / XLSX, sheet_name="Formulations", nrows=1)
    log(f"6월 판과 이름이 같은 열 {len([c for c in COLS if c in ref.columns])}/"
        f"{len(COLS)} — 우리만 쓰는 열 {[c for c in COLS if c not in ref.columns]}")
    log("")
    log(f"Formulations {len(W):,}행 {len(W.columns)}열 · 성분 {len(ING):,}행을 접었다 "
        f"(제품단위 근거 {int((W.Audit_Highlighted_Source_File != '').sum()):,}제형 · "
        f"대체근거 {int((W.Supplemental_Highlighted_Source_File != '').sum()):,}제형)")
    log(f"완료 → {OUT.name}/ (파일 {len(ondisk) + 1:,}건 · "
        f"{sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()) / 1024 ** 2:.0f} MB)")
    print(f"→ {OUT.name}")


if __name__ == "__main__":
    main()
