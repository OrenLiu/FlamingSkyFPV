# FlamingSkyFPV

[English](README_en.md) | [简体中文](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

[![Rendering](https://img.shields.io/badge/Rendering-soup3D-E34F26)](https://pypi.org/project/soup3D/)
[![Physics](https://img.shields.io/badge/Physics-PyBullet-76B900)](https://pybullet.org/)
[![UI](https://img.shields.io/badge/UI-Dear%20ImGui-20B2AA)](https://github.com/ocornut/imgui)
[![Window & Input](https://img.shields.io/badge/Window%20%26%20Input-pygame-6C5CE7)](https://www.pygame.org/)

Open-source FPV drone simulator where everything is customizable: import your own scenes, tracks and obstacles; every parameter is open (Rate / PID / weight / KV / camera angle) — change it and fly.

*The interface is currently Chinese-only. On-screen labels are given below in English, with the original Chinese in parentheses.*

## Compatibility
Supports 64-bit Windows, macOS and Linux.

### Minimum
- **CPU**  
  Dual-core processor (Intel Core i3 / AMD Ryzen 3 class)
- **GPU**  
  Any graphics card with OpenGL 3.3 support; integrated graphics is enough
- **Memory**  
  4 GB
- **Radio / gamepad**  
  A radio transmitter or gamepad that supports the `HID` protocol

### Recommended
- **CPU**  
  Quad-core processor or better (Intel Core i5 / AMD Ryzen 5 class)
- **GPU**  
  A dedicated graphics card, or a recent integrated one (the "Quality First" preset adds PBR, soft shadows and screen-space reflections, which is more demanding)
- **Memory**  
  8 GB
- **Radio / gamepad**  
  A radio transmitter or gamepad that supports the `HID` protocol

## Running the App
1. Download the file for your operating system from the project's `Releases` page
2. Extract the downloaded file
3. Run the executable (on Windows, for example, the executable is `FlamingSkyFPV.exe`)

## Building from Source
1. **Set up the environment**  
   Install Python 3.10 or 3.11 (3.10 is the version used for development and packaging). Prebuilt wheels of the imgui 2.0.0 dependency exist only up to Python 3.11 and only for x86_64 macOS, so a newer Python or an Apple Silicon Mac means compiling it yourself. A clean virtual environment is recommended. Then install the dependencies from the repository root:
   ```bash
   pip install pybullet pygame imgui "soup3D>=7.1" Pillow
   ```
   soup3D is the rendering engine; installing it pulls in its own dependencies PyOpenGL, pyglm, numpy and imageio, and Pillow is used by the "Quality First" preset. PyInstaller does not need to be installed manually either — the build script installs it automatically if it is missing. Once the dependencies are installed, run `python main.py` from the repository root to start the app directly, without packaging it
2. **Run the build script**  
   Run `python build.py` to start building the executable  
   Optional flags: `--console` (console build, useful for troubleshooting), `--no-scene` (do not bundle the scenes), `--zip` (compress the output to a zip after building)
3. **Collect the output**  
   The final output is placed in the `dist` folder
   - Windows: `dist/FlamingSkyFPV/FlamingSkyFPV.exe`
   - macOS: `dist/FlamingSkyFPV.app`
   - Linux: `dist/FlamingSkyFPV/FlamingSkyFPV`

   Note: PyInstaller cannot cross-compile, so the build has to run on the platform you want the executable for

## Note: Set Up Your Radio / Gamepad Before First Use!
1. Go to **RC Channel Setup**（遥控通道设置）and click **Start Setup Wizard**（启动配置向导）
2. Choose your transmitter mode (美国手 is Mode 2, 日本手 is Mode 1 and 中国手 is Mode 3). Mode 2, with the throttle on the left stick, is the most common
3. Follow the prompts and move the sticks as instructed
4. When you are done, move the sticks and check that each channel moves in the correct direction

## About the Simulator

FlamingSkyFPV is an open-source FPV drone simulator built around one idea: everything is customizable. It simulates more than just flying. Scenes, tracks and obstacles can all be imported, and tuning is simulated too — the program can act as a "virtual flight controller", so you tune the simulated drone with a real Betaflight ground station.

### Features

- **Custom scenes**: scenes, tracks and obstacles are scene units organized as folders — drop one into `scene/` and restart the program, and it shows up in the flight list.
- **Every parameter is open**: Rate, PID, TPA, camera angle, weight, motor KV, battery, air drag and more are all adjustable, and take effect as soon as you save. Multiple drone presets can be created, copied, renamed, deleted and switched at any time.
- **Two ways to tune**: in "Parameter Mode"（参数模式）you tune directly in the app; in "Virtual FC Mode"（虚拟飞控模式）Rate / PID / TPA are handed over to the Betaflight ground station and take effect as soon as you save.
- **Realistic battery simulation**: voltage sag and capacity drain are modeled from battery chemistry, cell count, capacity and C-rating, with OSD warnings when the voltage gets low.
- **Betaflight-style OSD**: Virtual FC Mode displays an OSD on the MAX7456 character grid; which elements are shown and where are edited in the ground station.
- **Radio transmitter / gamepad**: supports radios and gamepads using the HID protocol, with both a step-by-step setup wizard and manual channel binding, plus hot-plug support.
- **Graphics presets and shader packs**: two built-in presets, "Performance First"（性能优先）and "Quality First"（画面优先）, and support for home-made shader packs.
- **Cross-platform**: one code base runs on Windows, macOS and Linux; the release builds run straight after extracting, with no installation.

### Main Menu

| Menu | Description |
| --- | --- |
| Start Flying（开始飞行） | Pick a scene and fly. Freestyle is available now; racing is planned for a later release |
| Settings（设置） | Display mode (Fullscreen / Fixed-size Window / Windowed), resolution, frame-rate limit, graphics preset |
| Drone Parameters（无人机参数） | Manage drone presets, adjust flight parameters, connect a ground station |
| RC Channel Setup（遥控通道设置） | Configure the channels of your radio / gamepad, or start the setup wizard |
| Quit（退出） | Close the program |

### Flight Controls

- **`F3`**: show or hide debug info (frame rate and camera position) on any screen.
- **`Esc`**: open the pause menu — "Resume"（继续飞行）, "Reset Position"（重置位置）, "Back to Main Menu"（返回主菜单）. "Reset Position" returns the drone to the spawn point, disarms it and recharges the battery to full.

In **Parameter Mode** there is no arming step: throttle up and the drone flies. **Virtual FC Mode** works like a real drone: the ARM switch has to be on to arm, and the throttle must be below 10% when arming; if arming is refused because the throttle is too high, turn the ARM switch off and on again before retrying. The factory presets bind ARM to AUX1, flip-over-after-crash to AUX3 and ANGLE (self-level) to AUX4; these assignments can be changed on the Modes page of the ground station.

The simulator does not detect crashes: after a crash the motors keep running, so disarm, flip the drone over or use "Reset Position" yourself.

### Tuning

The "Drone Parameters" page manages all drone presets as a list: click a row to make it the current drone; each row has "Edit"（编辑）, "Copy"（复制）and "Delete"（删除）buttons, and "+ New Drone"（+新建无人机）adds one. The adjustable parameters are:

| Category | Parameters |
| --- | --- |
| Rate | Rate type: `betaflight_actual` (Betaflight Actual Rates) or `linear` (linear, with an extra "max deg/s at full stick" field); RC Rate, Super Rate and Expo for roll, pitch and yaw independently |
| PID | P, I and D for each axis, on the Betaflight scale |
| TPA | Attenuation percentage and breakpoint |
| Frame | Weight, motor KV, thrust-to-weight ratio, air drag, camera angle |
| Battery | With "Realistic Battery Simulation"（真实电池模拟）on: chemistry (LiPo / LiHV / Li-ion), cell count, capacity, C-rating, and the warning / minimum / maximum cell voltage; with it off, the battery voltage is set directly |

Every drone has two tuning modes, switched at the bottom of the edit view:

- **Parameter Mode**: all of the parameters above are adjusted directly in the app.
- **Virtual FC Mode**: after you click "Convert to Virtual FC"（转换为虚拟飞控）and confirm, Rate / PID / TPA are handed over to the ground station and the form keeps only the hardware parameters such as frame and battery; "Convert to Parameter Mode"（转换为参数模式）writes the ground-station tuning back into the app. In this mode the warning / minimum / maximum cell voltage can only be changed on the Power & Battery page of the ground station. The factory preset `virtual_FC` is in this mode.

With "Realistic Battery Simulation" enabled, thrust drops as the voltage sags, capacity is drained by integrating the current, and the battery can be run all the way down; the Virtual FC Mode OSD flashes LOW BATTERY when the cell voltage falls below the warning voltage and LAND NOW below the minimum voltage (the Parameter Mode HUD shows no battery information). Entering a flight or using "Reset Position" is the same as fitting a freshly charged battery.

### Tuning with the Betaflight Ground Station

1. On the "Drone Parameters" page select a drone and click "Edit"（编辑）; if it is not in Virtual FC Mode yet, click "Convert to Virtual FC"（转换为虚拟飞控） and confirm.
2. Click "Connect Ground Station"（接入地面站）. A persistent "Ground Station Connection"（地面站连接）window appears listing the available addresses (there is no PTY serial entry on Windows); use any one of them:

   | Transport | Address | Intended for |
   | --- | --- | --- |
   | TCP | `tcp://127.0.0.1:5761` | Betaflight Configurator desktop app |
   | WebSocket | `ws://127.0.0.1:5762` | Betaflight Configurator in the browser |
   | PTY serial | The path shown in the window (`/dev/pts/N` on Linux, `/dev/ttysNNN` on macOS) | Serial tools; Linux / macOS only |

3. In the ground station enable **Expert Mode**, choose manual device selection, enter the address above and click Connect. The browser version auto-connects to its built-in demo flight controller by default, so make sure to select the address manually.
4. You can now tune on the PID tuning, Rates, Modes, OSD and Power & Battery pages; saving takes effect in the simulator immediately. Click "Disconnect"（断开）in the window or quit the program to disconnect.

The simulator identifies itself as a Betaflight 4.5.1 flight controller. While flying in Virtual FC Mode the Betaflight-style OSD is shown; which elements it displays and where are edited on the OSD page of the ground station.

### Importing Scenes

Each subfolder of `scene/` is one scene; drop one in and restart the program, and it appears in the scene list under "Start Flying"（开始飞行）:

```
scene/<scene name>/
├── config.json      # scene configuration
├── <model>.obj      # main model, used together with its .mtl material file
├── <textures>       # referenced by the .mtl
└── <cover image>    # optional, shown as the cover in the scene list
```

Example `config.json`:

```json
{
  "name": "My Scene",
  "model": "map.obj",
  "cover": "cover.png",
  "scale": 1.0,
  "spawn": [0, 0, 2],
  "sky": { "sun_yaw": 120, "sun_pitch": -30, "sun_color": [1.0, 0.6, 0.2] }
}
```

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Scene name shown in the list |
| `model` | Yes | File name of the OBJ model inside the scene folder; used for both rendering and collision |
| `cover` | No | Cover image file name, relative to the scene folder |
| `scale` | No | Model scale factor, default `1.0` |
| `spawn` | No | Spawn point `[x, y, z]` in meters, default `[0, 0, 2]` |
| `sky` | No | `sun_yaw` and `sun_pitch` set the sun angles in degrees (default `30` and `-60`); `sun_color` is the sun color `[r, g, b]`; `panorama` is the file name of an equirectangular panorama inside the scene folder, used as the skybox by the "Quality First" preset (a procedural sky is generated when omitted) |
| `sun_toward` | No | Top-level shorthand: sun direction `[yaw, pitch]` in degrees, equivalent to `sun_yaw` + `sun_pitch` in `sky`; an explicit `sky` entry wins |
| `sun_color`, `ambient` | No | Top-level shorthand: sun color and ambient light color `[r, g, b]`, default `[0.8, 0.8, 0.75]` and `[0.45, 0.45, 0.45]` |

Only OBJ + MTL models are supported at the moment. Scenes with an invalid `config.json` or a missing model file are marked "invalid"（无效）and skipped rather than disappearing from the list. See "Data Directory" below for where scenes are stored.

### Graphics Presets and Shader Packs

Switch shader preset packs in "Settings → Graphics Preset"（画质预设）:

| Preset | Effect |
| --- | --- |
| Performance First (default) | The default rendering pipeline, with the best performance |
| Quality First | PBR materials + soft sun shadows + SSR reflections + HDR/ACES tone mapping |

To use a home-made or downloaded pack, put it in `shader_packs/<pack name>/` (containing `pack.json` and `pack.py`), restart the program and select it on the Settings page; it takes effect the next time you enter a flight scene. See the [shader pack author guide](docs/shader_packs.md) (in Chinese) for how to write one. Warning: running a third-party `pack.py` executes arbitrary code — there is no sandbox, so only install packs from trusted sources.

### Data Directory

Settings, drone presets, radio configuration, scenes, home-made shader packs and logs all live in the same data directory:

| Path | Contents |
| --- | --- |
| `config/settings.json` | Settings |
| `config/rc.json` | Radio channel configuration |
| `config/drones/` | Drone presets |
| `scene/` | Scenes |
| `shader_packs/` | Home-made shader packs |
| `logs/` | Run logs, one file per launch |

Where that directory is: the repository root when running from source; the folder containing the executable for the Windows and Linux builds (or the user data directory if that folder is not writable); `~/Library/Application Support/FlamingSkyFPV/` on macOS. The `FLAME_DATA_DIR` environment variable overrides it.

### Known Limitations

- Racing is not implemented yet; only freestyle is available.
- When a frame takes longer than 1/30 s the physics step is clamped to prevent tunneling, so at low frame rates the simulation runs slower than real time.
- The PID simulation has no feedforward, D-Min or I-term relax, so changing those in the ground station has no effect.
- The "Vertical Sync"（垂直同步）frame-rate option only takes effect on Linux (X11); on Windows and macOS selecting it behaves like "Unlimited"（无限制）.

## License

This project is open source under the [MIT License](LICENSE), Copyright (c) 2026 刘申怿. You are free to use, copy, modify and distribute it and to use it commercially, provided that the copyright and permission notices are kept in all copies; the software is provided "as is", without warranty of any kind.

This project is built on the following open-source projects:

| Project | Used for | License |
| --- | --- | --- |
| [soup3D](https://pypi.org/project/soup3D/) | 3D rendering engine | MIT |
| [PyBullet](https://pybullet.org/) | Physics engine | zlib |
| [Dear ImGui](https://github.com/ocornut/imgui) / [pyimgui](https://github.com/pyimgui/pyimgui) | User interface | MIT / BSD-3-Clause |
| [pygame](https://www.pygame.org/) | Window and input | LGPL-2.1 |
| [NumPy](https://numpy.org/) | Numerical computing | BSD-3-Clause |
| [PyOpenGL](https://pyopengl.sourceforge.io/) | OpenGL bindings | BSD |
| [PyGLM](https://github.com/Zuzu-Typ/PyGLM) | Math library | zlib |
| [imageio](https://imageio.github.io/) | Image reading | BSD-2-Clause |
| [Pillow](https://python-pillow.org/) | Image processing | HPND |

The release builds are packaged with [PyInstaller](https://pyinstaller.org/), whose bootloader is licensed under GPL-2.0-or-later with a special exception that allows it to be used for non-free programs.
