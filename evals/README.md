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

Windows PowerShell 可使用：

```powershell
$env:PYTHONPATH='.'
uv run pytest -q tests\ai
uv run python -m app.ai.cli.evaluate_regression --mode mock --output evals\latest_mock_report_after_fix.json
```

真实黄金案例闭环（会调用模型并产生费用）：

```powershell
$env:PYTHONPATH='.'
uv run python -m app.ai.cli.generate_verified_solution `
  examples\golden_a3\context.json `
  examples\golden_a3\retrieval_snapshot.json `
  --max-revisions 2 `
  --output evals\latest_verified_solution_after_fix.json `
  --env-file .env
```

本次验收结论、问题修复和未覆盖范围见 `AI服务模拟数据验收报告_20260808.md`。真实密钥只放在本地 `.env`，不得写入报告或评测输出。
