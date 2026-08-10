# AI 业务回归评测

`business_cases.json` 当前有 24 条题目，覆盖正常、边界、冲突、无依据和越权。

离线检查结构和安全边界：

```bash
PYTHONPATH=. python -m app.ai.cli.evaluate_regression --mode mock
```

真实模型回归（会调用百炼并产生费用）：

```bash
PYTHONPATH=. python -m app.ai.cli.evaluate_regression \
  --mode bailian \
  --env-file .env \
  --output evals/latest_report.json
```

每次修改模型、Prompt、Schema 或参数版本后都要重新运行。Mock 不执行真实语义检索，因此资产命中率为 0 不代表线上模型失败。

## V1.1 可信主张集

`trust_claims_v1.json` 新增 30 条中文高风险主张样例，覆盖负责人/参与人混淆、
数字、时间、能力限制、来源版本、权限失效、无证据和证据内恶意指令。

当前文件是单人开发阶段的黄金样例种子，不冒充已经完成双人标注与第三人仲裁。
正式阈值校准前，团队仍需按 PRD 扩到至少 100 条并完成双人独立标注。
