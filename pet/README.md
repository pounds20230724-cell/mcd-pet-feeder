# 桌宠端（基于 DyberPet 改造）

本目录是「麦门饲养员」的桌宠端实现，基于开源桌宠框架 [DyberPet](https://github.com/ChaozhongLiu/DyberPet)（**GPL-3.0**，原作者 Chaozhong Liu）改造。改造内容与原始代码同仓开源，遵循同一 License。

## 相对上游的改造点

| 文件 | 改动 |
|---|---|
| `DyberPet/mcd_feeder_bridge.py` | **新增**。麦门饲养员桥接模块：轮询 `~/.mcd-pet-feeder/feed_events.jsonl`（Skill 端追加的事件流），`ordered` → 气泡播报订单，`fed` → 调用底座原生 `use_item` 触发进食动画与饱食度/好感度结算，`cancelled` → 气泡告知；同时把饱食度（复用 DyberPet HP 条）持续写回 `~/.mcd-pet-feeder/pet_hunger.json` 供 Skill 读取 |
| `run_DyberPet.py` | 接线：在 `DyberPetApp.__init__` 中实例化 `MCDFeederBridge(self.p)` |

其余全部为 DyberPet 原始代码（含 GPL-3.0 LICENSE 原样保留）。项目内置的「喵当劳」汉堡/薯条/炸鸡食物道具恰好构成投喂物品映射：正餐→汉堡（饱食度停摆 buff）、小食→薯条、犒劳→炸鸡。

## 与 Skill 的接口

双文件单写者协议（详见仓库根目录 [docs/protocol.md](../docs/protocol.md)）：

```
Skill（WorkBuddy 内）                    桌宠端（DyberPet + 桥接）
        │ write append                          │ read poll 1s
        ├────────▶ ~/.mcd-pet-feeder/feed_events.jsonl ─────────▶ 气泡/进食动画
        │                                       │
        ◀──────── ~/.mcd-pet-feeder/pet_hunger.json ◀──────────── 饱食度快照
        │              read on trigger          │ write on HP change
```

任一端缺席另一端照常运行：桌宠离线时 Skill 走「桌宠缺席模式」；Skill 未触发时桌宠只是普通桌宠。

## 运行方法（macOS）

```bash
# 1. Python ≥3.10 + 依赖
python -m venv .venv && source .venv/bin/activate
pip install PySide6 tendo apscheduler pynput PySide6-Fluent-Widgets

# 2. 启动（在 pet/DyberPet 的上一级目录运行）
cd pet/DyberPet
python ../DyberPet/run_DyberPet.py   # 或按上游说明 python run_DyberPet.py
```

启动后桌宠出现在桌面。首次运行会在 `~/.mcd-pet-feeder/` 自动生成数据目录。

## 已验证

- [x] macOS + PySide6 6.11.2 启动渲染正常
- [x] `ordered` 事件 → 气泡「📦 麦门已下单：板烧鸡腿堡套餐×1｜实付 ¥33.5（券省 ¥6.0）」
- [x] `fed` 事件 → 进食动画 + 系统通知「饱食度 +N / 好感度 +N」+ HP 结算
- [x] `pet_hunger.json` 快照随 HP 变化实时更新（satiety/state/streak/last_fed）

## 待办（P3）

- [ ] 麦门红黄限定皮肤（自定义角色帧）
- [ ] 乞食气泡深链唤起 WorkBuddy（当前降级路径：剪贴板话术）
