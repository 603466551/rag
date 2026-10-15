import os
import socket
import shutil
import uuid
from typing import List, Dict, Any
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
import uvicorn
# 第三方库
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
# 项目内部工具/配置/客户端
from app.clients.minio_utils import get_minio_client
from app.conf.embedding_config import embedding_config
from app.conf.mineru_config import mineru_config
from app.conf.reranker_config import reranker_config
from app.utils.path_util import PROJECT_ROOT
from app.utils.task_utils import (
    add_running_task,
    add_done_task,
    get_done_task_list,
    get_running_task_list,
    update_task_status,
    get_task_status,
)
from app.import_process.agent.state import get_default_state
from app.core.logger import logger  # 项目统一日志工具

# 初始化FastAPI应用实例
# 标题和描述会在Swagger文档(http://ip:port/docs)中展示
app = FastAPI(
    title="File Import Service",
    description="Web service for uploading files to Knowledge Base (PDF/MD → 解析 → 切分 → 向量化 → Milvus/KG入库)"
)

# 跨域中间件配置：解决前端调用后端接口的跨域限制
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有前端域名访问（生产环境建议指定具体域名）
    allow_credentials=True,  # 允许携带Cookie等认证信息
    allow_methods=["*"],  # 允许所有HTTP方法（GET/POST/PUT/DELETE等）
    allow_headers=["*"],  # 允许所有请求头
)


