# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from typing import Optional
from dotenv import load_dotenv

# 提前加载.env配置文件（必须在读取环境变量前执行，确保os.getenv能获取到值）
# 若.env不在项目根目录，可指定路径：load_dotenv(dotenv_path=Path(__file__).parent / ".env")
load_dotenv()


# 定义minerU服务配置
@dataclass
class MineruConfig:
    mode: str
    cli_path: Optional[str]
    output_root: Optional[str]
    base_url: Optional[str]
    api_key: Optional[str]
    local_base_url: str
    local_tier: str
    local_poll_interval: float
    local_timeout_seconds: int

mineru_config = MineruConfig(
    # 默认使用本机 MinerU API；cli 与旧 api 模式仅为兼容保留。
    mode=os.getenv("MINERU_MODE", "local_api").strip().lower(),
    cli_path=os.getenv("MINERU_CLI_PATH"),
    output_root=os.getenv("MINERU_OUTPUT_ROOT"),
    base_url=os.getenv("MINERU_BASE_URL"),
    api_key=os.getenv("MINERU_API_TOKEN"),
    local_base_url=os.getenv("MINERU_LOCAL_BASE_URL", "http://127.0.0.1:8100").rstrip("/"),
    local_tier=os.getenv("MINERU_LOCAL_TIER", "standard").strip().lower(),
    local_poll_interval=float(os.getenv("MINERU_LOCAL_POLL_INTERVAL", "2")),
    local_timeout_seconds=int(os.getenv("MINERU_LOCAL_TIMEOUT_SECONDS", "3600")),
)
