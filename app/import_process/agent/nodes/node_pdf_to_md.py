# 系统库
import os
import sys
import time
import hashlib
import mimetypes
import requests
import zipfile
import shutil
import subprocess
from pathlib import Path

# 项目内部库
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.utils.format_utils import format_state
from app.utils.task_utils import add_running_task, add_done_task
from app.conf.mineru_config import mineru_config
from app.core.logger import logger  # 统一日志工具

# MinerU配置（缓存配置信息）
MINERU_BASE_URL = mineru_config.base_url
MINERU_API_TOKEN = mineru_config.api_key


def _resolve_mineru_cli() -> Path:
    """定位本机 magic-pdf 可执行文件，优先使用显式配置。"""
    candidates = [mineru_config.cli_path]
    python_dir = Path(sys.executable).resolve().parent
    candidates.extend([
        str(python_dir / "magic-pdf.exe"),
        str(python_dir / "magic-pdf"),
        shutil.which("magic-pdf"),
    ])

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)

    raise FileNotFoundError(
        "未找到本地 MinerU CLI。请在 .env 设置 MINERU_CLI_PATH，"
        "或确认当前 Python 虚拟环境已安装 magic-pdf。"
    )


def _find_local_markdown(output_dir_obj: Path, pdf_stem: str) -> str:
    """从本地 MinerU 输出目录中定位当前文档的 Markdown 文件。"""
    md_file_list = list(output_dir_obj.rglob("*.md"))
    if not md_file_list:
        raise FileNotFoundError(f"本地 MinerU 未生成 Markdown 文件：{output_dir_obj}")

    for md_file in md_file_list:
        if md_file.stem == pdf_stem:
            return str(md_file.resolve())
    for md_file in md_file_list:
        if md_file.name.lower() == "full.md":
            return str(md_file.resolve())
    return str(md_file_list[0].resolve())


def step_2_run_local_mineru(pdf_path_obj: Path, output_dir_obj: Path) -> str:
    """调用本机 magic-pdf，将 PDF 解析为 Markdown 并返回结果路径。"""
    cli_path = _resolve_mineru_cli()
    parse_root = Path(mineru_config.output_root) if mineru_config.output_root else output_dir_obj
    parse_root = parse_root / "mineru" / pdf_path_obj.stem
    parse_root.mkdir(parents=True, exist_ok=True)

    command = [
        str(cli_path),
        "--path", str(pdf_path_obj),
        "--output-dir", str(parse_root),
        "--method", "auto",
    ]
    logger.info(f"[本地MinerU] 开始解析：{pdf_path_obj.name}，输出目录：{parse_root}")

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"[本地MinerU] 解析超时（3600 秒）：{pdf_path_obj.name}") from exc
    except OSError as exc:
        raise RuntimeError(f"[本地MinerU] 无法启动 magic-pdf：{exc}") from exc

    if result.returncode != 0:
        output_tail = (result.stdout or "")[-2000:]
        raise RuntimeError(
            f"[本地MinerU] 解析失败，退出码：{result.returncode}，"
            f"最近输出：{output_tail}"
        )

    md_path = _find_local_markdown(parse_root, pdf_path_obj.stem)
    logger.info(f"[本地MinerU] 解析完成，Markdown：{md_path}")
    return md_path


def _local_mineru_api_url(path: str) -> str:
    """构造本机 MinerU API 地址，避免调用本机以外的服务。"""
    if not mineru_config.local_base_url.startswith(("http://127.0.0.1", "http://localhost")):
        raise ValueError(
            "MINERU_LOCAL_BASE_URL 必须指向本机地址（127.0.0.1 或 localhost）。"
        )
    return f"{mineru_config.local_base_url}{path}"


