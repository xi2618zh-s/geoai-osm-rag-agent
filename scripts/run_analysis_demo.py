"""Run one new school analysis through the same service as the local browser."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.web.analysis_service import AnalysisService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distance-m', type=float, required=True)
    parser.add_argument('--age-band', required=True)
    parser.add_argument('--minimum-children', type=int, required=True)
    args = parser.parse_args()
    result = AnalysisService().analyse({'scenario': 'school_coverage', 'place': 'Lund',
        'distance_m': args.distance_m, 'age_band': args.age_band, 'minimum_children': args.minimum_children}, run=True)
    print(json.dumps({k: v for k, v in result.items() if k != 'report'}, ensure_ascii=True, indent=2))
    if result['status'] == 'success':
        print('Report: ' + str(Path('output/demo_runs') / result['run_id'] / 'report.html'))
    return 0 if result['status'] == 'success' else 1


if __name__ == '__main__':
    sys.exit(main())
