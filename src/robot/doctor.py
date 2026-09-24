"""``robot doctor``: a pass/warn/fail table of everything that has to be right before the
robot runs. Each check is a small function; failures never abort the run."""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from robot.core import paths
from robot.core.config import RobotConfig
from robot.provision.download import Provisioner
from robot.provision.manifest import select

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


@dataclass
class Result:
    name: str
    status: str
    detail: str


Check = Callable[[RobotConfig], Result]


def on_pi() -> bool:
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except OSError:
        return False


def check_python(cfg: RobotConfig) -> Result:  # noqa: ARG001
    v = sys.version_info
    ok = v >= (3, 11)
    return Result("python", PASS if ok else FAIL, f"{platform.python_version()} at {sys.executable}")


def check_config(_cfg: RobotConfig) -> Result:
    cdir = paths.config_dir()
    files = [p.name for p in (cdir / "hardware.yaml", cdir / "local.yaml") if p.exists()]
    return Result(
        "config", PASS, f"valid; layers: defaults + {', '.join(files) if files else 'no local files'} ({cdir})"
    )


def check_data_dir(cfg: RobotConfig) -> Result:  # noqa: ARG001
    d = paths.data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return Result("data dir", PASS, str(d))
    except OSError as exc:
        return Result("data dir", FAIL, f"{d}: {exc}")


def check_models(cfg: RobotConfig) -> Result:
    prov = Provisioner()
    wanted = ["vision-cpu", "voice"]
    if cfg.perception.backend in ("auto", "hailo"):
        arch = _hailo_arch()
        if arch:
            wanted.append(f"vision-{arch}")
    if cfg.brain.managed == "llama_server":
        wanted.append("llm")
    missing = [m.name for m in select(wanted) if not prov.is_present(m)]
    prov.close()
    if not missing:
        return Result("models", PASS, f"groups {', '.join(wanted)} present in {paths.models_dir()}")
    return Result("models", WARN, f"missing: {', '.join(missing)}. Run `robot provision`")


def _hailo_arch() -> str | None:
    try:
        from robot.perception.hailo import detect_arch

        return detect_arch()
    except Exception:
        return None


def check_hailo(cfg: RobotConfig) -> Result:
    if cfg.perception.backend == "cpu":
        return Result("hailo", SKIP, "perception.backend=cpu")
    try:
        importlib.import_module("hailo_platform")
    except ImportError:
        if on_pi():
            return Result(
                "hailo",
                WARN,
                "hailo_platform not importable: is the venv created with --system-site-packages and hailo-all installed?",
            )
        return Result("hailo", SKIP, "not a Raspberry Pi; CPU backend will be used")
    arch = _hailo_arch()
    if arch is None:
        return Result(
            "hailo",
            FAIL,
            "HailoRT present but no device answered (check PCIe ribbon, dtparam=pciex1_gen=3, `hailortcli fw-control identify`)",
        )
    cli = shutil.which("hailortcli")
    fw = ""
    if cli:
        try:
            out = subprocess.run(
                [cli, "fw-control", "identify"], capture_output=True, text=True, timeout=10, check=False
            ).stdout
            fw = next((line.strip() for line in out.splitlines() if "Firmware Version" in line), "")
        except (OSError, subprocess.SubprocessError):
            pass
    return Result("hailo", PASS, f"{arch} {fw}".strip())


def check_display(cfg: RobotConfig) -> Result:
    from robot.hal.display.detect import detect_display

    det = detect_display(cfg.display)
    status = PASS if det.backend != "null" else WARN
    size = f" {det.width}x{det.height}" if det.width else ""
    return Result("display", status, f"{det.backend}{size} {det.device}: {det.reasons[0] if det.reasons else ''}")


def check_camera(cfg: RobotConfig) -> Result:
    from robot.hal.camera.detect import detect_camera

    det = detect_camera(cfg.camera)
    return Result(
        "camera",
        PASS if det.backend != "null" else WARN,
        f"{det.backend} {det.device}: {det.reasons[0] if det.reasons else ''}",
    )