def _upload_file_to_local_mineru(pdf_path_obj: Path) -> str:
    """按本机 MinerU 的上传协议保存文件，并返回可供解析的 file_id。"""
    file_size = pdf_path_obj.stat().st_size
    mime_type = mimetypes.guess_type(pdf_path_obj.name)[0] or "application/octet-stream"
    sha256 = hashlib.sha256()
    with pdf_path_obj.open("rb") as pdf_file:
        for chunk in iter(lambda: pdf_file.read(1024 * 1024), b""):
            sha256.update(chunk)
    sha256sum = sha256.hexdigest()
    create_upload_response = requests.post(
        _local_mineru_api_url("/v1/uploads"),
        json={
            "filename": pdf_path_obj.name,
            "bytes": file_size,
            "mime_type": mime_type,
            "purpose": "parse",
            "sha256sum": sha256sum,
        },
        timeout=30,
    )
    if create_upload_response.status_code != 200:
        raise RuntimeError(
            f"[本地MinerU API] 创建上传任务失败，HTTP {create_upload_response.status_code}："
            f"{create_upload_response.text[-1000:]}"
        )

    create_upload_payload = create_upload_response.json()
    if not isinstance(create_upload_payload, dict):
        raise RuntimeError("[本地MinerU API] 创建上传任务返回了非对象格式的响应。")

    # MinerU 会按文件哈希复用已上传的文件。此时 file 已可直接用于解析，
    # 不能再 PUT 内容或调用 complete，否则服务端会返回错误。
    if create_upload_payload.get("status") == "completed":
        existing_file = create_upload_payload.get("file") or {}
        existing_file_id = existing_file.get("id") if isinstance(existing_file, dict) else None
        if existing_file_id:
            logger.info(f"[本地MinerU API] 复用已上传文件：{pdf_path_obj.name}")
            return existing_file_id
        raise RuntimeError("[本地MinerU API] 已上传文件响应未返回 file_id。")

    upload_id = create_upload_payload.get("id")
    if not upload_id:
        raise RuntimeError("[本地MinerU API] 创建上传任务成功但未返回 upload_id。")

    with pdf_path_obj.open("rb") as pdf_file:
        upload_response = requests.put(
            _local_mineru_api_url(f"/v1/uploads/{upload_id}/content"),
            data=pdf_file,
            headers={"Content-Type": mime_type},
            timeout=120,
        )
    if upload_response.status_code != 200:
        raise RuntimeError(
            f"[本地MinerU API] 上传文件失败，HTTP {upload_response.status_code}："
            f"{upload_response.text[-1000:]}"
        )

    complete_response = requests.post(
        _local_mineru_api_url(f"/v1/uploads/{upload_id}/complete"),
        json={"sha256sum": sha256sum},
        timeout=30,
    )
    if complete_response.status_code != 200:
        raise RuntimeError(
            f"[本地MinerU API] 完成上传失败，HTTP {complete_response.status_code}："
            f"{complete_response.text[-1000:]}"
        )

    file_id = (complete_response.json().get("file") or {}).get("id")
    if not file_id:
        raise RuntimeError("[本地MinerU API] 完成上传但未返回 file_id。")
    return file_id


