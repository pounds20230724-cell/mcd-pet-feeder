# MCP 集成说明

## 使用的 MCP Server

| 项目 | 内容 |
|---|---|
| Server 名称 | mcd-mcp（麦当劳中国官方 MCP） |
| 接入地址 | `https://mcp.mcd.cn`（Streamable HTTP） |
| 认证方式 | Bearer Token（open.mcd.cn 控制台申请，见 mcp-config.example.json） |
| 集成方式 | 腾讯 WorkBuddy 自定义连接器，Skill（skill/SKILL.md）按场景编排调用 |

## 使用的 Tool 清单

### 核心链路（9 个）

| Tool | 用途 | 调用时机 |
|---|---|---|
| query-nearby-stores | 查附近可点门店（到店/得来速，返回预约时段） | 用户发起点餐，未指定门店 |
| query-meals | 查门店餐品列表（orderType=1 到店 / 2 外送；reservationDate 预约时段菜单） | 选定门店后 |
| query-meal-detail | 餐品详情与套餐特调选项 | 用户要改套餐组成/看单品构成 |
| list-nutrition-foods | 全量单品营养数据（能量/蛋白/脂肪/碳水/钠） | 构建本地热量四档归类表 |
| query-store-coupons | 指定门店+订单类型可用券 | 出方案前匹配优惠 |
| query-my-coupons | 用户卡包券资产 | 与门店可用券对照，识别临期券 |
| calculate-price | 含券算价（返回价格单位为分，takeWayList） | 确认下单前 |
| create-order | 创建订单（到店传 takeWayCode；外送传 addressId；预约传 reservationDate） | 用户明确确认后 |
| query-order | 订单状态查询 | 支付后回查、进度跟踪 |

### 辅助能力（5 个）

| Tool | 用途 | 调用时机 |
|---|---|---|
| delivery-query-addresses / delivery-create-address | 外送地址列表 / 新增地址 | 外送链路入口 |
| delivery-query-stores | 按地址查可配送门店 | 外送选店 |
| available-coupons / auto-bind-coupons | 查可领券 / 一键领券 | 用户说"先领券" |
| cancel-order | 取消订单（需用户确认+取消原因） | 用户主动取消 |

## 核心调用流程

### 链路 A：到店自取（orderType=1 / beType=1）

```
query-nearby-stores(searchType=2)
  └─▶ query-meals(storeCode, orderType=1, beType=1)
        └─▶ [需求引擎：list-nutrition-foods 缓存 × 意图预算 → 组合推荐]
              └─▶ query-store-coupons + query-my-coupons
                    └─▶ calculate-price(items, coupons) ──▶ 用户确认 ──▶ create-order(takeWayCode)
                                                                              └─▶ query-order
```

### 链路 B：外送（orderType=2 / beType=2）

```
delivery-query-addresses ──▶ delivery-query-stores(addressId, beType=2)
  └─▶ query-meals(orderType=2, beCode) ──▶ 查券 ──▶ calculate-price
        ──▶ 用户确认 ──▶ create-order(addressId, needTableware) ──▶ query-order
```

### 链路 C：预约错峰（reservationDate）

```
query-nearby-stores（过滤 reservation=true，解析 reservationTimeOptions）
  └─▶ 时段匹配（避开时段边界 30 分钟）
        └─▶ query-meals(reservationDate) ──▶ 券有效期对齐
              └─▶ calculate-price(reservationDate) ──▶ create-order(reservationDate)
```

### 数据流：事件落盘

create-order 成功 / 用户确认"吃上了"后，Skill 将 `ordered` / `fed` 事件追加至本地 `~/.mcd-pet-feeder/feed_events.jsonl`，供桌宠端播放投喂动画与吃麦日记统计（协议见 docs/protocol.md）。该部分不依赖 MCP 之外的任何在线服务。

## 业务价值

1. **补上了"谁来发起"的场景缺口**：传统点餐工具等用户打开 APP；本 Skill 由桌宠饥饿状态主动发起，MCP 的门店/菜单/券/下单能力被安置在"宠物饿了"这个天然场景里。
2. **把营养数据用成了决策引擎而非摆设**：list-nutrition-foods 提供的全量营养数据被归档为四档热量标签，"健身少吃点 / 真饿了多吃点"这类口语需求直接映射为选品预算。
3. **券的核销优先级**：query-my-coupons 的到期信息与 query-store-coupons 的门店匹配交叉，临期券优先核销，减少浪费。
4. **预约能力的产品化**：reservationTimeOptions 的时段文本被解析为结构化时段表，支撑"散会取餐""下午茶错峰"等真实办公场景。
