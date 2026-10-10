# 麦门饲养员 · mcd-pet-feeder

> **会饿的桌宠，用麦门投喂。** 你的 macOS 桌宠到饭点会乞食，你对 WorkBuddy 说一句话完成点餐，餐到投喂，吃麦全记录。

**麦当劳程序员创意开发大赛（2026 M-CODE）参赛作品** · 基于 [麦当劳中国官方 MCP](https://github.com/M-China/mcd-mcp-server) · 桌宠端基于 [DyberPet](https://github.com/ChaozhongLiu/DyberPet)（GPL-3.0）改造

<!-- 📹 首屏 GIF 占位：乞食气泡 → 对话下单 → 桌宠吃汉堡动画（P3 录制后替换） -->

## 这是什么

桌宠类应用有一个共同的缺陷：**它们只是桌面上的一张图**。麦门饲养员给它装上一个真的会饿的胃，和一条真的能买到饭的通道：

- **情感层（桌宠 DyberPet + 饲养员桥接）**：内置饥饿数值系统（HP 条即饱食度，随时间衰减）。饿了会弹气泡乞食——对 WorkBuddy 说一句话完成投喂闭环。
- **决策层（本 Skill）**：读懂桌宠的饥饿状态和你的口语需求（"健身呢少吃点" / "饿死了来顿大的"），在热量预算内生成搭配方案，查券、算价、下单、跟踪订单。
- **能力层（麦当劳官方 MCP）**：门店、菜单、优惠券、价格计算、下单支付、营养数据，全部来自官方接口。

每一单都会写进「吃麦日记」：本月花了多少、券省了多少、连吃几天、最常点什么——桌宠会用气泡播报你的麦门战绩。

## 核心特性

| 特性 | 说明 |
|---|---|
| 🐼 桌宠乞食 | 四档饥饿状态（平静/馋/饿/饥饿警报），带节流防打扰，夜间免打扰 |
| 🥗 需求引擎 | 餐品热量四档归类（轻食/标准/充饥/放纵），口语意图路由，组合生成带热量账单 |
| 🏪 三种取餐 | 到店自取 / 得来速（DT）/ 麦乐送外送，一条链路全覆盖 |
| ⏰ 预约错峰 | 解析门店可约时段，"12 点散会 12:40 取餐"，饭点不排队 |
| 🎫 券管家 | 卡包+门店可用券自动匹配，推荐方案默认核销临期券 |
| 📔 吃麦日记 | 月度支出 / 券节省 / 连吃 streak / Top3 单品，桌宠气泡播报 |
| 🔒 交易确认制 | 桌宠端零交易 UI，所有下单在对话内明确确认后执行 |

## 安装方法

### 1. 配置麦当劳官方 MCP 连接器

在 WorkBuddy【连接器 → 自定义连接器 → 配置 MCP】中填入（Token 从 [open.mcd.cn](https://open.mcd.cn/mcp) 控制台申请）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

完整示例见 [mcp-config.example.json](./mcp-config.example.json)。⚠️ 请勿在任何公开文件中提交真实 Token。

### 2. 安装 Skill

```bash
# 复制到 WorkBuddy 用户级技能目录
mkdir -p ~/.workbuddy/skills
cp -r skill ~/.workbuddy/skills/mcd-pet-feeder
```

重启 WorkBuddy 后，对它说「喂它」即可触发。

### 3.（可选）桌宠端联动

桌宠端基于开源框架 [DyberPet](https://github.com/ChaozhongLiu/DyberPet)（GPL-3.0）改造，通过桥接模块与 Skill 交换事件（详见 [pet/README.md](./pet/README.md) 与 [docs/protocol.md](./docs/protocol.md)）：

```bash
pip install PySide6 tendo apscheduler pynput PySide6-Fluent-Widgets
python pet/DyberPet/run_DyberPet.py
```

没有桌宠也能完整使用本 Skill 的全部点餐功能——桌宠联动是增量彩蛋，不是前置依赖。

## 使用示例

```text
🐼 桌宠气泡：「咕噜噜…想吃麦麦了」（点击气泡唤起对话）
用户：饲养员，它饿了
Skill：它现在有点饿（饱食度 32）。正餐还是垫垫？
用户：健身呢，少吃点
Skill：控卡模式（400–600 kcal）：
  方案 A · 高蛋白控卡（券后 ¥29）
   板烧鸡腿堡 🍔 403 kcal｜蛋白 23g
   玉米杯 🥗 105 kcal
   零卡可乐 🥗 0 kcal
   合计 508 kcal ✅ 已省 ¥6
  确认回复「下」
用户：下
Skill：✅ 已下单，取餐码 8848。桌宠进入等待投喂状态，取餐后说「吃上了」。
（取餐后）
用户：吃上了
Skill：🍔 投喂成功！饱食度 +50，连吃第 3 天。本月已省 ¥21。
```

## 目标用户

- 桌宠/AI 玩具爱好者：想要一个「活的」桌面伙伴，而不是一张贴图
- 独居打工人：到饭点没人提醒吃饭、选择困难的人
- 健身/控卡人群：想吃麦当劳但不想把热量吃爆的人
- WorkBuddy 重度用户：希望点餐长在对话流里、顺手完成的人

## 项目结构

```
.
├── README.md                 # 本文件
├── CONTEST_DECLARATION.md    # 参赛声明（官方原样文件）
├── MCP_INTEGRATION.md        # 麦当劳 MCP 集成说明
├── mcp-config.example.json   # 脱敏 MCP 配置示例
├── workbuddy.md              # WorkBuddy 开发上下文（专项奖励核验用）
├── skill/SKILL.md            # 决策层：饲养员主逻辑
├── docs/protocol.md          # 桌宠 ↔ Skill 事件协议
└── pet/                      # 桌宠端（基于 DyberPet GPL-3.0 改造）
    ├── README.md             # 改造点说明与运行方法
    └── DyberPet/             # 框架源码（含桥接模块 mcd_feeder_bridge.py）
        └── ...
```

## 免责声明

本项目为参赛者独立开发的参赛作品，非麦当劳官方产品。输出仅供参考，不构成医疗、营养或其他专业建议；餐品信息、价格、营养数据及供应状态以麦当劳官方渠道实时结果为准。
