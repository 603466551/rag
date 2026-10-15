# GitHub 首次发布清单

## 创建仓库时填写

- Repository name：建议 `local-knowledge-base`（也可按你的项目命名）。
- Description：**可不填**。建议填写：`本地文档导入、向量检索与智能问答服务。`
- Visibility：含模型服务配置和业务文档时，建议先选 **Private**。
- 不要勾选 GitHub 自动创建的 README、`.gitignore` 或 License；这些文件已在本地准备好。

## 创建仓库后执行

将 `<你的仓库地址>` 替换为 GitHub 页面提供的 HTTPS 或 SSH 地址：

```powershell
Set-Location D:\kaifa\PythonProject16
git init
git add .
git status
git commit -m "Initial commit: local knowledge base service"
git branch -M main
git remote add origin <你的仓库地址>
git push -u origin main
```

## 推送前必查

```powershell
git status
git check-ignore .env doc\设备手册汇总.zip
```

预期：`.env` 与 `doc/` 中的大型本地样本文档会被忽略；`.env.example`、`README.md`、`DEPLOYMENT.md`、`scripts/` 和 `app/` 会被提交。

若 `git status` 中出现密钥、`.env`、`logs/`、`output/`、`.venv/` 或大量 PDF/ZIP 文件，先停止并检查 `.gitignore`，不要继续推送。
