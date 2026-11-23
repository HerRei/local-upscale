import sys
from types import SimpleNamespace

import pytest

from localsr.worker.cli import main


@pytest.mark.parametrize(("architectures", "expected"), [(["sm_120", "sm_90"], 0), (["sm_90"], 1)])
def test_packaged_worker_cuda_arch_check(monkeypatch, architectures, expected):
    monkeypatch.setattr(sys, "argv", ["worker", "--check-cuda-arch"])
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(get_arch_list=lambda: architectures)),
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == expected
