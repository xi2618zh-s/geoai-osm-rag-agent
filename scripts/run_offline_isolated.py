"""Run the existing unittest modules in separate processes to bound retained memory.

Does not change test cases or expectations. Keeps each module's raw output and an
aggregate JSON receipt. Network model lookup is disabled for this offline run.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    results = []
    for path in sorted((ROOT / 'tests').glob('test_*.py')):
        module = 'tests.' + path.stem
        log = destination / (path.stem + '.txt')
        try:
            with log.open('w', encoding='utf-8') as stream:
                result = subprocess.run([sys.executable, '-m', 'unittest', module, '-v'], cwd=ROOT,
                                        env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        output = log.read_text(encoding='utf-8', errors='replace')
        count = re.search(r'Ran (\d+) tests?', output)
        skips = re.search(r'skipped=(\d+)', output)
        item = {'module': module, 'exit_code': code, 'tests': int(count[1]) if count else 0,
                'skipped': int(skips[1]) if skips else 0, 'log': log.name}
        results.append(item)
        receipt = {'modules': results, 'tests': sum(x['tests'] for x in results),
                   'skipped': sum(x['skipped'] for x in results),
                   'failed_modules': [x['module'] for x in results if x['exit_code']]}
        (destination / 'summary.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
        print(f"{'PASS' if code == 0 else 'FAIL'} {module}: {item['tests']} tests, exit {code}", flush=True)
    return bool(receipt['failed_modules'])


if __name__ == '__main__':
    sys.exit(main())
