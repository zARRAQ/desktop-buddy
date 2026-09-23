"""Configuration model and layered loading.

Layers, lowest priority first:

1. ``robot/data/defaults.yaml`` shipped with the package
2. ``config/hardware.yaml``  (actuator map for this chassis; committed per build, optional)
3. ``config/local.yaml``     (per-robot: detected display/camera, calibration; gitignored)
4. environment variables ``ROBOT__SECTION__KEY=value`` (double underscore = nesting)
5. explicit ``--set section.key=value`` overrides from the CLI

Everything is validated by pydantic, so a typo in a key fails at startup instead of at 2am.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterable, Mapping, MutableMapping
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from robot.core import paths

# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusConfig(StrictModel):
    """ZeroMQ XSUB/XPUB broker endpoints. Publishers connect to xsub, subscribers to xpub."""

    xsub: str = "tcp://127.0.0.1:7770"
    xpub: str = "tcp://127.0.0.1:7771"
    heartbeat_s: float = 1.0


class SystemConfig(StrictModel):
    name: str = "buddy"
    log_level: str = "INFO"
    temp_throttle_c: float = 75.0
    temp_critical_c: float = 82.0


class DisplaySpiConfig(StrictModel):
    bus: int = 0
    device: int = 0
    speed_hz: int = 62_500_000
    pin_dc: int = 25
    pin_reset: int = 4
    pin_backlight: int = 24
    x_offset: int = 0
    y_offset: int = 0


DisplayBackend = Literal["auto", "kms", "fbdev", "spi", "i2c", "window", "null"]
Controller = Literal["auto", "st7789", "ili9341", "ili9488", "gc9a01", "ssd1306", "sh1106"]


class DisplayConfig(StrictModel):
    backend: DisplayBackend = "auto"
    device: str = "auto"
    controller: Controller = "auto"
    shape: Literal["auto", "rect", "round"] = "auto"
    rotation: Literal[0, 90, 180, 270] = 0
    flip: Literal["none", "h", "v", "hv"] = "none"
    color: Literal["auto", "rgb888", "rgb565", "bgr888", "mono1"] = "auto"
    scale_mode: Literal["fit", "fill", "stretch"] = "fit"
    width: int | None = Field(default=None, description="Panel width; auto when None")
    height: int | None = None
    target_fps: int = 30
    min_fps: int = 15
    brightness: float = Field(default=1.0, ge=0.0, le=1.0)
    spi: DisplaySpiConfig = DisplaySpiConfig()
    i2c_address: int = 0x3C
    kms_device_index: int | None = Field(
        default=None, description="SDL_KMSDRM_DEVICE_INDEX; None = auto-detect the connected card"
    )


class FaceConfig(StrictModel):
    eye_color: str = "#3EE0E6"
    background: str = "#000000"
    design_scale: float = Field(default=1.15, gt=0.2, lt=3.0, description="Eye size multiplier")
    eye_separation: float = Field(default=1.0, gt=0.2, lt=2.0)
    blink_interval_s: tuple[float, float] = (2.5, 6.5)
    blink_duration_ms: int = 180
    saccade_interval_s: tuple[float, float] = (0.8, 3.0)
    idle_drift: bool = True
    transition_ms: int = 220
    antialias: bool = True
    indicators: bool = Field(default=True, description="Level bars while listening, dots while thinking")
    mouth: bool = Field(default=True, description="Animated mouth while speaking")


CameraBackend = Literal["auto", "csi", "uvc", "rtsp", "file", "synthetic", "null"]


class CameraConfig(StrictModel):
    backend: CameraBackend = "auto"
    device: str = "auto"
    width: int = 640
    height: int = 480
    fps: int = 30
    format: Literal["auto", "MJPG", "YUYV", "NV12"] = "auto"
    rotation: Literal[0, 90, 180, 270] = 0
    flip: Literal["none", "h", "v", "hv"] = "none"
    mirror_preview: bool = True
    loop_file: bool = True


class PerceptionConfig(StrictModel):
    backend: Literal["auto", "hailo", "cpu", "null"] = "auto"
    hailo_arch: Literal["auto", "hailo8", "hailo8l", "hailo10h"] = "auto"
    detect_every_n: int = Field(default=1, ge=1)
    min_face_px: int = 40
    score_threshold: float = 0.6
    match_threshold: float | None = Field(
        default=None,
        description="Cosine similarity for a positive identity. None = backend default",
    )
    recognize_votes: int = Field(default=3, ge=1, description="Consistent matches before naming")
    lost_after_s: float = 1.5
    publish_hz: float = 10.0


class WakeConfig(StrictModel):
    engine: Literal["auto", "openwakeword", "sherpa_kws", "fake", "none"] = "auto"
    model: str = "hey_jarvis_v0.1"
    threshold: float = 0.5


class SttConfig(StrictModel):
    engine: Literal["auto", "sherpa_moonshine", "sherpa_whisper", "fake", "none"] = "auto"
    model: str = "sherpa-onnx-moonshine-tiny-en-int8"
    num_threads: int = 2


class TtsConfig(StrictModel):
    engine: Literal["auto", "sherpa_vits", "fake", "none"] = "auto"
    voice: str = "vits-piper-en_US-lessac-medium"
    speed: float = 1.0
    num_threads: int = 2


class AudioConfig(StrictModel):
    input_device: str | int | None = None
    output_device: str | int | None = None
    sample_rate: int = 16000
    block_ms: int = 80


class VadConfig(StrictModel):
    silence_ms: int = 700
    max_utterance_s: float = 12.0
    min_speech_ms: int = 250


class VoiceConfig(StrictModel):
    enabled: bool = True
    wake: WakeConfig = WakeConfig()
    stt: SttConfig = SttConfig()
    tts: TtsConfig = TtsConfig()
    audio: AudioConfig = AudioConfig()
    vad: VadConfig = VadConfig()
    listen_timeout_s: float = 8.0


class LlamaServerConfig(StrictModel):
    binary: str = "llama-server"
    host: str = "127.0.0.1"
    port: int = 8080
    threads: int = 3
    ctx_size: int = 2048
    extra_args: list[str] = Field(default_factory=list)


class BrainConfig(StrictModel):
    backend: Literal["auto", "openai_compat", "scripted"] = "auto"
    endpoint: str = "http://127.0.0.1:8080/v1"
    model: str = "default"
    api_key: str = "not-needed"
    managed: Literal["none", "llama_server"] = "none"
    model_file: str = "gemma-3-1b-it-Q4_K_M.gguf"
    llama_server: LlamaServerConfig = LlamaServerConfig()
    persona: str = (
        "You are Buddy, a small desk robot with expressive eyes. You are warm, curious, a "
        "little playful, and brief: one or two short sentences. You cannot see text or "
        "screens, only faces. Never claim to do things you cannot do."
    )
    max_tokens: int = 120
    temperature: float = 0.7
    timeout_s: float = 30.0
    rss_budget_mb: int = 2500


class EncoderConfig(StrictModel):
    pin_a: int
    pin_b: int
    ticks_per_rev: int = 700


class DcDriverConfig(StrictModel):
    """H-bridge wiring.

    ``tb6612fng`` uses one hardware PWM pin per motor plus two direction pins each, which
    is the only layout that gets 20 kHz PWM on a Pi 5 whose PWM-capable pins 18/19 are
    taken by I2S. ``drv8833`` needs PWM on all four inputs and therefore falls back to
    software PWM (roughly 800 Hz, audible, jittery under load).
    """

    type: Literal["tb6612fng", "drv8833", "none"] = "none"
    pins: dict[str, int] = Field(
        default_factory=lambda: {
            "pwma": 12,
            "ain1": 5,
            "ain2": 6,
            "pwmb": 13,
            "bin1": 14,
            "bin2": 15,
            "standby": 26,
        }
    )
    pwm_hz: int = 20_000
    hardware_pwm: bool = True


class ServoDriverConfig(StrictModel):
    type: Literal["pca9685", "gpio_pwm", "none"] = "none"
    i2c_bus: int = 1
    i2c_address: int = 0x40
    pwm_hz: int = 50
    oe_pin: int | None = Field(
        default=16, description="PCA9685 OE (active low). Safety gate when there is no dc driver"
    )


class DriversConfig(StrictModel):
    dc: DcDriverConfig = DcDriverConfig()
    servo: ServoDriverConfig = ServoDriverConfig()


Role = Literal["drive", "pan", "tilt", "lift", "arm_left", "arm_right"]


class ChannelConfig(StrictModel):
    role: Role
    driver: Literal["dc", "servo"]
    # dc
    motor: Literal["a", "b"] | None = None
    invert: bool = False
    encoder: EncoderConfig | None = None
    # servo
    channel: int | None = None
    min_deg: float = -90.0
    max_deg: float = 90.0
    center_us: float = 1500.0
    us_per_deg: float = 10.0
    max_deg_s: float = 180.0
    relax_after_s: float = 3.0

    @model_validator(mode="after")
    def _check_driver_fields(self) -> ChannelConfig:
        if self.driver == "dc" and self.motor is None:
            raise ValueError("dc channel needs 'motor: a|b'")
        if self.driver == "servo" and self.channel is None:
            raise ValueError("servo channel needs 'channel: <int>'")
        if self.min_deg >= self.max_deg:
            raise ValueError("min_deg must be below max_deg")
        return self


class GeometryConfig(StrictModel):
    wheel_diameter_mm: float = 40.0
    track_width_mm: float = 95.0
    ticks_per_mm: float | None = Field(default=None, description="Set by `robot calibrate drive`")


class ActuatorsConfig(StrictModel):
    layout: Literal["A", "B", "C", "none"] = "none"
    drivers: DriversConfig = DriversConfig()
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)
    geometry: GeometryConfig = GeometryConfig()
    max_speed_mm_s: float = 250.0
    max_turn_deg_s: float = 180.0


class CliffSensorConfig(StrictModel):
    name: str
    mux_channel: int
    threshold_mm: int = 60


class CliffConfig(StrictModel):
    enabled: bool = False
    mux_address: int = 0x70
    sensor_address: int = 0x29
    poll_hz: float = 20.0
    sensors: list[CliffSensorConfig] = Field(
        default_factory=lambda: [
            CliffSensorConfig(name="front", mux_channel=0),
            CliffSensorConfig(name="left", mux_channel=1),
            CliffSensorConfig(name="right", mux_channel=2),
        ]
    )


class ImuConfig(StrictModel):
    enabled: bool = False
    type: Literal["mpu6050", "none"] = "mpu6050"
    i2c_address: int = 0x68
    pickup_accel_g: float = 1.35
    tilt_deg: float = 35.0


class SafetyConfig(StrictModel):
    enable_pin: int = 26
    enable_mode: Literal["level", "pulse"] = Field(
        default="level",
        description=(
            "level: GPIO26 held high while allowed (a killed supervisor leaves it high). "
            "pulse: square wave into the hardware pulse watchdog (HARDWARE.md 5.3), the only "
            "true failsafe. Use pulse on any build with a drivetrain."
        ),
    )
    heartbeat_hz: float = Field(
        default=100.0,
        description=(
            "The enable line carries a square wave, not a static high. A hardware pulse "
            "watchdog on the motor driver STBY pin turns it into an enable only while pulses "
            "keep coming, so a SIGKILLed supervisor cannot leave the motors enabled."
        ),
    )
    motion_heartbeat_timeout_ms: int = 500
    startup_grace_s: float = 2.0
    cliff: CliffConfig = CliffConfig()
    imu: ImuConfig = ImuConfig()


class MemoryConfig(StrictModel):
    path: str | None = Field(default=None, description="SQLite file; default under data dir")
    greet_cooldown_s: float = 900.0
    max_facts_per_person: int = 50


class PowerConfig(StrictModel):
    enabled: bool = False
    ina219_address: int = 0x41
    i2c_bus: int = 1
    poll_s: float = 5.0
    cells: int = 2
    low_voltage: float = 6.6
    critical_voltage: float = 6.2


class OrchestratorConfig(StrictModel):
    sleep_after_s: float = 300.0
    greet: bool = True
    greet_unknown: bool = True
    attention_timeout_s: float = 20.0
    tick_hz: float = 10.0
    camera_hfov_deg: float = 66.0
    camera_vfov_deg: float = 41.0
    track_with_head: bool = True
    mirror_gaze: bool = Field(
        default=True,
        description="Camera looks outward, the screen faces the person: a face on the image's "
        "right is on the robot's right, which is the viewer's left. Set False if your camera "
        "is mounted mirrored.",
    )
    history_turns: int = 6


class RobotConfig(StrictModel):
    system: SystemConfig = SystemConfig()
    bus: BusConfig = BusConfig()
    display: DisplayConfig = DisplayConfig()
    face: FaceConfig = FaceConfig()
    camera: CameraConfig = CameraConfig()
    perception: PerceptionConfig = PerceptionConfig()
    voice: VoiceConfig = VoiceConfig()
    brain: BrainConfig = BrainConfig()
    actuators: ActuatorsConfig = ActuatorsConfig()
    safety: SafetyConfig = SafetyConfig()
    memory: MemoryConfig = MemoryConfig()
    power: PowerConfig = PowerConfig()
    orchestrator: OrchestratorConfig = OrchestratorConfig()

    def memory_path(self) -> Path:
        if self.memory.path:
            return Path(self.memory.path).expanduser()
        return paths.data_dir() / "memory.sqlite"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

ENV_PREFIX = "ROBOT__"


def deep_merge(base: MutableMapping[str, Any], overlay: Mapping[str, Any]) -> None:
    """Recursively merge ``overlay`` into ``base`` in place. Lists and scalars replace."""
    for key, value in overlay.items():
        existing = base.get(key)
        if isinstance(existing, MutableMapping) and isinstance(value, Mapping):
            deep_merge(existing, value)
        else:
            base[key] = copy.deepcopy(value)


_NULL_WORDS = frozenset({"", "null", "~", "None", "none", "NULL", "Null"})


def _coerce_scalar(text: str) -> Any:
    """Parse an override value the way YAML would, so ``true``, ``0x40`` and ``1.5`` work.

    Anything YAML would swallow that is not a null word (a ``#hex`` colour is a YAML
    comment, for example) comes back as the literal string.
    """
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if value is None and text.strip() not in _NULL_WORDS:
        return text
    return value


def set_path(target: MutableMapping[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, MutableMapping):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def env_overrides(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``ROBOT__DISPLAY__BACKEND=null`` becomes ``{"display": {"backend": None}}``.

    Note that YAML parses ``null`` as ``None``; use the string ``"null"`` with quotes
    (``ROBOT__DISPLAY__BACKEND='"null"'``) to select the null display backend from the
    environment, or prefer ``--set display.backend=null`` which is quoted for you.
    """
    environ = os.environ if environ is None else environ
    out: dict[str, Any] = {}
    for key, raw in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        dotted = key[len(ENV_PREFIX) :].lower().replace("__", ".")
        set_path(out, dotted, _coerce_scalar(raw))
    return out


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def package_defaults() -> dict[str, Any]:
    text = resources.files("robot.data").joinpath("defaults.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    assert isinstance(data, dict)
    return data


def default_layer_paths(config_dir: Path | None = None) -> list[Path]:
    cdir = config_dir or paths.config_dir()
    return [cdir / "hardware.yaml", cdir / "local.yaml"]


LAYOUTS = ("A", "B", "C")


def example_layout(name: str) -> dict[str, Any]:
    """The ``actuators`` block for layout A, B or C from the package's ``layouts.yaml``."""
    if name not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}, got {name!r}")
    text = resources.files("robot.data").joinpath("layouts.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    block = data["layouts"][name]
    assert isinstance(block, dict)
    return copy.deepcopy(block)


def load_config(
    *,
    config_dir: Path | None = None,
    extra_files: Iterable[Path] = (),
    overrides: Iterable[str] = (),
    environ: Mapping[str, str] | None = None,
    use_env: bool = True,
    layout: str | None = None,
) -> RobotConfig:
    """Build the merged, validated configuration.

    ``overrides`` are ``"section.key=value"`` strings from the CLI. Values that should stay
    strings but look like YAML scalars (``null``, ``yes``) can be quoted: ``a.b='"null"'``.
    ``layout`` merges one of the example actuator layouts over the files (the simulator and
    the tests use it; a real robot declares its actuators in ``hardware.yaml`` instead).
    """
    merged: dict[str, Any] = package_defaults()
    for path in [*default_layer_paths(config_dir), *extra_files]:
        deep_merge(merged, load_yaml(path))
    if layout is not None:
        deep_merge(merged, {"actuators": example_layout(layout)})
    if use_env:
        deep_merge(merged, env_overrides(environ))
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"override must look like section.key=value, got {item!r}")
        dotted, _, raw = item.partition("=")
        value = _coerce_scalar(raw)
        if value is None and raw.strip() in _NULL_WORDS and raw.strip():
            value = "null"  # our enums use the string "null" for a disabled backend
        set_path(merged, dotted.strip(), value)
    return RobotConfig.model_validate(merged)


def write_local_setting(dotted: str, value: Any, *, config_dir: Path | None = None) -> Path:
    """Persist one value into ``config/local.yaml``, creating the file if needed.

    Comments in the existing file are not preserved; local.yaml is machine-written by the
    detect and calibrate commands and is not meant to be hand-edited at length.
    """
    cdir = config_dir or paths.config_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "local.yaml"
    data = load_yaml(path)
    set_path(data, dotted, value)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Written by `robot ... --save` and `robot calibrate`. Per-robot, not committed.\n")
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    return path


def write_local_section(section: str, values: Mapping[str, Any], *, config_dir: Path | None = None) -> Path:
    cdir = config_dir or paths.config_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "local.yaml"
    data = load_yaml(path)
    existing = data.get(section)
    if not isinstance(existing, dict):
        existing = {}
        data[section] = existing
    deep_merge(existing, values)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Written by `robot ... --save` and `robot calibrate`. Per-robot, not committed.\n")
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    return path
