# QSAR Irritation Model - 배포 가이드

**버전**: 1.0  
**날짜**: 2026-06-29  
**프로젝트**: Eye & Skin Irritation QSAR

---

## 📋 개요

이 프로젝트는 안구 및 피부 자극성 예측을 위한 **4개의 QSAR 모델**을 제공합니다:

1. **Binary 모델 (2개)**: Irritant vs Non-irritant 이진 분류
2. **Ordinal 모델 (2개)**: EPA 4-class 등급 예측

---

## 🎯 모델 선택 가이드

### Binary 모델을 사용하세요:

✅ 화합물 스크리닝 (자극성 있음/없음만 판단)  
✅ 규제 준수 (REACH, GHS)  
✅ 높은 민감도가 중요한 경우  
✅ Threshold 조정이 필요한 경우

**위치**: `deployment_package/`

### Ordinal 모델을 사용하세요:

✅ 위험도 등급 평가 (EPA 1,2,3,4)  
✅ 화합물 우선순위 결정  
✅ 자세한 등급 정보가 필요한 경우  
✅ 규제 문서에 EPA 등급 명시가 필요한 경우

**위치**: `deployment_package_ordinal/`

### 통합 전략 (권장):

1. **1차 스크리닝**: Binary 모델로 Irritant 여부 판단
2. **2차 등급 평가**: Positive 화합물에 대해 Ordinal 모델로 EPA 등급 예측
3. **최종 판단**: 두 모델 결과를 종합

---

## 📦 배포 패키지 구조

```
aidd/model/
├── deployment_package/              # Binary 모델
│   ├── qsar_prediction.py
│   ├── README.md
│   ├── model_config.json
│   ├── requirements.txt
│   └── examples/
│       └── example_usage.py
│
├── deployment_package_ordinal/      # Ordinal 모델
│   ├── qsar_ordinal_prediction.py
│   ├── README_ORDINAL.md
│   ├── model_config_ordinal.json
│   └── examples/
│       └── example_ordinal_usage.py
│
├── applicability_domain_results/    # AD 체크
│   ├── applicability_domain_summary.json
│   ├── applicability_domain_check.py
│   └── applicability_domain_distribution.png
│
├── external_validation_results/     # 외부 검증
│   ├── external_validation_summary.json
│   └── external_validation_curves.png
│
├── ordinal_model_results/           # Ordinal 학습 결과
│   ├── ordinal_model_summary.json
│   ├── ordinal_confusion_matrices.png
│   └── ordinal_training_log.txt
│
└── FINAL_REPORT.md                  # 최종 보고서
```

---

## 🚀 빠른 시작

### 1. 설치

```bash
cd deployment_package
pip install -r requirements.txt
```

### 2. Binary 모델 사용

```python
from qsar_prediction import IrritationPredictor

predictor = IrritationPredictor()

# 예측
result = predictor.predict("CCO", dataset='eye')

print(f"예측: {result['prediction']}")        # 'Irritant' or 'Non-irritant'
print(f"확률: {result['probability']:.3f}")   # 0.650
print(f"신뢰도: {result['confidence']}")      # 'High'/'Medium'/'Low'
```

### 3. Ordinal 모델 사용

```python
from qsar_ordinal_prediction import IrritationOrdinalPredictor

predictor = IrritationOrdinalPredictor()

# 예측
result = predictor.predict("CCO", dataset='eye')

print(f"EPA 등급: {result['epa_class']}")      # 1,2,3,4
print(f"해석: {result['interpretation']}")     # EPA Class 3 (Mildly Irritating)
print(f"확률: {result['probabilities']}")      # [0.1, 0.2, 0.5, 0.2]
```

---

## 📊 모델 성능 요약

### Binary 모델

| 모델 | 알고리즘 | Threshold | ROC-AUC | Sensitivity | Specificity |
|------|----------|-----------|---------|-------------|-------------|
| Eye | Gradient Boosting | 0.32 | 0.765 | 70% | 66% |
| Skin | SVM (RBF) | 0.47 | 0.756 | 72% | 73% |

**검증**: Holdout 20%, Group-aware split

### Ordinal 모델

| 모델 | 알고리즘 | Quadratic Kappa | Accuracy | MAE |
|------|----------|-----------------|----------|-----|
| Eye | Gradient Boosting | 0.405 | 41% | 0.789 |
| Skin | Random Forest | 0.100 | 71% | 0.476 |

