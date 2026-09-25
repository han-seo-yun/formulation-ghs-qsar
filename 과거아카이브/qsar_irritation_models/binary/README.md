# QSAR Irritation Model - 배포 패키지

**버전**: 1.0
**날짜**: 2026-06-29
**모델**: Eye & Skin Irritation QSAR

---

## 📦 패키지 내용

```
deployment_package/
├── qsar_prediction.py          # 예측 파이프라인
├── README.md                   # 이 문서
├── model_config.json           # 모델 설정
├── requirements.txt            # 의존성
└── examples/                   # 사용 예제
    └── example_usage.py
```

---

## 🚀 빠른 시작

### 1. 설치

```bash
pip install -r requirements.txt
```

### 2. 사용

```python
from qsar_prediction import IrritationPredictor

# 초기화
predictor = IrritationPredictor()

# 예측
result = predictor.predict("CCO", dataset='eye')

print(f"예측: {result['prediction']}")
print(f"확률: {result['probability']:.3f}")
print(f"신뢰도: {result['confidence']}")
```

---

## 📊 모델 사양

### Eye Irritation

- **알고리즘**: Gradient Boosting
- **Threshold**: 0.32
- **성능** (Holdout):
  - ROC-AUC: 0.765
  - Sensitivity: 81.7%
  - Specificity: 57.0%

### Skin Irritation

- **알고리즘**: SVM (RBF kernel)
- **Threshold**: 0.47
- **성능** (Holdout):
  - ROC-AUC: 0.756
  - Sensitivity: 76.5%
  - Specificity: 64.8%

---

## 🔧 API 레퍼런스

### `IrritationPredictor.predict(smiles, dataset='eye', mixture=False)`

**파라미터**:
- `smiles` (str): SMILES string
- `dataset` (str): 'eye' or 'skin'
- `mixture` (bool): 혼합물 여부

**반환값** (dict):
- `prediction`: 'Irritant' or 'Non-irritant'
- `probability`: 자극성 확률 (0~1)
- `confidence`: 'High', 'Medium', 'Low'
- `threshold`: 사용된 threshold
- `ad_warning`: AD 경고 메시지 (있는 경우)

---

## ⚠️ 주의사항

### Applicability Domain

모델이 신뢰할 수 있는 화학 구조 범위가 있습니다:
- **High confidence**: 학습 데이터와 유사한 구조
- **Medium confidence**: 일부 유사성 있음
- **Low confidence**: 학습 데이터와 매우 다름

`ad_warning`이 있는 경우 예측을 주의해서 사용하세요.

### Threshold 조정

용도에 따라 threshold 조정 가능:
- **안전 중시**: Threshold 낮춤 → Sensitivity 증가
- **정확도 중시**: Threshold 높임 → Specificity 증가

---

## 📚 예제

### 예제 1: 단일 예측

```python
predictor = IrritationPredictor()
result = predictor.predict("c1ccccc1", dataset='skin')

if result['confidence'] == 'Low':
    print(f"경고: {result['ad_warning']}")
```

### 예제 2: 다중 예측

```python
smiles_list = ["CCO", "c1ccccc1", "CC(C)O"]
results = predictor.predict_batch(smiles_list, dataset='eye')

for result in results:
    print(f"{result['smiles']}: {result['prediction']}")
```

### 예제 3: Custom Threshold

```python
result = predictor.predict("CCO", dataset='eye')
prob = result['probability']

# Custom threshold
my_threshold = 0.40
my_prediction = 'Irritant' if prob >= my_threshold else 'Non-irritant'
```

---

## 🔍 제한사항

1. **In Vivo 데이터만 학습**: In vitro 데이터는 포함되지 않음
2. **Mixture 제한**: SMILES는 활성 성분만 표현, 전체 제형 효과는 부분적
3. **Binary 분류**: EPA 4-class를 2-class로 단순화
4. **외부 검증 필요**: 독립적인 데이터셋으로 추가 검증 권장

---

## 📞 지원

- **문서**: FINAL_REPORT.md
- **모델 성능**: external_validation_results/
- **Applicability Domain**: applicability_domain_results/

---

**Copyright**: 2026
**License**: 내부 사용 전용
