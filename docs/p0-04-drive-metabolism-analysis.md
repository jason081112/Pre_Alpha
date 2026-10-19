# P0-4 · OpenHer `drive_metabolism.py` 5 维驱力代谢与 `phase_threshold` / `temp_coeff` 语义

> 阶段：P0 调研验证 · 任务：读 `drive_metabolism.py`：5 维驱力代谢函数，`phase_threshold` / `temp_coeff` 语义

> ⚠️ **环境假设已修订（2026-10-19）**：文中"可在 Termux 直接跑"属当时的开发环境假设；部署形态已改为**引擎云端运行、Web/PWA 优先**（见 [`adr/0001-deployment-cloud-web-first.md`](adr/0001-deployment-cloud-web-first.md)）。**代码层结论不受影响。**

## 一、DriveMetabolism 核心设计

`engine/genome/drive_metabolism.py` —— 时间感知的驱力代谢引擎 v3，基于两个纯物理时间方程：

```
1. 冷却（Cooling）：frustration *= e^(-λ * Δt_hours)      时间流逝会冷却所有挫败
2. 饥饿（Hunger）：frustration += k * Δt_hours           连结/新鲜感随时间线性积累（孤独感/无聊感增长）
```

所有物理常数均可通过 `engine_params`（SOUL.md frontmatter）按 persona 覆盖。

### 全局默认值（可被 persona 覆盖）

| 参数 | 默认值 | 含义 |
|---|---|---|
| frustration_decay | 0.08 /小时 | 挫败指数衰减率，半衰期约 8.7 小时 |
| connection_hunger_k | 0.15 /小时 | 连结饥饿积累率（孤独感随时间增长） |
| novelty_hunger_k | 0.05 /小时 | 新鲜感饥饿积累率（无聊感随时间增长） |
| temp_coeff | 0.12 | 温度系数，控制热力学噪声强度 |
| temp_floor | 0.03 | 温度下限（最小噪声，永不完全平静） |

---

## 二、关键方法解析

### 1. `time_metabolism(now)` —— 时间箭头代谢

两个方程在两个驱力上分别作用：

| 方程 | 作用对象 | 效果 |
|---|---|---|
| 冷却：`frustration *= e^(-λΔt)` | 5 维驱力全部 | 挫败随时间指数衰减（冷却） |
| 饥饿：`frustration += k * Δt` | 仅 `connection` 和 `novelty` | 孤独感/无聊感随时间线性增长 |

挫败值 clamp 到 `[0, 5]`。

返回值：`delta_hours`（本次代谢经过的小时数，用于上层调度）。

### 2. `apply_llm_delta(delta_dict)` → reward

LLM（Critic）判断本轮对话的挫败变化：

| 输入 | 含义 |
|---|---|
| `delta_dict[d]` | 正值 = 该驱力更沮丧，负值 = 更满足 |

返回值 `reward = old_total - new_total`（正值 = 挫败降低 = 好轮对话）。

注意：每个驱力在应用 delta 后还会乘 `(1 - decay_rate)` 做一次额外衰减（`decay_rate = 0.1` 每轮）。

### 3. `temperature()` —— tanh 饱和曲线（`temp_coeff` / `temp_floor` 语义）

```python
max_temp = temp_coeff * 2.5
temp = max_temp * tanh(total_frustration * temp_coeff / max_temp) + temp_floor
```

- **线性公式的问题**：frustration=5, coeff=0.10 → temp=0.52 → 噪声 σ=0.52 → 8D 信号完全随机化，信号破坏
- **tanh 饱和**：同样输入 → temp≈0.26 → 信号仍保留方向性

**语义总结**：
- `temp_coeff`：控制温度上限（`max_temp = coeff * 2.5`）和曲线陡峭度。越高，同样挫败下噪声越强。
- `temp_floor`：温度下限，保证即使挫败为 0 也有最小噪声，永不完全平静。

### 4. `apply_thermodynamic_noise(base_signals)` —— 注入噪声

```python
temp = self.temperature()
noisy[key] = clamp(base_signals[key] + gauss(0, temp), 0, 1)
```

挫败越高 → 温度越高 → 噪声越大 → 行为越不可预测。噪声作用在 8D 行为信号上。

### 5. `sync_to_agent(agent)` —— 代谢状态回写 Agent

```python
agent.drive_state[d] = min(1.0, agent.drive_baseline[d] + frustration[d] * 0.15)
agent._frustration = self.total()
```

驱力当前状态 = 基线 + 挫败 × 0.15（clamp 到 1.0）。挫败直接推高驱力状态，使角色更“饥饿”。

---

## 三、`phase_threshold` 语义（注意：不在本文件中）

`phase_threshold` **定义在 `engine/genome/genome_engine.py` 的 `Agent.__init__`**：

```python
self.phase_threshold = params.get('phase_threshold', 2.0)
```

作用：当 Agent 累积挫败 `_frustration` 超过 `phase_threshold` 时，触发**相位转移（Phase Transition）**：

- 输出层偏置 b2 随信号值随机扰动（`kick = -0.3 * (sig_val - 0.5) + gauss(0, 0.15)`）
- 隐藏层偏置 b1 随机扰动（`gauss(0, 0.1)`）
- 挫败清零，标记 `_last_phase_transition = True`

**语义**：挫败积累到临界点，角色性格发生突变（情感相变），打破原有行为模式，产生新的表达方式。

---

## 四、P3 桥接层映射建议（`profile2soul.py`）

| Distilly 产出（文本人格） | OpenHer 落点 | 映射方式 |
|---|---|---|
| 冲突强度 / 爆发阈值 | `engine_params.phase_threshold` | 阈值越低，越容易触发相位转移（情绪波动大） |
| 情感相变频率 | `engine_params.temp_coeff` + `phase_threshold` 配合 | temp_coeff 控制噪声强度，phase_threshold 控制相变触发频率 |
| 基础焦虑/孤独感 | `engine_params.connection_hunger_k` | 越高，长时间不互动后连结挫败增长越快 |
| 基础无聊感 | `engine_params.novelty_hunger_k` | 越高，越容易对日常话题产生厌倦 |
| 情绪回复速度 | `engine_params.frustration_decay` | 越高，挫败冷却越快，情绪恢复越快 |
| 基础情绪波动性 | `engine_params.temp_floor` | 最小噪声下限，决定角色是否永远有一点“躁动” |

---

## 五、P1 跑通可行性（补充）

`drive_metabolism.py` 仅依赖标准库（math, random, time），无额外依赖。Termux 上可直接跑。

---

**P0-4 状态**：调研完成。`phase_threshold` 实际定义在 `genome_engine.py`（Agent 类），`temp_coeff` / `temp_floor` 定义在 `drive_metabolism.py`，两者共同控制情绪相变与热力学噪声。

下一步可进入 P0-5（读 `critic.py` + `signal_buckets.yaml`）或直接开始 P3 桥接层设计。
