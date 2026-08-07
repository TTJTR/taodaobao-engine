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
