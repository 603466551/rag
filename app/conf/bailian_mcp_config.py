# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


# 定义 MCP 搜索服务配置。默认使用 Streamable HTTP，兼容保留旧 SSE 配置。
@dataclass
class McpConfig:
    enabled: bool
    transport: str
    mcp_base_url: Optional[str]
    authorization: Optional[str]
    tool_name: Optional[str]

mcp_config = McpConfig(
    enabled=os.getenv("MCP_ENABLED", "True").lower() in ("1", "true", "yes"),
    transport=os.getenv("MCP_TRANSPORT", "streamable_http").strip().lower(),
    # ModelScope 地址优先；旧百炼 SSE 地址作为兼容回退。
    mcp_base_url=os.getenv("MCP_MODELSCOPE_URL") or os.getenv("MCP_DASHSCOPE_BASE_URL"),
    # ModelScope MCP 地址可包含自身访问标识，默认不复用 DeepSeek 的 API Key。
    authorization=os.getenv("MCP_AUTHORIZATION"),
    tool_name=os.getenv("MCP_SEARCH_TOOL_NAME"),
)
