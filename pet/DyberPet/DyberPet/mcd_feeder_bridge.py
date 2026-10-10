"""
麦门饲养员桥接模块（mcd-pet-feeder）
====================================
把 DyberPet 桌宠接入麦当劳 MCP 点餐事件流（基于 DyberPet GPL-3.0 之上改造）。

数据流（协议见 mcd-pet-feeder 仓库 docs/protocol.md，单写者原则）：
- 读取 ~/.mcd-pet-feeder/feed_events.jsonl（由 WorkBuddy 内的饲养员 Skill 追加）
    * ordered   → 桌宠气泡播报订单（门店/餐品/实付/券省）
    * fed       → 调用 use_item 触发进食动画，饱食度/好感度由 DyberPet 数值系统结算
    * cancelled → 气泡告知取消
- 持续把饱食度写回 ~/.mcd-pet-feeder/pet_hunger.json（状态快照，Skill 只读）
    * satiety 直接复用 DyberPet HP 条（0-100），四档状态映射与 Skill 端一致
"""
import json
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

import DyberPet.settings as settings

FEED_DIR = Path.home() / '.mcd-pet-feeder'
EVENTS_FILE = FEED_DIR / 'feed_events.jsonl'
HUNGER_FILE = FEED_DIR / 'pet_hunger.json'

# satiety_delta → 内置食物映射（底座自带「喵当劳」系列，无需新增素材）
DELTA_ITEM = [(50, '汉堡'), (20, '薯条'), (0, '薯条')]


class MCDFeederBridge(QObject):
    """桌宠(Skill 事件消费者) + 饱食度快照提供者"""

    def __init__(self, pet_widget, parent=None):
        super().__init__(parent)
        self.p = pet_widget
        self._offset = 0
        self._streak = 0
        self._last_fed = None
        FEED_DIR.mkdir(parents=True, exist_ok=True)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll_events)
        self._timer.start()

        # 饱食度条（DP_HpBar）变化 → 写状态快照
        hp_bar = getattr(self.p, 'pet_hp', None)
        if hp_bar is not None and hasattr(hp_bar, 'hp_updated'):
            hp_bar.hp_updated.connect(self._on_hp_updated)
        self._write_hunger(self._current_hp())

    # ---------------- 饱食度快照 ----------------

    def _current_hp(self):
        bar = getattr(self.p, 'pet_hp', None)
        try:
            return int(bar.value())
        except Exception:
            return None

    def _on_hp_updated(self, value):
        self._write_hunger(value)

    def _write_hunger(self, satiety):
        if satiety is None:
            return
        if satiety >= 60:
            state = 'calm'
        elif satiety >= 35:
            state = 'craving'
        elif satiety >= 15:
            state = 'hungry'
        else:
            state = 'alert'
        data = {
            'satiety': int(satiety),
            'state': state,
            'streak': self._streak,
            'last_fed': self._last_fed,
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        }
        try:
            HUNGER_FILE.write_text(
                json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass

    # ---------------- 事件轮询 ----------------

    def _poll_events(self):
        try:
            if not EVENTS_FILE.exists():
                self._offset = 0
                return
            size = EVENTS_FILE.stat().st_size
            if size < self._offset:  # 文件被重置/轮转
                self._offset = 0
            if size == self._offset:
                return
            with open(EVENTS_FILE, 'r', encoding='utf-8') as f:
                f.seek(self._offset)
                new_text = f.read()
                self._offset = f.tell()
        except OSError:
            return
        for line in new_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self._dispatch(json.loads(line))
            except json.JSONDecodeError:
                continue  # 半行/脏数据，跳过
            except Exception:
                traceback.print_exc()

    def _dispatch(self, ev):
        etype = ev.get('type')
        if etype == 'ordered':
            self._notify_ordered(ev)
        elif etype == 'fed':
            self._apply_fed(ev)
        elif etype == 'cancelled':
            self._bubble('mcd_cancelled', '😢 麦门订单取消了…它眼巴巴地看着你')

    # ---------------- 动作实现 ----------------

    def _bubble(self, btype, message):
        try:
            self.p.bubble_manager.register_bubble.emit(
                {'bubble_type': btype, 'icon': 'system', 'message': message})
        except Exception:
            traceback.print_exc()

    def _notify_ordered(self, ev):
        items = '、'.join(
            f"{i.get('name', '餐品')}×{i.get('qty', 1)}"
            for i in ev.get('items', [])) or '麦门好餐'
        msg = f'📦 麦门已下单：{items}'
        amount = ev.get('amount')
        saved = ev.get('saved', 0)
        if amount is not None:
            msg += f'｜实付 ¥{amount}'
            if saved:
                msg += f'（券省 ¥{saved}）'
        msg += '\n餐到了跟我说「吃上了」～'
        self._bubble('mcd_ordered', msg)

    def _apply_fed(self, ev):
        item = ev.get('item')
        items_data = getattr(settings, 'items_data', None)
        if item and items_data is not None and item in items_data.item_dict:
            try:
                itype = items_data.item_dict[item]['item_type']
            except Exception:
                itype = None
            if itype != 'consumable':
                item = None
        if not item:
            # 未指定食物时按饱食度档位兜底
            try:
                delta = int(ev.get('satiety_delta', 0) or 0)
            except (TypeError, ValueError):
                delta = 0
            item = next((name for th, name in DELTA_ITEM if delta >= th), '汉堡')

        if items_data is None or item not in items_data.item_dict:
            self._bubble('mcd_fed', '🍔 投喂收到！但当前宠物没有匹配的食物道具，先记一笔～')
        else:
            # 进食动画 + 饱食度/好感度/buff 全部走底座原生链路
            self.p.use_item(item)

        self._last_fed = ev.get('ts') or time.strftime('%Y-%m-%dT%H:%M:%S%z')
        streak = ev.get('streak')
        self._streak = streak if isinstance(streak, int) and streak > 0 else self._streak + 1
        self._write_hunger(self._current_hp())
