# Deep Interview Spec: 독성코드 시트 재검토필요 잔여건 재조사 + 추출정보 공란 검증 + 접근경로 탐색

## Metadata
- Interview ID: tox-review-recheck-20260826
- Rounds: 2
- Final Ambiguity Score: 16%
- Type: brownfield
- Generated: 2026-08-26
- Threshold: 0.2
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal Clarity | 0.8 | 35% | 0.28 |
| Constraint Clarity | 0.8 | 25% | 0.20 |
| Success Criteria | 0.9 | 25% | 0.225 |
| Context Clarity | 0.9 | 15% | 0.135 |
| **Total Clarity** | | | **0.84** |
| **Ambiguity** | | | **0.16** |

## Topology
| Component | Status | Description | Coverage |
|---|---|---|---|
| 잔여 258건 재검토 | active | 지난 라운드 실제 접근했으나 미해결 처리된 행(제품불일치 123·접근/추출실패 37·구형문서-GHS없음 20·기타 78)을 다른 방식으로 재시도 | 전건 1회 실시도 + 전수 보고 |
| 미착수 550건 접근경로 확보 | active | URL·로컬파일 전혀 없는 행에 대해 구조화 공개DB(PubChem/EPA PPLS/ECHA) 경로 추정 등 실제 우회 시도, 실패 시 신규 도구(웹서치 MCP 등) 확보 방법 탐색·제안 | 전건 1회 실시도 + 전수 보고 |
| 추출정보 공란 검증 | active | 독성코드 시트 전체에서 추출정보 공란 건수·사유를 재집계 | 전수 집계 |

## Goal
독성코드 시트의 재검토필요 808건(잔여 258 + 미착수 550) 전체를 대상으로, 지난 라운드보다 확장된 접근 경로(구조화 공개DB 직접 조회, curl+pdftotext 등 텍스트 추출 우회, 필요 시 새 도구 확보 방법 제안)로 1건씩 실제 재시도하고, 성공/실패 여부와 상세 사유를 전수 보고한다. 동시에 추출정보 공란 현황을 재집계해 정확한 사유 분포를 제시한다.

## Constraints
- 검색엔진(WebSearch) 도구 없음 — WebFetch(URL 직접 접근)만 가용. 구조화 공개DB(PubChem PUG-View REST API, EPA PPLS, ECHA C&L)는 URL 패턴 추정으로 접근 시도 가능.
- 성분/제품 불일치가 확인된 값은 임의로 삭제하지 않고 근거와 함께 재검토필요로 유지(사용자 최종 승인 필요 항목).
- 원본 `dataset_배정_검증미완.xlsx`는 이번에도 수정하지 않음. 작업 대상은 `dataset_배정_검증완료_20260824.xlsx`.
- 전량 해결이 목표가 아님 — 안 되는 건 사유만 정확히 남긴다(이번 세션 전체에 걸친 공통 원칙).

## Non-Goals
- 100% 해결 보장 없음(검색 도구 부재로 일부는 이번 라운드에도 미해결로 남을 수 있음).
- 팀원에게 새 URL을 요청하는 행위(이번 라운드는 Claude 단독 재시도).

## Acceptance Criteria
- [ ] 808건(258+550) 전건에 대해 최소 1회 실제 접근 시도(WebFetch/구조화DB URL 추정) 완료
- [ ] 시도 결과가 `검증여부`/`검증근거` 컬럼에 반영됨(검증완료/정보없음/재검토필요 중 하나로 갱신, 사유 포함)
- [ ] 추출정보 공란 현황(건수·사유별 분포)이 재집계되어 보고됨
- [ ] 끝까지 해결되지 않은 건에 대해, 신규 도구(웹서치 MCP 등) 확보가 필요하다는 결론이면 구체적 방법을 제시
- [ ] 원본 파일 무손상 확인

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|---|---|---|
| "재검토필요 SDS를 다시 서치"가 550건(접근경로 자체 없음)까지 포함하는지 | Round 0에서 토폴로지로 명시 분리 | 사용자가 3개 구성요소 모두 확인(맞음) |
| "접근 방법 탐색"이 실제 시도인지 조사 보고서인지 | Round 1 질문 | 실제 우회 시도 + 실패 시 새도구 탐색으로 확정 |
| 종료 기준(성공률 목표 vs 전수 시도) | Round 2 질문 | 전건 1회 실시도 후 전수 보고로 확정(권장안 채택) |

## Technical Context
- 대상 파일: `/Users/hanseoyun/Desktop/260820/dataset_배정_검증완료_20260824.xlsx` (독성코드 시트)
- 지난 라운드 산출물: `work/verify/tox_merged_results.json`(451건 결과), `work/verify_sheets_v2.py`(규칙엔진)
- 가용 도구: WebFetch(URL 지정 fetch), Read(로컬 PDF), Agent(병렬 서브에이전트, 이미 세션 초반 승인됨)
- 미가용: WebSearch/구글 검색 MCP — 이번 세션에서 확인된 제약

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|---|---|---|---|
| 독성코드 행 | core domain | Formulation_ID, product_name, tox_signal_word, tox_h_statements, cat_eye/skin/sens_ghs, 검증여부, 검증근거 | 1 제형 = 1행 |
| 재검토필요 사유 | supporting | 제품불일치/접근실패/구형문서/기타/출처없음 | 행에 귀속 |
| 접근경로 시도 | supporting | 시도 URL, 성공여부, 근거인용 | 행에 귀속, 여러 번 시도 가능 |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|---|---|---|---|---|---|
| 1 | 3 | 3 | - | - | N/A |
| 2 | 3 | 0 | 0 | 3 | 100% |

## Interview Transcript
<details>
<summary>전체 Q&A (2 rounds)</summary>

### Round 0 (토폴로지 확인)
**Q:** 3개 구성요소(잔여258 재검토 / 미착수550 접근경로 / 추출정보 공란검증)로 나눈 게 맞나요?
**A:** 맞음

### Round 1
**Q:** 미착수 550건에 대해 "접근 가능한 방법 탐색"을 어느 수준까지 원하시나요?
**A:** 실제로 우회 시도 및 실패 시 새도구 확보 방법 탐색
**명확도:** 34.5%

### Round 2
**Q:** 808건을 실제로 우회 시도할 때, 어떻게 종료 기준을 정하면 되나요?
**A:** 전건 1번씩 실제 시도 후 전수 보고 (권장)
**명확도:** 16%

</details>
