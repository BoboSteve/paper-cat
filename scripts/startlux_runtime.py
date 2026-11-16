"""Load local deployment settings and validate the decision service contract."""
import hashlib
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "runtime.local.json"


def settings():
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def validate_service(payload, backend=None):
    config = settings()
    expected = backend or config.get("backend", "mlx")
    model = str(payload.get("model") or "")
    engine = payload.get("engine") or {}
    if payload.get("status") != "ok" or payload.get("kind") != "instant":
        raise ValueError("StartLux must be healthy and use an Instant model")
    if not model.lower().startswith("startlux"):
        raise ValueError("Unexpected decision model")
    if config.get("model_name") and model != config["model_name"]:
        raise ValueError("Service model differs from runtime.local.json")
    if engine.get("name") != expected:
        raise ValueError(f"Expected backend {expected}, got {engine.get('name')}")
    if payload.get("prompt_attention") != "bidirectional":
        raise ValueError("Instant model requires bidirectional prompt attention")
    if expected == "mlx":
        bits = (engine.get("quantization") or {}).get("bits")
        if bits != config.get("quantization_bits", 8):
            raise ValueError("MLX quantization does not match deployment settings")
    elif expected == "llamacpp":
        mode = str(engine.get("bidir_mode") or "").split(" ", 1)[0]
        if mode not in {"server", "request"}:
            raise ValueError("llama.cpp must enable bidirectional prompt evaluation")
    else:
        raise ValueError(f"Unsupported decision backend: {expected}")
    return payload


MODEL_KEYS = ("backend", "model_name", "quantization", "quantization_bits", "model_revision",
              "config_revision", "startlux_commit", "llama_build", "context_tokens")


def service_fingerprint(payload):
    """Scores are reused only while the model, engine and these deployment keys stay the same.

    Paths, comments and start/stop commands are deliberately excluded.
    """
    config = settings()
    identity = {
        "model": payload.get("model"), "kind": payload.get("kind"),
        "prompt_attention": payload.get("prompt_attention"),
        "engine": payload.get("engine"), "deployment": {key: config.get(key) for key in MODEL_KEYS},
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
