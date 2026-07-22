# 页面实探运行目录

## 默认结构

```text
<run-id>/
├─ batch-status.csv
├─ page-discovery.csv
├─ artifacts/
│  ├─ 可选诊断证据
│  └─ scripts/
│     ├─ test-cases-01-<功能块>.json
│     └─ ...
└─ final-review.md   # 最终Review时创建
```

多页面、多模块或大范围任务额外生成精简`batch-plan.md`。单页面不强制计划，不预创建空白Review。

## 规则

- 首次写入页面事实时由工具自动创建run-dir；用户无需执行独立初始化步骤。
- `page-discovery.csv`必须使用标准表头和CSV writer，记录`已实测/页面观察/DFX设计/待确认`事实状态。
- `batch-status.csv`只用于进度、数量、路径和断点恢复，不作为逐元素义务或语义门禁。
- 保留按功能块拆分的多个JSON；单个JSON/CSV/Markdown/TXT建议小于256KB，Python建议小于200KB。
- 分片写入时只做语法、必填字段、ID、编号和具体内容的轻量自检。
- 禁止生成`fix_*.py`和其他一次性补丁脚本；问题只修改所属事实或源分片。
- 全部交付完成后只执行一次跨产物语义Review并创建`final-review.md`。
- 内网IP、URL、账号、密码、Token、密钥、Cookie、部署路径和测试载荷允许原样记录，不做脱敏或拦截。
- 页面已有数据只读深探；保存、编辑、删除和状态变更只作用于本轮创建的数据。
