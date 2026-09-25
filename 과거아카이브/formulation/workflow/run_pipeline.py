"""
Formulation Characteristics Pipeline — Master Runner
전체 파이프라인을 단계별로 실행한다.

사용법:
  # 전체 실행
  python workflow/run_pipeline.py

  # 특정 단계부터 재시작 (이전 단계 캐시 사용)
  python workflow/run_pipeline.py --from-phase 2

  # API 키 없이 Phase 0.5까지만 (PDF 파싱)
  python workflow/run_pipeline.py --no-llm

단계:
  Phase 0.5  pdf_section_extractor   highlighted/ PDF → section_extracts/ JSON
  Phase 1    enrich_with_sections    review_queue + section_extracts → enriched_queue.csv
  Phase 2    extract_formulation_features  enriched_queue → LLM 5-agent 병렬 추출
  Phase 3    (자동) 결과 병합 → formulation_characteristics_output.csv + .xlsx
"""

import sys
import argparse
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
WORKFLOW  = Path(__file__).parent

# ─── 단계 정의 ────────────────────────────────────────────────────────────────
PHASES = {
    "0.5": {
        "name": "PDF Section Extractor",
        "script": WORKFLOW / "pdf_section_extractor.py",
        "output_check": BASE_DIR / "ingredient_source_audit/section_extracts",
        "needs_api": False,
        "description": "highlighted/ PDF 411개 → Section별 JSON 추출 (LLM 없음, ~2분)",
    },
    "1": {
        "name": "Enrich with Sections",
        "script": WORKFLOW / "enrich_with_sections.py",
        "output_check": BASE_DIR / "artifacts/enriched_queue.csv",
        "needs_api": False,
        "description": "review_queue + section_extracts 조인 → enriched_queue.csv",
    },
    "1.5": {
        "name": "Pre-fill Merge",
        "script": WORKFLOW / "pre_fill_merge.py",
        "output_check": BASE_DIR / "artifacts/enriched_queue_prefilled.csv",
        "needs_api": False,
        "description": "Formulation_Name 패턴 추출 → name_formulation_code 컬럼 추가",
    },
    "2": {
        "name": "LLM Feature Extraction",
        "script": WORKFLOW / "extract_formulation_features.py",
        "output_check": BASE_DIR / "artifacts/formulation_characteristics_output.csv",
        "needs_api": True,
        "description": "enriched_queue → 5-agent 병렬 LLM 추출 (~15-20분, API 비용 발생)",
    },
}


def check_output_exists(phase_key: str) -> bool:
    """단계 출력이 이미 존재하는지 확인."""
    check = PHASES[phase_key]["output_check"]
    if isinstance(check, Path) and check.exists():
        if check.is_dir():
            return any(check.iterdir())
        return True
    return False


def run_phase(phase_key: str, force: bool = False) -> bool:
    """단일 단계 실행. 성공 여부 반환."""
    phase = PHASES[phase_key]
    print(f"\n{'='*60}")
    print(f"Phase {phase_key}: {phase['name']}")
    print(f"  {phase['description']}")
    print(f"{'='*60}")

    if not force and check_output_exists(phase_key):
        print(f"  → 출력 이미 존재 (스킵). --force 옵션으로 재실행 가능.")
        return True

    t0 = time.time()
    result = subprocess.run(
        [sys.executable, str(phase["script"])],
        cwd=str(BASE_DIR),
    )
    elapsed = time.time() - t0

    if result.returncode == 0:
        print(f"  → 완료 ({elapsed:.0f}초)")
        return True
    else:
        print(f"  → 실패 (exit code {result.returncode}, {elapsed:.0f}초)")
        return False


def print_status():
    """현재 파이프라인 상태 출력."""
    print("\n현재 파이프라인 상태:")
    for key, phase in PHASES.items():
        exists = check_output_exists(key)
        status = "✓ 완료" if exists else "○ 미완료"
        print(f"  Phase {key:3s}  {status}  {phase['name']}")


def main():
    parser = argparse.ArgumentParser(description="Formulation Characteristics Pipeline")
    parser.add_argument("--from-phase", default="0.5",
                        choices=["0.5", "1", "1.5", "2"],
                        help="이 단계부터 실행 (기본: 0.5 = 처음부터)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Phase 2 (LLM) 제외하고 Phase 0.5~1만 실행")
    parser.add_argument("--force", action="store_true",
                        help="출력이 이미 있어도 재실행")
    parser.add_argument("--status", action="store_true",
                        help="파이프라인 상태만 확인하고 종료")
    args = parser.parse_args()

    print("=" * 60)
    print("Formulation Characteristics Pipeline")
    print("=" * 60)

    if args.status:
        print_status()
        return

    # AWS Bedrock 자격증명 확인 (Phase 2 실행 예정인 경우)
    import os
    if not args.no_llm and args.from_phase in ["0.5", "1", "1.5", "2"]:
        has_iam = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
        has_bearer = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        if not has_iam and not has_bearer:
            print("\n경고: AWS 자격증명 미설정 (AWS_ACCESS_KEY_ID/SECRET 또는 AWS_BEARER_TOKEN_BEDROCK)")
            print("  Phase 2 (LLM 추출) 는 건너뜁니다.")
            print("  환경변수 설정 후 --from-phase 2 로 재실행하세요:")
            print("    export AWS_ACCESS_KEY_ID=...")
            print("    export AWS_SECRET_ACCESS_KEY=...")
            print("    export AWS_DEFAULT_REGION=us-east-1")
            args.no_llm = True

    phase_order = ["0.5", "1", "1.5", "2"]
    start_idx = phase_order.index(args.from_phase)
    phases_to_run = phase_order[start_idx:]
    if args.no_llm:
        phases_to_run = [p for p in phases_to_run if not PHASES[p]["needs_api"]]

    print(f"\n실행할 단계: {' → '.join(f'Phase {p}' for p in phases_to_run)}")
    print_status()

    total_start = time.time()
    for phase_key in phases_to_run:
        ok = run_phase(phase_key, force=args.force)
        if not ok:
            print(f"\n파이프라인 중단: Phase {phase_key} 실패")
            print("문제 해결 후 --from-phase {phase_key} 로 재시작하세요.")
            sys.exit(1)

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"전체 완료 ({total_elapsed:.0f}초 = {total_elapsed/60:.1f}분)")
    print_status()

    # 최종 결과 요약
    output = BASE_DIR / "artifacts/formulation_characteristics_output.csv"
    if output.exists():
        import pandas as pd
        df = pd.read_csv(output)
        print(f"\n최종 출력: {output}")
        print(f"  행: {len(df)}, 컬럼: {len(df.columns)}")


if __name__ == "__main__":
    main()
