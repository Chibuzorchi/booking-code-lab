"""Local HTTP API contract; provider jobs remain owned by RunRegistry."""
import dataclasses
import hashlib
import json
import math
import re

from .worker import BookingUnavailableError, UnknownProviderError
from .decorrelation import ExtractError, build_pool, decorrelate_extract, harvest_odds_band
from .selection import ParamError, parse_decorrelate_params
from .uniqueness import analyze_extract
from .results_tracking.jobs import JobError, JobNotFoundError
from .results_tracking.results_api import (
    ReportNotFoundError, ResultsApiError, cancel_job, get_latest_report, list_codes, list_saved_codes,
    submit_evaluation,
)


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def fields(body, allowed):
    extra = body.keys() - set(allowed)
    if extra:
        raise ParamError('unsupported fields: ' + ', '.join(sorted(extra)))


def provider_name(value):
    if not isinstance(value, str) or value not in ('bet9ja', 'sportybet'):
        raise ParamError('provider must be bet9ja or sportybet')
    return value


def scan_params(body):
    fields(body, ('provider', 'seed', 'fresh_seed', 'min_odds', 'max_odds',
                  'min_legs', 'max_legs', 'want_count', 'try_budget'))
    provider = provider_name(body.get('provider'))
    fresh = body.get('fresh_seed', False)
    if type(fresh) is not bool:
        raise ParamError('fresh_seed must be a boolean')
    if fresh:
        # The worker books the code in the provider's browser flow, so there is
        # nothing to validate here and nothing for the caller to supply.
        if body.get('seed') not in (None, ''):
            raise ParamError('omit seed when fresh_seed is true; the run books its own')
        seed = None
    else:
        seed = body.get('seed')
        pattern = r'[A-Za-z0-9]{7}' if provider == 'bet9ja' else r'[A-Za-z0-9]{6}'
        if not isinstance(seed, str) or not re.fullmatch(pattern, seed):
            raise ParamError('seed must be a valid booking code for the provider')
        if provider == 'sportybet':
            seed = seed.upper()
    values = {}
    for name, default, minimum in (('want_count', 0, 0), ('try_budget', 25000, 1)):
        value = body.get(name, default)
        if value is None or value == '':
            value = default
        if type(value) is not int or value < minimum:
            raise ParamError(f'{name} must be an integer >= {minimum}')
        values[name] = value
    for name in ('min_legs', 'max_legs'):
        value = body.get(name)
        if value is None or value == '':
            value = 0
        if type(value) is not int or value < 0:
            raise ParamError(f'{name} must be an integer >= 0')
        values[name] = value
    if 0 < values['max_legs'] < values['min_legs']:
        raise ParamError('positive max_legs must be >= min_legs')
    for name, default in (('min_odds', 1), ('max_odds', 350000)):
        value = body.get(name, default)
        if value is None or value == '':
            value = default
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ParamError(f'{name} must be a finite non-negative number')
        if name == 'min_odds' and 0 < value < 1:
            raise ParamError('min_odds must be 0 or >= 1')
        values[name] = 1 if name == 'min_odds' and value == 0 else value
    if 0 < values['max_odds'] < values['min_odds']:
        raise ParamError('positive max_odds must be >= min_odds')
    return dict(provider=provider, seed=seed, fresh_seed=fresh,
                min_odds=values['min_odds'],
                max_odds=values['max_odds'], min_legs=values['min_legs'],
                max_legs=values['max_legs'], max_qualifying=values['want_count'],
                max_codes=values['try_budget'], depth=3)


_RESULTS_JOB_CANCEL_RE = re.compile(r'/api/results/jobs/[^/]+/cancel')
_RESULTS_JOB_RE = re.compile(r'/api/results/jobs/[^/]+')
_RESULTS_CODES_RE = re.compile(r'/api/results/(?!jobs(?:/|$))[^/]+/codes')
_RESULTS_RUN_RE = re.compile(r'/api/results/(?!jobs(?:/|$))[^/]+')


