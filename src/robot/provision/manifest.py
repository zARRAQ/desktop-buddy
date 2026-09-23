"""What ``robot provision`` downloads.

Where a publisher gives a checksum it is pinned here. Where none is published (Hailo S3,
sherpa release archives) the first download's hash is recorded in ``models/models.lock.json``
and later runs verify against it (trust on first use, stated plainly).
"""

from __future__ import annotations

from dataclasses import dataclass, field

HAILO_MZ = "https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled"
SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"


@dataclass(frozen=True)
class ModelFile:
    name: str
    group: str
    url: str
    dest: str  # relative to the models directory; for archives, the directory to extract into
    size: int | None = None
    sha256: str | None = None
    extract: bool = False
    license: str = ""
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


MANIFEST: tuple[ModelFile, ...] = (
    # --- CPU face recognition (OpenCV Zoo, Apache-2.0) ---------------------------------
    ModelFile(
        "yunet",
        "vision-cpu",
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "face_detection_yunet_2023mar.onnx",
        232589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        license="Apache-2.0",
        note="face detector, OpenCV Zoo",
    ),
    ModelFile(
        "sface",
        "vision-cpu",
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        "face_recognition_sface_2021dec.onnx",
        38696353,
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        license="Apache-2.0",
        note="128-d face embedding, OpenCV Zoo",
    ),
    # --- Hailo face recognition (same models hailo-apps uses) ---------------------------
    ModelFile(
        "scrfd_2.5g",
        "vision-hailo8l",
        f"{HAILO_MZ}/v2.17.0/hailo8l/scrfd_2.5g.hef",
        "hailo/hailo8l/scrfd_2.5g.hef",
        5666066,
        license="Hailo Model Zoo (MIT)",
        note="face detector",
    ),
    ModelFile(
        "arcface_mobilefacenet",
        "vision-hailo8l",
        f"{HAILO_MZ}/v2.17.0/hailo8l/arcface_mobilefacenet.hef",
        "hailo/hailo8l/arcface_mobilefacenet.hef",
        6381733,
        license="Hailo Model Zoo (MIT)",
        note="512-d face embedding",
    ),
    ModelFile(
        "scrfd_10g",
        "vision-hailo8",
        f"{HAILO_MZ}/v2.17.0/hailo8/scrfd_10g.hef",
        "hailo/hailo8/scrfd_10g.hef",
        license="Hailo Model Zoo (MIT)",
    ),
    ModelFile(
        "arcface_mobilefacenet",
        "vision-hailo8",
        f"{HAILO_MZ}/v2.17.0/hailo8/arcface_mobilefacenet.hef",
        "hailo/hailo8/arcface_mobilefacenet.hef",
        license="Hailo Model Zoo (MIT)",
    ),
    ModelFile(
        "scrfd_10g",
        "vision-hailo10h",
        f"{HAILO_MZ}/v5.2.0/hailo10h/scrfd_10g.hef",
        "hailo/hailo10h/scrfd_10g.hef",
        license="Hailo Model Zoo (MIT)",
    ),
    ModelFile(
        "arcface_mobilefacenet",
        "vision-hailo10h",
        f"{HAILO_MZ}/v5.2.0/hailo10h/arcface_mobilefacenet.hef",
        "hailo/hailo10h/arcface_mobilefacenet.hef",
        license="Hailo Model Zoo (MIT)",
    ),
    # --- Voice (sherpa-onnx release archives, openWakeWord) -----------------------------
    ModelFile(
        "moonshine-tiny-en",
        "voice",
        f"{SHERPA}/asr-models/sherpa-onnx-moonshine-tiny-en-int8.tar.bz2",
        "voice",
        107600538,
        extract=True,
        license="MIT (Useful Sensors)",
        note="speech to text, ~5x faster than Whisper tiny at equal accuracy",
    ),
    ModelFile(
        "piper-lessac-medium",
        "voice",
        f"{SHERPA}/tts-models/vits-piper-en_US-lessac-medium.tar.bz2",
        "voice",
        67230653,
        extract=True,
        license="MIT voice, Apache-2.0 runtime",
        note="text to speech (Piper voice run by sherpa-onnx)",
    ),
    ModelFile(
        "silero-vad",
        "voice",
        f"{SHERPA}/asr-models/silero_vad.onnx",
        "voice/silero_vad.onnx",
        643854,
        license="MIT",
        note="voice activity detection",
    ),
    ModelFile(
        "oww-hey-jarvis",
        "voice",
        f"{OWW}/hey_jarvis_v0.1.onnx",
        "voice/openwakeword/hey_jarvis_v0.1.onnx",
        1271370,
        license="Apache-2.0",
        note='wake word "hey jarvis"',
    ),
    ModelFile(
        "oww-melspectrogram",
        "voice",
        f"{OWW}/melspectrogram.onnx",
        "voice/openwakeword/melspectrogram.onnx",
        1087958,
        license="Apache-2.0",
    ),
    ModelFile(
        "oww-embedding",
        "voice",
        f"{OWW}/embedding_model.onnx",
        "voice/openwakeword/embedding_model.onnx",
        1326578,
        license="Apache-2.0",
    ),
    ModelFile(
        "kws-gigaspeech",
        "voice-kws",
        f"{SHERPA}/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2",
        "voice",
        17626723,
        extract=True,
        license="Apache-2.0",
        note="alternative wake word: any English phrase, from text",
    ),
    # --- Language model --------------------------------------------------------------------
    ModelFile(
        "gemma-3-1b-it",
        "llm",
        "https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
        "llm/gemma-3-1b-it-Q4_K_M.gguf",
        806058240,
        "8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135",
        license="Gemma Terms of Use",
        note="default: 0.8 GB, ~12 tok/s on a Pi 5, fits the 2.5 GB budget with room to spare",
    ),
    ModelFile(
        "qwen3-1.7b",
        "llm-alt",
        "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
        "llm/Qwen3-1.7B-Q4_K_M.gguf",
        1107409472,
        "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
        license="Apache-2.0",
        note="alternative: stronger, 1.1 GB, ~8 tok/s; disable thinking (see docs/PLAN.md)",
    ),
)

