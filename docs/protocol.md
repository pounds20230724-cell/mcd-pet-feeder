# 桌宠 ↔ Skill 事件协议 v1.1

桌宠端（DyberPet + 饲养员桥接）与 Skill 端之间通过**本地文件**通信，无网络依赖、无进程依赖。

## 设计原则：单写者

| 文件 | 谁写 | 谁读 | 内容 |
|---|---|---|---|
| `~/.mcd-pet-feeder/pet_hunger.json` | 桌宠独占写 | Skill 只读 | 桌宠饥饿状态快照（由 DyberPet 底座 + 桥接模块生成，饱食度=HP 条） |
| `~/.mcd-pet-feeder/feed_events.jsonl` | Skill 独占写 | 桌宠只读 | 投喂事件流（追加式，含支付状态机） |
| `~/.mcd-pet-feeder/pet_requests.jsonl` | 桌宠独占写 | Skill 只读 | 桌宠聊天发起的点单需求（追加式，Skill 处理后把 ts 记入自己的 skill_state.json） |
| `~/.mcd-pet-feeder/bridge_state.json` | 桌宠独占写 | — | 桥接轮询断点（重启不重放历史事件） |
| `~/.mcd-pet-feeder/diary.md` | Skill 独占写 | 人读 | 吃麦日记日报（每次 fed 后全量重写） |
| `~/.mcd-pet-feeder/stats.json` | Skill 独占写 | 桌宠可读 | 吃麦统计摘要（机器读） |

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

### 2.1 ordered — 订单已创建【待支付】

```json
{"ts":"2026-10-10T13:39:49+08:00","type":"ordered","order_id":"1030433270000754936107874883","store_code":"1450533","store_name":"麦当劳上海闵行区益梅路力波九坊餐厅","channel":"pickup","items":[{"name":"麦辣鸡翅2块","qty":1}],"amount":24.0,"saved":0.0,"pay_url":"https://m.mcd.cn/mcp/scanToPay?orderId=...","pay_deadline":"13:54 前付款有效"}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| ts | ✅ | 事件时间（ISO 8601 本地时区） |
| type | ✅ | 固定 "ordered" |
| order_id | ✅ | MCP create-order 返回的订单号 |
| store_code / store_name | ✅ | 门店编号/名称 |
| channel | ✅ | pickup / drive_through / delivery / reservation |
| items | ✅ | [{name, qty}]，name 用餐品展示名 |
| amount | ✅ | 应付金额（元，已由分转换） |
| saved | ✅ | 券抵扣合计（元），无券为 0 |
| pay_url | 推荐 | 支付链接（H5），桌宠气泡引导付款 |
| pay_deadline | 推荐 | 支付截止时间文案（如「13:54 前付款有效」） |

**桌宠气泡语义 = 「去付款」**：`🧾 订单已创建【待支付】：… ¥24 ⏰ 13:54 前完成支付 付完款跟饲养员说声「支付完成」`。此时尚未支付，禁止出现「取餐码」等成功字样。

### 2.2 paid — 支付成功

```json
{"ts":"...","type":"paid","order_id":"1030433270000754936107874883","pickup_code":"8848","store_name":"麦当劳上海闵行区益梅路力波九坊餐厅"}
```

| 字段 | 说明 |
|---|---|
| order_id | 关联订单 |
| pickup_code | 取餐码（query-order 返回；为空时回退展示 order_id） |
| store_name | 门店名 |

**写入前置条件（红线）**：Skill 必须先 query-order 确认 orderStatus ∈ {2 配餐中-已支付, 10 餐厅确认配餐中} 才能写 paid。桌宠气泡：`✅ 支付成功！取餐码【8848】📍 门店名 取到餐开吃后说声「吃上了」`。

### 2.3 fed — 投喂完成

```json
{"ts":"2026-10-10T13:05:00+08:00","type":"fed","order_id":"1030...","satiety_delta":50,"streak":3}
```

| 字段 | 说明 |
|---|---|
| order_id | 关联订单 |
| item | 可选，食物道具名（汉堡/薯条/炸鸡）——桌宠端用它触发对应进食动画与数值；缺省时按 satiety_delta 兜底（≥50 汉堡，否则薯条） |
| satiety_delta | 正餐 50 / 小食 20 / 饮料甜点 15 |
| streak | Skill 按相邻自然日计算后的当前连吃天数 |

### 2.4 cancelled — 订单取消

```json
{"ts":"...","type":"cancelled","order_id":"1030..."}
```

桌宠端收到后回滚「等待投喂」表情，不回滚饱食度（尚未进食）。支付超时未付（expirePayTime 已过）也走此事件。

### 2.5 order_request — 桌宠聊天点单（pet_requests.jsonl）

```json
{"ts":"2026-10-10T09:40:00+08:00","type":"order_request","item":"汉堡","source":"pet_chat"}
```

用户在桌宠聊天面板说「点个汉堡」或点「帮我点餐」时产生。Skill 在下次对话启动时读取未处理项，主动向用户确认后走真实点餐链路；`item` 与食物道具/档位对齐（汉堡=正餐、薯条=小食、炸鸡=犒劳）。

### 2.6 桌宠端行为映射

| 事件 | 桌宠动画/反馈 |
|---|---|
| ordered | 气泡「🧾 待支付 + 截止时间」（不做成功假象） |
| paid | 气泡「✅ 支付成功！取餐码 XXXX」+ 切换「等待投喂」表情 |
| fed | 播放吃汉堡动画（麦门限定皮肤帧）→ 饱食度 + delta → 气泡播报 streak/省钱战绩 |
| cancelled | 气泡告知 + 回滚等待表情 |

## 3. 数据沉淀

三层载体（写入方唯一）：

```
feed_events.jsonl（Skill 追加，原始流水）
        │ 每次 fed 后全量重写
        ├──▶ diary.md   （Skill 写，人读日报：单量/支出/券省/streak/Top3，结论先行）
        └──▶ stats.json （Skill 写，机器读：{month, orders, spend, saved, streak, top[]})
```

桌宠聊天问「日记/账单」时，桥接直接从事件流水现算摘要回答（只读，不写）。

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
