# 依赖与许可证边界

本项目没有复制参考仓库的源码；参考项目的许可证核对和不采用范围见
[`reference-selection.md`](reference-selection.md)。Python 和前端依赖分别由
`backend/requirements.lock`、`backend/pyproject.toml`、`apps/web/package-lock.json`
和 `apps/web/package.json` 锁定。

本轮基于锁文件生成的依赖清单见 [`license-inventory.md`](license-inventory.md)。
它保留安装元数据中无法判断的 `UNKNOWN`，发布前必须逐项复核；依赖变更后重新运行：

```powershell
backend\\.venv\\Scripts\\python.exe scripts\\generate_license_inventory.py
```

交付或重新打包前，应从锁文件生成当前环境的完整许可证清单，不凭记忆补写版本或许可证：

```powershell
backend\.venv\Scripts\python.exe -m pip install pip-licenses
backend\.venv\Scripts\python.exe -m piplicenses --from=mixed --format=markdown
npm --prefix apps/web query "*" --json
```

仓库只保留官方文档/标准来源的摘要和链接；来源的版权、访问日期与复用边界记录在
[`knowledge-base.md`](knowledge-base.md)。这些记录不授予将企业资料、第三方站点内容或
远程页面复制到发布内容的权限。
