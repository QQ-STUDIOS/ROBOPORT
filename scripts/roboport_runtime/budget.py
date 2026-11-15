"""Pre-dispatch run accounting. Unknown billing retains its reservation."""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Callable

from .pricing import cost_for


_WINDOWS = os.name == 'nt'


class BudgetPersistenceError(OSError):
    """Accounting could not be durably recorded; dispatch must stop."""


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
        self.persistence = {'replace_retries': 0, 'last_retry': None}

    def _persist(self):
        if getattr(self, 'artifact_dir', None) is None:
            return
        path = self.artifact_dir / 'budget.json'
        # Readers on Windows may omit FILE_SHARE_DELETE. Keep replacement atomic
        # and retry only this filesystem operation, never dispatch/accounting.
        last_error = None
        for attempt in range(1, 7):
            if attempt > 1 and self.clock() - self.started >= self.limits['max_wall_seconds']:
                raise BudgetPersistenceError(f'budget replace retry exceeded run wall limit: {path}') from last_error
            temporary = path.with_name(f'.budget-{uuid.uuid4().hex}.tmp')
            operation = 'write'
            delay = None
            try:
                with temporary.open('x', encoding='utf-8') as stream:
                    json.dump(self.snapshot(), stream)
                operation = 'replace'
                temporary.replace(path)
                return
            except OSError as exc:
                last_error = exc
                wait = .01 * 2 ** (attempt - 1)
                remaining = self.limits['max_wall_seconds'] - (self.clock() - self.started)
                if (operation == 'replace' and _WINDOWS
                        and isinstance(exc, PermissionError)
                        and getattr(exc, 'winerror', None) in (5, 32, 33)
                        and attempt < 6 and remaining > wait):
                    self.persistence['replace_retries'] += 1
                    self.persistence['last_retry'] = {
                        'operation': 'replace', 'path': str(path),
                        'winerror': exc.winerror, 'attempt': attempt}
                    delay = wait
                else:
                    raise BudgetPersistenceError(
                        f'budget {operation} failed after {attempt} attempt(s): '
                        f'{path}; errno={exc.errno}; '
                        f'winerror={getattr(exc, "winerror", None)}; {exc}') from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # A locked candidate may remain as evidence. Do not mask the
                    # primary error or overwrite another writer's temporary file.
                    pass
            if delay is not None:
                time.sleep(delay)

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
                'elapsed_seconds': self.clock() - self.started, 'limits': self.limits,
                'persistence': {'replace_retries': self.persistence['replace_retries'],
                                'last_retry': dict(self.persistence['last_retry']) if self.persistence['last_retry'] else None}}


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


def local_prompt_ceiling(kwargs: dict) -> int:
    """Conservative input size of a local call: UTF-8 bytes of the request
    (an upper bound for the deployed byte-fallback tokenizer) plus a chat and
    tool template allowance."""
    return len(json.dumps(kwargs, ensure_ascii=False).encode('utf-8')) + 1024


def fits_local_context(*, config: dict | None = None, **kwargs) -> bool:
    """Whether a call would pass governed_chat's local context check."""
    from .config import load_runtime_config
    defaults = (load_runtime_config() if config is None else config).get('defaults') or {}
    completion = int(defaults.get('max_tokens', 2048))
    return local_prompt_ceiling(kwargs) + completion <= int(defaults.get('context_tokens', 8192))


def governed_chat(p, *, message_fallbacks: list[list[dict]] | None = None, prompt_selected: Callable[[str], None] | None = None, **kwargs) -> dict:
    # A routing wrapper picks its concrete backend and model first, so the
    # call is reserved and dispatched against the same provider and model.
    routed = p.select(kwargs['model_hint']) if hasattr(p, 'select') else None
    if routed is not None:
        p, model, decision = routed
        kwargs['model'] = model
    selected_prompt = None
    if message_fallbacks is not None:
        # Pick evidence at the dispatch boundary, after routing pins the model.
        # Every candidate includes all required evidence; only supporting
        # evidence/notices may be omitted. Never truncate artifacts to fit.
        defaults = (getattr(p, 'config', {}).get('defaults') or {})
        completion = int(defaults.get('max_tokens', 2048))
        context = int(defaults.get('context_tokens', 8192))
        for messages in [kwargs['messages'], *message_fallbacks]:
            kwargs['messages'] = messages
            if p.name in ('ollama', 'fault'):
                selected_prompt = local_prompt_ceiling(kwargs)
            elif p.name == 'anthropic':
                selected_prompt = p.count_input_tokens(**kwargs)
            else:
                raise BudgetExceeded('provider has no enforceable paid token/price contract')
            if selected_prompt + completion <= context:
                break
        else:
            raise BudgetExceeded('input exceeds configured context ceiling')
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
        if selected_prompt is not None:
            # Reuse the count for the exact selected request, including paid
            # token counts, rather than calling the token-count endpoint twice.
            prompt = selected_prompt
        elif local:
            # Reject oversized context rather than silently truncating evidence.
            prompt = local_prompt_ceiling(kwargs)
        else:
            prompt = p.count_input_tokens(**kwargs)
        if prompt + completion > context:
            raise BudgetExceeded('input exceeds configured context ceiling')
        model = kwargs.get('model') or (p.model_for(kwargs['model_hint']) if hasattr(p, 'model_for') else p.name)
        rid = b.reserve_model_call(model, prompt, completion, local)
    try:
        if prompt_selected is not None:
            prompt_selected(kwargs['messages'][0]['content'])
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
