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
    # a fresh install has no actuators: nothing claims GPIO, nothing to interlock
    assert cfg.actuators.layout == "none"
    assert cfg.actuators.channels == {}
    assert cfg.actuators.drivers.dc.type == "none" and cfg.actuators.drivers.servo.type == "none"


def test_example_layouts_merge_and_match_the_copyable_file():
    from robot.core.config import LAYOUTS, example_layout

    cfg = load_config(config_dir=Path("/nonexistent"), use_env=False, layout="A")
    assert cfg.actuators.layout == "A"
    assert set(cfg.actuators.channels) == {"drive_left", "drive_right", "head_pan", "head_tilt"}
    assert load_config(config_dir=Path("/nonexistent"), use_env=False, layout="C").actuators.drivers.dc.type == "none"
    with pytest.raises(ValueError):
        load_config(config_dir=Path("/nonexistent"), use_env=False, layout="D")
    # config/hardware.example.yaml is what users copy; keep it identical to the package data
    example = yaml.safe_load(Path("config/hardware.example.yaml").read_text())["layouts"]
    for name in LAYOUTS:
        assert example[name] == example_layout(name), name


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


def test_run_all_child_argv_keeps_override_values():
    from pathlib import Path

    from robot.cli.common import Ctx
    from robot.cli.main import child_argv

    c = Ctx(config_dir=Path("/x/config"), overrides=["display.backend=null", "camera.backend=null"], log_level="DEBUG")
    argv = child_argv(c, "motion", mock=True, enable_mode="pulse")
    assert argv[-5:] == ["run", "motion", "--mock", "--enable-mode", "pulse"]
    assert argv[argv.index("--set") + 1] == "display.backend=null"
    assert argv.count("--set") == 2 and "--config-dir" in argv and "/x/config" in argv


def test_autostart_entry_renders_a_valid_desktop_file(tmp_path, monkeypatch):
    from robot.cli import autostart_cmd as ac

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert ac.entry_path() == tmp_path / "xdg" / "autostart" / ac.ENTRY_NAME
    text = ac.render_entry(Path("/opt/venv/bin/robot"), Path("/home/pi/desktop-buddy/config"), Path("/tmp/a.log"))
    assert text.startswith("[Desktop Entry]\n")
    assert (
        "Exec=sh -c '/opt/venv/bin/robot --config-dir /home/pi/desktop-buddy/config run all >> /tmp/a.log 2>&1'" in text
    )
    assert "Terminal=false" in text


def test_window_display_fullscreen_flag_covers_screen(monkeypatch):
    import pygame

    from robot.hal.display.window import WindowDisplay

    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    d = WindowDisplay(200, 100, fullscreen=True)
    d.open()
    try:
        assert d.info.width > 0 and d.info.height > 0
        assert isinstance(pygame.display.get_surface(), pygame.Surface)
    finally:
        d.close()
