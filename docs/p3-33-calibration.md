# P3-33 标定 v1：特征 → drive_baseline / engine_params

对应看板 #33（与 #34 合并交付）。产出 `bridge/calibrate.py` + `alpha persona calibrate`。

## 先澄清一处口径

issue 标题写「13 个 `engine_params`」，**OpenHer 实际只有 11 个**
（`bridge/model.py: ENGINE_PARAM_KEYS`，来自 `drive_metabolism.py` 与 `personas/*/SOUL.md`）。
本实现按 11 个交付。

## 依据：不是凭感觉，是 10 个上游人格的真实参数

从 `vendor/OpenHer/persona/personas/*/SOUL.md`（commit `ef5b214`）提取：

| id | MBTI | conn | nov | expr | safe | play |
|---|---|---:|---:|---:|---:|---:|
| ember | INFP | 0.40 | 0.60 | 0.50 | 0.60 | 0.35 |
| iris | INFP | 0.45 | 0.55 | 0.60 | 0.65 | 0.40 |
| kai | ISTP | 0.35 | 0.45 | 0.25 | 0.55 | 0.40 |
| kelly | ENTP | 0.40 | 0.80 | 0.75 | 0.20 | 0.70 |
| luna | ENFP | 0.75 | 0.65 | 0.75 | 0.25 | 0.80 |
| mia | ESFP | 0.80 | 0.45 | 0.85 | 0.20 | 0.90 |
| nova | ENFP | 0.70 | 0.85 | 0.75 | 0.30 | 0.85 |
| rex | ENTJ | 0.35 | 0.60 | 0.65 | 0.80 | 0.25 |
| sora | INFJ | 0.55 | 0.60 | 0.50 | 0.70 | 0.30 |
| vivian | INTJ | 0.30 | 0.70 | 0.35 | 0.70 | 0.20 |

### MBTI 二分 → drive 组均值（先验）

| 二分 | drive | 组均值 | n |
|---|---|---:|---:|
| E | connection | 0.600 | 5 |
| I | connection | 0.410 | 5 |
| N | novelty | 0.669 | 8 |
| S | novelty | 0.450 | 2 |
| F | expression | 0.658 | 6 |
| T | expression | 0.500 | 4 |
| J | safety / play | 0.733 / 0.250 | 3 |
| P | safety / play | 0.393 / 0.629 | 7 |

规律很直观：E→联结、N→新鲜、F→表达、J→安全且低玩、P→松弛且高玩。

## 11 个参数的拟合

对每个参数穷举「两维 drive」的线性组合，取 R² 最高者：

| 参数 | 依赖 | 截距 | 系数 | MAE | R² |
|---|---|---:|---|---:|---:|
| `baseline_lr` | connection + safety | 0.0133 | +0.0085 / −0.0122 | 9.5% | 0.904 |
| `elasticity` | novelty + safety | −0.0001 | +0.0207 / +0.0770 | 9.7% | 0.883 |
| `hebbian_lr` | connection + safety | 0.0161 | +0.0158 / −0.0081 | 10.5% | 0.773 |
| `phase_threshold` | connection + safety | 2.3361 | −1.8013 / +1.9869 | 5.1% | 0.951 |
| `connection_hunger_k` | connection + novelty | 0.0646 | +0.1828 / −0.0799 | 7.1% | 0.921 |
| `novelty_hunger_k` | novelty + safety | −0.0438 | +0.2274 / −0.0311 | 14.8% | 0.823 |
| `frustration_decay` | novelty + safety | 0.2076 | −0.0912 / −0.1124 | 12.5% | 0.709 |
| `hawking_gamma` | novelty + safety | 0.0027 | −0.0009 / −0.0022 | 16.5% | 0.815 |
| `crystal_threshold` | connection + safety | 0.4358 | −0.1315 / +0.1567 | 7.0% | 0.686 |
| `temp_coeff` | connection + safety | 0.0881 | +0.1250 / −0.0933 | 5.2% | 0.964 |
| `temp_floor` | expression + safety | 0.0493 | +0.0118 / −0.0562 | 7.2% | 0.971 |

**单变量时 11 个参数的最佳预测因子全部是 `play`** —— 上游显然以"玩闹度"为主调参轴。
加第二个维度后 R² 明显提升，故 v1 用双变量。

## 回代精度（v1 的实际水平）

用 10 个样本的真实 drive 值预测参数，与真实值比较：

```
110 个参数值，平均相对误差 10.3%
最差 5 项：
  rex     connection_hunger_k  实际 0.06000 预测 0.08064  34.4%
  vivian  novelty_hunger_k     实际 0.07000 预测 0.09361  33.7%
  kai     frustration_decay    实际 0.08000 预测 0.10474  30.9%
  mia     novelty_hunger_k     实际 0.04000 预测 0.05231  30.8%
  luna    hawking_gamma        实际 0.00120 预测 0.00156  30.0%
```

目标线 25%，实测 10.3% —— 方向与量级都对，个别参数（饥饿系数）偏差较大。

## 局限（必须一起交付）

1. **样本 10 个、自由度 7**：系数是经验拟合，不是理论推导；过拟合风险真实存在。
2. **MBTI 先验只是组均值**：ENFP 的先验 connection 0.60，但 luna 是 0.75、nova 0.70 ——
   个体差异远大于组间差异，先验只能当起点。
3. **无 MBTI 时只能靠文本线索**（短句 / 嘴硬 / 行动关心 / 边界），调整幅度限 ±0.15，
   且一律标 `inferred` + 写明依据。
4. **外推有边界**：输出 clamp 在样本观察范围外扩 10% 内，避免给出样本外的荒谬值。
5. 未做**行为验证**：这套数值是否真的让"嘴硬"表现出来，要等 #44 的 20 轮验收。

## 用法

```bash
alpha persona calibrate <id>          # 只展示建议
alpha persona calibrate <id> --yes    # 写回 profile（engine 块整体替换）
```

实测（luna 复制体）：

```
drive_baseline: connection 0.6  novelty 0.67  expression 0.66  safety 0.39  play 0.63
engine_params:  phase_threshold 2.03021  temp_coeff 0.12671  connection_hunger_k 0.12075 …
依据: v1 启发式：MBTI 先验 → drive → engine_params；拟合样本 n=10，外推需复核
```

## 验证

```bash
python3 tests/test_calibrate.py   # 10 passed（含回代精度断言）
```

覆盖：MBTI 先验与样本组均值一致（E/I 双向）、无 MBTI 无线索时全 missing（不猜）、
文本线索识别、clamp 边界、参数覆盖完整、basis 不含 `insufficient`、
输出通过 `validate_profile`、回代平均相对误差 < 25%。
