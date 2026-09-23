# Third-party notices

This project is MIT licensed. It reuses the following work. Runtime dependencies installed
from PyPI carry their own licences (all permissive: MIT, BSD, Apache-2.0, LGPL for pygame-ce
which is used unmodified as a shared library).

## Code ported or copied into this repository

### PyCozmo (MIT)

`src/robot/face/procedural.py` and `src/robot/face/expressions.py` port the procedural face
parameter model and the expression presets from PyCozmo, https://github.com/zayfod/pycozmo.
The presets in turn follow Catherine Chambers' "Expressive Eyes" work
(https://git.brl.ac.uk/ca2-chambers/expressive-eyes).

```
The MIT License (MIT)

Copyright (c) 2019-2020

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Adafruit CircuitPython RGB Display (MIT)

The SPI panel initialisation sequences in `src/robot/hal/display/spi.py` (ST7789, ILI9341,
GC9A01A) are taken from https://github.com/adafruit/Adafruit_CircuitPython_RGB_Display.

```
SPDX-FileCopyrightText: 2017 Radomir Dopieralski for Adafruit Industries
SPDX-FileCopyrightText: 2019 Melissa LeBlanc-Williams for Adafruit Industries
SPDX-FileCopyrightText: 2023 Matt Land
SPDX-FileCopyrightText: 2025 Liz Clark for Adafruit Industries
SPDX-License-Identifier: MIT
```

### InsightFace (MIT)

The five-point ArcFace alignment template in `src/robot/perception/align.py` is the
`arcface_dst` constant from https://github.com/deepinsight/insightface.

### Hailo Application Infrastructure (MIT)

Model selection for the Hailo backend (SCRFD 2.5G / 10G plus ArcFace MobileFaceNet, Model
Zoo v2.17.0 and v5.2.0) and the 0.5 identity threshold follow
https://github.com/hailo-ai/hailo-apps. No code is copied.

## Models downloaded by `robot provision`

| Model | Source | Licence |
|---|---|---|
| YuNet face detector, SFace embedder | OpenCV Zoo | Apache-2.0 |
| SCRFD, ArcFace MobileFaceNet `.hef` | Hailo Model Zoo | MIT (model zoo); HailoRT runtime is Hailo's licence |
| Moonshine tiny (English) | Useful Sensors via sherpa-onnx releases | MIT |
| Piper voice `en_US-lessac-medium` | rhasspy/piper voices via sherpa-onnx | MIT model card (voice data public domain) |
| Silero VAD | snakers4/silero-vad via sherpa-onnx | MIT |
| openWakeWord `hey_jarvis` and feature models | dscripka/openWakeWord | Apache-2.0 |
| sherpa-onnx keyword spotter (gigaspeech) | k2-fsa | Apache-2.0 |
| Gemma 3 1B instruct (GGUF) | Google via ggml-org | Gemma Terms of Use |
| Qwen3 1.7B (GGUF) | Alibaba via unsloth | Apache-2.0 |

Read the Gemma Terms of Use before shipping a product that includes it; the alternative
Qwen3 model is Apache-2.0.
