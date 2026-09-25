"""
QSAR Irritation Model - Ordinal Prediction Pipeline (EPA 4-class)

사용 방법:
    from qsar_ordinal_prediction import IrritationOrdinalPredictor

    # 초기화
    predictor = IrritationOrdinalPredictor()

    # 예측
    result = predictor.predict("CCO", dataset='eye')

    print(f"예측 클래스: {result['prediction']}")  # 0,1,2,3
    print(f"EPA 등급: {result['epa_class']}")     # 1,2,3,4
    print(f"해석: {result['interpretation']}")
"""

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors
from rdkit.ML.Descriptors import MoleculeDescriptors
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier

# 모델 설정
MODEL_CONFIG = {
    'eye': {
        'algorithm': 'GradientBoostingClassifier',
        'params': {
            'n_estimators': 100,
            'learning_rate': 0.05,
            'max_depth': 3,
            'subsample': 1.0,
            'random_state': 42
        }
    },
    'skin': {
        'algorithm': 'RandomForestClassifier',
        'params': {
            'n_estimators': 100,
            'max_depth': 10,
            'min_samples_split': 5,
            'class_weight': 'balanced',
            'random_state': 42
        }
    }
}

# EPA 클래스 해석
EPA_INTERPRETATION = {
    0: "EPA Class 1 (Corrosive/Severely Irritating)",
    1: "EPA Class 2 (Moderately Irritating)",
    2: "EPA Class 3 (Mildly Irritating)",
    3: "EPA Class 4 (Non-irritating)"
}

class IrritationOrdinalPredictor:
    """안구/피부 자극성 Ordinal 예측 클래스 (EPA 4-class)"""

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

    def predict(self, smiles, dataset='eye', mixture=False):
        """
        자극성 Ordinal 예측 (EPA 4-class)

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
            - prediction: 예측 클래스 (0,1,2,3)
            - epa_class: EPA 등급 (1,2,3,4)
            - interpretation: 해석
            - probabilities: 각 클래스 확률 [P(0), P(1), P(2), P(3)]
            - confidence: 'High', 'Medium', 'Low'
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

        # 4. 예측 (모델이 로드되어 있다고 가정)
        # 실제로는 self.models[dataset]를 사용
        # 여기서는 데모를 위한 placeholder
        predicted_class = 2  # Placeholder
        probabilities = [0.1, 0.2, 0.5, 0.2]  # Placeholder

        # 실제 예측 로직:
        # if dataset in self.models:
        #     scaler = self.scalers[dataset]
        #     model = self.models[dataset]
        #     features_scaled = scaler.transform(features)
        #     predicted_class = model.predict(features_scaled)[0]
        #     probabilities = model.predict_proba(features_scaled)[0].tolist()
        # else:
        #     raise RuntimeError(f"Model for {dataset} not loaded")

        # 5. 결과 해석
        epa_class = predicted_class + 1  # 0,1,2,3 -> 1,2,3,4
        interpretation = EPA_INTERPRETATION[predicted_class]

        # 6. 신뢰도 평가
        max_prob = max(probabilities)
        if max_prob >= 0.7:
            confidence = 'High'
        elif max_prob >= 0.5:
            confidence = 'Medium'
        else:
            confidence = 'Low'

        return {
            'smiles': smiles,
            'dataset': dataset,
            'prediction': int(predicted_class),
            'epa_class': int(epa_class),
            'interpretation': interpretation,
            'probabilities': probabilities,
            'confidence': confidence,
            'metadata': {
                'mixture': mixture
            }
        }

    def predict_batch(self, smiles_list, dataset='eye', mixture_list=None):
        """
        다중 화합물 Ordinal 예측

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

    predictor = IrritationOrdinalPredictor()

    # 단일 예측
    print("1. 단일 화합물 Ordinal 예측:")
    result = predictor.predict("CCO", dataset='eye', mixture=False)
    print(f"   SMILES: {result['smiles']}")
    print(f"   예측 클래스: {result['prediction']}")
    print(f"   EPA 등급: {result['epa_class']}")
    print(f"   해석: {result['interpretation']}")
    print(f"   확률: {[f'{p:.3f}' for p in result['probabilities']]}")
    print(f"   신뢰도: {result['confidence']}")

    # 다중 예측
    print("\n2. 다중 화합물 Ordinal 예측:")
    smiles_list = ["CCO", "c1ccccc1", "CC(C)O"]
    results = predictor.predict_batch(smiles_list, dataset='skin')
    for i, result in enumerate(results, 1):
        if 'error' in result:
            print(f"   {i}. {result['smiles']}: Error - {result['error']}")
        else:
            print(f"   {i}. {result['smiles']}: EPA {result['epa_class']} ({result['interpretation']})")

if __name__ == '__main__':
    example_usage()
