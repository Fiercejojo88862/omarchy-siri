#!/usr/bin/env python3
"""Apply CPU-only compatibility patches to an installed coqui-tts.
Needed on CPU-only / newer-Python installs; harmless elsewhere (skips cleanly).
Patches:
 1. TTS/__init__.py — don't hard-require torchcodec (training-IO only).
 2. TTS/tts/layers/tortoise/autoregressive.py — shim removed isin_mps_friendly.
 3. TTS/tts/models/xtts.py — soundfile fallback in load_audio (no torchcodec).
"""
import site
import sys
from pathlib import Path


def find_tts() -> Path | None:
    for base in site.getsitepackages() + [site.getusersitepackages()]:
        cand = Path(base) / "TTS" / "__init__.py"
        if cand.is_file():
            return cand.parent
    return None


PATCHES = [
    ("__init__.py",
     'if is_torch_greater_or_equal("2.9"):\n'
     '    if not is_torchcodec_available():\n'
     '        raise ImportError(TORCHCODEC_IMPORT_ERROR)',
     'if is_torch_greater_or_equal("2.9"):\n'
     '    # torchcodec is only needed for dataset/training IO; XTTS inference uses\n'
     '    # librosa/soundfile. The stock torchcodec wheel links CUDA libs missing on\n'
     '    # CPU-only installs, so don\'t hard-fail here.\n'
     '    try:\n'
     '        if not is_torchcodec_available():\n'
     '            pass\n'
     '    except Exception:\n'
     '        pass'),
    ("tts/layers/tortoise/autoregressive.py",
     '# TODO: use torch.isin from Pytorch 2.4\n'
     'from transformers.pytorch_utils import isin_mps_friendly as isin',
     '# TODO: use torch.isin from Pytorch 2.4\n'
     'try:\n'
     '    from transformers.pytorch_utils import isin_mps_friendly as isin\n'
     'except ImportError:  # transformers>=5 removed the helper; torch>=2.4 has isin natively\n'
     '    def isin(elements, test_elements):\n'
     '        return torch.isin(elements, test_elements)'),
    ("tts/models/xtts.py",
     '    # torchaudio should chose proper backend to load audio depending on platform\n'
     '    audio, lsr = torchaudio.load(audiopath)',
     '    # torchaudio should chose proper backend to load audio depending on platform\n'
     '    try:\n'
     '        audio, lsr = torchaudio.load(audiopath)\n'
     '    except Exception:\n'
     '        # CPU-only installs lack torchcodec (CUDA-linked); use soundfile instead.\n'
     '        import soundfile as sf\n'
     '\n'
     '        data, lsr = sf.read(str(audiopath), always_2d=True)\n'
     '        audio = torch.from_numpy(data.T).float()'),
]


def main() -> int:
    root = find_tts()
    if root is None:
        print("coqui-tts not installed; skipping patches.")
        return 0
    changed = 0
    for rel, old, new in PATCHES:
        path = root / rel
        try:
            text = path.read_text()
        except OSError:
            print(f"missing {rel}; skipping.")
            continue
        if new in text:
            print(f"already patched: {rel}")
            continue
        if old not in text:
            print(f"pattern not found (upstream changed?): {rel}; skipping.")
            continue
        path.write_text(text.replace(old, new, 1))
        print(f"patched: {rel}")
        changed += 1
    print(f"{changed} file(s) patched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
