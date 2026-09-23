from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from robot.core.config import (
    RobotConfig,
    deep_merge,
    env_overrides,
    load_config,
    write_local_section,
    write_local_setting,
)


def test_defaults_validate():
    cfg = load_config(config_dir=Path("/nonexistent"), use_env=False)
    assert cfg.display.backend == "auto"
    assert cfg.actuators.layout == "A"
    assert set(cfg.actuators.channels) == {"drive_left", "drive_right", "head_pan", "head_tilt"}


def test_layering_local_over_hardware(tmp_path: Path):
    cdir = tmp_path / "config"
    cdir.mkdir()
    (cdir / "hardware.yaml").write_text("display:\n  rotation: 90\n  backend: fbdev\n")
    (cdir / "local.yaml").write_text("display:\n  rotation: 180\n")
    cfg = load_config(config_dir=cdir, use_env=False)
    assert cfg.display.backend == "fbdev"
    assert cfg.display.rotation == 180


def test_env_and_cli_overrides(tmp_path: Path):
    env = {"ROBOT__CAMERA__WIDTH": "1280", "ROBOT__PERCEPTION__BACKEND": "cpu", "HOME": "/x"}
    assert env_overrides(env) == {"camera": {"width": 1280}, "perception": {"backend": "cpu"}}
    cfg = load_config(
        config_dir=tmp_path,
        environ=env,
        overrides=["camera.height=720", "display.backend=null", "face.eye_color=#ff0000"],
    )
    assert cfg.camera.width == 1280
    assert cfg.camera.height == 720
    assert cfg.perception.backend == "cpu"
    assert cfg.display.backend == "null"
    assert cfg.face.eye_color == "#ff0000"


def test_unknown_key_is_an_error(tmp_path: Path):
    (tmp_path / "local.yaml").write_text("display:\n  rotaton: 90\n")
    with pytest.raises(ValueError):
        load_config(config_dir=tmp_path, use_env=False)


def test_channel_validation():
    with pytest.raises(ValueError):
        RobotConfig.model_validate({"actuators": {"channels": {"x": {"role": "pan", "driver": "servo"}}}})
    with pytest.raises(ValueError):
        RobotConfig.model_validate(
            {
                "actuators": {
                    "channels": {"x": {"role": "pan", "driver": "servo", "channel": 0, "min_deg": 5, "max_deg": 1}}
                }
            }
        )


def test_deep_merge_replaces_lists_and_scalars():
    base = {"a": {"b": 1, "c": [1, 2]}, "d": 4}
    deep_merge(base, {"a": {"c": [9], "e": 5}, "d": None})
    assert base == {"a": {"b": 1, "c": [9], "e": 5}, "d": None}


def test_write_local_setting_round_trip(tmp_path: Path):
    path = write_local_setting("display.rotation", 270, config_dir=tmp_path)
    write_local_setting("display.backend", "spi", config_dir=tmp_path)
    write_local_section("camera", {"backend": "uvc", "device": "/dev/video2"}, config_dir=tmp_path)
    data = yaml.safe_load(path.read_text())
    assert data == {
        "display": {"rotation": 270, "backend": "spi"},
        "camera": {"backend": "uvc", "device": "/dev/video2"},
    }
    cfg = load_config(config_dir=tmp_path, use_env=False)
    assert cfg.display.rotation == 270
    assert cfg.camera.device == "/dev/video2"