GROUP_HELP: dict[str, str] = {
    "vision-cpu": "OpenCV YuNet + SFace: face recognition on any CPU (laptop, Pi without Hailo)",
    "vision-hailo8l": "SCRFD 2.5G + ArcFace for the Hailo-8L (AI Kit, AI HAT+ 13 TOPS)",
    "vision-hailo8": "SCRFD 10G + ArcFace for the Hailo-8 (AI HAT+ 26 TOPS)",
    "vision-hailo10h": "SCRFD 10G + ArcFace for the Hailo-10H (AI HAT+ 2)",
    "voice": "Moonshine STT, Piper voice for TTS, Silero VAD, openWakeWord 'hey jarvis'",
    "voice-kws": "sherpa-onnx keyword spotter (custom wake phrase from text)",
    "llm": "Gemma 3 1B instruct, Q4_K_M",
    "llm-alt": "Qwen3 1.7B instruct, Q4_K_M",
}


def groups() -> list[str]:
    seen: list[str] = []
    for m in MANIFEST:
        if m.group not in seen:
            seen.append(m.group)
    return seen


def select(group_names: list[str] | None) -> list[ModelFile]:
    if not group_names:
        return list(MANIFEST)
    return [m for m in MANIFEST if m.group in group_names]


def default_groups_for(*, hailo_arch: str | None, has_llm: bool = True) -> list[str]:
    out = ["vision-cpu", "voice"]
    if hailo_arch in ("hailo8", "hailo8l", "hailo10h"):
        out.append(f"vision-{hailo_arch}")
    if has_llm:
        out.append("llm")
    return out