def step_2_parse_with_local_mineru_api(pdf_path_obj: Path, output_dir_obj: Path) -> str:
    """通过本机 MinerU API 上传解析文件并取得包含图片资源的 ZIP 结果。"""
    create_job_url = _local_mineru_api_url("/v1/parse/jobs")
    logger.info(f"[本地MinerU API] 上传解析文件：{pdf_path_obj.name}")
    try:
        file_id = _upload_file_to_local_mineru(pdf_path_obj)
    except requests.RequestException as exc:
        raise RuntimeError(f"[本地MinerU API] 无法连接服务：{exc}") from exc

    request_body = {
        "files": [{"source": {"type": "file_id", "file_id": file_id}}],
        "tier": mineru_config.local_tier,
        "ocr_mode": "auto",
        "output_formats": ["markdown", "zip"],
    }
    logger.info(
        f"[本地MinerU API] 提交解析任务：{pdf_path_obj.name}，"
        f"档位：{mineru_config.local_tier}"
    )
    try:
        response = requests.post(
            create_job_url,
            json=request_body,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"[本地MinerU API] 无法连接服务：{exc}") from exc

    if response.status_code != 202:
        raise RuntimeError(
            f"[本地MinerU API] 创建解析任务失败，HTTP {response.status_code}：{response.text[-1000:]}"
        )
    job_id = response.json().get("job_id")
    if not job_id:
        raise RuntimeError("[本地MinerU API] 创建解析任务成功但未返回 job_id。")

    job_url = _local_mineru_api_url(f"/v1/parse/jobs/{job_id}")
    deadline = time.monotonic() + mineru_config.local_timeout_seconds
    while time.monotonic() < deadline:
        try:
            job_response = requests.get(job_url, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f"[本地MinerU API] 查询解析任务失败：{exc}") from exc

        if job_response.status_code != 200:
            raise RuntimeError(
                f"[本地MinerU API] 查询解析任务失败，HTTP {job_response.status_code}："
                f"{job_response.text[-1000:]}"
            )

        job = job_response.json()
        job_status = job.get("status")
        file_result = (job.get("files") or [{}])[0]
        file_status = file_result.get("status")
        if job_status in {"failed", "canceled"} or file_status == "failed":
            error = file_result.get("error") or {}
            error_message = error.get("message", "无详细错误信息")
            raise RuntimeError(f"[本地MinerU API] 解析失败：{error_message}")

        output_files = file_result.get("output_files") or {}
        zip_file = output_files.get("zip") or {}
        zip_file_id = zip_file.get("file_id")
        if job_status in {"completed", "partial"} and file_status == "completed":
            if not zip_file_id:
                raise RuntimeError("[本地MinerU API] 解析完成但未返回 ZIP 结果文件。")
            zip_url = _local_mineru_api_url(f"/v1/files/{zip_file_id}/content")
            return step_3_download_and_extract(zip_url, output_dir_obj, pdf_path_obj.stem)

        logger.debug(
            f"[本地MinerU API] 任务处理中：job_id={job_id}，"
            f"任务状态={job_status}，文件状态={file_status}"
        )
        time.sleep(mineru_config.local_poll_interval)

    raise TimeoutError(
        f"[本地MinerU API] 解析超时（{mineru_config.local_timeout_seconds} 秒）：{pdf_path_obj.name}"
    )


def step_1_validate_paths(state):
    """
    步骤1：校验PDF文件路径和输出目录
    核心职责：参数非空校验 | PDF文件有效性校验 | 输出目录自动创建
    返回：合法的PDF文件Path对象、输出目录Path对象
    异常：ValueError(参数缺失)、FileNotFoundError(文件无效)
    """
    log_prefix = "[step_1_validate_paths] "
    pdf_path = state.get("pdf_path", "").strip()
    local_dir = state.get("local_dir", "").strip()

    # 参数非空校验
    if not pdf_path:
        raise ValueError(f"{log_prefix}工作流状态缺失有效参数：pdf_path，当前值：{repr(pdf_path)}")
    if not local_dir:
        raise ValueError(f"{log_prefix}工作流状态缺失有效参数：local_dir，当前值：{repr(local_dir)}")

    # 转换为Path对象统一处理路径
    pdf_path_obj = Path(pdf_path)
    output_dir_obj = Path(local_dir)

    # PDF文件有效性校验（存在且为文件，非目录）
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"{log_prefix}PDF文件不存在，绝对路径：{pdf_path_obj.absolute()}")
    if not pdf_path_obj.is_file():
        raise FileNotFoundError(f"{log_prefix}指定路径非文件（是目录），绝对路径：{pdf_path_obj.absolute()}")

    # 确保输出目录存在，不存在则递归创建
    if not output_dir_obj.exists():
        logger.info(f"{log_prefix}输出目录不存在，自动创建：{output_dir_obj.absolute()}")
        output_dir_obj.mkdir(parents=True, exist_ok=True)

    return pdf_path_obj, output_dir_obj