**검증**: Holdout 20%, Group-aware split

### Applicability Domain

| 데이터셋 | 방법 | Threshold | 학습 샘플 |
|---------|------|-----------|----------|
| Eye | Tanimoto (ECFP4) | 0.303 | 1,107 |
| Skin | Tanimoto (ECFP4) | 1.000 | 1,708 |

⚠️ **경고**: Skin threshold 1.000은 매우 높음 - 새 화합물 대부분이 AD 밖일 가능성

---

## ⚠️ 사용 시 주의사항

### 1. Applicability Domain 확인

```python
from applicability_domain_check import check_applicability_domain

smiles = "CCO"
result = check_applicability_domain(smiles, dataset='eye')

if not result['in_AD']:
    print(f"경고: {result['warning']}")
    # 예측을 주의해서 사용하세요
```

### 2. Threshold 조정 (Binary 모델)

용도에 따라 threshold 조정:
- **안전 중시** (False Negative 최소화): Threshold 낮춤 → Sensitivity ↑
- **정확도 중시** (False Positive 최소화): Threshold 높임 → Specificity ↑

```python
result = predictor.predict("CCO", dataset='eye')
prob = result['probability']

# Custom threshold
my_threshold = 0.40
my_prediction = 'Irritant' if prob >= my_threshold else 'Non-irritant'
```

### 3. 모델 한계

**Binary 모델**:
- EPA 4-class 정보 손실
- 등급 구분 불가

**Ordinal 모델**:
- 클래스 불균형 (특히 Skin EPA 4: 80%)
- 인접 클래스 혼동 (EPA 2 vs 3)
- Binary보다 낮은 성능

**공통**:
- In Vivo 데이터만 학습 (In Vitro 포함 안 됨)
- Mixture 데이터 포함 (제형 효과 부분적)
- 독립적인 외부 검증 권장

---

## 📚 추가 문서

1. **Binary 모델**
   - `deployment_package/README.md`: 상세 사용 가이드
   - `deployment_package/model_config.json`: 모델 설정 및 성능 지표

2. **Ordinal 모델**
   - `deployment_package_ordinal/README_ORDINAL.md`: 상세 사용 가이드
   - `deployment_package_ordinal/model_config_ordinal.json`: 모델 설정 및 성능 지표

3. **프로젝트 전체**
   - `FINAL_REPORT.md`: 전체 프로젝트 보고서
   - `external_validation_results/`: 외부 검증 결과
   - `applicability_domain_results/`: AD 평가 결과
   - `ordinal_model_results/`: Ordinal 모델 학습 결과

---

## 🔍 예제: 통합 사용

```python
from qsar_prediction import IrritationPredictor
from qsar_ordinal_prediction import IrritationOrdinalPredictor
from applicability_domain_check import check_applicability_domain

# 초기화
binary_predictor = IrritationPredictor()
ordinal_predictor = IrritationOrdinalPredictor()

smiles = "CCO"
dataset = 'eye'

# 1. AD 체크
ad_result = check_applicability_domain(smiles, dataset)
print(f"AD: {ad_result['in_AD']}, Similarity: {ad_result['similarity']:.3f}")

# 2. Binary 예측
binary_result = binary_predictor.predict(smiles, dataset)
print(f"Binary: {binary_result['prediction']} (P={binary_result['probability']:.3f})")

# 3. Ordinal 예측 (Binary가 Irritant인 경우만)
if binary_result['prediction'] == 'Irritant':
    ordinal_result = ordinal_predictor.predict(smiles, dataset)
    print(f"Ordinal: EPA {ordinal_result['epa_class']} - {ordinal_result['interpretation']}")

# 4. 최종 판단
if not ad_result['in_AD']:
    print("⚠️ 경고: 예측 신뢰도 낮음 (AD 밖)")
elif binary_result['prediction'] == 'Irritant':
    print(f"⚠️ 자극성 있음 (EPA {ordinal_result['epa_class']} 추정)")
else:
    print("✅ 자극성 없음")
```

---

## 📞 지원

- **문의**: 프로젝트 관리자
- **문서**: `FINAL_REPORT.md`
- **버그 리포트**: 이슈 트래커

---

**Copyright**: 2026  
**License**: 내부 사용 전용  
**최종 업데이트**: 2026-06-29
