"""UniConvert 测试包（零依赖：只用标准库 unittest）。"""

import sys
from pathlib import Path

# 保证从仓库根目录或任意位置运行都能 import uniconvert
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