def step_2_upload_and_poll(pdf_path_obj: Path, output_dir_obj: Path):
    """
    步骤2：上传PDF至MinerU并轮询解析任务状态
    核心流程：配置校验 → 获取上传链接 → 文件上传（含重试） → 任务轮询（直至完成/失败/超时）
    参数：pdf_path_obj-已校验的PDF Path对象；output_dir_obj-输出目录Path对象
    返回：解析结果ZIP包下载链接full_zip_url
    异常：ValueError(配置缺失)、RuntimeError(请求/上传失败)、TimeoutError(任务超时)
    """
    # 前置配置校验，拦截无效配置
    if not MINERU_BASE_URL or not MINERU_API_TOKEN:
        raise ValueError("MinerU配置缺失：请在.env中正确配置MINERU_BASE_URL和MINERU_API_TOKEN")
    logger.info(f"[配置校验] MinerU基础配置加载成功，开始处理文件：{pdf_path_obj.name}")

    # 构造请求头（符合HTTP规范，Bearer鉴权）
    request_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MINERU_API_TOKEN}"
    }

    # 1. 调用批量接口，获取上传Signed URL和任务batch_id
    url_get_upload = f"{MINERU_BASE_URL}/file-urls/batch"
    req_data = {
        "files": [{"name": pdf_path_obj.name}],
        "model_version": "vlm"  # 官方推荐解析模型
    }
    logger.debug(f"[获取上传链接] 调用接口：{url_get_upload}，请求参数：{req_data}")
    resp = requests.post(url=url_get_upload, headers=request_headers, json=req_data, timeout=30)

    # 响应校验：先验HTTP状态，再验业务返回码
    if resp.status_code != 200:
        raise RuntimeError(f"[获取上传链接] 网络请求失败，状态码：{resp.status_code}，响应内容：{resp.text}")

    resp_data = resp.json()
    if resp_data["code"] != 0:
        raise RuntimeError(f"[获取上传链接] API业务错误，返回数据：{resp_data}")

    # 提取核心数据：上传链接和任务唯一标识
    signed_url = resp_data["data"]["file_urls"][0]
    batch_id = resp_data["data"]["batch_id"]
    logger.info(f"[获取上传链接] 成功，batch_id：{batch_id}，上传链接已生成")

    # 2. 读取PDF二进制数据，准备上传
    logger.info(f"[文件上传] 开始读取PDF文件：{pdf_path_obj.name}")
    with open(pdf_path_obj, "rb") as f:
        file_data = f.read()

    # 创建Session（复用TCP连接，禁用代理避免签名验证失败）
    upload_session = requests.Session()
    upload_session.trust_env = False

    try:
        # 首次上传：自动识别文件类型
        put_resp = upload_session.put(url=signed_url, data=file_data, timeout=60)
        # 重试逻辑：首次失败则强制指定PDF的Content-Type
        if put_resp.status_code != 200:
            logger.warning(f"[文件上传] 首次上传失败（状态码：{put_resp.status_code}），强制指定PDF类型重试")
            pdf_headers = {"Content-Type": "application/pdf"}
            put_resp = upload_session.put(url=signed_url, data=file_data, headers=pdf_headers, timeout=60)
            # 重试仍失败则抛出异常
            if put_resp.status_code != 200:
                raise RuntimeError(f"[文件上传] 重试后仍失败，状态码：{put_resp.status_code}，响应内容：{put_resp.text}")
        logger.info(f"[文件上传] 成功，文件{pdf_path_obj.name}已存入云存储")
    except Exception as e:
        raise RuntimeError(f"[文件上传] 网络异常导致上传失败，错误信息：{str(e)}")
    finally:
        # 无论成败，关闭Session释放网络连接，避免资源泄漏
        upload_session.close()

    # 3. 根据batch_id轮询任务状态，直至完成/失败/超时
    poll_url = f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}"
    start_time = time.time()
    timeout_seconds = 600  # 最大超时时间10分钟（适配600页内PDF）
    poll_interval = 3      # 轮询间隔3秒（平衡查询频率和服务端压力）
    logger.info(f"[任务轮询] 开始监控任务状态，batch_id：{batch_id}，最大超时：{timeout_seconds}s")

    while True:
        # 超时检查：超过最大时间直接终止轮询
        elapsed_time = time.time() - start_time
        if elapsed_time > timeout_seconds:
            raise TimeoutError(f"[任务轮询] 超时！任务处理超{int(timeout_seconds)}秒，batch_id：{batch_id}")

        # 发起轮询请求，短超时10秒，异常则重试
        try:
            poll_resp = requests.get(url=poll_url, headers=request_headers, timeout=10)
        except Exception as e:
            logger.warning(f"[任务轮询] 网络请求异常，{poll_interval}秒后重试：{str(e)}")
            time.sleep(poll_interval)
            continue

        # 处理HTTP响应错误：5xx服务端繁忙则重试，其他错误直接抛出
        if poll_resp.status_code != 200:
            if 500 <= poll_resp.status_code < 600:
                logger.warning(f"[任务轮询] 服务端繁忙（状态码：{poll_resp.status_code}），{poll_interval}秒后重试")
                time.sleep(poll_interval)
                continue
            else:
                raise RuntimeError(f"[任务轮询] HTTP请求失败，状态码：{poll_resp.status_code}，响应内容：{poll_resp.text}")

        # 解析轮询结果，校验业务状态
        poll_data = poll_resp.json()
        if poll_data["code"] != 0:
            raise RuntimeError(f"[任务轮询] API业务错误，返回数据：{poll_data}")

        extract_results = poll_data["data"]["extract_result"]
        # 结果暂空，继续轮询
        if not extract_results:
            logger.debug(f"[任务轮询] 结果暂为空，已耗时{int(elapsed_time)}s，继续等待")
            time.sleep(poll_interval)
            continue
        # 解析任务状态，分支处理
        result_item = extract_results[0]
        state_status = result_item["state"]
        # 状态1：任务完成，提取ZIP下载链接
        if state_status == "done":
            logger.info(f"[任务轮询] 解析任务完成！总耗时：{int(elapsed_time)}s，batch_id：{batch_id}")
            full_zip_url = result_item.get("full_zip_url")
            if not full_zip_url:
                raise RuntimeError("[任务轮询] 任务完成但未返回ZIP包下载链接，batch_id：{batch_id}")
            logger.info(f"[任务轮询] 结果ZIP包下载链接：{full_zip_url}...")
            return full_zip_url
        # 状态2：任务失败，提取错误信息抛出
        elif state_status == "failed":
            err_msg = result_item.get("err_msg", "未知错误，无具体信息")
            raise RuntimeError(f"[任务轮询] 解析任务失败，batch_id：{batch_id}，错误信息：{err_msg}")
        # 状态3：处理中，实时打印进度（覆盖当前行）
        else:
            logger.debug(
                f"[任务轮询] 处理中（已耗时{int(elapsed_time)}s），状态：{state_status} | 刷新间隔{poll_interval}s",
                end="\r"
            )
            time.sleep(poll_interval)

