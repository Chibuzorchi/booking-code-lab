"""Local HTTP API contract; provider jobs remain owned by RunRegistry."""
import json
import math
import re

from .worker import UnknownProviderError
from .decorrelation import ExtractError, decorrelate_extract, harvest_odds_band
from .selection import ParamError, parse_decorrelate_params
from .uniqueness import analyze_extract


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
                  'want_count', 'try_budget'))
    provider = provider_name(body.get('provider'))
    fresh = body.get('fresh_seed', False)
    if type(fresh) is not bool:
        raise ParamError('fresh_seed must be a boolean')
    if fresh:
        raise APIError(501, 'fresh_seed_unavailable',
                       'Fresh-seed booking is not implemented; supply an explicit seed.')
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
    return dict(provider=provider, seed=seed, min_odds=values['min_odds'],
                max_odds=values['max_odds'], max_qualifying=values['want_count'],
                max_codes=values['try_budget'], depth=3)


def allowed_methods(path):
    if path in ('/api/scan', '/api/distinct', '/api/decorrelate') or re.fullmatch(r'/api/scan/[^/]+/cancel', path):
        return ('POST',)
    if path == '/api/runs' or re.fullmatch(r'/api/scan/[^/]+', path):
        return ('GET', 'HEAD')
    raise APIError(404, 'not_found', 'Unknown API route')


def validate_method(method, path):
    allowed = allowed_methods(path)
    if method not in allowed:
        raise APIError(405, 'method_not_allowed', 'Allowed methods: ' + ', '.join(allowed))


def dispatch(registry, method, path, body, query):
    validate_method(method, path)
    if method in ('GET', 'HEAD'):
        if path == '/api/runs':
            fields(query, ('provider',))
            provider = provider_name(query['provider']) if 'provider' in query else None
            return 200, {'runs': registry.list_runs(provider=provider)}
        if path.startswith('/api/scan/') and '/' not in path[len('/api/scan/'):]:
            fields(query, ())
            return 200, registry.get(path[len('/api/scan/'):])
    elif method == 'POST':
        fields(query, ())
        if path == '/api/scan':
            params = scan_params(body)
            try:
                return 202, registry.start_scan(**params)
            except UnknownProviderError as exc:
                raise APIError(500, 'server_error', 'Provider scanner is unavailable') from exc
        if path.startswith('/api/scan/') and path.endswith('/cancel'):
            fields(body, ())
            return 200, registry.cancel(path[len('/api/scan/'):-len('/cancel')])
        if path in ('/api/distinct', '/api/decorrelate'):
            fields(body, ('run_id', 'max_exposure', 'target') if path.endswith('decorrelate') else ('run_id',))
            params = parse_decorrelate_params(body) if path.endswith('decorrelate') else None
            run_id = body.get('run_id')
            if not isinstance(run_id, str) or not run_id:
                raise ParamError('run_id is required')
            status = registry.get(run_id)
            extract, snapshot = registry.committed_extract_snapshot(run_id)
            try:
                data = json.loads(snapshot)
            except (ValueError, RecursionError) as exc:
                raise ExtractError('Cannot parse committed extract') from exc
            policy = harvest_odds_band(data)
            if params is None:
                report = analyze_extract(extract, status['provider'], data=data, **policy)
            else:
                report = decorrelate_extract(extract, status['provider'], params,
                                             data=data, **policy)
            return 200, {**report, 'run_id': run_id, 'odds_label': 'recorded, not verified payout'}
    raise APIError(404, 'not_found', 'Unknown API route')
