"""Flask transport for the bounded local school analysis."""
from flask import Blueprint, Flask, Response, current_app, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from src.scenarios import ScenarioError
from .analysis_data import ROOT
from .analysis_service import AnalysisService


def analysis_blueprint(service=None):
    service = service or AnalysisService()
    bp = Blueprint('school_analysis', __name__)

    @bp.get('/explore')
    def explore():
        page = (ROOT / 'site/index.html').read_text(encoding='utf-8').replace('data-mode="saved"', 'data-mode="local"')
        return Response(page, mimetype='text/html')

    @bp.get('/assets/<path:name>')
    def assets(name):
        return send_from_directory(ROOT / 'site/assets', name)

    @bp.get('/guide.html')
    def guide():
        page = (ROOT / 'site/guide.html').read_text(encoding='utf-8').replace('href="index.html"', 'href="explore"')
        return Response(page, mimetype='text/html')

    @bp.post('/api/analyses/<action>')
    def analyse(action):
        if action not in ('preview', 'run'):
            return jsonify(status='unsupported', reason_code='DEMO_ACTION_UNKNOWN'), 404
        if request.content_length is not None and request.content_length > 8192:
            return jsonify(status='failed', reason_code='DEMO_REQUEST_TOO_LARGE'), 413
        # Same-origin JSON only; the launcher binds exclusively to loopback.
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return jsonify(status='failed', reason_code='DEMO_ORIGIN_REJECTED'), 403
        try:
            body = request.get_json()
            result = service.analyse(body, run=action == 'run')
            return jsonify(result), 422 if result['status'] == 'unsupported' else 200
        except (BadRequest, UnsupportedMediaType):
            return jsonify(status='failed', reason_code='DEMO_JSON_INVALID', message='Send a valid application/json object.'), 400
        except ScenarioError as error:
            state = 'clarification' if error.code == 'DEMO_MISSING_PARAMETERS' else 'failed'
            return jsonify(status=state, reason_code=error.code, message=str(error)), 422
        except Exception:
            current_app.logger.exception('School analysis failed')
            return jsonify(status='failed', reason_code='DEMO_EXECUTION_FAILED',
                           message='Analysis could not be completed. Check the included data and local server log.'), 500

    @bp.get('/api/analyses/<run_id>')
    def result(run_id):
        try:
            return jsonify(service.load(run_id))
        except FileNotFoundError:
            return jsonify(status='failed', reason_code='DEMO_RUN_NOT_FOUND'), 404

    @bp.get('/api/analyses/<run_id>/report')
    def report(run_id):
        try:
            return send_file(service.artifact(run_id, 'report.html'), mimetype='text/html')
        except FileNotFoundError:
            return jsonify(status='failed', reason_code='DEMO_FILE_NOT_FOUND'), 404

    @bp.get('/api/analyses/<run_id>/files/<name>')
    def download(run_id, name):
        try:
            return send_file(service.artifact(run_id, name), as_attachment=True, download_name=name)
        except FileNotFoundError:
            return jsonify(status='failed', reason_code='DEMO_FILE_NOT_FOUND'), 404

    return bp


def create_demo_app(service=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 8192
    app.register_blueprint(analysis_blueprint(service))

    @app.get('/')
    def home():
        from flask import redirect
        return redirect('/explore')

    @app.get('/saved/<path:name>')
    def saved(name):
        return send_from_directory(ROOT / 'build/public_demo', name)

    return app
