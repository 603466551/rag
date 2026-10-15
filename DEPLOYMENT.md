# 本机实施部署手册

## 1. 目标与范围

本手册说明如何在 Windows 本机启动、验证和排查本项目。项目上传 PDF/Markdown，将其解析、图片摘要、切分和向量化后写入 Milvus，再通过浏览器检索问答。

```text
PDF / Markdown
  → 导入服务 :8000
  → 本机 MinerU API :8100（PDF 时）
  → 图片摘要模型 + MinIO :9000
  → BGE-M3 本地模型 → Milvus :19530

浏览器提问
  → 查询服务 :8001
  → Milvus :19530 + MongoDB :27017
  → BGE-M3 / BGE-Reranker 本地模型
  → 文本模型服务（以及可选 MCP 搜索）
```

## 2. 哪些组件在本机运行

### 本机常驻基础服务

这些服务必须在启动 Python 应用前已运行；本仓库只连接它们，并不负责启动。

| 组件 | 应用连接地址 | 管理页面 | 用途 |
| --- | --- | --- | --- |
| Milvus | `http://localhost:19530` | Attu: <http://localhost:9091/webui/> | 向量入库和检索 |
| MinIO | `localhost:9000`（S3 API） | <http://localhost:9001/browser> | 原始文件、图片和图片 URL |
| MongoDB | `mongodb://localhost:27017` | - | 问答会话历史 |
| MinerU 本地 API | `http://127.0.0.1:8100` | 由 MinerU 自身提供 | PDF 转 Markdown 与图片资源 |

`9091` 是 Attu 页面而不是 Milvus API；`9001` 是 MinIO 控制台而不是 S3 API。应用应分别使用 `19530` 和 `9000`。

### 本机 Python 应用

| 应用 | 模块入口 | 端口 | 页面 |
| --- | --- | --- | --- |
| 文档导入服务 | `app.import_process.api.file_import_service` | `8000` | `/import.html` |
| 智能查询服务 | `app.query_process.api.query_service` | `8001` | `/chat.html` |

两项服务绑定 `127.0.0.1`，仅供本机浏览器使用。

### 本机模型文件

| 模型 | `.env` 配置项 | 用途 |
| --- | --- | --- |
| BGE-M3 | `BGE_M3_PATH` | 文档切片、产品名称与查询文本的稠密/稀疏向量 |
| BGE-Reranker-Large | `BGE_RERANKER_LARGE` | 检索结果重排序 |

模型文件由项目下载脚本保存到 `.env` 指定的本机目录。不要将模型文件提交到版本库。

### 外部或可选依赖

| 依赖 | 配置项 | 用途 |
| --- | --- | --- |
| OpenAI 兼容模型服务 | `OPENAI_BASE_URL`、`OPENAI_API_KEY` | 名称识别、图片摘要、最终问答 |
| MCP 搜索服务 | `MCP_*` | 可选联网搜索 |

模型服务地址由 `.env` 决定，可能是局域网或云端。API Key 不能写入文档、日志或版本库。

> 当前图谱查询节点是占位逻辑，并未实际调用 Neo4j；Neo4j 不是当前导入和查询服务的启动前置条件。

## 3. 项目目录

| 目录 | 内容 |
| --- | --- |
| `app/import_process/` | 上传、PDF/MD 导入图与节点 |
| `app/query_process/` | 检索、问答图与查询 API |
| `app/conf/` | 从 `.env` 读取的配置对象 |
| `app/clients/` | Milvus、MinIO、MongoDB 客户端 |
| `app/lm/` | 嵌入、重排和大模型调用 |
| `prompts/` | 提示词模板 |
| `doc/` | 示例文档 |
| `output/` | 上传、解析与切分产物；运行生成，不提交 |
| `logs/` | 日志；运行生成，不提交 |
| `.env` | 本机配置和密钥；仅本机维护，不提交 |

## 4. 首次部署

### 4.1 要求