def _check_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    """检查本机或配置服务的 TCP 可达性，不写入任何数据。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _service_item(name: str, required: bool, status: str, detail: str) -> Dict[str, Any]:
    return {
        "name": name,
        "required": required,
        "status": status,
        "detail": detail,
    }


def _parse_host_port(value: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname or "localhost", parsed.port or default_port


def _get_service_status() -> Dict[str, Any]:
    """收集本机依赖状态，供状态页面轮询使用。"""
    services: Dict[str, Dict[str, Any]] = {
        "import_api": _service_item("导入服务", True, "up", "当前服务正在响应"),
    }

    try:
        with urlopen("http://127.0.0.1:8001/health", timeout=1.5) as response:
            query_ok = response.status == 200
        services["query_api"] = _service_item(
            "查询服务", True, "up" if query_ok else "down", "健康检查成功" if query_ok else "健康检查返回异常"
        )
    except OSError as exc:
        services["query_api"] = _service_item("查询服务", True, "down", f"8001 不可访问：{type(exc).__name__}")

    milvus_url = os.getenv("MILVUS_URL", "")
    milvus_host, milvus_port = _parse_host_port(milvus_url, 19530)
    if not milvus_url or not _check_tcp(milvus_host, milvus_port):
        services["milvus"] = _service_item("Milvus", True, "down", f"{milvus_host}:{milvus_port} 不可连接")
    else:
        services["milvus"] = _service_item("Milvus", True, "up", f"{milvus_host}:{milvus_port} 可连接")

    minio_endpoint = os.getenv("MINIO_ENDPOINT", "")
    minio_host, minio_port = _parse_host_port(minio_endpoint, 9000)
    if not minio_endpoint or not _check_tcp(minio_host, minio_port):
        services["minio"] = _service_item("MinIO", True, "down", f"{minio_host}:{minio_port} 不可连接")
    elif get_minio_client() is None:
        services["minio"] = _service_item("MinIO", True, "warning", "端口可达，但客户端初始化失败，请检查凭据和桶名")
    else:
        services["minio"] = _service_item("MinIO", True, "up", f"{minio_host}:{minio_port} 可连接，客户端已初始化")

    mongo_url = os.getenv("MONGO_URL", "")
    mongo_host, mongo_port = _parse_host_port(mongo_url, 27017)
    if _check_tcp(mongo_host, mongo_port):
        services["mongodb"] = _service_item("MongoDB", True, "up", f"{mongo_host}:{mongo_port} 可连接")
    else:
        services["mongodb"] = _service_item("MongoDB", True, "down", f"{mongo_host}:{mongo_port} 不可连接")

    mineru_host, mineru_port = _parse_host_port(mineru_config.local_base_url, 8100)
    mineru_required = mineru_config.mode == "local_api"
    mineru_ok = _check_tcp(mineru_host, mineru_port)
    services["mineru"] = _service_item(
        "MinerU 本地 API",
        mineru_required,
        "up" if mineru_ok else ("down" if mineru_required else "warning"),
        f"{mineru_host}:{mineru_port} " + ("可连接" if mineru_ok else "不可连接"),
    )

    bge_path_exists = bool(embedding_config.bge_m3_path and Path(embedding_config.bge_m3_path).is_dir())
    reranker_path_exists = bool(
        reranker_config.bge_reranker_large and Path(reranker_config.bge_reranker_large).is_dir()
    )
    try:
        import torch
        cuda_available = torch.cuda.is_available()
    except Exception:
        cuda_available = False
    device_mismatch = embedding_config.bge_device.startswith("cuda") and not cuda_available
    model_status = "warning" if device_mismatch or not bge_path_exists or not reranker_path_exists else "up"
    if device_mismatch:
        model_detail = "BGE_DEVICE 配置为 CUDA，但当前 PyTorch 未检测到 CUDA"
    elif not bge_path_exists or not reranker_path_exists:
        model_detail = "BGE-M3 或 Reranker 本地模型目录不存在"
    else:
        model_detail = f"模型目录存在；BGE 设备：{embedding_config.bge_device}"
    services["local_models"] = _service_item("本地 BGE 模型", True, model_status, model_detail)

    mcp_enabled = os.getenv("MCP_ENABLED", "True").lower() in ("1", "true", "yes")
    mcp_url = os.getenv("MCP_MODELSCOPE_URL") or os.getenv("MCP_DASHSCOPE_BASE_URL")
    if not mcp_enabled:
        services["mcp"] = _service_item("MCP 联网搜索", False, "disabled", "已通过 MCP_ENABLED 关闭")
    elif mcp_url:
        services["mcp"] = _service_item("MCP 联网搜索", False, "up", "已配置，实际连通性在查询时验证")
    else:
        services["mcp"] = _service_item("MCP 联网搜索", False, "warning", "已启用但未配置服务地址")

    return {"checked_at": datetime.now().isoformat(timespec="seconds"), "services": services}


# --------------------------
# 静态页面路由：返回文件导入前端页面import.html
# 访问地址：http://localhost:8000/import.html
# --------------------------
@app.get("/import.html", response_class=FileResponse)
async def get_import_page():
    """返回文件导入前端页面：import.html"""
    # 拼接HTML文件绝对路径，基于项目根目录定位
    html_abs_path = PROJECT_ROOT / "app/import_process/page/import.html"
    # 日志记录页面访问的文件路径，方便排查文件不存在问题
    logger.info(f"前端页面访问，文件绝对路径：{html_abs_path}")

    # 校验文件是否存在，不存在则抛出404异常
    if not os.path.exists(html_abs_path):
        logger.error(f"前端页面文件不存在，路径：{html_abs_path}")
        raise HTTPException(status_code=404, detail="import.html page not found")

    # 以FileResponse返回HTML文件，浏览器自动渲染
    return FileResponse(
        path=html_abs_path,
        media_type="text/html"  # 显式指定媒体类型为HTML，确保浏览器正确解析
    )


@app.get("/status.html", response_class=FileResponse)
async def get_status_page():
    """返回本机服务状态页面。"""
    html_abs_path = PROJECT_ROOT / "app/import_process/page/status.html"
    if not os.path.exists(html_abs_path):
        raise HTTPException(status_code=404, detail="status.html page not found")
    return FileResponse(path=html_abs_path, media_type="text/html")


@app.get("/service-status", summary="本机服务状态")
async def get_service_status():
    """检查本机依赖连通性和模型配置，仅执行只读操作。"""
    return _get_service_status()


# --------------------------
# 后台任务：LangGraph全流程执行
# 独立于主请求线程，由BackgroundTasks触发，避免阻塞接口响应
# --------------------------
def run_graph_task(task_id: str, local_dir: str, local_file_path: str):
    """
    LangGraph全流程执行后台任务
    核心流程：初始化状态 → 流式执行图节点 → 实时更新任务状态 → 异常捕获
    任务状态更新：pending → processing → completed/failed
    节点进度更新：每完成一个节点，将节点名加入done_list，供前端轮询查看

    :param task_id: 全局唯一任务ID，关联单个文件的全流程处理
    :param local_dir: 该任务的本地文件存储目录（含临时文件/解析结果）
    :param local_file_path: 上传文件的本地绝对路径
    """
    try:
        # 延迟加载导入图：服务状态页和上传接口可先启动，实际处理任务时再初始化模型相关依赖。
        from app.import_process.agent.main_graph import kb_import_app

        # 1. 更新任务全局状态为：处理中
        update_task_status(task_id, "processing")
        logger.info(f"[{task_id}] 开始执行LangGraph全流程，本地文件路径：{local_file_path}")

        # 2. 初始化LangGraph状态：加载默认状态 + 注入当前任务的核心参数
        init_state = get_default_state()
        init_state["task_id"] = task_id  # 任务ID关联
        init_state["local_dir"] = local_dir  # 任务本地目录
        init_state["local_file_path"] = local_file_path  # 上传文件本地路径

        # 3. 流式执行LangGraph全流程（stream模式：实时获取每个节点的执行结果）
        for event in kb_import_app.stream(init_state):
            for node_name, node_result in event.items():
                # 记录每个节点完成的日志，包含任务ID和节点名，方便追踪执行顺序
                logger.info(f"[{task_id}] LangGraph节点执行完成：{node_name}")
                # 将完成的节点名加入【已完成列表】，前端轮询/status/{task_id}可实时获取
                add_done_task(task_id, node_name)

        # 4. 全流程执行完成，更新任务全局状态为：已完成
        update_task_status(task_id, "completed")
        logger.info(f"[{task_id}] LangGraph全流程执行完毕，任务完成")

    except Exception as e:
        # 5. 捕获全流程异常，更新任务全局状态为：失败，并记录错误日志（含堆栈）
        update_task_status(task_id, "failed")
        logger.error(f"[{task_id}] LangGraph全流程执行失败，异常信息：{str(e)}", exc_info=True)


# --------------------------
# 核心接口：文件上传接口
# 支持多文件上传，核心流程：接收文件 → 本地保存 → MinIO上传 → 启动后台任务
# 访问地址：http://localhost:8000/upload （POST请求，form-data格式传参）
# --------------------------
@app.post("/upload", summary="文件上传接口", description="支持多文件批量上传，自动触发知识库导入全流程")
async def upload_files(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    """
    文件上传核心接口
    1. 接收前端上传的多文件（PDF/MD为主）
    2. 按「日期/任务ID」分层保存到本地输出目录，避免文件冲突
    3. 将文件上传至MinIO对象存储，做持久化保存
    4. 为每个文件生成唯一TaskID，启动独立的LangGraph后台处理任务
    5. 实时更新任务状态，供前端轮询监控进度

    :param background_tasks: FastAPI后台任务对象，用于异步执行LangGraph流程
    :param files: 前端上传的文件列表（form-data格式）
    :return: 包含上传结果和所有任务ID的JSON响应
    """
    # 1. 构建本地存储根目录：项目根目录/output/YYYYMMDD（按日期分层，方便管理）
    date_based_root_dir = os.path.join(PROJECT_ROOT / "output", datetime.now().strftime("%Y%m%d"))
    # 初始化任务ID列表，用于返回给前端（一个文件对应一个TaskID）
    task_ids = []

    # 2. 遍历处理每个上传的文件（多文件批量处理，各自独立生成TaskID）
    for file in files:
        # 生成全局唯一TaskID（UUID4），作为单个文件的全流程标识
        task_id = str(uuid.uuid4())
        task_ids.append(task_id)
        logger.info(f"[{task_id}] 开始处理上传文件，文件名：{file.filename}，文件类型：{file.content_type}")

        # 3. 标记「文件上传」阶段为「运行中」，前端轮询可查
        add_running_task(task_id, "upload_file")

        # 4. 构建该任务的本地独立目录：output/YYYYMMDD/TaskID，避免多文件重名冲突
        task_local_dir = os.path.join(date_based_root_dir, task_id)
        os.makedirs(task_local_dir, exist_ok=True)  # 目录不存在则创建，存在则不做处理
        # 构建上传文件的本地保存绝对路径
        local_file_abs_path = os.path.join(task_local_dir, file.filename)

        # 5. 将上传的文件保存到本地临时目录（后续MinIO上传/文件解析均基于此文件）
        with open(local_file_abs_path, "wb") as file_buffer:
            shutil.copyfileobj(file.file, file_buffer)
        logger.info(f"[{task_id}] 文件已保存至本地，路径：{local_file_abs_path}")

        # 6. 将本地文件上传至MinIO对象存储，做持久化保存
        # 从环境变量获取MinIO的PDF存储目录配置
        minio_pdf_base_dir = os.getenv("MINIO_PDF_DIR", "pdf_files")  # 缺省值：pdf_files
        # 构建MinIO中的文件对象名：配置目录/YYYYMMDD/文件名（按日期分层，和本地一致）
        minio_object_name = f"{minio_pdf_base_dir}/{datetime.now().strftime('%Y%m%d')}/{file.filename}"
        try:
            # 获取MinIO客户端实例
            minio_client = get_minio_client()
            if minio_client is None:
                # MinIO客户端获取失败，抛出500服务异常
                raise HTTPException(status_code=500,
                                    detail="MinIO service connection failed, please check MinIO config")
            # 从环境变量获取MinIO的桶名配置
            minio_bucket_name = os.getenv("MINIO_BUCKET_NAME", "kb-import-bucket")  # 缺省值：kb-import-bucket

            # 本地文件上传至MinIO（同名文件会自动覆盖，保证文件最新）
            minio_client.fput_object(
                bucket_name=minio_bucket_name,
                object_name=minio_object_name,
                file_path=local_file_abs_path,
                content_type=file.content_type  # 传递文件原始MIME类型
            )
            logger.info(f"[{task_id}] 文件已成功上传至MinIO，桶名：{minio_bucket_name}，对象名：{minio_object_name}")
        except Exception as e:
            # MinIO上传失败，记录警告日志（不中断后续流程，本地文件仍可继续处理）
            logger.warning(f"[{task_id}] 文件上传MinIO失败，将继续执行本地处理流程，异常信息：{str(e)}", exc_info=True)

        # 7. 标记「文件上传」阶段为「已完成」，前端轮询可查
        add_done_task(task_id, "upload_file")

        # 8. 将LangGraph全流程处理加入FastAPI后台任务（异步执行，不阻塞当前接口响应）
        background_tasks.add_task(run_graph_task, task_id, task_local_dir, local_file_abs_path)
        logger.info(f"[{task_id}] 已将LangGraph全流程加入后台任务，任务已启动")

    # 9. 所有文件处理完毕，返回上传成功信息和所有TaskID（前端基于TaskID轮询进度）
    logger.info(f"多文件上传处理完毕，共处理{len(files)}个文件，生成TaskID列表：{task_ids}")
    return {
        "code": 200,
        "message": f"Files uploaded successfully, total: {len(files)}",
        "task_ids": task_ids
    }


# --------------------------
# 核心接口：任务状态查询接口
# 前端轮询此接口获取单个任务的处理进度和状态
# 访问地址：http://localhost:8000/status/{task_id} （GET请求）
# --------------------------
@app.get("/status/{task_id}", summary="任务状态查询", description="根据TaskID查询单个文件的处理进度和全局状态")
async def get_task_progress(task_id: str):
    """
    任务状态查询接口
    前端轮询此接口（如每秒1次），获取任务的实时处理进度
    返回数据均来自内存中的任务管理字典（task_utils.py），高性能无IO

    :param task_id: 全局唯一任务ID（由/upload接口返回）
    :return: 包含任务全局状态、已完成节点、运行中节点的JSON响应
    """
    # 构造任务状态返回体
    task_status_info: Dict[str, Any] = {
        "code": 200,
        "task_id": task_id,
        "status": get_task_status(task_id),  # 任务全局状态：pending/processing/completed/failed
        "done_list": get_done_task_list(task_id),  # 已完成的节点/阶段列表
        "running_list": get_running_task_list(task_id)  # 正在运行的节点/阶段列表
    }
    # 记录状态查询日志，方便追踪前端轮询情况
    logger.info(
        f"[{task_id}] 任务状态查询，当前状态：{task_status_info['status']}，已完成节点：{task_status_info['done_list']}")
    return task_status_info


# --------------------------
# 服务启动入口
# 直接运行此脚本即可启动FastAPI服务，无需额外执行uvicorn命令
# --------------------------
if __name__ == "__main__":
    """服务启动入口：本地开发环境直接运行"""
    logger.info("File Import Service 服务启动中...")
    # 启动uvicorn服务，绑定本地IP和8000端口，关闭自动重载（生产环境建议用workers多进程）
    uvicorn.run(
        app=app,
        host="0.0.0.0",  # 监听所有网卡，允许局域网访问
        port=8000  # 服务端口
    )
