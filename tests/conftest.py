import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中，这样 `import app` 才会解析到根包。
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