def allowed_methods(path):
    if path in ('/api/scan', '/api/codes', '/api/distinct', '/api/decorrelate') or re.fullmatch(r'/api/scan/[^/]+/cancel', path):
        return ('POST',)
    if path == '/api/runs' or re.fullmatch(r'/api/scan/[^/]+', path):
        return ('GET', 'HEAD')
    if _RESULTS_JOB_CANCEL_RE.fullmatch(path):
        return ('POST',)
    if _RESULTS_JOB_RE.fullmatch(path) or _RESULTS_CODES_RE.fullmatch(path):
        return ('GET', 'HEAD')
    if _RESULTS_RUN_RE.fullmatch(path):
        return ('GET', 'HEAD', 'POST')
    raise APIError(404, 'not_found', 'Unknown API route')


def validate_method(method, path):
    allowed = allowed_methods(path)
    if method not in allowed:
        raise APIError(405, 'method_not_allowed', 'Allowed methods: ' + ', '.join(allowed))


def _job_payload(record):
    return dataclasses.asdict(record)


def _bounded_int(query, name, default, minimum):
    value = query.get(name)
    if value is None or value == '':
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise ParamError(f'{name} must be an integer') from None
    if parsed < minimum:
        raise ParamError(f'{name} must be an integer >= {minimum}')
    return parsed


