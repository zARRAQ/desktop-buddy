# What is reused, and from where

The brief was "check online for anything similar and use it". This is what was found, what was
taken, and what was deliberately not taken.

## Reused directly

| Piece of the robot | Reused from | Licence | How it is used |
|---|---|---|---|
| Face parameter model and 25 expressions | [PyCozmo](https://github.com/zayfod/pycozmo) (Cozmo's procedural face) | MIT | Ported to a resolution-independent polygon renderer in `robot/face/procedural.py`; presets kept numerically identical in `robot/face/expressions.py` |
| Face detection + recognition on CPU | [OpenCV Zoo](https://github.com/opencv/opencv_zoo) YuNet and SFace, built into `cv2` | Apache-2.0 | `robot/perception/cpu.py`. Same service code as the Hailo path. Verified on a real six-person photo: same person 0.76 to 0.96 cosine, impostors 0.21 to 0.33, threshold 0.363 |
| Face detection + recognition on Hailo | [hailo-apps](https://github.com/hailo-ai/hailo-apps) model choice: SCRFD 2.5G/10G + ArcFace MobileFaceNet, Model Zoo v2.17.0 (8/8L) and v5.2.0 (10H) | MIT / Hailo | `robot/perception/hailo.py` drives the same `.hef` files through HailoRT's `InferModel` API, without GStreamer, so perception is one class of object regardless of backend |
| SPI panel init sequences | [Adafruit CircuitPython RGB Display](https://github.com/adafruit/Adafruit_CircuitPython_RGB_Display) | MIT | ST7789, ILI9341, GC9A01A tables in `robot/hal/display/spi.py` |
| Face alignment template | [InsightFace](https://github.com/deepinsight/insightface) `arcface_dst` | MIT | `robot/perception/align.py` |
| Speech to text, text to speech, wake word, VAD | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Moonshine, Piper voices via VITS, Zipformer KWS, Silero VAD) | Apache-2.0 | One dependency for the whole voice stack, with aarch64 wheels for Python 3.13 |
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord) `hey_jarvis` | Apache-2.0 | Default wake engine (ONNX path; the tflite dependency is masked in `pyproject.toml`) |
| Language model runtime | [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server`, or Ollama, or Hailo's `hailo-ollama` | MIT / MIT / Hailo | The brain speaks the OpenAI-compatible chat API, so all three are interchangeable by changing one URL |
| Language models | Gemma 3 1B (default), Qwen3 1.7B (alternative) | Gemma ToU / Apache-2.0 | GGUF Q4_K_M via `robot provision --group llm` |
| GPIO | [gpiozero 2](https://gpiozero.readthedocs.io) on lgpio | BSD-3 | Digital IO and encoders on the Pi 5 |
| Bus | [pyzmq](https://pyzmq.readthedocs.io) XSUB/XPUB proxy | BSD-3 / LGPL | `robot/core/bus.py`, `robot/core/broker.py` |
| Rendering | [pygame-ce](https://pyga.me) | LGPL-2.1 | Window, KMS/DRM fullscreen, drawing primitives |

## Looked at, used as reference only

| Project | Why it matters | What was taken |
|---|---|---|
| [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) (Pollen Robotics / Hugging Face, Apache-2.0) | The closest commercial open-source desk companion: daemon + SDK + apps, Pi CM4 inside the wireless version | The daemon/app split validated our process-per-service design. Its apps rely on cloud speech; ours stays local |
| [Qubit](https://github.com/0xaiwhisperer/qubit) | Pi 5 desk robot in Python with Ollama, LED eyes, OpenCV face tracking | Confirmed Ollama on a Pi 5 as a practical brain; nothing copied |
| [Coglet](https://github.com/will-cogley/Coglet) | Expressive 3D-printed companion, ESP32 based | Not applicable to a Pi stack |
| [hailo-rpi5-examples](https://github.com/hailo-ai/hailo-rpi5-examples) | The repo the original plan pointed at | Deprecated by Hailo in favour of hailo-apps; nothing taken |

## Considered and rejected

| Candidate | Reason |
|---|---|
| Piper TTS (`piper-tts` / `piper1-gpl`) as a dependency | The maintained fork is GPL-3.0 since October 2025. Piper *voices* are still permissively licensed and run under sherpa-onnx, which is what we do |
| `rpi-hardware-pwm` | GPL-3.0. Replaced by 60 lines of sysfs code in `robot/hal/gpio/sysfs_pwm.py` |
| FluxGarage RoboEyes, ggldnl Procedural-Expression-Library | GPL-3.0 face libraries; PyCozmo's MIT model is richer anyway |
| luma.lcd / luma.oled | Good libraries, but no GC9A01 support and RPi.GPIO-era GPIO on the Pi 5 needs a shim; our SPI backend is ~250 lines including four controllers |
| Adafruit Blinka + servokit for the PCA9685 | Pulls in a large dependency tree for a 40-line register map; own driver is easier to mock and test |
| ROS 2 | Far heavier than a 4-core Pi running an LLM can afford next to a 30 fps face |
| Whisper via faster-whisper | Works, but Moonshine tiny is several times faster at equal word error rate and ships in sherpa-onnx |
