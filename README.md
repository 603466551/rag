# 本地知识库：文档导入与智能问答

这是一个面向本机 Windows 环境的知识库项目：上传 PDF/Markdown，解析并向量化后写入 Milvus，再通过网页进行检索问答。

完整的实施、部署、启动、验证和排障说明见 [本机实施部署手册](DEPLOYMENT.md)。

## 本机访问地址

| 功能 | 地址 |
| --- | --- |
| 文档导入页面 | <http://127.0.0.1:8000/import.html> |
| 导入 API 文档 | <http://127.0.0.1:8000/docs> |
| 智能问答页面 | <http://127.0.0.1:8001/chat.html> |
| 查询 API 文档 | <http://127.0.0.1:8001/docs> |
| 查询健康检查 | <http://127.0.0.1:8001/health> |
| Attu（Milvus 管理界面） | <http://localhost:9091/webui/> |
| MinIO 控制台 | <http://localhost:9001/browser> |

## 命令行快速启动

在两个 PowerShell 窗口分别执行。必须从项目根目录以**模块方式**启动：

```powershell
Set-Location D:\kaifa\PythonProject16
uv run python -m app.import_process.api.file_import_service
```

```powershell
Set-Location D:\kaifa\PythonProject16
uv run python -m app.query_process.api.query_service
```

不要在终端和 PyCharm 中同时启动同一个服务；导入服务固定使用 `8000`，查询服务固定使用 `8001`。

## 一键启动与状态看板

在 PowerShell 中执行：

```powershell
Set-Location D:\kaifa\PythonProject16
.\scripts\start-local.ps1
```

脚本会检查 Milvus、MinIO、MongoDB、MinerU 的本机端口；未占用时后台启动导入与查询服务，并打开状态页面：<http://127.0.0.1:8000/status.html>。

不希望自动打开浏览器时：

```powershell
.\scripts\start-local.ps1 -NoBrowser
```

## 当前启动前必须处理的事项

当前 Python 环境检测到 `torch.cuda.is_available()` 为 `False`，但 `.env` 配置为 `BGE_DEVICE=cuda:0`。没有可用 CUDA 环境时，请改为：

```dotenv
BGE_DEVICE=cpu
BGE_FP16=0
```

详见 [本机实施部署手册](DEPLOYMENT.md#7-启动前检查)。
