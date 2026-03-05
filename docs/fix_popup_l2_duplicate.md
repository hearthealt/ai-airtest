# 修复：关闭弹窗后回到上一个L1导致L2重复测试

## 问题描述

L1-A 测试完毕 → 切换到 L1-B → 弹窗出现 → 关闭弹窗 → App 回到 L1-A 页面 →
引擎仍处于 DISCOVER_L2 状态 → AI 识别到 L1-A 的 L2 → 存储到 L1-B 名下 → 重复测试。

## 修改位置

`ai_explorer/exploration_engine.py` → `_step_discover_l2` 方法

在 `self.menu_structure.l2_map[l1.name] = l2_items` 之前插入校验逻辑。

## 方案

发现 L2 后、存储前，将当前 L2 名称集合与所有已测 L1 的 L2 名称集合比对：

- 当前 L2 为空 → 跳过比对，直接走 TEST_L1_DIRECT
- 当前 L2 非空 → 遍历 `l2_map` 中所有已测 L1，完全相同则判定页面未切换成功
- 命中 → 状态转 SWITCH_L1 重新点击目标 L1
- **最多重试 2 次**，超过后放弃 L2 测试，直接走 CHECK_L1_BLOCK 验证 L1

## 需要新增的状态变量

```python
# __init__ 中新增
self._l2_dedup_retry_count: int = 0
self._max_l2_dedup_retries: int = 2
```

## 代码改动

```python
def _step_discover_l2(self, step_number: int) -> ExplorationStep:
    # ... 省略前面的弹窗检测、L2解析逻辑 ...

    # ======== 新增：L2去重校验 ========
    if l2_items:
        curr_l2_names = {i.name for i in l2_items}
        for prev_l1_name, prev_l2_list in self.menu_structure.l2_map.items():
            if prev_l1_name == l1.name:
                continue
            prev_l2_names = {i.name for i in prev_l2_list}
            if not prev_l2_names:
                continue
            if curr_l2_names == prev_l2_names:
                self._l2_dedup_retry_count += 1
                if self._l2_dedup_retry_count >= self._max_l2_dedup_retries:
                    # 多次重试仍回到错误页面，放弃L2测试，直接检查L1
                    logger.warning(
                        f"[步骤{step_number}] L1'{l1.name}'的L2连续{self._l2_dedup_retry_count}次"
                        f"与已测L1'{prev_l1_name}'重复，放弃L2测试，直接检查L1"
                    )
                    self._l2_dedup_retry_count = 0
                    self.last_clicked_target = l1.name
                    if l1.name not in self.tested_controls:
                        self.tested_controls.append(l1.name)
                    self.loading_retry_count = 0
                    self.state = EngineState.CHECK_L1_BLOCK
                    return self._make_info_step(
                        step_number, screenshot_path, elements,
                        f"L2重复{self._max_l2_dedup_retries}次，跳过L2直接检查L1"
                    )
                else:
                    # 还有重试机会，重新切换L1
                    logger.warning(
                        f"[步骤{step_number}] L1'{l1.name}'的L2与已测L1'{prev_l1_name}'"
                        f"完全相同({curr_l2_names})，第{self._l2_dedup_retry_count}次重试SWITCH_L1"
                    )
                    self.state = EngineState.SWITCH_L1
                    return self._make_info_step(
                        step_number, screenshot_path, elements,
                        f"L2与已测L1'{prev_l1_name}'重复，重试切换L1({self._l2_dedup_retry_count}/{self._max_l2_dedup_retries})"
                    )
    # ======== 校验结束 ========

    # 校验通过，重置计数器
    self._l2_dedup_retry_count = 0

    self.menu_structure.l2_map[l1.name] = l2_items
    self.menu_structure.current_l2_index = 0
    # ... 后续原有逻辑 ...
```

## 流程图

```
DISCOVER_L2 识别到 L2
    │
    ├─ L2为空 → TEST_L1_DIRECT（不比对）
    │
    └─ L2非空 → 与所有已测L1的L2比对
        │
        ├─ 无重复 → 重置计数器 → 正常存储L2 → TEST_L2
        │
        └─ 有重复（页面没切对）
            │
            ├─ 重试次数 < 2 → SWITCH_L1 重新点击目标L1
            │
            └─ 重试次数 >= 2 → 放弃L2 → CHECK_L1_BLOCK 直接验证L1
```

## 不处理的情况

当前 L1 的 L2 为空时不做比对。原因：

- 空集之间无法区分，比对无意义
- L2 为空走 TEST_L1_DIRECT，AI 直接检查 L1 页面状态，不会触发 L2 遍历
- 即使在错误页面做了一次 L1 级别检查，不会导致重复测试 L2
