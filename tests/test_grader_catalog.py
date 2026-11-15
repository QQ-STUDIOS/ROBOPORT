import json
import sys
from pathlib import Path
import pytest
import jsonschema
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from roboport_runtime.grader import _reference_schema, _pointer_value


def test_catalog_binds_source_to_real_escaped_pointers():
    sources = {"final_output": json.dumps({"flags": {"~audit/key": False}}),
               "steps": json.dumps({"s1": {"output": {"row_count": 7}}})}
    schema = _reference_schema(sources)
    jsonschema.validate({"source":"final_output", "pointer":"/flags/~0audit~1key"}, schema)
    for ref in [{"source":"final_output", "pointer":"/flags/~audit/key"},
                {"source":"steps", "pointer":"/s1/output/dataset/row_count"},
                {"source":"plan", "pointer":""}]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(ref, schema)


def test_every_catalog_pointer_resolves_and_catalog_is_bounded():
    sources={"final_output": json.dumps({"many": [{"value":i} for i in range(10000)]})}
    schema=_reference_schema(sources)
    paths=schema["oneOf"][0]["properties"]["pointer"]["enum"]
    assert "" in paths and len(paths) <= 16
    for pointer in paths:
        _pointer_value(sources["final_output"], pointer)


def test_runtime_checks_override_two_false_acceptances(tmp_path, monkeypatch):
    from roboport_runtime import client, grader, budget
    (tmp_path / 'final_output.json').write_text('{"summary":{"total_amount":38},"error":"failed"}')
    (tmp_path / 'plan.json').write_text('{"steps":[{"id":"extract","owner":"data_engineering"}]}')
    (tmp_path / 'steps').mkdir()
    (tmp_path / 'steps' / 'extract.json').write_text(json.dumps({"status":"ok","output":{"transactions":[{"amount":10},{"amount":27}]}}))
    class Backend:
        name='ollama'
        config={'defaults':{'context_tokens':100000,'max_tokens':2048}}
        def model_for(self,hint): return 'qwen3:14b'
        def chat_with_tools(self, **kwargs):
            return {'content':json.dumps({'results':[
                {'expectation_id':0,'finding':'SUPPORTED','citations':[{'source':'final_output','pointer':'/summary/total_amount'}]},
                {'expectation_id':1,'finding':'SUPPORTED','citations':[{'source':'final_output','pointer':'/error'}]}]}),'usage':{'cost_usd':0}}
    monkeypatch.setattr(client,'provider',lambda:Backend())
    with budget.run_budget(budget.RunBudget({})):
        result=grader.call_grader(['The reported total_amount equals the sum of all recorded transaction amounts','The error field is an empty string'],None,tmp_path)
    assert [r['verdict'] for r in result['results']] == ['FAIL','FAIL']
    assert result['pass_rate']==0
    assert all(r['deterministic_check']['method']=='deterministic' for r in result['results'])
    assert all(r['assessments'][0]['finding']=='SUPPORTED' for r in result['results'])


@pytest.mark.parametrize('payload,expected', [('{"answer":2,"answer":1}', 'INCONCLUSIVE'), ('{"answer":1e999999999999999999999999999999}', 'INCONCLUSIVE'), ('{"answer":9007199254740993.0}', 'FAIL')])
def test_grader_preserves_strict_operands(tmp_path, monkeypatch, payload, expected):
    from roboport_runtime import client, grader, budget
    (tmp_path/'final_output.json').write_text(payload)
    class Backend:
        name='ollama'
        config={'defaults':{'context_tokens':100000,'max_tokens':2048}}
        def model_for(self,hint): return 'qwen3:14b'
        def chat_with_tools(self, **kwargs):
            return {'content':json.dumps({'results':[{'expectation_id':0,'finding':'SUPPORTED','citations':[{'source':'final_output','pointer':''}]}]}),'usage':{'cost_usd':0}}
    monkeypatch.setattr(client,'provider',lambda:Backend())
    assertion='answer is 1' if expected=='INCONCLUSIVE' else 'answer is 9007199254740992'
    with budget.run_budget(budget.RunBudget({})):
        result=grader.call_grader([assertion],None,tmp_path)
    assert result['results'][0]['verdict']==expected
