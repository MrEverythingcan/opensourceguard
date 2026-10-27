# AIC 演示视频脚本（3—5 分钟）

1. **0:00—0:30 问题**：展示一个开源 Issue，说明定位、复现和回归验证都需要维护者手动完成。
2. **0:30—1:10 架构**：展示“Webhook 验签 → 队列 → 风险策略 → 混合检索 → 隔离验证 → Draft PR”的流程。
3. **1:10—2:20 实际案例**：运行 `python scripts/analyze_demo.py`，展示 `parser.py` 证据和空 CSV 复现。
4. **2:20—3:00 安全边界**：展示 `security` 标签被阻断、错误签名在 JSON 解析前返回 401、原始目录未被修改。
5. **3:00—3:40 自动维护接口**：启动 `python -m opensourceguard.server`，打开演示页，展示队列状态和 Draft PR dry-run。
6. **3:40—4:20 验证与开放**：运行 `python scripts/verify_release.py`，展示测试通过、MIT License、资源清单和维护计划。

视频中要明确说明：远程写入需要真实仓库授权；演示默认不创建线上 PR、不自动合并、不修改主分支。
