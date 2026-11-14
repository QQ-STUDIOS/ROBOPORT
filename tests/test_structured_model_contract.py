import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from roboport_runtime import client, providers


def test_json_schema_reaches_ollama_and_prompt(monkeypatch):
    sent = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message': {'content': '{"wave":0}'}}
    class Requests:
        import requests
        exceptions = requests.exceptions
        def post(self, url, **kwargs): sent.append(kwargs['json']); return Response()
    backend = providers.OllamaProvider.__new__(providers.OllamaProvider)
    backend._requests = Requests()
    backend.config = {'defaults': {}}
    backend.host = 'http://unused'
    backend.model_for = lambda hint: 'qwen3:14b'
    monkeypatch.setattr(client, 'provider', lambda: backend)
    schema = {'type': 'object', 'required': ['wave'], 'properties': {'wave': {'type': 'integer'}}}
    assert client.call_model_json(system_spec='planner', user_prompt='plan', schema=schema) == {'wave': 0}
    assert sent[0]['format'] == schema
    assert '"wave"' in sent[0]['messages'][0]['content']
