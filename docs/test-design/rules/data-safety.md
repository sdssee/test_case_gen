# 数据安全规则

## 页面操作边界

- 既有数据只读，可以查询、筛选、排序、翻页、查看详情和进入编辑页观察，但不得最终提交变更。
- 只有本轮创建且带 `AI_TEST`、`CODEX_TEST` 或用户明确提供测试标识的数据，才允许保存、编辑、变更状态和删除。
- 无法判断归属的数据按既有数据处理。
- 删除前再次核对测试标识和对象 ID，删除后验证对象不可查询。

## 敏感信息

facts、证据、用例、Excel 和归档中不得保留真实 URL/IP、内部主机名、生产账号、手机号、证件号、密码、密钥或令牌。使用 `<test_env_base_url>`、`<test_user_account>`、`<test_user_password>`、`<valid_api_key>` 等占位符。

## 资产保护

框架修改不得覆盖、删除或清空 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/` 中已有用户资产。标识：`PROTECTED_ASSET_DIRS`。
