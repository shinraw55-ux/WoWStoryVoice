"""Lazy multi-engine registry for WoW Story Voice.

Heavy ML libraries are imported only when the selected backend is loaded. This
keeps a broken optional backend from preventing the companion from starting.
"""
from dataclasses import dataclass

ENGINE_KOKORO = "kokoro"
ENGINE_CHATTERBOX = "chatterbox"
ENGINE_COSYVOICE = "cosyvoice"
DEFAULT_ENGINE = ENGINE_CHATTERBOX

@dataclass(frozen=True)
class EngineSpec:
    key: str
    label: str
    module: str
    backend_class: str
    bundled: bool

SPECS = {
    ENGINE_KOKORO: EngineSpec(ENGINE_KOKORO, "Kokoro ONNX (external)", "kokoro_backend", "KokoroBackend", False),
    ENGINE_CHATTERBOX: EngineSpec(ENGINE_CHATTERBOX, "Chatterbox Turbo", "chatterbox_backend", "ChatterboxBackend", True),
    # CosyVoice is isolated because upstream uses a different Python/dependency
    # stack. The adapter is still first-class, but availability is probed rather
    # than imported at companion startup.
    ENGINE_COSYVOICE: EngineSpec(ENGINE_COSYVOICE, "CosyVoice (external)", "cosyvoice_backend", "CosyVoiceBackend", False),
}

def normalize_engine(value):
    key = str(value or "").strip().casefold()
    return key if key in SPECS else DEFAULT_ENGINE

def labels():
    return {key: spec.label for key, spec in SPECS.items()}

def load_backend(key, data_dir):
    import importlib
    key = normalize_engine(key)
    spec = SPECS[key]
    module = importlib.import_module(spec.module)
    cls = getattr(module, spec.backend_class)
    return cls(data_dir)

def probe(key, data_dir):
    key = normalize_engine(key)
    try:
        backend = load_backend(key, data_dir)
        return True, getattr(backend, "engine_name", SPECS[key].label), getattr(backend, "device", "")
    except Exception as e:
        return False, SPECS[key].label, f"{type(e).__name__}: {e}"


def bundled_engine_keys():
    return [key for key, spec in SPECS.items() if spec.bundled]