def dispatch(registry, method, path, body, query, results=None):
    validate_method(method, path)
    if method in ('GET', 'HEAD'):
        if path == '/api/runs':
            fields(query, ('provider',))
            provider = provider_name(query['provider']) if 'provider' in query else None
            return 200, {'runs': registry.list_runs(provider=provider)}
        if path.startswith('/api/scan/') and '/' not in path[len('/api/scan/'):]:
            fields(query, ())
            return 200, registry.get(path[len('/api/scan/'):])
        if results is not None:
            if _RESULTS_JOB_RE.fullmatch(path):
                fields(query, ())
                job_id = path[len('/api/results/jobs/'):]
                try:
                    return 200, _job_payload(results.job_store.get(job_id))
                except JobNotFoundError as exc:
                    raise APIError(404, 'job_not_found', 'Result job not found') from exc
            if _RESULTS_CODES_RE.fullmatch(path):
                fields(query, ('revision', 'offset', 'limit', 'view', 'snapshot', 'allow_partial', 'status', 'row_id'))
                run_id = path[len('/api/results/'):-len('/codes')]
                revision_id = query.get('revision')
                saved = query.get('view') == 'saved'
                if query.get('view') not in (None, 'saved'):
                    raise ParamError('view must be saved when supplied')
                if not saved and (not isinstance(revision_id, str) or not revision_id):
                    raise ParamError('revision is required')
                if query.get('allow_partial') not in (None, 'true', 'false'):
                    raise ParamError('allow_partial must be true or false')
                offset = _bounded_int(query, 'offset', 0, 0)
                limit = _bounded_int(query, 'limit', 50, 1)
                try:
                    if saved:
                        return 200, list_saved_codes(registry, run_id, offset=offset, limit=limit,
                            snapshot=query.get('snapshot'), allow_partial=query.get('allow_partial') == 'true', status=query.get('status'), row_id=query.get('row_id'))
                    return 200, list_codes(results.config, run_id, revision_id,
                                            offset=offset, limit=limit, status=query.get('status'), row_id=query.get('row_id'))
                except ReportNotFoundError as exc:
                    raise APIError(404, 'report_not_found', 'No such revision for this run') from exc
                except ResultsApiError as exc:
                    raise APIError(422, 'invalid_results_state', str(exc)) from exc
            if _RESULTS_RUN_RE.fullmatch(path):
                fields(query, ())
                run_id = path[len('/api/results/'):]
                try:
                    return 200, get_latest_report(results.config, run_id)
                except ReportNotFoundError as exc:
                    raise APIError(404, 'report_not_found', 'No completed report for this run') from exc
                except ResultsApiError as exc:
                    raise APIError(422, 'invalid_results_state', str(exc)) from exc
    elif method == 'POST':
        if results is not None and _RESULTS_JOB_CANCEL_RE.fullmatch(path):
            fields(query, ())
            fields(body, ())
            job_id = path[len('/api/results/jobs/'):-len('/cancel')]
            try:
                return 200, _job_payload(cancel_job(results.job_store, job_id))
            except JobNotFoundError as exc:
                raise APIError(404, 'job_not_found', 'Result job not found') from exc
        if results is not None and _RESULTS_RUN_RE.fullmatch(path):
            fields(query, ())
            fields(body, ('refresh', 'allow_partial', 'row_id'))
            refresh = body.get('refresh', False)
            allow_partial = body.get('allow_partial', False)
            if type(refresh) is not bool:
                raise ParamError('refresh must be a boolean')
            if type(allow_partial) is not bool:
                raise ParamError('allow_partial must be a boolean')
            if 'row_id' in body and (not isinstance(body['row_id'], str) or not body['row_id']):
                raise ParamError('row_id must be a nonempty frozen row identity')
            run_id = path[len('/api/results/'):]
            try:
                record = submit_evaluation(registry, results.config, results.job_store, run_id,
                                            refresh=refresh, allow_partial=allow_partial, row_id=body.get('row_id'))
            except ResultsApiError as exc:
                raise APIError(422, 'invalid_results_state', str(exc)) from exc
            return 202, _job_payload(record)
        fields(query, ())
        if path == '/api/scan':
            params = scan_params(body)
            try:
                return 202, registry.start_scan(**params)
            except BookingUnavailableError as exc:
                raise APIError(501, 'fresh_seed_unavailable',
                               'This provider has no fresh-seed booking entrypoint; '
                               'supply an explicit seed.') from exc
            except UnknownProviderError as exc:
                raise APIError(500, 'server_error', 'Provider scanner is unavailable') from exc
        if path.startswith('/api/scan/') and path.endswith('/cancel'):
            fields(body, ())
            return 200, registry.cancel(path[len('/api/scan/'):-len('/cancel')])
        if path in ('/api/codes', '/api/distinct', '/api/decorrelate'):
            fields(body, ('run_id', 'max_exposure', 'target') if path.endswith('decorrelate') else ('run_id',))
            params = parse_decorrelate_params(body) if path.endswith('decorrelate') else None
            run_id = body.get('run_id')
            if not isinstance(run_id, str) or not run_id:
                raise ParamError('run_id is required')
            status = registry.get(run_id)
            partial = False
            if path == '/api/codes':
                # Listing also serves a run that is still going or that stopped
                # early; the analyses below stay on committed extracts only.
                extract, snapshot, partial = registry.saved_codes_snapshot(run_id)
            else:
                extract, snapshot = registry.committed_extract_snapshot(run_id)
            try:
                data = json.loads(snapshot)
            except (ValueError, RecursionError) as exc:
                raise ExtractError('Cannot parse committed extract') from exc
            policy = harvest_odds_band(data)
            if path == '/api/codes':
                # Keep the analysis pool intact, and expose started historical
                # tickets separately so the console can reveal their snapshots.
                pool = build_pool(extract, status['provider'], data=data,
                                  collect_expired=True, **policy)
                codes = pool.pop('tickets')
                expired = pool.pop('expired_tickets')
                digest = hashlib.sha256(snapshot).hexdigest()
                indices = {id(raw): index for index, raw in enumerate(data['qualifying'])}
                for ticket in codes + expired:
                    ticket['row_id'] = f"{digest}:{indices[id(ticket['raw'])]}"
                report = {**pool, 'codes': codes, 'partial': partial,
                          'expired_codes': expired, 'expired_count': len(expired),
                          'saved_count': len(data['qualifying']),
                          'state': status['state']}
            elif params is None:
                report = analyze_extract(extract, status['provider'], data=data, **policy)
            else:
                report = decorrelate_extract(extract, status['provider'], params,
                                             data=data, **policy)
            return 200, {**report, 'run_id': run_id, 'odds_label': 'recorded, not verified payout'}
    raise APIError(404, 'not_found', 'Unknown API route')
