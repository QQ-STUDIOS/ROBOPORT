import ctypes
import json
import os
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from roboport_runtime import budget


def denied(code=5):
    exc=PermissionError(13,'Access is denied');exc.winerror=code;return exc


@pytest.mark.parametrize('code',[5,32,33])
def test_transient_replace_retries_atomic_snapshot_not_accounting(tmp_path,monkeypatch,code):
    b=budget.RunBudget({});b.artifact_dir=tmp_path;b._persist()
    original=Path.replace;attempts=[];waits=[]
    def replace(source,target):
        attempts.append(source)
        assert json.loads(source.read_text())['llm_calls']==1
        if len(attempts)<=2:
            assert json.loads(target.read_text())['llm_calls']==0
            raise denied(code)
        return original(source,target)
    monkeypatch.setattr(budget,'_WINDOWS',True,raising=False)
    monkeypatch.setattr(Path,'replace',replace)
    monkeypatch.setattr(budget.time,'sleep',waits.append)
    reservation=b.reserve_model_call('qwen3:14b',10,10,True)
    assert b.llm_calls==1 and len(b.reservations)==1 and reservation in b.reservations
    saved=json.loads((tmp_path/'budget.json').read_text())
    assert saved['llm_calls']==1 and saved['persistence']['replace_retries']==2
    assert len(attempts)==3 and len(set(attempts))==3 and len(waits)==2
    assert not list(tmp_path.glob('*.tmp'))


def test_permanent_denial_is_bounded_and_preserves_old_snapshot(tmp_path,monkeypatch):
    b=budget.RunBudget({});b.artifact_dir=tmp_path;b._persist();attempts=[]
    def fail(source,target):attempts.append(source);raise denied()
    monkeypatch.setattr(budget,'_WINDOWS',True,raising=False)
    monkeypatch.setattr(Path,'replace',fail)
    monkeypatch.setattr(budget.time,'sleep',lambda _:None)
    with pytest.raises(OSError) as caught:b.consume_tool_call()
    assert len(attempts)==6
    assert 'budget.json' in str(caught.value) and 'replace' in str(caught.value)
    assert isinstance(caught.value.__cause__,PermissionError)
    assert json.loads((tmp_path/'budget.json').read_text())['tool_calls']==0
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('windows,code',[(False,5),(True,19)])
def test_unrelated_os_error_is_not_retried(tmp_path,monkeypatch,windows,code):
    b=budget.RunBudget({});b.artifact_dir=tmp_path;attempts=[]
    def fail(source,target):attempts.append(source);raise denied(code)
    monkeypatch.setattr(budget,'_WINDOWS',windows,raising=False)
    monkeypatch.setattr(Path,'replace',fail)
    monkeypatch.setattr(budget.time,'sleep',lambda _:pytest.fail('unexpected delay'))
    with pytest.raises(OSError):b.consume_tool_call()
    assert len(attempts)==1


def test_retry_does_not_extend_run_wall_budget(tmp_path,monkeypatch):
    b=budget.RunBudget({'budgets':{'per_run':{'max_wall_seconds':.005}}},clock=lambda:0);b.artifact_dir=tmp_path
    attempts=[]
    def fail(source,target):attempts.append(source);raise denied()
    monkeypatch.setattr(budget,'_WINDOWS',True,raising=False)
    monkeypatch.setattr(Path,'replace',fail)
    monkeypatch.setattr(budget.time,'sleep',lambda _:pytest.fail('must not sleep past wall limit'))
    with pytest.raises(OSError):b.consume_tool_call()
    assert len(attempts)==1


@pytest.mark.skipif(os.name!='nt',reason='Real Windows sharing semantics')
def test_native_windows_reader_lock_recovers_without_repeating_call(tmp_path,monkeypatch):
    b=budget.RunBudget({});b.artifact_dir=tmp_path;b._persist();target=tmp_path/'budget.json'
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle.restype=ctypes.c_int
    handle=kernel.CreateFileW(str(target),0x80000000,1|2,None,3,0,None)
    assert handle!=ctypes.c_void_p(-1).value
    state={'closed':False,'waits':0}
    def release(delay):
        assert json.loads(target.read_text())['llm_calls']==0
        state['waits']+=1;assert kernel.CloseHandle(handle);state['closed']=True
    monkeypatch.setattr(budget.time,'sleep',release)
    try:reservation=b.reserve_model_call('qwen3:14b',10,10,True)
    finally:
        if not state['closed']:kernel.CloseHandle(handle)
    assert state['waits']==1 and b.llm_calls==1 and reservation in b.reservations
    saved=json.loads(target.read_text())
    assert saved['llm_calls']==1 and saved['persistence']['replace_retries']==1
    assert saved['persistence']['last_retry']['winerror'] in (5,32,33)


def test_no_replace_retry_after_scheduler_exhausts_deadline(tmp_path,monkeypatch):
    now=[0]
    b=budget.RunBudget({'budgets':{'per_run':{'max_wall_seconds':.5}}},clock=lambda:now[0]);b.artifact_dir=tmp_path
    original=Path.replace;attempts=[]
    def replace(source,target):
        attempts.append(source)
        if len(attempts)==1:raise denied()
        return original(source,target)
    monkeypatch.setattr(budget,'_WINDOWS',True,raising=False)
    monkeypatch.setattr(Path,'replace',replace)
    monkeypatch.setattr(budget.time,'sleep',lambda _:now.__setitem__(0,1))
    with pytest.raises(OSError):b.consume_tool_call()
    assert len(attempts)==1


