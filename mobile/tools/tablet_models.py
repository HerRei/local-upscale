"""Tablet extension for export_models.py. Workers isolate models and release memory."""
import collections
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import export_models as common

ROOT = common.TOOLS.parent
IOS = common.MODELS_DIR / 'ios-tablet'
ANDROID = common.MODELS_DIR / 'android-tablet'
SOURCE = Path.home() / 'Library/Application Support/LocalSR/models'
CORE_SRC = Path(os.environ.get('LOCALSR_CORE_SRC', common.WORKSPACE / 'LocalSR-coreml' / 'src'))
# A 256px fixed window limits the activation memory of the larger restoration networks.
MODELS = {
    'best': (common.CANDIDATES / 'localsr_photo_pulse48_v1_x4.safetensors', 384),
    'fast': (common.CANDIDATES / 'localsr_photo_span28_v1_x4.safetensors', 384),
    'nomos_realplksr': (SOURCE / '4xNomosWebPhoto_RealPLKSR.pth', 256),
    'denoise_realplksr': (SOURCE / '1xDeNoise_realplksr_otf.pth', 256),
    'fbcnn': (SOURCE / 'fbcnn_color.pth', 256),
    'anime_esrgan': (SOURCE / 'RealESRGAN_x4plus_anime_6B.pth', 384),
    'nomos_span': (SOURCE / '4xNomosUni_span_multijpg.pth', 384),
    'span48': (SOURCE / '4x-spanx4-ch48.pth', 384),
    'scunet': (SOURCE / 'scunet_color_real_psnr.pth', 256),
}

