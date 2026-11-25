"""Start only the public-fixture school analysis, with no model/PBF preflight."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.web.analysis_routes import create_demo_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    print(f'GeoAI: http://127.0.0.1:{args.port}/explore', flush=True)
    create_demo_app().run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