def test_persistence_failure_prevents_provider_dispatch(tmp_path,monkeypatch):
    class Backend:
        name='ollama'
        config={'defaults':{'context_tokens':100000,'max_tokens':16}}
        calls=0
        def model_for(self,hint):return 'qwen3:14b'
        def chat_with_tools(self,**kwargs):self.calls+=1;return {'content':'{}','usage':{'cost_usd':0}}
    b=budget.RunBudget({});b.artifact_dir=tmp_path;p=Backend()
    def fail(source,target):raise denied()
    monkeypatch.setattr(budget,'_WINDOWS',True,raising=False)
    monkeypatch.setattr(Path,'replace',fail)
    monkeypatch.setattr(budget.time,'sleep',lambda _:None)
    with budget.run_budget(b),pytest.raises(OSError):
        budget.governed_chat(p,system='',messages=[{'role':'user','content':'test'}],tools=None,force_json=True,model_hint='any')
    assert p.calls==0 and b.llm_calls==1


def test_successful_slow_persist_cannot_authorize_expired_tool(tmp_path,monkeypatch):
    now=[0]
    b=budget.RunBudget({'budgets':{'per_run':{'max_wall_seconds':.5}}},clock=lambda:now[0]);b.artifact_dir=tmp_path
    original=Path.replace
    def slow(source,target):
        result=original(source,target);now[0]=1;return result
    monkeypatch.setattr(Path,'replace',slow)
    with pytest.raises(budget.BudgetExceeded):b.consume_tool_call()
    assert json.loads((tmp_path/'budget.json').read_text())['tool_calls']==1


def test_benchmark_finalizer_does_not_truncate_preserved_budget(tmp_path,monkeypatch):
    import benchmark
    path=tmp_path/'budget.json';path.write_text('{"sentinel":"last valid snapshot"}')
    before=path.read_bytes();attempts=[]
    def fail_replace(source,target):attempts.append(source);raise denied()
    def run_one(*args,**kwargs):
        budget.current_budget().consume_tool_call()
        pytest.fail('dispatch must stop')
    monkeypatch.setattr(benchmark,'run_one',run_one)
    monkeypatch.setattr(budget,'_WINDOWS',True)
    monkeypatch.setattr(Path,'replace',fail_replace)
    monkeypatch.setattr(budget.time,'sleep',lambda _:None)
    outcome,grade=benchmark.execute_run({'id':1,'prompt':'test'},tmp_path,{},config={},live=True,grade=False)
    assert outcome['status']=='failed' and grade is None
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('*.tmp'))


def test_benchmark_finalizer_failure_changes_success_to_failure(tmp_path,monkeypatch):
    import benchmark
    path=tmp_path/'budget.json';path.write_text('{"sentinel":"last valid snapshot"}')
    before=path.read_bytes()
    monkeypatch.setattr(benchmark,'run_one',lambda *a,**k:{'status':'ok','steps':[]})
    monkeypatch.setattr(budget,'_WINDOWS',True)
    def fail(source,target):raise denied()
    monkeypatch.setattr(Path,'replace',fail)
    monkeypatch.setattr(budget.time,'sleep',lambda _:None)
    outcome,_=benchmark.execute_run({'id':1,'prompt':'test'},tmp_path,{},config={},live=True,grade=False)
    assert outcome['status']=='failed'
    assert path.read_bytes()==before
    assert json.loads((tmp_path/'provenance.json').read_text())['status']=='failed'


def test_slow_reservation_persist_cannot_dispatch_expired_model(tmp_path,monkeypatch):
    now=[0]
    b=budget.RunBudget({'budgets':{'per_run':{'max_wall_seconds':.5}}},clock=lambda:now[0]);b.artifact_dir=tmp_path
    class Backend:
        name='ollama';config={'defaults':{'context_tokens':100000,'max_tokens':16}};calls=0
        def model_for(self,hint):return 'qwen3:14b'
        def chat_with_tools(self,**kwargs):self.calls+=1;return {'content':'{}','usage':{'cost_usd':0}}
    original=Path.replace
    def slow(source,target):
        result=original(source,target);now[0]=1;return result
    monkeypatch.setattr(Path,'replace',slow);p=Backend()
    with budget.run_budget(b),pytest.raises(budget.BudgetExceeded):
        budget.governed_chat(p,system='',messages=[{'role':'user','content':'test'}],tools=None,force_json=True,model_hint='any')
    assert p.calls==0


def test_finished_model_accounting_can_persist_after_deadline(tmp_path):
    now=[0]
    b=budget.RunBudget({'budgets':{'per_run':{'max_wall_seconds':.5}}},clock=lambda:now[0]);b.artifact_dir=tmp_path
    reservation=b.reserve_model_call('qwen3:14b',10,10,True);now[0]=1
    b.settle_model_call(reservation,{'cost_usd':0})
    saved=json.loads((tmp_path/'budget.json').read_text())
    assert saved['llm_calls']==1 and saved['reserved_usd']==0 and saved['elapsed_seconds']==1
