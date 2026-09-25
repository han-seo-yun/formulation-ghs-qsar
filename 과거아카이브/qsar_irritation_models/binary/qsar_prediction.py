"""
QSAR Irritation Model - Prediction Pipeline

사용 방법:
    from qsar_prediction import IrritationPredictor

    # 초기화
    predictor = IrritationPredictor()

    # 예측
    result = predictor.predict("CCO", dataset='eye')

    print(f"예측: {result['prediction']}")  # 'Irritant' or 'Non-irritant'
    print(f"확률: {result['probability']:.3f}")
    print(f"신뢰도: {result['confidence']}")
"""

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors
from rdkit.ML.Descriptors import MoleculeDescriptors
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.svm import SVC

# 모델 설정
MODEL_CONFIG = {
    'eye': {
        'algorithm': 'GradientBoostingClassifier',
        'threshold': 0.32,
        'params': {
            'n_estimators': 100,
            'learning_rate': 0.05,
            'max_depth': 3,
            'subsample': 1.0,
            'random_state': 42
        }
    },
    'skin': {
        'algorithm': 'SVC',
        'threshold': 0.47,
        'params': {
            'C': 0.1,
            'gamma': 0.001,
            'kernel': 'rbf',
            'class_weight': 'balanced',
            'probability': True,
            'random_state': 42
        }
    }
}

# Applicability Domain Thresholds
AD_THRESHOLDS = {
    'eye': 0.7000,  # 실제 값은 applicability_domain.py 결과로 업데이트
    'skin': 0.7000
}

