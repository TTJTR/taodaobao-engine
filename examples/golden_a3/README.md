# 星瀚 A3 单线质检黄金案例

这两个 JSON 文件用于本地演示 AI 闭环，不代表真实客户、产品或项目：

- `context.json`：客户画像和本轮需求，相当于销售交来的问题。
- `retrieval_snapshot.json`：后端检索并固化的 2 条经验和 5 张能力卡，相当于图书管理员取出的资料。

在项目根目录运行：

```bash
PYTHONPATH=. /opt/anaconda3/bin/python3.13 -m app.ai.cli.generate_verified_solution \
  examples/golden_a3/context.json \
  examples/golden_a3/retrieval_snapshot.json \
  --max-revisions 2 \
  --output examples/golden_a3/result.json
```

程序会依次生成报告、逐条质检，并在需要时最多重写两次。最终 JSON 中：

- `passed` 表示是否通过独立质检；
- `revision_count` 表示实际重写次数；
- `solution` 是最终八区块报告；
- `quality_report` 是最终质检结果；
- `attempts` 保留每一版报告和对应批改记录。