def model(key):
    import torch
    import spandrel
    sys.path.insert(0, str(common.TRAIN_SRC))
    sys.path.insert(0, str(CORE_SRC))
    from localsr_train.spandrel_pulse import register
    from localsr.core.onnx_runtime import prepare_export_model
    register()
    desc = spandrel.ModelLoader().load_from_file(MODELS[key][0])
    module = desc.model.cpu().float().eval()
    prepare_export_model(module, desc.architecture.name)
    for child in module.modules():
        for name, value in list(vars(child).items()):
            if isinstance(value, torch.Tensor):
                setattr(child, name, value.cpu())
    class ImageOnly(torch.nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net
        def forward(self, x):
            return desc._call_fn(self.net, x)
    return ImageOnly(module).eval(), desc.scale, desc.architecture.name

def tile(key):
    return common.probe_array(MODELS[key][1]).astype('float32').transpose(2, 0, 1)[None].copy()

def export_ios(key):
    import numpy as np
    import torch
    import coremltools as ct
    import coremltools.optimize.coreml as opt
    import threading
    import tqdm
    tqdm.tqdm.set_lock(threading.RLock())
    net, scale, arch = model(key)
    side = MODELS[key][1]
    with torch.no_grad():
        traced = torch.jit.trace(net, torch.from_numpy(tile(key)), check_trace=False)
    del net
    ml = ct.convert(traced, convert_to='mlprogram',
        inputs=[ct.TensorType(name='image', shape=(1, 3, side, side), dtype=np.float16)],
        outputs=[ct.TensorType(name='out', dtype=np.float16)],
        compute_precision=ct.precision.FLOAT16, minimum_deployment_target=ct.target.iOS17)
    del traced
    gc.collect()
    def compile_one(program, suffix):
        with tempfile.TemporaryDirectory(prefix='tablet-', dir=ROOT) as temp:
            package = Path(temp) / 'model.mlpackage'
            program.save(str(package))
            subprocess.run(['xcrun', 'coremlcompiler', 'compile', str(package), temp], check=True)
            target = IOS / f'{key}{suffix}.mlmodelc'
            if target.exists(): shutil.rmtree(target)
            shutil.move(str(Path(temp) / 'model.mlmodelc'), target)
    compile_one(ml, '')
    if common.size_kb(IOS / f'{key}.mlmodelc') > 20 * 1024:
        config = opt.OptimizationConfig(global_config=opt.OpPalettizerConfig(mode='kmeans', nbits=8))
        pal = opt.palettize_weights(ml, config)
        compile_one(pal, '-pal8')

def export_android(key):
    import torch
    import litert_torch
    from ai_edge_quantizer import quantizer
    from ai_edge_quantizer.algorithm_manager import AlgorithmName
    from ai_edge_quantizer.recipe_manager import RecipeManager
    net, _, _ = model(key)
    nhwc = litert_torch.to_channel_last_io(net, args=[0], outputs=[0]).eval()
    side = MODELS[key][1]
    with tempfile.TemporaryDirectory(prefix='tablet-', dir=ROOT) as temp:
        fp32 = Path(temp) / 'fp32.tflite'
        litert_torch.convert(nhwc, (torch.rand(1, side, side, 3),)).export(str(fp32))
        rm = RecipeManager()
        rm.add_weight_only_config(regex='.*', operation_name='*', num_bits=16,
                                  algorithm_key=AlgorithmName.FLOAT_CASTING)
        q = quantizer.Quantizer(str(fp32))
        q.load_quantization_recipe(rm.get_quantization_recipe())
        q.quantize().export_model(str(ANDROID / f'{key}.tflite'), overwrite=True)

def placement(path, cu):
    from coremltools.models.compute_plan import MLComputePlan
    plan = MLComputePlan.load_from_path(str(path), compute_units=cu)
    counts = collections.Counter()
    def visit(block):
        for op in block.operations:
            usage = plan.get_compute_device_usage_for_mlprogram_operation(op)
            counts[type(usage.preferred_compute_device).__name__ if usage else 'unknown'] += 1
            for b in op.blocks: visit(b)
    for fn in plan.model_structure.program.functions.values(): visit(fn.block)
    return dict(counts)

def check_ios(key):
    import torch
    import numpy as np
    import coremltools as ct
    net, scale, arch = model(key)
    x = tile(key)
    with torch.no_grad(): ref = net(torch.from_numpy(x)).numpy()
    del net
    rows = []
    base = IOS / f'{key}.mlmodelc'
    if common.size_kb(base) > 20 * 1024:
        assert (IOS / f'{key}-pal8.mlmodelc').exists(), 'required 8-bit palette artifact missing'
    for suffix in ('', '-pal8'):
        path = IOS / f'{key}{suffix}.mlmodelc'
        if not path.exists(): continue
        metadata = json.loads((path / 'metadata.json').read_text())[0]
        inp, out_meta = metadata['inputSchema'][0], metadata['outputSchema'][0]
        assert (inp['name'], inp['dataType'], json.loads(inp['shape'])) == ('image', 'Float16', list(x.shape))
        assert (out_meta['name'], out_meta['dataType'], json.loads(out_meta['shape'])) == ('out', 'Float16', list(ref.shape))
        for name, cu in [('ANE', ct.ComputeUnit.CPU_AND_NE), ('GPU', ct.ComputeUnit.CPU_AND_GPU), ('CPU', ct.ComputeUnit.CPU_ONLY)]:
            m = ct.models.CompiledMLModel(str(path), compute_units=cu)
            ms, out = common.timed(lambda: m.predict({'image': x.astype(np.float16)})['out'], runs=3)
            assert out.shape == ref.shape
            p = common.psnr(ref, out)
            try: devices = placement(path, cu)
            except Exception as e: devices = {'error': str(e)}
            rows.append(dict(model=key, format='Core ML' + suffix, unit=name, psnr=p, ms=ms, mb=common.size_kb(path)/1024, placement=devices, ok=bool(np.isfinite(out).all() and p >= 45)))
            del m, out
    return rows

def check_android(key):
    import torch
    import numpy as np
    from ai_edge_litert.interpreter import Interpreter
    net, _, _ = model(key)
    x = tile(key)
    with torch.no_grad(): ref = net(torch.from_numpy(x)).numpy()
    del net
    path = ANDROID / f'{key}.tflite'
    it = Interpreter(model_path=str(path), num_threads=4)
    it.allocate_tensors()
    inp, out = it.get_input_details()[0], it.get_output_details()[0]
    assert any(t['dtype'] == np.float16 for t in it.get_tensor_details())
    def predict():
        it.set_tensor(inp['index'], x.transpose(0, 2, 3, 1).copy())
        it.invoke()
        return it.get_tensor(out['index']).transpose(0, 3, 1, 2).copy()
    ms, result = common.timed(predict, runs=3)
    assert result.shape == ref.shape
    p = common.psnr(ref, result)
    return [dict(model=key, format='LiteRT', unit='CPU', psnr=p, ms=ms, mb=common.size_kb(path)/1024, ok=bool(np.isfinite(result).all() and p >= 45))]

def worker(action, key, result):
    try:
        value = {'ios': export_ios, 'android': export_android, 'check-ios': check_ios, 'check-android': check_android}[action](key)
        Path(result).write_text(json.dumps({'ok': True, 'rows': value or []}))
    except Exception:
        Path(result).write_text(json.dumps({'ok': False, 'error': traceback.format_exc()}))
        raise

def run(check=False):
    IOS.mkdir(parents=True, exist_ok=True)
    ANDROID.mkdir(parents=True, exist_ok=True)
    report = {}
    for key in MODELS:
        if check and key == 'scunet': continue
        report[key] = {}
        for platform in ('ios', 'android'):
            if key == 'scunet' and platform == 'android': continue
            path = IOS / f'{key}.mlmodelc' if platform == 'ios' else ANDROID / f'{key}.tflite'
            if check and not path.exists():
                report[key][platform] = {'ok': False, 'error': 'artifact missing'}
                continue
            action = ('check-' if check else '') + platform
            if not check:
                stale = [path]
                if platform == 'ios':
                    stale.append(IOS / f'{key}-pal8.mlmodelc')
                for item in stale:
                    if item.is_dir(): shutil.rmtree(item)
                    elif item.exists(): item.unlink()
            print(f'[{key}] {action}', flush=True)
            with tempfile.TemporaryDirectory(prefix='tablet-', dir=ROOT) as temp:
                result = Path(temp) / 'result.json'
                if platform != 'ios':
                    common.ensure_litert_venv()
                python = sys.executable if platform == 'ios' else str(common.LITERT_PYTHON)
                try:
                    proc = subprocess.run([python, str(common.TOOLS / 'export_models.py'), '--tablet-worker', action, key, str(result)], timeout=600 if key == 'scunet' else None, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                    data = json.loads(result.read_text()) if result.exists() else {'ok': False, 'error': f'worker exited {proc.returncode} without a result'}
                except subprocess.TimeoutExpired:
                    data = {'ok': False, 'error': 'conversion exceeded 10-minute limit'}
                report[key][platform] = data
                print(f'[{key}] {action}: {"ok" if data["ok"] else data.get("error", "failed")[-1200:]}', flush=True)
        (ROOT / 'tools' / ('tablet-check.json' if check else 'tablet-export.json')).write_text(json.dumps(report, indent=2))
    failed = any(not d['ok'] or any(not r['ok'] for r in d.get('rows', [])) for k, v in report.items() if k != 'scunet' for d in v.values())
    if check:
        for key, platforms in report.items():
            for data in platforms.values():
                for r in data.get('rows', []):
                    print(f"{key:20} {r['format']:12} {r['unit']:4} {r['ms']:9.1f} ms {r['psnr']:7.2f} dB {'ok' if r['ok'] else 'FAIL'}")
        print('CHECK FAILED' if failed else 'CHECK PASSED')
    return int(failed)
