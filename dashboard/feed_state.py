"""Bounded, reconstructible state for SSE joins. Call under the broadcaster lock."""
from collections import OrderedDict, deque
from copy import deepcopy
from datetime import datetime, timezone
import time


class SnapshotStore:
    def __init__(self, limit=8000, clock=time.time):
        self.limit, self.clock = limit, clock
        self.initialized = False
        self.seq = 0
        self._reset({})

    def _reset(self, data):
        self.config = deepcopy(data.get('config') or {'stations': []})
        self.metrics = deepcopy(data.get('metrics') or {})
        self.peak_rate = self.metrics.get('tasks_per_min', 0)
        self.agents, self.stations, self.tasks = OrderedDict(), OrderedDict(), OrderedDict()
        self.logs, self.incidents, self.metric_history = deque(maxlen=60), deque(maxlen=100), deque(maxlen=80)
        self.active_alerts = deque(maxlen=100)
        self.completions = deque(maxlen=self.limit)
        self.latencies, self.agent_history = OrderedDict(), OrderedDict()
        self.truncated = False
        self.incidents.extend(deepcopy((data.get('recent_incidents') or data.get('alerts') or [])[:100]))
        for alert in (data.get('alerts') or [])[:100]:
            self.active_alerts.append((deepcopy(alert), self.clock() * 1000 + alert.get('ttl_s', 8) * 1000))
        for kind, key in [('agents', 'agent_id'), ('stations', 'station_id'), ('tasks', 'task_id')]:
            for row in data.get(kind) or []:
                self._put(getattr(self, kind), row.get(key), row)

    def _put(self, table, key, data):
        if not key:
            return
        prior = table.get(key, {})
        if table is self.agents or table is self.stations:
            old_rev, new_rev = prior.get('rev'), data.get('rev')
            if isinstance(old_rev, (int, float)) and isinstance(new_rev, (int, float)) and new_rev <= old_rev:
                return
        table[key] = {**prior, **deepcopy(data)}
        table.move_to_end(key)
        cap = self.limit if table is self.tasks else min(self.limit, 512)
        if len(table) > cap:
            table.popitem(last=False)
            self.truncated = True

    def accept(self, env):
        self.seq = env.get('seq', self.seq)
        kind, data = env.get('type'), env.get('data') or {}
        if kind == 'snapshot':
            self._reset(data)
            self.initialized = True
            return
        if not self.initialized:
            return
        if kind in ('agent.added', 'agent.updated'):
            self._put(self.agents, data.get('agent_id'), data)
        elif kind == 'agent.removed':
            self.agents.pop(data.get('agent_id'), None)
        elif kind == 'station.updated':
            if data.get('station_id') in {s.get('station_id') for s in (self.config.get('stations') or [])}:
                self._put(self.stations, data.get('station_id'), data)
        elif kind in ('task.completed', 'task.failed', 'task.cancelled'):
            task = {**self.tasks.pop(data.get('task_id'), {}), **data}
            sid, aid = task.get('station_id'), task.get('assigned_agent_id')
            ok = kind == 'task.completed'
            latency = None
            if ok and task.get('started_at') and task.get('finished_at'):
                try:
                    latency = (datetime.fromisoformat(task['finished_at']) - datetime.fromisoformat(task['started_at'])).total_seconds() * 1000
                except (ValueError, TypeError):
                    pass
            if ok and sid:
                if len(self.completions) == self.limit:
                    self.truncated = True
                self.completions.append((sid, self.clock() * 1000))
                if latency is not None:
                    self.latencies.setdefault(sid, deque(maxlen=30)).append(latency)
            if aid and kind != 'task.cancelled':
                self.agent_history.setdefault(aid, deque(maxlen=10)).appendleft({
                    'station': self.stations.get(sid, {}).get('name', sid or '?'),
                    'task_id': task.get('task_id'), 'ok': ok, 'lat_ms': latency,
                })
            for table in (self.latencies, self.agent_history):
                while len(table) > min(self.limit, 512):
                    table.popitem(last=False)
                    self.truncated = True
        elif kind and kind.startswith('task.'):
            self._put(self.tasks, data.get('task_id'), data)
        elif kind == 'metrics.tick':
            self.metrics.update(deepcopy(data))
            self.peak_rate = max(self.peak_rate, data.get('tasks_per_min', 0))
            self.metric_history.append(deepcopy(data))
        elif kind == 'log.appended':
            self.logs.appendleft(deepcopy(data))
        elif kind == 'alert.raised':
            self.incidents.appendleft(deepcopy(data))
            self.active_alerts.append((deepcopy(data), self.clock() * 1000 + data.get('ttl_s', 8) * 1000))

    def snapshot(self):
        if not self.initialized:
            return None
        now = self.clock() * 1000
        while self.completions and self.completions[0][1] < now - 60000:
            self.completions.popleft()
        throughput = {}
        for sid, at in self.completions:
            throughput.setdefault(sid, []).append(at)
        data = {
            'config': self.config, 'agents': list(self.agents.values()),
            'stations': list(self.stations.values()), 'tasks': list(self.tasks.values()),
            'metrics': self.metrics,
            'alerts': [{**alert, 'ttl_s': (expires-now)/1000} for alert, expires in self.active_alerts if expires > now],
            'recent_logs': list(self.logs),
            'peak_rate': self.peak_rate,
            'recent_incidents': list(self.incidents), 'metrics_history': list(self.metric_history),
            'history_limited': self.truncated,
            'observed_at_ms': now,
            'derived': {'throughput_ms': throughput,
                        'latencies': {k: list(v) for k, v in self.latencies.items()},
                        'agent_history': {k: list(v) for k, v in self.agent_history.items()}},
        }
        return deepcopy({'v': 1, 'seq': self.seq, 'ts': datetime.now(timezone.utc).isoformat(),
                         'type': 'snapshot', 'data': data})