- Windows 10/11、Python 3.11+、`uv`；
- 10 GB 以上可用磁盘空间（本地模型缓存）；
- Milvus、MinIO、MongoDB、MinerU 本地 API 已启动；
- 可访问 `.env` 中配置的模型 API。

安装锁定依赖：

```powershell
Set-Location D:\kaifa\PythonProject16
uv sync
```

### 4.2 下载模型（仅模型目录不存在时）

```powershell
uv run python app/tool/download_bgem3.py
uv run python app/tool/download_reranker.py
```

下载后确认 `.env` 中的 `BGE_M3_PATH`、`BGE_RERANKER_LARGE` 指向真实模型目录。

### 4.3 配置 `.env`

| 分组 | 必需配置 | 本机说明 |
| --- | --- | --- |
| 大模型 | `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_DEFAULT_MODEL`、`LLM_DEFAULT_TEMPERATURE`、`VL_MODEL` | OpenAI 兼容文本/视觉模型 |
| Milvus | `MILVUS_URL`、`CHUNKS_COLLECTION`、`ITEM_NAME_COLLECTION`、`EMBEDDING_DIM` | 使用 `http://localhost:19530` |
| MinIO | `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET_NAME`、`MINIO_IMG_DIR`、`MINIO_SECURE` | 使用 `localhost:9000`，不含协议 |
| MongoDB | `MONGO_URL`、`MONGO_DB_NAME` | 使用 `mongodb://localhost:27017` |
| BGE | `BGE_M3_PATH`、`BGE_DEVICE`、`BGE_FP16`、`BGE_RERANKER_LARGE`、`BGE_RERANKER_DEVICE`、`BGE_RERANKER_FP16` | 目录必须存在 |
| MinerU | `MINERU_MODE`、`MINERU_LOCAL_BASE_URL`、`MINERU_LOCAL_TIER` | 当前本机方式为 `local_api`，默认地址 `http://127.0.0.1:8100` |
| MCP | `MCP_ENABLED` 及相关 `MCP_*` | 不需要联网搜索时设为 `False` |

MinIO 桶名只能使用小写字母、数字、连字符和英文句点，不能以句点或连字符结尾。例如：

```dotenv
MINIO_BUCKET_NAME=knowledge-base-files
```

## 5. 推荐启动顺序

1. 确认 Milvus、MinIO、MongoDB、MinerU API 已运行。
2. 确认模型目录存在，并完成 CPU/GPU 检查。
3. 启动导入服务。
4. 启动查询服务。
5. 上传一个小 PDF；导入完成后再测试问答。

### 导入服务（终端 A）

```powershell
Set-Location D:\kaifa\PythonProject16
uv run python -m app.import_process.api.file_import_service
```

成功标志：

```text
Uvicorn running on http://127.0.0.1:8000
```

打开 <http://127.0.0.1:8000/import.html>。

### 查询服务（终端 B）

