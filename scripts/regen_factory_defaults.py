#!/usr/bin/env python3
"""重新生成 factory_defaults.py(出厂无人机预设快照,机器生成勿手改)。

流程:在 config/drones/ 放定稿的 init_drone.json / virtual_FC.json,
仓库根目录运行 python scripts/regen_factory_defaults.py 即完成快照更新。
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

presets = {}
for preset_id in ("init_drone", "virtual_FC"):
    with open(f"config/drones/{preset_id}.json", encoding="utf-8") as f:
        presets[preset_id] = json.load(f)

payload = json.dumps(presets, ensure_ascii=False, indent=1)
header = '''"""出厂无人机预设(机器生成,勿手改)。

内容 = 生成时刻 config/drones/init_drone.json 与 virtual_FC.json 的原样
快照(5inch 为作者个人调参档,刻意不纳入出厂)。独立运行自举(bootstrap.
ensure_user_files)在预设目录为空时将其播种到 config/drones/。重新生成:
编辑 config/factory...
'''
# 与既有 factory_defaults.py 同构:直接复用生成逻辑,保持文件格式一致
header = '''"""出厂无人机预设(机器生成,勿手改)。

内容 = 生成时刻 config/drones/init_drone.json 与 virtual_FC.json 的原样
快照(5inch 为作者个人调参档,刻意不纳入出厂)。独立运行自举(bootstrap.
ensure_user_files)在预设目录为空时将其播种到 config/drones/。重新生成:
编辑 config/drones/*.json 后运行 scripts/regen_factory_defaults.py。
"""
'''
body = "FACTORY_DRONES_JSON = " + repr(payload) + "\n"
footer = '''

def factory_drones() -> dict:
    """出厂预设表(preset_id → DroneParams 兼容 dict);每次返回新解析副本"""
    import json
    return json.loads(FACTORY_DRONES_JSON)
'''
with open("factory_defaults.py", "w", encoding="utf-8") as f:
    f.write(header + body + footer)
import ast
ast.parse(open("factory_defaults.py", encoding="utf-8").read())
print("factory_defaults.py 已重新生成并通过语法校验")
