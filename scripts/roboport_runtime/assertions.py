"""Conservative, runtime-owned assertion grammar; never executes artifact text.

Supports scalar ``is``/``==`` comparisons, ledger sum/count/refund comparisons,
and extraction hash equality. Broader prose (including CSV lineage claims) is
unsupported. Only final root/summary fields and successful direct step outputs
are eligible evidence; no recursive field discovery or model predicates.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, localcontext


class Missing(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise Missing('duplicate JSON key')
        result[key] = value
    return result


def _parse(text):
    def invalid(value):
        raise Missing('non-finite JSON number')
    def number(value):
        parsed = Decimal(value)
        if len(parsed.as_tuple().digits) > 1000 or abs(parsed.as_tuple().exponent) > 1000:
            raise Missing('numeric operand exceeds supported precision or exponent')
        return parsed
    return json.loads(text, parse_int=number, parse_float=number,
                      parse_constant=invalid, object_pairs_hook=_pairs)


def _quote(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return '[' + ','.join(_quote(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + ':' + _quote(v) for k, v in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


# Public strict JSON helpers shared with the grader. Numbers retain their exact
# decimal values and encoding never coerces them through binary floating point.
parse_artifact = _parse
encode_artifact = _quote


def _literal(text):
    if text == 'an empty string':
        return ''
    if text in ('true', 'false', 'null') or re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?', text) or text.startswith('"'):
        try:
            value = _parse(text)
            if not isinstance(value, (dict, list)):
                return value
        except (ValueError, TypeError):
            pass
        raise Missing('invalid literal')
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', text):
        return text
    raise Missing('unsupported literal')


def check_assertion(expectation: str, sources: dict[str, str]) -> dict | None:
    """Return an auditable deterministic verdict, or None for unsupported syntax.

    Recognized comparisons with absent, malformed or ambiguous operands return
    INCONCLUSIVE. Numeric equality uses Decimal; booleans never equal numbers.
    """
    text = expectation.strip()
    aggregate = re.fullmatch(r'The reported ([A-Za-z_]\w*) equals the (sum of all recorded transaction amounts|number of recorded transactions|number of negative transaction amounts)', text)
    hash_match = text == 'The report source_sha256 equals the source_sha256 of the successful extraction step'
    boolean = re.fullmatch(r"The boolean at ([A-Za-z_]\w*)\['([^'\r\n]+)'\] is (true|false)", text)
    scalar = re.fullmatch(r'(?:The (?:reported |produced report |run )?)?((?:final_output\.)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?: field| flag)? (?:is|==) (.+)', text)
    if not any((aggregate, hash_match, boolean, scalar)):
        return None
    expected = None
    if scalar:
        try:
            expected = _literal(scalar[2])
        except Missing:
            return None
    citations = []

    def get(source, parts):
        try:
            value = _parse(sources[source])
            for part in parts:
                if not isinstance(value, dict):
                    raise Missing('operand parent is not an object')
                value = value[part]
        except (KeyError, ValueError, TypeError) as exc:
            raise Missing(f'missing or malformed {source} operand') from exc
        pointer = ''.join('/' + p.replace('~', '~0').replace('/', '~1') for p in parts)
        citations.append({'source': source, 'pointer': pointer, 'quote': _quote(value)})
        return value

    def report(field):
        try:
            final = _parse(sources['final_output'])
        except (KeyError, ValueError, TypeError) as exc:
            raise Missing('missing or malformed final output') from exc
        if not isinstance(final, dict):
            raise Missing('final output is not an object')
        locations = []
        if field in final:
            locations.append([field])
        if isinstance(final.get('summary'), dict) and field in final['summary']:
            locations.append(['summary', field])
        if len(locations) != 1:
            raise Missing('missing or ambiguous report field ' + field)
        return get('final_output', locations[0])

    def extraction(field):
        declarations = get('plan', ['steps'])
        if not isinstance(declarations, list) or not all(isinstance(item, dict) for item in declarations):
            raise Missing('plan steps must be an array of objects')
        ids = [item.get('id') for item in declarations]
        if not all(isinstance(item, str) for item in ids) or len(ids) != len(set(ids)):
            raise Missing('plan step IDs must be unique strings')
        candidates = [item['id'] for item in declarations if item.get('owner') == 'data_engineering']
        if len(candidates) != 1:
            raise Missing('missing or ambiguous planned extraction')
        step_id = candidates[0]
        try:
            steps = _parse(sources['steps'])
        except (KeyError, ValueError, TypeError) as exc:
            raise Missing('missing or malformed step outputs') from exc
        if not isinstance(steps, dict):
            raise Missing('steps must be an object')
        step = steps.get(step_id)
        if not isinstance(step, dict) or step.get('status') != 'ok' or not isinstance(step.get('output'), dict):
            raise Missing('planned extraction did not complete successfully')
        get('steps', [step_id, 'status'])
        return get('steps', [step_id, 'output', field])

    try:
        if aggregate:
            actual = report(aggregate[1])
            rows = extraction('transactions')
            if not isinstance(rows, list):
                raise Missing('transactions must be an array')
            amounts = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('amount'), Decimal):
                    raise Missing('every transaction needs a finite numeric amount')
                amounts.append(row['amount'])
            operation = aggregate[2]
            if operation.startswith('sum'):
                # Precision spans every input digit and exponent, without rounding.
                with localcontext() as ctx:
                    ctx.prec = max(28, max((len(a.as_tuple().digits) + abs(a.as_tuple().exponent) for a in amounts), default=1) * 2 + len(str(len(amounts))) + 2)
                    expected = sum(amounts, Decimal(0))
            else:
                expected = Decimal(len(amounts) if operation == 'number of recorded transactions' else sum(a < 0 for a in amounts))
        elif hash_match:
            actual = report('source_sha256')
            expected = extraction('source_sha256')
            if not isinstance(actual, str) or not isinstance(expected, str):
                raise Missing('source hashes must be strings')
        elif boolean:
            actual = get('final_output', [boolean[1], boolean[2]])
            expected = boolean[3] == 'true'
        else:
            field = scalar[1]
            if field.startswith('final_output.'):
                actual = get('final_output', field.split('.')[1:])
            elif '.' in field:
                actual = get('final_output', field.split('.'))
            else:
                actual = report(field)
        if isinstance(actual, (dict, list)):
            raise Missing('comparison requires a scalar')
        equal = type(actual) is type(expected) and actual == expected
        return {'verdict': 'PASS' if equal else 'FAIL', 'evidence': f'Compared actual {_quote(actual)} with expected {_quote(expected)}.',
                'citations': citations, 'method': 'deterministic'}
    except (Missing, ArithmeticError) as exc:
        return {'verdict': 'INCONCLUSIVE', 'evidence': str(exc), 'citations': citations, 'method': 'deterministic'}