class IrritationPredictor:
    """안구/피부 자극성 예측 클래스"""

    def __init__(self):
        """초기화"""
        self.descriptor_names = [
            'MolWt', 'MolLogP', 'TPSA', 'NumHAcceptors', 'NumHDonors',
            'NumRotatableBonds', 'NumAromaticRings', 'FractionCSP3',
            'NumHeteroatoms', 'NumSaturatedRings', 'NumAliphaticRings',
            'RingCount', 'HeavyAtomCount'
        ]
        self.calc = MoleculeDescriptors.MolecularDescriptorCalculator(self.descriptor_names)
        self.scalers = {}
        self.models = {}

        # 모델 로드 (실제 환경에서는 저장된 모델 로드)
        # self._load_models()

    def validate_smiles(self, smiles):
        """SMILES 유효성 검증"""
        if not smiles or not isinstance(smiles, str):
            return False, "SMILES must be a non-empty string"

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return False, f"Invalid SMILES: {smiles}"

        return True, mol

    def compute_descriptors(self, mol):
        """분자 서술자 계산"""
        try:
            desc_values = self.calc.CalcDescriptors(mol)
            return np.array(desc_values).reshape(1, -1), None
        except Exception as e:
            return None, f"Failed to compute descriptors: {str(e)}"

    def check_applicability_domain(self, smiles, dataset='eye'):
        """Applicability Domain 체크"""
        # 간단한 구현 (실제로는 학습 데이터와 Tanimoto 유사도 계산)
        # 여기서는 placeholder
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return False, 0.0

        # TODO: 실제 AD 체크 로직 구현
        # 현재는 항상 통과로 가정
        return True, 0.85  # (in_AD, similarity)

    def predict(self, smiles, dataset='eye', mixture=False):
        """
        자극성 예측

        Parameters:
        -----------
        smiles : str
            SMILES string
        dataset : str
            'eye' or 'skin'
        mixture : bool
            혼합물 여부 (기본: False)

        Returns:
        --------
        dict
            - prediction: 'Irritant' or 'Non-irritant'
            - probability: 자극성 확률 (0~1)
            - confidence: 'High', 'Medium', 'Low'
            - threshold: 사용된 threshold
            - ad_warning: AD 경고 메시지
        """
        # 1. 입력 검증
        if dataset not in ['eye', 'skin']:
            raise ValueError("dataset must be 'eye' or 'skin'")

        valid, result = self.validate_smiles(smiles)
        if not valid:
            raise ValueError(result)

        mol = result

        # 2. 분자 서술자 계산
        descriptors, error = self.compute_descriptors(mol)
        if error:
            raise ValueError(error)

        # 3. Feature 준비
        mixture_feature = np.array([[1 if mixture else 0]])
        features = np.hstack([descriptors, mixture_feature])

        # 4. Applicability Domain 체크
        in_ad, similarity = self.check_applicability_domain(smiles, dataset)

        # 5. 예측 (모델이 로드되어 있다고 가정)
        # 실제로는 self.models[dataset]를 사용
        # 여기서는 데모를 위한 placeholder
        probability = 0.65  # Placeholder

        # 실제 예측 로직:
        # if dataset in self.models:
        #     scaler = self.scalers[dataset]
        #     model = self.models[dataset]
        #     features_scaled = scaler.transform(features)
        #     probability = model.predict_proba(features_scaled)[0, 1]
        # else:
        #     raise RuntimeError(f"Model for {dataset} not loaded")

        # 6. Threshold 적용
        threshold = MODEL_CONFIG[dataset]['threshold']
        prediction = 'Irritant' if probability >= threshold else 'Non-irritant'

        # 7. 신뢰도 평가
        if not in_ad:
            confidence = 'Low'
            ad_warning = f"Warning: Compound outside Applicability Domain (similarity={similarity:.3f})"
        elif similarity < 0.75:
            confidence = 'Medium'
            ad_warning = f"Caution: Low similarity to training data (similarity={similarity:.3f})"
        else:
            confidence = 'High'
            ad_warning = None

        return {
            'smiles': smiles,
            'dataset': dataset,
            'prediction': prediction,
            'probability': float(probability),
            'threshold': threshold,
            'confidence': confidence,
            'ad_warning': ad_warning,
            'metadata': {
                'mixture': mixture,
                'in_AD': in_ad,
                'similarity': float(similarity)
            }
        }

    def predict_batch(self, smiles_list, dataset='eye', mixture_list=None):
        """
        다중 화합물 예측

        Parameters:
        -----------
        smiles_list : list of str
            SMILES strings
        dataset : str
            'eye' or 'skin'
        mixture_list : list of bool
            각 화합물의 혼합물 여부 (None이면 모두 False)

        Returns:
        --------
        list of dict
            각 화합물의 예측 결과
        """
        if mixture_list is None:
            mixture_list = [False] * len(smiles_list)

        results = []
        for smiles, mixture in zip(smiles_list, mixture_list):
            try:
                result = self.predict(smiles, dataset=dataset, mixture=mixture)
                results.append(result)
            except Exception as e:
                results.append({
                    'smiles': smiles,
                    'dataset': dataset,
                    'error': str(e)
                })

        return results

def example_usage():
    """사용 예제"""
    print("\n=== 사용 예제 ===\n")

    predictor = IrritationPredictor()

    # 단일 예측
    print("1. 단일 화합물 예측:")
    result = predictor.predict("CCO", dataset='eye', mixture=False)
    print(f"   SMILES: {result['smiles']}")
    print(f"   예측: {result['prediction']}")
    print(f"   확률: {result['probability']:.3f}")
    print(f"   신뢰도: {result['confidence']}")
    if result['ad_warning']:
        print(f"   경고: {result['ad_warning']}")

    # 다중 예측
    print("\n2. 다중 화합물 예측:")
    smiles_list = ["CCO", "c1ccccc1", "CC(C)O"]
    results = predictor.predict_batch(smiles_list, dataset='skin')
    for i, result in enumerate(results, 1):
        if 'error' in result:
            print(f"   {i}. {result['smiles']}: Error - {result['error']}")
        else:
            print(f"   {i}. {result['smiles']}: {result['prediction']} (P={result['probability']:.3f})")

if __name__ == '__main__':
    example_usage()
