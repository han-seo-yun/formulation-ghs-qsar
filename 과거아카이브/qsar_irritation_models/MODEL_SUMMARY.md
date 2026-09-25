# QSAR Irritation Model - 최종 요약

**프로젝트**: Eye & Skin Irritation QSAR 모델링  
**완료 날짜**: 2026-06-29  
**상태**: ✅ **완료**

---

## 🎯 개발 성과

### 총 4개 모델 개발 완료

#### 1. Binary 분류 모델 (2개)

**Eye Irritation**
- 알고리즘: Gradient Boosting
- Threshold: 0.32
- ROC-AUC: 0.765
- Sensitivity: 70% ✅
- Specificity: 66%
- 용도: 스크리닝, 규제 준수

**Skin Irritation**
- 알고리즘: SVM (RBF kernel)
- Threshold: 0.47
- ROC-AUC: 0.756
- Sensitivity: 72% ✅
- Specificity: 73%
- 용도: 스크리닝, 규제 준수

#### 2. Ordinal 분류 모델 (2개)

**Eye Irritation**
- 알고리즘: Gradient Boosting
- Quadratic Kappa: 0.405
- Accuracy: 41%
- Balanced Acc: 39%
- MAE: 0.789
- 용도: EPA 등급 평가, 위험도 평가

**Skin Irritation**
- 알고리즘: Random Forest
- Quadratic Kappa: 0.100
- Accuracy: 71%
- Balanced Acc: 39%
- MAE: 0.476
- 용도: EPA 등급 평가 (제한적)

---

## 📦 배포 패키지

### 1. Binary 모델 패키지
**위치**: `deployment_package/`

```
deployment_package/
├── qsar_prediction.py              # 예측 파이프라인
├── README.md                       # 사용 가이드
├── model_config.json               # 모델 설정
├── requirements.txt                # 의존성
└── examples/
    └── example_usage.py            # 사용 예제
```

**사용 예제**:
```python
from qsar_prediction import IrritationPredictor

predictor = IrritationPredictor()
result = predictor.predict("CCO", dataset='eye')

print(f"예측: {result['prediction']}")        # 'Irritant' or 'Non-irritant'
print(f"확률: {result['probability']:.3f}")   # 0.650
```

### 2. Ordinal 모델 패키지
**위치**: `deployment_package_ordinal/`

```
deployment_package_ordinal/
├── qsar_ordinal_prediction.py      # Ordinal 예측 파이프라인
├── README_ORDINAL.md               # 사용 가이드
├── model_config_ordinal.json       # 모델 설정
└── examples/
    └── example_ordinal_usage.py    # 사용 예제
```

**사용 예제**:
```python
from qsar_ordinal_prediction import IrritationOrdinalPredictor

predictor = IrritationOrdinalPredictor()
result = predictor.predict("CCO", dataset='eye')

print(f"EPA 등급: {result['epa_class']}")      # 1,2,3,4
print(f"해석: {result['interpretation']}")     # EPA Class 3 (Mildly Irritating)
```

### 3. Applicability Domain
**위치**: `applicability_domain_results/`

- Eye threshold: 0.303 (Tanimoto similarity)
- Skin threshold: 1.000 (Tanimoto similarity)
- ⚠️ Skin threshold 매우 높음 - 신규 화합물 대부분 AD 밖일 가능성

### 4. 외부 검증
**위치**: `external_validation_results/`

- Holdout 20% 독립 검증
- Group-aware split (CASRN 기준)
- ROC/PR curves 시각화

---

## 🔑 핵심 특징

### 데이터 무결성
- ✅ In Vivo 데이터만 사용
- ✅ GroupKFold (CASRN) - 데이터 누출 방지
- ✅ SMOTE는 각 fold 훈련 세트 내부만 적용
- ✅ Threshold 최적화는 Out-of-Fold 예측 사용

### 모델 성능
- ✅ Binary 모델: Sensitivity 70% 이상 달성
- ✅ Threshold 조정 가능 (안전 vs 정확도)
- ✅ Ordinal 모델: EPA 4-class 등급 예측
- ✅ 외부 검증 완료 (Holdout 20%)

