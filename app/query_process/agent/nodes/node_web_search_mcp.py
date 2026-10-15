import sys
import json
import asyncio
from typing import Any
from app.utils.task_utils import add_done_task, add_running_task
from app.conf.bailian_mcp_config import mcp_config
from agents.mcp import MCPServerSse, MCPServerStreamableHttp
from app.core.logger import logger


def _create_mcp_server():
    """按配置创建 MCP 客户端，支持 Streamable HTTP 与旧 SSE 协议。"""
    if not mcp_config.mcp_base_url:
        raise ValueError("MCP 配置缺失：请设置 MCP_MODELSCOPE_URL")

    params: dict[str, Any] = {
        "url": mcp_config.mcp_base_url,
        "timeout": 30,
        "sse_read_timeout": 300,
    }
    if mcp_config.authorization:
        params["headers"] = {"Authorization": mcp_config.authorization}

    if mcp_config.transport == "streamable_http":
        return MCPServerStreamableHttp(
            name="ai_connected_search",
            params=params,
            cache_tools_list=True,
        )
    if mcp_config.transport == "sse":
        return MCPServerSse(name="search_mcp", params=params)
    raise ValueError(
        f"不支持的 MCP_TRANSPORT={mcp_config.transport!r}，仅支持 streamable_http 或 sse"
    )


async def _select_search_tool(search_mcp):
    """从 MCP 工具列表中选择搜索工具，避免硬编码供应商专属工具名。"""
    tools = await search_mcp.list_tools()
    if not tools:
        raise RuntimeError("MCP 服务未提供任何工具")

    if mcp_config.tool_name:
        for tool in tools:
            if tool.name == mcp_config.tool_name:
                return tool
        available_names = ", ".join(tool.name for tool in tools)
        raise RuntimeError(
            f"未找到 MCP_SEARCH_TOOL_NAME={mcp_config.tool_name!r}，可用工具：{available_names}"
        )

    if len(tools) == 1:
        return tools[0]

    search_tools = [tool for tool in tools if "search" in tool.name.lower()]
    if len(search_tools) == 1:
        return search_tools[0]

    available_names = ", ".join(tool.name for tool in tools)
    raise RuntimeError(
        "MCP 服务包含多个工具，无法自动确定搜索工具。"
        f"请设置 MCP_SEARCH_TOOL_NAME，可用工具：{available_names}"
    )


def _build_search_arguments(tool, query: str) -> dict[str, Any]:
    """依据 MCP 工具 schema 选择查询参数名，并在支持时附带结果条数。"""
    # mcp.types.Tool 使用 inputSchema（驼峰），部分 SDK 封装使用 input_schema。
    input_schema = (
        getattr(tool, "input_schema", None)
        or getattr(tool, "inputSchema", None)
        or {}
    )
    properties = input_schema.get("properties", {})
    query_key = next(
        (
            key
            for key in properties
            if key.lower() in {"query", "q", "keyword", "keywords", "search_query"}
        ),
        None,
    )
    if query_key is None:
        raise RuntimeError(f"MCP 工具 {tool.name} 未声明可识别的查询参数")

    arguments = {query_key: query}
    if "count" in properties:
        arguments["count"] = 5
    elif "limit" in properties:
        arguments["limit"] = 5
    return arguments


def _parse_mcp_result(result) -> list[dict[str, str]]:
    """将不同 MCP 服务的文本结果规整为 rerank 节点可消费的网页文档。"""
    docs: list[dict[str, str]] = []
    for content in result.content or []:
        raw_text = getattr(content, "text", "")
        if not raw_text:
            continue
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            docs.append({"title": "联网搜索结果", "url": "", "snippet": raw_text.strip()})
            continue

        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            docs.append({"title": "联网搜索结果", "url": "", "snippet": raw_text.strip()})
            continue

        for item in pages:
            if not isinstance(item, dict):
                continue
            snippet = (item.get("snippet") or item.get("content") or "").strip()
            if snippet:
                docs.append({
                    "title": (item.get("title") or "").strip(),
                    "url": (item.get("url") or "").strip(),
                    "snippet": snippet,
                })
    return docs


