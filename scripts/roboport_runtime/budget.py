"""Pre-dispatch run accounting. Unknown billing retains its reservation."""
from __future__ import annotations

import json
import math
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

from .pricing import cost_for


class BudgetExceeded(RuntimeError):
    pass


class RunBudget:
    def __init__(self, config: dict, clock=time.monotonic):
        self.limits = {'max_llm_calls': 25, 'max_tool_calls': 50, 'max_wall_seconds': 600, 'max_usd': 5.0}
        self.limits.update((config.get('budgets') or {}).get('per_run') or {})
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in self.limits.values()):
            raise ValueError('invalid run budget')
        self.clock, self.started = clock, clock()
        self.llm_calls = self.tool_calls = 0
        self.spent = 0.0
        self.reservations: dict[str, float] = {}
        self.lock = RLock()

    def _persist(self):
        if getattr(self, 'artifact_dir', None) is not None:
            path = self.artifact_dir / 'budget.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.snapshot()))
            temporary.replace(path)

    def remaining_seconds(self) -> float:
        remaining = self.limits['max_wall_seconds'] - (self.clock() - self.started)
        if remaining <= 0:
            raise BudgetExceeded('run wall-time budget exhausted')
        return remaining

    def reserve_model_call(self, model: str, prompt_token_ceiling: int, completion_token_ceiling: int, local: bool) -> str:
        with self.lock:
            self.remaining_seconds()
            if self.llm_calls >= self.limits['max_llm_calls']:
                raise BudgetExceeded('run model call budget exhausted')
            cost = cost_for('ollama' if local else 'anthropic', model, prompt_token_ceiling, completion_token_ceiling)
            if cost is None:
                raise BudgetExceeded('required model price is unknown')
            if self.spent + sum(self.reservations.values()) + cost > self.limits['max_usd']:
                raise BudgetExceeded('run cost budget cannot cover next call')
            rid = uuid.uuid4().hex
            self.reservations[rid] = cost
            self.llm_calls += 1
            self._persist()
            return rid

    def settle_model_call(self, reservation_id: str, usage: dict | None) -> None:
        with self.lock:
            reserved = self.reservations[reservation_id]
            actual = (usage or {}).get('cost_usd')
            if actual is None:
                return
            if not isinstance(actual, (int, float)) or not math.isfinite(actual) or actual < 0:
                raise BudgetExceeded('invalid provider cost; reservation retained')
            self.reservations.pop(reservation_id)
            self.spent += actual
            self._persist()
            if actual > reserved + 0.000001:
                raise BudgetExceeded('provider exceeded reserved cost; run stopped')

    def release_model_call(self, reservation_id: str) -> None:
        """Drop the cost hold of a call that raised before returning usage.

        The attempt still counts against ``max_llm_calls``."""
        with self.lock:
            if self.reservations.pop(reservation_id, None) is not None:
                self._persist()

    def consume_tool_call(self) -> None:
        with self.lock:
            self.remaining_seconds()
            if self.tool_calls >= self.limits['max_tool_calls']:
                raise BudgetExceeded('run tool call budget exhausted')
            self.tool_calls += 1
            self._persist()

    def snapshot(self) -> dict:
        return {'llm_calls': self.llm_calls, 'tool_calls': self.tool_calls,
                'spent_usd': self.spent, 'reserved_usd': sum(self.reservations.values()),
                'elapsed_seconds': self.clock() - self.started, 'limits': self.limits}


_current = ContextVar('roboport_run_budget', default=None)
_step_calls = ContextVar('roboport_step_calls', default=None)


def current_budget() -> RunBudget | None:
    return _current.get()


@contextmanager
def run_budget(budget: RunBudget):
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


@contextmanager
def model_call_scope(limit: int):
    token = _step_calls.set([0, limit])
    try:
        yield
    finally:
        _step_calls.reset(token)


def request_timeout(default: float = 60) -> float:
    b = current_budget()
    return min(default, b.remaining_seconds()) if b else default


def governed_chat(p, **kwargs) -> dict:
    # A routing wrapper picks its concrete backend and model first, so the
    # call is reserved and dispatched against the same provider and model.
    routed = p.select(kwargs['model_hint']) if hasattr(p, 'select') else None
    if routed is not None:
        p, model, decision = routed
        kwargs['model'] = model
    scope = _step_calls.get()
    if scope is not None:
        if scope[0] >= scope[1]:
            raise BudgetExceeded('per-agent model call budget exhausted')
        scope[0] += 1
    b = current_budget()
    rid = None
    if b:
        b.remaining_seconds()
        # The offline fault provider is accounted like a local model: zero
        # cost, with call, context and wall-time limits still enforced.
        local = p.name in ('ollama', 'fault')
        if not local and p.name != 'anthropic':
            raise BudgetExceeded('provider has no enforceable paid token/price contract')
        config = getattr(p, 'config', {})
        defaults = config.get('defaults') or {}
        completion = int(defaults.get('max_tokens', 2048))
        context = int(defaults.get('context_tokens', 8192))
        if local:
            # UTF-8 byte count is conservative for the deployed byte-fallback
            # tokenizer. Include chat/tool template allowance; reject oversized
            # context rather than silently truncating evidence.
            prompt = len(json.dumps(kwargs, ensure_ascii=False).encode('utf-8')) + 1024
        else:
            prompt = p.count_input_tokens(**kwargs)
        if prompt + completion > context:
            raise BudgetExceeded('input exceeds configured context ceiling')
        model = kwargs.get('model') or (p.model_for(kwargs['model_hint']) if hasattr(p, 'model_for') else p.name)
        rid = b.reserve_model_call(model, prompt, completion, local)
    try:
        result = p.chat_with_tools(**kwargs)
    except BaseException as exc:
        # A provider marks errors after which billing is unknown (a timeout or
        # dropped connection); those keep their hold, like unknown cost does.
        if b and rid and not getattr(exc, 'billing_unknown', False):
            b.release_model_call(rid)
        raise
    if routed is not None and isinstance(result.get('usage'), dict):
        result['usage'].update(routed_hint=decision['hint'], routed_provider=decision['chosen']['provider'],
                               routed_model=model)
    if b and rid:
        b.settle_model_call(rid, result.get('usage'))
        b.remaining_seconds()
    return result
