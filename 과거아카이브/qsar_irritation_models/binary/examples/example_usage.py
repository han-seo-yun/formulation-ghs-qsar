"""
QSAR Irritation Model - 사용 예제
"""

from qsar_prediction import IrritationPredictor

def main():
    # 초기화
    predictor = IrritationPredictor()

    print("="*80)
    print("QSAR Irritation Model - 사용 예제")
    print("="*80)

    # 예제 1: Eye Irritation
    print("\n예제 1: Eye Irritation 예측")
    result = predictor.predict("CCO", dataset='eye', mixture=False)
    print(f"  SMILES: {result['smiles']}")
    print(f"  예측: {result['prediction']}")
    print(f"  확률: {result['probability']:.3f}")
    print(f"  신뢰도: {result['confidence']}")

    # 예제 2: Skin Irritation
    print("\n예제 2: Skin Irritation 예측")
    result = predictor.predict("c1ccccc1", dataset='skin', mixture=False)
    print(f"  SMILES: {result['smiles']}")
    print(f"  예측: {result['prediction']}")
    print(f"  확률: {result['probability']:.3f}")
    print(f"  신뢰도: {result['confidence']}")

    # 예제 3: 다중 예측
    print("\n예제 3: 다중 화합물 예측")
    smiles_list = ["CCO", "c1ccccc1", "CC(C)O", "CCN"]
    results = predictor.predict_batch(smiles_list, dataset='eye')

    for i, result in enumerate(results, 1):
        if 'error' in result:
            print(f"  {i}. Error: {result['error']}")
        else:
            print(f"  {i}. {result['smiles']}: {result['prediction']} (P={result['probability']:.3f}, Conf={result['confidence']})")

if __name__ == '__main__':
    main()
