# tests/fixtures

脱敏后的聊天记录样本与 golden 快照存放处。

## 规则

- 只有**脱敏后**的样本可以提交入库（真实姓名/手机号/地址必须 mask）。
- **原始聊天记录一律不入库**：放 `tests/fixtures/raw/`，该目录已被 `.gitignore` 屏蔽。
- 样本用途见 P2（#25 准备脱敏样本）/ P3（#43 golden 快照回归）。
