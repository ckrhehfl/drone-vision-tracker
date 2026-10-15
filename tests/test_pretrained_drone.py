import hashlib
import io
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import tools.pretrained_drone as pretrained


class Values(list):
    def copy_(self, values):
        self[:] = values


class Conv:
    def __init__(self, inputs, outputs, kernel):
        self.in_channels, self.out_channels = inputs, outputs
        self.kernel_size = (kernel, kernel)
        self.weight, self.bias = Values(range(outputs)), Values(range(10, 10 + outputs))

    def to(self, other):
        return self


class Detect:
    def __init__(self):
        self.nc, self.no, self.reg_max, self.end2end = 2, 66, 16, False
        self.cv3 = [[Conv(64, 2, 1)] for _ in range(3)]


def model_fixture():
    core = SimpleNamespace(
        model=[Detect()],
        yaml={"nc": 2},
        names={0: "quadcopter", 1: "fixed-wing"},
        criterion="old two-class loss",
    )
    return SimpleNamespace(task="detect", names=core.names, model=core, ckpt={"optimizer": "old"})


TORCH = SimpleNamespace(nn=SimpleNamespace(Conv2d=Conv), no_grad=nullcontext)


def test_conversion_keeps_learned_quadcopter_channel_and_drops_stale_training_state():
    model = model_fixture()
    head = model.model.model[-1]
    original = [(list(v[-1].weight), list(v[-1].bias)) for v in head.cv3]
    pretrained.retain_quadcopter(model, TORCH)
    assert model.model.names == {0: "drone"} and model.model.yaml["nc"] == 1
    assert head.nc == 1 and head.no == 65
    for branch, (weight, bias) in zip(head.cv3, original, strict=True):
        assert branch[-1].out_channels == 1
        assert branch[-1].weight == weight[:1] and branch[-1].bias == bias[:1]
    assert not hasattr(model.model, "criterion") and model.ckpt == {}


@pytest.mark.parametrize("problem", ["task", "names", "head", "channels", "kernel", "bias"])
def test_converter_rejects_unexpected_source_model(problem):
    model = model_fixture()
    head = model.model.model[-1]
    if problem == "task":
        model.task = "segment"
    elif problem == "names":
        model.names = {0: "person", 1: "drone"}
    elif problem == "head":
        head.nc = 3
    elif problem == "channels":
        head.cv3[0][-1].out_channels = 3
    elif problem == "kernel":
        head.cv3[0][-1].kernel_size = (3, 3)
    else:
        head.cv3[0][-1].bias = None
    with pytest.raises(ValueError):
        pretrained.retain_quadcopter(model, TORCH)


def test_download_install_receipt_and_idempotence_without_ai(tmp_path, monkeypatch):
    source, root = tmp_path / "source.pt", tmp_path / "installed"
    content = b"pinned original weights"
    monkeypatch.setattr(pretrained, "SOURCE_SHA256", hashlib.sha256(content).hexdigest())
    urls, models = [], []

    def fetch(url, timeout):
        urls.append(url)
        assert timeout == 60
        return io.BytesIO(content)

    def yolo(path, task):
        model = model_fixture()
        if path == str(source):
            model.save = lambda path: (root / "best.pt").write_bytes(b"derived drone weights")
            models.append(model)
        else:
            model.names = {0: "drone"}
        return model

    monkeypatch.setattr(pretrained.urllib.request, "urlopen", fetch)
    monkeypatch.setattr("tools.train.prepare_runtime", lambda: (TORCH, yolo))
    assert pretrained.installed_model(root) is None
    with pytest.raises(ValueError):
        pretrained.install(source, root)
    assert not root.exists() and not urls
    weights = pretrained.install(source, root, download=True)
    assert urls == [pretrained.SOURCE_URL]
    assert models[0].model.names == {0: "drone"}
    assert pretrained.installed_model(root) == weights.resolve()
    record = json.loads((root / "source.json").read_text())
    assert record["revision"] == pretrained.REVISION and not record["trained_locally"]
    assert not record["physical_accuracy_verified"]
    assert pretrained.install(source, root, download=True) == weights
    assert len(urls) == 1
    weights.write_bytes(b"corruption")
    assert pretrained.installed_model(root) is None
    with pytest.raises(ValueError, match="이미"):
        pretrained.install(source, root, download=True)
    assert weights.read_bytes() == b"corruption"


def test_checksum_failure_never_loads_or_overwrites_files(tmp_path, monkeypatch):
    source, root = tmp_path / "source.pt", tmp_path / "installed"
    monkeypatch.setattr(pretrained.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"bad"))
    monkeypatch.setattr(
        "tools.train.prepare_runtime", lambda: pytest.fail("No unsafe weights load")
    )
    with pytest.raises(ValueError, match="SHA-256"):
        pretrained.install(source, root, download=True)
    assert not source.exists() and not root.exists()
    assert source.with_suffix(".pt.part").read_bytes() == b"bad"
    with pytest.raises(FileExistsError):
        pretrained.install(source, root, download=True)
    source.write_bytes(b"bad existing weights")
    with pytest.raises(ValueError):
        pretrained.install(source, root, download=True)
    assert source.read_bytes() == b"bad existing weights"


@pytest.mark.parametrize("receipt", [[], {}, {"status": "installed"}])
def test_incomplete_receipts_never_enable_automatic_model(tmp_path, receipt):
    (tmp_path / "source.json").write_text(json.dumps(receipt))
    (tmp_path / "best.pt").write_bytes(b"unknown")
    assert pretrained.installed_model(tmp_path) is None