def step_3_download_and_extract(zip_url: str, output_dir_obj: Path, pdf_stem: str) -> str:
    """
    步骤3：下载MinerU解析结果ZIP包并解压，提取目标MD文件（重命名统一规范）
    核心流程：下载ZIP → 清理旧目录并解压 → 查找MD文件（按优先级） → 重命名统一为PDF同名
    参数：zip_url-ZIP包下载链接；output_dir_obj-输出目录Path；pdf_stem-PDF无后缀纯名称
    返回：最终MD文件的字符串格式绝对路径
    异常：RuntimeError(下载失败)、FileNotFoundError(无MD文件)
    """
    logger.info(f"===== 开始处理[{pdf_stem}]的MinerU解析结果 =====")
    output_dir_obj.mkdir(parents=True, exist_ok=True)

    # 1. 下载解析结果ZIP包，120秒超时适配大文件
    logger.info(f"[步骤1/4] 开始下载ZIP包，链接：{zip_url}...")
    resp = requests.get(zip_url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"[步骤1/4] ZIP包下载失败，HTTP状态码：{resp.status_code}")

    # 拼接ZIP包保存路径，按PDF名称唯一命名
    zip_save_path = output_dir_obj / f"{pdf_stem}_result.zip"
    with open(zip_save_path, "wb") as f:
        f.write(resp.content)
    logger.info(f"[步骤1/4] ZIP包下载成功，保存路径：{zip_save_path}")

    # 2. 清理旧解压目录并解压ZIP包（避免旧文件干扰，为每个PDF创建专属目录）
    logger.info(f"[步骤2/4] 开始解压ZIP包...")
    extract_target_dir = output_dir_obj / pdf_stem

    # 清理旧目录，异常则警告不终止
    if extract_target_dir.exists():
        try:
            # 递归删除整个目录树，包括目录本身及其所有子目录和文件。
            shutil.rmtree(extract_target_dir)
            logger.info(f"[步骤2/4] 已清理旧的解压目录：{extract_target_dir}")
        except Exception as e:
            logger.warning(f"[步骤2/4] 清理旧目录失败，可能不影响新文件解压：{str(e)}")

    # 重新创建解压目录
    extract_target_dir.mkdir(parents=True, exist_ok=True)

    # 核心解压操作，保留原目录结构
    with zipfile.ZipFile(zip_save_path, 'r') as zip_file_obj:
        zip_file_obj.extractall(extract_target_dir)
    logger.info(f"[步骤2/4] ZIP包解压完成，解压目录：{extract_target_dir}")

    # 3. 递归查找解压目录下所有MD文件（适配子目录结构）
    logger.info(f"[步骤3/4] 开始查找解压目录中的MD文件...")
    md_file_list = list(extract_target_dir.rglob("*.md"))
    if not md_file_list:
        raise FileNotFoundError(f"[步骤3/4] 解压目录中未找到任何.md格式文件：{extract_target_dir}")
    logger.info(f"[步骤3/4] 共找到{len(md_file_list)}个MD文件，按优先级匹配目标文件")

    # 4. 按优先级匹配目标MD文件（同名→full.md→第一个，兜底避免流程中断）
    target_md_file = None
    # 优先级1：与PDF纯名称完全同名的MD文件
    for md_file in md_file_list:
        if md_file.stem == pdf_stem:
            target_md_file = md_file
            logger.info(f"[步骤4/4] 匹配到优先级1目标：与PDF同名的MD文件 {target_md_file.name}")
            break
    # 优先级2：MinerU默认生成的full.md（不区分大小写）
    if not target_md_file:
        for md_file in md_file_list:
            if md_file.name.lower() == "full.md":
                target_md_file = md_file
                logger.info(f"[步骤4/4] 匹配到优先级2目标：MinerU默认文件 {target_md_file.name}")
                break
    # 优先级3：兜底取第一个MD文件
    if not target_md_file:
        target_md_file = md_file_list[0]
        logger.info(f"[步骤4/4] 未匹配到前两级目标，兜底取第一个MD文件 {target_md_file.name}")

    # 重命名MD文件：统一为PDF纯名称，便于后续流程处理（仅不同名时执行）
    if target_md_file.stem != pdf_stem:
        logger.info(f"[步骤4/4] 开始重命名MD文件，统一为PDF同名：{pdf_stem}.md")
        new_md_path = target_md_file.with_name(f"{pdf_stem}.md")
        try:
            # 将磁盘上的文件进行重命名
            target_md_file.rename(new_md_path)
            # 更新变量引用
            target_md_file = new_md_path
            logger.info(f"[步骤4/4] MD文件重命名成功：{pdf_stem}.md")
        except OSError as e:
            logger.warning(f"[步骤4/4] MD文件重命名失败，将使用原文件名继续流程：{str(e)}")

    # 转换为字符串绝对路径返回，适配后续仅支持字符串路径的函数
    final_md_path = str(target_md_file.absolute())
    logger.info(f"===== [{pdf_stem}]解析结果处理完成，最终MD文件路径：{final_md_path} =====")
    return final_md_path

