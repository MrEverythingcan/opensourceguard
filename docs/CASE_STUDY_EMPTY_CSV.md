# 可复现案例：空 CSV 输入

Issue：读取空 CSV 文件时 `parse_csv` 崩溃，应该返回空列表。

基线代码对空输入形成空 `rows`，随后执行 `rows[0]`，触发 `IndexError`。候选补丁改为：

```python
return [] if not rows else (rows[0] if len(rows) == 1 else rows)
```

这样保留已有的单行和多行返回形状，只修复空输入。运行：

```bash
cd examples/buggy_csv
python -m unittest -v test_parser.py
```

OpenSourceGuard 的验证引擎会在临时副本中先运行基线，再应用候选补丁并运行相同测试；原始目录不会被修改。
