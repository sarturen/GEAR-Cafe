import json
import uuid
import pytest

PLUGIN_CODE = """
import threading
class Plugin:
    def __init__(self):
        self.calls=[]; self.open_count=0; self.connected=False
        self.entered=threading.Event(); self.release=threading.Event()
        self.block=False; self.fail=False; self.cleanup_fail=False; self.begin_fail=False
        self.threads=[]
    def record(self,name):
        self.calls.append(name); self.threads.append(threading.get_ident())
    def configure(self,slice): self.record("configure"); self.slice=slice
    def validate_config(self,slice):
        self.record("validate")
        return {"status":"VALID","diagnostics":[]}
    def begin_run(self,binding,context):
        self.record("begin"); self.binding=binding; self.context=context
        if self.begin_fail: raise ValueError("begin broke")
    def invoke(self,resource,operation,args,context):
        self.record("invoke")
        if not self.connected: self.connected=True; self.open_count+=1
        self.entered.set()
        if self.block: self.release.wait()
        return {"ok":not self.fail,"diagnostic":None,"details":{}}
    def evaluate(self,resource,condition,args,context):
        self.record("evaluate")
        return {"ok":True,"satisfied":not self.fail,"diagnostic":None,"details":{}}
    def collect(self,resource,evidence,args,context):
        self.record("collect")
        return {"ok":True,"diagnostic":None,"artifacts":[]}
    def end_run(self):
        self.record("end")
        if self.cleanup_fail: raise ValueError("cleanup broke")
    def close(self): self.record("close"); self.connected=False
def create_plugin(): return Plugin()
"""


@pytest.fixture
def bench(tmp_path):
    app = tmp_path / "app"
    package = "gear_test_" + uuid.uuid4().hex
    directory = app / "plugins" / "demo"
    (directory / package).mkdir(parents=True)
    (directory / package / "__init__.py").write_text("", encoding="utf-8")
    (directory / package / "runtime.py").write_text(PLUGIN_CODE, encoding="utf-8")
    manifest = {
        "api": "gear.plugin/v1",
        "id": "gear.demo",
        "version": "1.0.0",
        "entrypoints": {"runtime": package + ".runtime:create_plugin"},
        "resource_types": {
            "SCREEN": {
                "operations": {"ON": {}},
                "conditions": {"LIT": {}},
                "evidence": {"CAPTURE": {}},
            }
        },
    }
    (directory / "gear-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
    project = {
        "api": "gear.project/v1",
        "name": "demo",
        "resources": {"SCREEN.a": {"type": "SCREEN"}, "SCREEN.b": {"type": "SCREEN"}},
    }
    env = {
        "api": "gear.environment/v1",
        "name": "demo",
        "plugins": {"gear.demo": {}},
        "resources": {"SCREEN.a": {"type": "SCREEN", "plugin": "gear.demo"}},
    }
    case = {
        "api": "gear.dsl/v1",
        "name": "demo",
        "body": [{"do": {"resource": "SCREEN.a", "operation": "ON"}}],
    }
    paths = {}
    for name, data in (("project", project), ("environment", env), ("case", case)):
        paths[name] = app / (name + ".yaml")
        paths[name].write_text(json.dumps(data), encoding="utf-8")
    return {"app": app, "manifest": manifest, "directory": directory, **paths}


def write_case(bench, case):
    bench["case"].write_text(json.dumps(case), encoding="utf-8")
