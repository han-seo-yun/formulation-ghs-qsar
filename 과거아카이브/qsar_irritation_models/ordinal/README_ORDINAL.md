# QSAR Irritation Model - Ordinal 배포 패키지 (EPA 4-class)

**버전**: 1.0
**날짜**: 2026-06-29
**모델**: Eye & Skin Irritation QSAR (Ordinal Classification)

---

## 📦 패키지 내용

```
deployment_package_ordinal/
├── qsar_ordinal_prediction.py   # Ordinal 예측 파이프라인
├── README.md                    # 이 문서
├── model_config_ordinal.json    # 모델 설정
└── examples/
    └── example_ordinal_usage.py # 사용 예제
```

---

## 🚀 빠른 시작

### 1. 사용

```python
from qsar_ordinal_prediction import IrritationOrdinalPredictor

# 초기화
predictor = IrritationOrdinalPredictor()

# 예측
result = predictor.predict("CCO", dataset='eye')

print(f"EPA 등급: {result['epa_class']}")
print(f"해석: {result['interpretation']}")
print(f"신뢰도: {result['confidence']}")
```

---

## 📊 모델 사양

### Eye Irritation (Ordinal)

- **알고리즘**: Gradient Boosting
- **클래스**: 4-class (EPA 1-4)
- **성능** (Holdout):
  - Accuracy: 40.8%
  - Balanced Accuracy: 39.2%
  - MAE: 0.789
  - Quadratic Kappa: 0.405

### Skin Irritation (Ordinal)

- **알고리즘**: Random Forest
- **클래스**: 4-class (EPA 1-4)
- **성능** (Holdout):
  - Accuracy: 70.5%
  - Balanced Accuracy: 39.3%
  - MAE: 0.476
  - Quadratic Kappa: 0.100

---

## 🔧 API 레퍼런스

### `IrritationOrdinalPredictor.predict(smiles, dataset='eye', mixture=False)`

**파라미터**:
- `smiles` (str): SMILES string
- `dataset` (str): 'eye' or 'skin'
- `mixture` (bool): 혼합물 여부

**반환값** (dict):
- `prediction`: 예측 클래스 (0,1,2,3)
- `epa_class`: EPA 등급 (1,2,3,4)
- `interpretation`: 해석 문자열
- `probabilities`: 각 클래스 확률 리스트
- `confidence`: 'High', 'Medium', 'Low'

---

## 📚 EPA 클래스 해석

| Class | EPA | 해석 | 위험도 |
|-------|-----|------|--------|
| 0 | 1 | Corrosive/Severely Irritating | 매우 높음 |
| 1 | 2 | Moderately Irritating | 높음 |
| 2 | 3 | Mildly Irritating | 중간 |
| 3 | 4 | Non-irritating | 낮음 |

---

## 🔍 Binary vs Ordinal 모델

### Binary 모델
- **목적**: Irritant vs Non-irritant 이진 분류
- **장점**: 높은 민감도/특이도, Threshold 조정 가능
- **용도**: 스크리닝, 규제 준수

### Ordinal 모델
- **목적**: EPA 4-class 원본 정보 보존
- **장점**: 세밀한 등급 예측, Ordinal 관계 유지
- **용도**: 위험도 평가, 우선순위 결정

---

## 📞 지원

- **Binary 모델 문서**: deployment_package/README.md
- **Ordinal 모델 결과**: ordinal_model_results/
- **최종 보고서**: FINAL_REPORT.md

---

**Copyright**: 2026
**License**: 내부 사용 전용
