import os
import subprocess
import sys
import uuid
import pytest
from gear_contracts.api import GearError
from gear_framework.locking import HostLock


def test_lock_excludes_other_process_and_releases():
    name = "GEAR.Test." + uuid.uuid4().hex
    lock = HostLock(name)
    lock.acquire()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(["src", "doc/contracts"])
    script = (
        "from gear_framework.locking import HostLock; x=HostLock("
        + repr(name)
        + "); x.acquire(); x.close()"
    )
    try:
        p = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env
        )
        assert p.returncode != 0 and "HOST_BUSY" in p.stderr
        with pytest.raises(GearError):
            HostLock(name).acquire()
    finally:
        lock.close()
    p = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env
    )
    assert p.returncode == 0, p.stderr