async def mcp_call(query):
    """
    异步调用百炼MCP搜索服务的核心函数。
    
    该函数负责初始化MCP客户端，建立SSE连接，调用远程工具，并返回原始结果。
    
    :param query: 搜索查询词（通常是经过改写后的精准Query）
    :return: MCP返回的原始结果对象 (包含 content, isError 等字段)
    """
    
    # ==================================================================================
    search_mcp = _create_mcp_server()

    try:
        logger.info(f"[MCP] 正在连接搜索服务，传输协议：{mcp_config.transport}")
        await search_mcp.connect()
        tool = await _select_search_tool(search_mcp)
        arguments = _build_search_arguments(tool, query)
        logger.info(f"[MCP] 连接成功，正在调用搜索工具：{tool.name}")
        result = await search_mcp.call_tool(
            tool_name=tool.name,
            arguments=arguments,
        )
        logger.info("[MCP] 工具调用完成，已获取返回结果")
        return result
        
    except Exception as e:
        logger.error(f"[MCP] 调用过程中发生异常: {e}", exc_info=True)
        return None
        
    finally:
        # 无论调用成功/失败，最终都关闭MCP连接（释放资源，异步方法）
        await search_mcp.cleanup()


def node_web_search_mcp(state):
    """
    LangGraph同步节点函数：处理MCP搜索逻辑，作为整个搜索流程的入口。
    
    该节点会调用 mcp_call 异步函数获取搜索结果，并将其解析为结构化数据存储到 state 中。
    
    :param state: LangGraph的全局状态对象，包含 session_id, rewritten_query 等信息
    :return: 字典，包含结构化的搜索结果 web_search_docs，供后续节点使用
    """
    logger.info("---node_web_search_mcp 开始处理---")
    
    # 1. 标记任务开始
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    # 2. 获取查询词
    query = state.get("rewritten_query", "")
    if not query:
        # 尝试回退到原始查询
        query = state.get("original_query", "")
        
    docs = []

    if not mcp_config.enabled:
        logger.info("MCP 联网搜索已通过 MCP_ENABLED 关闭")
        add_done_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))
        return {"web_search_docs": docs}
    
    # 3. 执行搜索
    if query:
        try:
            # 同步-异步桥接：通过asyncio.run()执行异步的mcp_call函数
            logger.info(f"启动异步 MCP 调用，Query: {query}")
            
            # ======================================================================
            # MCP 返回结果格式解析说明
            # ----------------------------------------------------------------------
            # result 是一个 CallToolResult 对象 (定义在 agents.mcp.types 中)
            # result.content 是一个 TextContent 对象的列表，通常只有一项
            # result.content[0].text 是一个 JSON 字符串，包含实际的搜索结果
            #
            # 示例数据结构：
            # result.content[0].text = """
            # {
            #   "pages": [
            #     {
            #       "title": "HAK 180 烫金机使用手册",
            #       "url": "http://example.com/manual",
            #       "snippet": "在出厂默认状态下，若想设置局部转印..."
            #     },
            #     ...
            #   ]
            # }
            # """
            # ======================================================================
            result = asyncio.run(mcp_call(query))
            
            # 4. 解析结果
            if result and not result.isError and result.content:
                docs = _parse_mcp_result(result)
                logger.info(f"MCP 返回结构化文档数量: {len(docs)}")
            else:
                if result and result.isError:
                    logger.error(f"MCP 返回错误: {result}")
                else:
                    logger.warning("MCP 返回结果为空或无效")

            logger.info(f"结构化搜索结果数量: {len(docs)}")
            
        except Exception as e:
            logger.error(f"MCP 搜索节点执行异常: {e}", exc_info=True)
    else:
        logger.warning("查询词为空，跳过 MCP 搜索")

    # 5. 标记任务结束
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))
    
    logger.info("---node_web_search_mcp 处理结束---")
    
    # 若有有效搜索结果，返回结果供后续节点使用；无则返回空字典
    if docs:
        return {"web_search_docs": docs}
    return {}


if __name__ == '__main__':
    # 测试代码：单独运行该文件时，验证MCP搜索功能是否正常
    print("\n" + "="*50)
    print(">>> 启动 node_web_search_mcp 本地测试")
    print("="*50)
    
    test_state = {
        "session_id": "test_mcp_session",
        "rewritten_query": "HAK 180 在出厂默认状态下，若想在纸张上只把烫金膜转印到顶部 50 mm–170 mm 的局部区域，应在操作面板上如何设置",
        "is_stream": False
    }

    try:
        # 调用MCP搜索节点函数，执行测试
        result_state = node_web_search_mcp(test_state)

        print("\n" + "="*50)
        print(">>> 测试结果摘要:")
        search_results = result_state.get('web_search_docs', [])
        print(f"搜索结果数量: {len(search_results)}")
        if search_results:
            print("首条结果预览:")
            print(json.dumps(search_results[0], indent=2, ensure_ascii=False))
        else:
            print("未获取到搜索结果")
        print("="*50)
        
    except Exception as e:
        logger.exception(f"测试运行期间发生未捕获异常: {e}")