```powershell
Set-Location D:\kaifa\PythonProject16
uv run python -m app.query_process.api.query_service
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

预期：

```json
{"ok": true}
```

## 6. 首次业务验证

1. 打开导入页面，上传一个 `.pdf` 或 `.md` 文件。
2. 页面或上传接口返回 `task_id`，等待任务状态为 `completed`。
3. 导入链路会完成解析、图片处理、切分、名称识别、BGE-M3 向量化和 Milvus 入库。
4. 打开查询页面，对刚导入文档中的内容提问。

API 示例：

```powershell
curl.exe -X POST http://127.0.0.1:8000/upload -F "files=@D:\path\to\manual.pdf"
```

```powershell
$body = @{ query = "这份说明书如何设置？"; is_stream = $false } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8001/query -Method Post -ContentType "application/json" -Body $body
```

## 7. 一键启动与状态看板

项目提供 Windows PowerShell 启动脚本：

```powershell
Set-Location D:\kaifa\PythonProject16
.\scripts\start-local.ps1
```

脚本行为：

1. 检查 Milvus `19530`、MinIO `9000`、MongoDB `27017`、MinerU `8100`；
2. 仅当 `8000` 或 `8001` 未被占用时，后台启动对应 Python 服务；
3. 输出日志到 `logs/services/`；
4. 打开本机服务状态页。

状态页面地址为 <http://127.0.0.1:8000/status.html>，每 5 秒刷新一次，报告应用、基础服务、MinerU、本地模型和 MCP 配置状态。检测不显示密钥，也不修改数据。

若不自动打开浏览器：

```powershell
.\scripts\start-local.ps1 -NoBrowser
```

## 8. 启动前检查

### 基础服务端口

```powershell
@(19530, 9000, 27017, 8100) | ForEach-Object {
  "localhost:$($_) = " + (Test-NetConnection localhost -Port $_ -InformationLevel Quiet)
}
```

四项均应为 `True`。MinerU 仅在导入 PDF 时必需。

### GPU / CPU 配置

```powershell
uv run python -c "import torch; print(torch.cuda.is_available())"
```

输出为 `False` 时必须使用：

```dotenv
BGE_DEVICE=cpu
BGE_FP16=0
BGE_RERANKER_DEVICE=cpu
BGE_RERANKER_FP16=0
```

当前环境检测为 `False`，但现有 `BGE_DEVICE=cuda:0`，应在首次实际导入前改为 `cpu`；否则 BGE-M3 加载会失败。

### 快速检查

```powershell
uv run python test/06-test-local-mineru-cli.py
uv run python test/07-test-mcp-search-tool.py
```

## 9. PyCharm 运行与调试

建议创建两个“Python 模块”配置：

| 配置 | 模块名 | 工作目录 |
| --- | --- | --- |
| `file_import_service` | `app.import_process.api.file_import_service` | `D:\kaifa\PythonProject16` |
| `query_service` | `app.query_process.api.query_service` | `D:\kaifa\PythonProject16` |

点击绿色运行按钮启动。不要在终端和 PyCharm 中同时启动同一个服务。

当前 PyCharm 调试器与 Uvicorn 存在兼容问题：调试器替换 `asyncio.run()` 后，不支持 Uvicorn 传入的 `loop_factory`，会出现：

```text
TypeError: _patch_asyncio.<locals>.run() got an unexpected keyword argument 'loop_factory'
```

这是 IDE 调试器问题，不是服务代码问题。优先升级 PyCharm；升级前请使用“运行”或 PowerShell 命令启动。

## 10. 常见故障

| 现象 | 原因与处理 |
| --- | --- |
| `ModuleNotFoundError: No module named 'app'` | 终端直接执行深层脚本。改用 `python -m app...`。 |
| `WinError 10048` | 8000/8001 被旧服务占用。不要重复启动同一服务。 |
| `invalid bucket name` | MinIO 桶名有中文符号、尾部句点等。使用合法桶名。 |
| `loop_factory` 调试报错 | PyCharm Debug 与 Uvicorn 不兼容。升级 IDE 或用 Run。 |
| BGE-M3 CUDA 报错 | GPU 配置与 PyTorch 不匹配。无 CUDA 时设为 CPU。 |
| `transformers` 导入时报 PyTorch 版本为 `None` | `.venv` 中存在不完整或重复的 Torch 安装。先停止服务，再执行 `uv sync --reinstall` 重建依赖。 |
| PDF 解析失败 | MinerU API 未运行或 `MINERU_LOCAL_BASE_URL` 不正确。 |
| 图片访问失败 | 检查 MinIO 的 `9000`、桶名、凭据和对象策略。 |
| MCP 搜索错误 | 补齐 MCP 配置，或设 `MCP_ENABLED=False`。 |

检查端口归属：

```powershell
Get-NetTCPConnection -LocalPort 8000,8001 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalPort, OwningProcess
```

## 11. 停止与重启

在服务所在终端按 `Ctrl+C`。停止 Python 服务不会删除 Milvus、MinIO、MongoDB 中的数据。重启时按第 5 节的顺序执行。
