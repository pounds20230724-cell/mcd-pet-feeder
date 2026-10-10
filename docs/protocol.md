# 桌宠 ↔ Skill 事件协议 v1

桌宠端（WorkBuddyPet）与 Skill 端之间通过**两个本地文件**通信，无网络依赖、无进程依赖。

## 设计原则：单写者

| 文件 | 谁写 | 谁读 | 内容 |
|---|---|---|---|
| `~/.mcd-pet-feeder/pet_hunger.json` | 桌宠独占写 | Skill 只读 | 桌宠饥饿状态快照（由 DyberPet 底座 + 桥接模块生成，饱食度=HP 条） |
| `~/.mcd-pet-feeder/feed_events.jsonl` | Skill 独占写 | 桌宠只读 | 投喂事件流（追加式） |

任一端缺席时另一端照常工作：

- 桌宠离线 → Skill 读不到 pet_hunger.json，进入「桌宠缺席模式」，点餐功能完整可用
- Skill 未触发 → 桌宠照常跑饥饿状态机，feed_events.jsonl 无新行则无事发生

---

## 1. pet_hunger.json（状态快照）

桌宠端每次状态变化整体覆写（JSON 单对象，非追加）：

```json
{
  "satiety": 32,
  "state": "hungry",
  "streak": 3,
  "last_fed": "2026-10-10T13:05:00+08:00",
  "updated_at": "2026-10-11T11:52:14+08:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| satiety | int 0–100 | 饱食度。基础衰减 −1/8min（07:00–23:30）；饭点 11:00–13:00 / 17:00–19:00 / 21:00–23:00 衰减 ×2；夜间 23:30–07:00 冻结 |
| state | string | calm(60–100) / craving(35–59) / hungry(15–34) / alert(0–14) |
| streak | int | 当前连吃天数（由 fed 事件驱动） |
| last_fed | string\|null | 上次投喂时间（ISO 8601 本地时区），从未投喂为 null |
| updated_at | string | 本条快照时间 |

**乞食节流（桌宠端执行）**：craving 每 2h / hungry 每 30min / alert 每 15min 最多弹一次气泡；夜间静默。

---

## 2. feed_events.jsonl（事件流）

Skill 端**只追加**，一行一个 JSON 对象，UTF-8。桌宠端启动时全量读入、运行中 tail 增量行。

### 2.1 ordered — 下单成功

```json
{"ts":"2026-10-10T12:41:00+08:00","type":"ordered","order_id":"1030108270000795607354880945","store_code":"1450713","store_name":"麦当劳上海黄浦华旭国际大厦餐厅","channel":"pickup","items":[{"name":"板烧鸡腿堡套餐","qty":1}],"amount":33.5,"saved":6.0}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| ts | ✅ | 事件时间（ISO 8601 本地时区） |
| type | ✅ | 固定 "ordered" |
| order_id | ✅ | MCP create-order 返回的订单号 |
| store_code / store_name | ✅ | 门店编号/名称 |
| channel | ✅ | pickup / drive_through / delivery / reservation |
| items | ✅ | [{name, qty}]，name 用餐品展示名 |
| amount | ✅ | 实付金额（元，已由分转换） |
| saved | ✅ | 券抵扣合计（元），无券为 0 |

### 2.2 fed — 投喂完成

```json
{"ts":"2026-10-10T13:05:00+08:00","type":"fed","order_id":"1030...","satiety_delta":50,"streak":3}
```

| 字段 | 说明 |
|---|---|
| order_id | 关联订单 |
| item | 可选，食物道具名（汉堡/薯条/炸鸡）——桌宠端用它触发对应进食动画与数值；缺省时按 satiety_delta 兜底（≥50 汉堡，否则薯条） |
| satiety_delta | 正餐 50 / 小食 20 / 饮料甜点 15 |
| streak | Skill 按相邻自然日计算后的当前连吃天数 |

### 2.3 cancelled — 订单取消

```json
{"ts":"...","type":"cancelled","order_id":"1030..."}
```

桌宠端收到后回滚「等待投喂」表情，不回滚饱食度（尚未进食）。

### 2.4 桌宠端行为映射

| 事件 | 桌宠动画/反馈 |
|---|---|
| ordered | 切换「等待投喂」表情（盯着你看） |
| fed | 播放吃汉堡动画（麦门限定皮肤帧）→ 饱食度 + delta → 气泡播报 streak/省钱战绩 |
| cancelled | 回滚等待表情 |

---

## 3. 桌宠乞食 → 对话唤起

桌宠点击乞食气泡后的唤起顺序：

1. 尝试 WorkBuddy deep link，并预填话术「饲养员，它饿了」（桌宠状态由 Skill 自行读文件获取，无需在话术中携带）
2. deep link 不可用 → 将话术复制到剪贴板 + 打开 WorkBuddy 主窗口
3. 以上都失败 → 气泡提示「跟 WorkBuddy 说『喂它』」

## 4. 健壮性约定

- 解析失败的一行 jsonl 记 log 后跳过，不中断
- 文件不存在视为空状态，不报错
- 双方都**不删除**历史事件（吃麦日记依赖全量数据）；目录级清理交给用户
- 时间一律本地时区 ISO 8601；金额一律元（Skill 负责把 MCP 返回的分转换）
