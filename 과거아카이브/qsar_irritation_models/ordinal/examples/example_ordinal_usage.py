"""
QSAR Ordinal Model - 사용 예제
"""

from qsar_ordinal_prediction import IrritationOrdinalPredictor

def main():
    # 초기화
    predictor = IrritationOrdinalPredictor()

    print("="*80)
    print("QSAR Ordinal Model - 사용 예제 (EPA 4-class)")
    print("="*80)

    # 예제 1: Eye Irritation Ordinal
    print("\n예제 1: Eye Irritation Ordinal 예측")
    result = predictor.predict("CCO", dataset='eye', mixture=False)
    print(f"  SMILES: {result['smiles']}")
    print(f"  예측 클래스: {result['prediction']}")
    print(f"  EPA 등급: {result['epa_class']}")
    print(f"  해석: {result['interpretation']}")
    print(f"  확률: {[f'{p:.3f}' for p in result['probabilities']]}")
    print(f"  신뢰도: {result['confidence']}")

    # 예제 2: Skin Irritation Ordinal
    print("\n예제 2: Skin Irritation Ordinal 예측")
    result = predictor.predict("c1ccccc1", dataset='skin', mixture=False)
    print(f"  SMILES: {result['smiles']}")
    print(f"  예측 클래스: {result['prediction']}")
    print(f"  EPA 등급: {result['epa_class']}")
    print(f"  해석: {result['interpretation']}")
    print(f"  확률: {[f'{p:.3f}' for p in result['probabilities']]}")
    print(f"  신뢰도: {result['confidence']}")

    # 예제 3: 다중 예측
    print("\n예제 3: 다중 화합물 Ordinal 예측")
    smiles_list = ["CCO", "c1ccccc1", "CC(C)O", "CCN"]
    results = predictor.predict_batch(smiles_list, dataset='eye')

    for i, result in enumerate(results, 1):
        if 'error' in result:
            print(f"  {i}. Error: {result['error']}")
        else:
            print(f"  {i}. {result['smiles']}: EPA {result['epa_class']} (Class {result['prediction']}, Conf={result['confidence']})")

if __name__ == '__main__':
    main()