def check_audio(cfg: RobotConfig) -> Result:
    if not cfg.voice.enabled:
        return Result("audio", SKIP, "voice disabled")
    try:
        from robot.voice.audio import list_devices

        devs = list_devices()
    except Exception as exc:
        return Result("audio", WARN, f"sounddevice unavailable: {exc}")
    ins = [d["name"] for d in devs if d.get("max_input_channels", 0) > 0]
    outs = [d["name"] for d in devs if d.get("max_output_channels", 0) > 0]
    if not ins or not outs:
        return Result("audio", WARN, f"inputs={len(ins)} outputs={len(outs)}; check `arecord -l` / `aplay -l`")
    return Result("audio", PASS, f"in: {ins[0]}; out: {outs[0]}")


def check_i2c(cfg: RobotConfig) -> Result:
    dev = Path("/dev/i2c-1")
    if not dev.exists():
        return Result("i2c", SKIP if not on_pi() else WARN, "/dev/i2c-1 missing (raspi-config nonint do_i2c 0)")
    if not os.access(dev, os.R_OK | os.W_OK):
        return Result("i2c", FAIL, "/dev/i2c-1 not accessible; add the user to the i2c group or install the udev rule")
    try:
        from robot.hal.i2c.base import SmbusI2c

        bus = SmbusI2c(1)
        found = bus.scan([0x29, 0x3C, 0x3D, 0x40, 0x41, 0x68, 0x70])
        bus.close()
    except Exception as exc:
        return Result("i2c", WARN, f"scan failed: {exc}")
    names = {
        0x29: "VL53L1X",
        0x3C: "OLED",
        0x3D: "OLED",
        0x40: "PCA9685",
        0x41: "INA219",
        0x68: "MPU6050",
        0x70: "TCA9548A",
    }
    seen = ", ".join(f"{names[a]}@{hex(a)}" for a in found) or "nothing"
    status = PASS
    if cfg.actuators.drivers.servo.type == "pca9685" and 0x40 not in found:
        status = WARN
    return Result("i2c", status, f"found {seen}")


def check_gpio(cfg: RobotConfig) -> Result:  # noqa: ARG001
    chips = sorted(Path("/dev").glob("gpiochip*"))
    if not chips:
        return Result("gpio", SKIP if not on_pi() else FAIL, "no /dev/gpiochip*")
    if not all(os.access(c, os.R_OK | os.W_OK) for c in chips):
        return Result("gpio", FAIL, "gpiochip not accessible; add the user to the gpio group")
    try:
        importlib.import_module("gpiozero")
    except ImportError:
        return Result("gpio", WARN, "gpiozero not installed (uv sync --extra pi)")
    pwm = sorted(Path("/sys/class/pwm").glob("pwmchip*")) if Path("/sys/class/pwm").exists() else []
    return Result(
        "gpio",
        PASS if pwm else WARN,
        f"{len(chips)} chips; hardware PWM chips: {len(pwm)}"
        + ("" if pwm else " (add dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4)"),
    )


def check_spi(cfg: RobotConfig) -> Result:
    if cfg.display.backend not in ("spi", "auto"):
        return Result("spi", SKIP, "not needed")
    dev = Path(f"/dev/spidev{cfg.display.spi.bus}.{cfg.display.spi.device}")
    if not dev.exists():
        return Result("spi", SKIP, f"{dev} absent (only needed for a bare SPI panel)")
    return Result("spi", PASS if os.access(dev, os.R_OK | os.W_OK) else FAIL, str(dev))