def node_pdf_to_md(state: ImportGraphState) -> ImportGraphState:
    """
    LangGraph工作流节点：PDF转MD核心处理节点
    核心流程：路径校验 → MinerU上传解析 → 结果下载解压 → 读取MD内容并更新工作流状态
    参数：state-工作流状态对象，需包含pdf_path/local_dir/task_id
    返回：更新后的工作流状态，新增md_path/md_content
    """

    # 动态获取函数名避免硬编码
    func_name = sys._getframe().f_code.co_name

    # 节点启动日志，打印当前工作流状态
    logger.debug(f"【{func_name}】节点启动，\n当前工作流状态：{format_state(state)}")

    # 开始：记录节点运行状态
    add_running_task(state["task_id"], func_name)


    try:
        # 步骤1：校验PDF路径和输出目录
        pdf_path_obj, output_dir_obj = step_1_validate_paths(state)

        # 步骤2：优先通过本机常驻 MinerU 服务解析；CLI 和旧 api 模式仅兼容保留。
        if mineru_config.mode == "local_api":
            md_path = step_2_parse_with_local_mineru_api(pdf_path_obj, output_dir_obj)
        elif mineru_config.mode == "cli":
            md_path = step_2_run_local_mineru(pdf_path_obj, output_dir_obj)
        elif mineru_config.mode == "api":
            zip_url = step_2_upload_and_poll(pdf_path_obj, output_dir_obj)
            md_path = step_3_download_and_extract(zip_url, output_dir_obj, pdf_path_obj.stem)
        else:
            raise ValueError(
                f"不支持的 MINERU_MODE={mineru_config.mode!r}，"
                "仅支持 local_api、cli 或 api"
            )

        # 更新工作流状态：记录MD文件路径和内容
        state["md_path"] = md_path
        logger.info(f"【{func_name}】MD文件生成成功，路径：{md_path}")

        # 读取MD文件内容，捕获异常仅警告不终止
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                state["md_content"] = f.read()
            logger.debug(f"【{func_name}】MD文件内容读取成功，内容长度：{len(state['md_content'])}字符")
        except Exception as e:
            logger.error(f"【{func_name}】读取MD文件内容失败：{str(e)}")

        logger.info(f"【{func_name}】节点执行完成，更新后工作流状态键：{list(state.keys())}")

    except Exception as e:
        # 异常日志分级，精准提示配置问题
        logger.error(f"【{func_name}】PDF转MD流程执行失败：{str(e)}", exc_info=True)
        raise  # 抛出异常，终止工作流
    finally:

        # 结束：记录节点运行状态
        add_done_task(state["task_id"], func_name)

        # 节点完成日志，打印当前工作流状态
        logger.debug(f"【{func_name}】节点执行完成，\n更新后工作流状态：{format_state(state)}")

    return state

if __name__ == "__main__":

    # 单元测试：验证PDF转MD全流程
    logger.info("===== 开始node_pdf_to_md节点单元测试 =====")

    from app.utils.path_util import PROJECT_ROOT
    logger.info(f"测试获取根地址：{PROJECT_ROOT}")

    test_pdf_name = os.path.join("doc", "hak180产品安全手册.pdf")
    test_pdf_path = os.path.join(PROJECT_ROOT, test_pdf_name)

    # 构造测试状态
    test_state = create_default_state(
        task_id="test_pdf2md_task_001",
        pdf_path=test_pdf_path,
        local_dir=os.path.join(PROJECT_ROOT, "output")
    )

    node_pdf_to_md(test_state)

    logger.info("===== 结束node_pdf_to_md节点单元测试 =====")