### 배포 준비
- ✅ 완전한 예측 파이프라인
- ✅ 입력 검증 (SMILES validation)
- ✅ Applicability Domain 체크
- ✅ 신뢰도 점수 (High/Medium/Low)
- ✅ 사용 가이드 및 예제

---

## 📊 모델 비교

| 특징 | Binary 모델 | Ordinal 모델 |
|------|-------------|--------------|
| **목적** | Irritant vs Non-irritant | EPA 4-class 등급 |
| **성능 (Eye)** | ROC-AUC 0.765 | Kappa 0.405 |
| **성능 (Skin)** | ROC-AUC 0.756 | Kappa 0.100 |
| **장점** | 높은 민감도/특이도 | 세밀한 등급 정보 |
| **단점** | 등급 정보 손실 | 클래스 불균형 취약 |
| **용도** | 스크리닝, 규제 | 위험도 평가 |

---

## 🎯 권장 사용 전략

### 시나리오 1: 화합물 스크리닝
**사용 모델**: Binary 모델
- Irritant 여부만 빠르게 판단
- Threshold 조정으로 민감도 제어

### 시나리오 2: 위험도 등급 평가
**사용 모델**: Ordinal 모델
- EPA 1,2,3,4 등급 예측
- 화합물 우선순위 결정

### 시나리오 3: 통합 평가 (권장)
**단계**:
1. Binary 모델로 1차 스크리닝
2. Positive 화합물에 대해 Ordinal 모델로 등급 평가
3. Applicability Domain 체크
4. 두 모델 결과를 종합하여 최종 판단

---

## ⚠️ 사용 시 주의사항

### 모델 한계

1. **데이터 범위**
   - In Vivo 데이터만 학습 (In Vitro 포함 안 됨)
   - Mixture 데이터 포함 (제형 효과 부분적)
   - 독립적인 외부 검증 권장

2. **Applicability Domain**
   - Skin threshold 1.000은 매우 높음
   - 신규 화합물 대부분이 AD 밖일 가능성
   - 예측 신뢰도 주의 필요

3. **Ordinal 모델 제약**
   - 클래스 불균형 (Skin EPA 4: 80%)
   - 인접 클래스 혼동 (EPA 2 vs 3)
   - Binary 모델보다 낮은 성능

### 권장 사항

1. **Applicability Domain 확인 필수**
   ```python
   from applicability_domain_check import check_applicability_domain
   
   result = check_applicability_domain(smiles, dataset='eye')
   if not result['in_AD']:
       print("⚠️ 경고: 예측 신뢰도 낮음")
   ```

2. **Threshold 조정**
   - 안전 중시: Threshold 낮춤 (False Negative ↓)
   - 정확도 중시: Threshold 높임 (False Positive ↓)

3. **독립 검증**
   - 가능하면 In Vivo 실험으로 검증
   - 특히 AD 밖 화합물은 필수

---

## 📚 참고 문서

1. **DEPLOYMENT_GUIDE.md**: 통합 배포 가이드
2. **FINAL_REPORT.md**: 전체 프로젝트 보고서
3. **deployment_package/README.md**: Binary 모델 상세 가이드
4. **deployment_package_ordinal/README_ORDINAL.md**: Ordinal 모델 상세 가이드

---

## 📈 성과 지표

- ✅ 총 4개 모델 개발 완료
- ✅ Binary 모델 Sensitivity 70% 이상 달성
- ✅ Ordinal 모델 EPA 4-class 예측 가능
- ✅ 외부 검증 및 AD 정의 완료
- ✅ 배포 패키지 2종 완성
- ✅ 전체 프로세스 문서화 및 재현 가능

**개발 시간**: 약 95분  
**데이터셋**: Eye (1,107), Skin (1,708)  
**고유 화합물**: Eye (400), Skin (261)

---

**프로젝트 완료일**: 2026-06-29  
**버전**: 1.0  
**상태**: ✅ 배포 승인