def check_thermal(cfg: RobotConfig) -> Result:
    vc = shutil.which("vcgencmd")
    if not vc:
        return Result("thermal", SKIP, "vcgencmd not available")
    try:
        temp = subprocess.run(
            [vc, "measure_temp"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
        thr = subprocess.run(
            [vc, "get_throttled"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return Result("thermal", WARN, str(exc))
    throttled = thr.split("=")[-1] if "=" in thr else thr
    status = PASS if throttled in ("0x0", "0") else WARN
    t = float(temp.replace("temp=", "").replace("'C", "")) if "temp=" in temp else None
    if t is not None and t > cfg.system.temp_throttle_c:
        status = WARN
    return Result(
        "thermal",
        status,
        f"{temp} throttled={throttled}"
        + ("" if status == PASS else " (undervoltage or heat: see docs/INSTALL.md troubleshooting)"),
    )


def check_brain(cfg: RobotConfig) -> Result:
    if cfg.brain.backend == "scripted":
        return Result("brain", SKIP, "scripted replies configured")
    if cfg.brain.managed == "llama_server":
        binary = shutil.which(cfg.brain.llama_server.binary)
        model = paths.models_dir() / "llm" / cfg.brain.model_file
        if not binary:
            return Result(
                "brain", WARN, f"{cfg.brain.llama_server.binary} not on PATH; scripted replies until installed"
            )
        if not model.exists():
            return Result("brain", WARN, f"model {model.name} missing: `robot provision --group llm`")
        return Result("brain", PASS, f"llama-server managed, model {model.name}")
    from robot.brain.client import ChatClient

    c = ChatClient(cfg.brain.endpoint, cfg.brain.model, cfg.brain.api_key, 5.0)
    ok = c.healthy()
    models = c.models() if ok else []
    c.close()
    return Result(
        "brain",
        PASS if ok else WARN,
        f"{cfg.brain.endpoint}: {'reachable, models ' + ', '.join(models[:3]) if ok else 'unreachable (scripted replies will be used)'}",
    )


def check_interlock(cfg: RobotConfig) -> Result:
    from robot.safety.service import has_drivetrain

    if not has_drivetrain(cfg):
        return Result(
            "interlock",
            PASS,
            f"no drivetrain configured; enable line GPIO{cfg.safety.enable_pin} in {cfg.safety.enable_mode} mode",
        )
    if cfg.safety.enable_mode == "pulse":
        return Result("interlock", PASS, "pulse mode: confirm with `robot safety killtest` before untethered use")
    return Result(
        "interlock",
        WARN,
        "drivetrain configured but safety.enable_mode is level: a killed supervisor leaves the motors enabled. "
        "Fit the pulse watchdog (HARDWARE.md 5.3), set safety.enable_mode: pulse, run `robot safety killtest`",
    )


def check_openmv(cfg: RobotConfig) -> Result:
    if not cfg.openmv.enabled:
        return Result("openmv", SKIP, "openmv.enabled: false")
    from robot.hal.openmv.link import find_port

    port = cfg.openmv.port if cfg.openmv.port != "auto" else find_port()
    if not port or not Path(port).exists():
        return Result("openmv", FAIL, "no /dev/ttyACM* device: plug the OpenMV in; it must be running openmv/main.py")
    if not os.access(port, os.R_OK | os.W_OK):
        return Result("openmv", FAIL, f"{port} not accessible; add the user to the dialout group and log in again")
    notes = []
    if cfg.camera.backend != "bus":
        notes.append("camera.backend should be bus")
    if cfg.display.backend != "bus":
        notes.append("display.backend is not bus (face will not reach the board's LCD)")
    return Result("openmv", WARN if notes else PASS, f"{port}" + (f"; {'; '.join(notes)}" if notes else ""))


def check_memory(cfg: RobotConfig) -> Result:
    from robot.memory import Memory

    try:
        m = Memory(cfg.memory_path())
        people = m.list_people()
        m.close()
        return Result("memory", PASS, f"{cfg.memory_path()}: {len(people)} people")
    except Exception as exc:
        return Result("memory", FAIL, str(exc))


CHECKS: tuple[Check, ...] = (
    check_python,
    check_config,
    check_data_dir,
    check_models,
    check_display,
    check_camera,
    check_hailo,
    check_audio,
    check_i2c,
    check_gpio,
    check_spi,
    check_thermal,
    check_brain,
    check_interlock,
    check_openmv,
    check_memory,
)


def run_all(cfg: RobotConfig) -> list[Result]:
    out: list[Result] = []
    for check in CHECKS:
        try:
            out.append(check(cfg))
        except Exception as exc:
            out.append(Result(check.__name__.removeprefix("check_"), FAIL, f"check crashed: {exc}"))
    return out
