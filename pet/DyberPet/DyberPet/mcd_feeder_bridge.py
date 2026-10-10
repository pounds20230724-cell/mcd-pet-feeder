"""
麦门饲养员桥接模块 v2（mcd-pet-feeder）
========================================
把 DyberPet 桌宠接入麦当劳 MCP 点餐事件流（基于 DyberPet GPL-3.0 之上改造）。

事件状态机（协议见 mcd-pet-feeder 仓库 docs/protocol.md）：
  ordered   订单已创建【待支付】 → 气泡提示去付款（含截止时间）
  paid      支付成功            → 气泡展示取餐码，引导取餐
  fed       已取餐进食          → 进食动画 + 饱食度/好感度结算（走底座原生 use_item）
  cancelled 订单取消            → 气泡告知

轮询断点：~/.mcd-pet-feeder/bridge_state.json 记录已读偏移，
桥接重启不重放历史事件（首次接入从当前文件末尾开始，只消费新事件）。
"""
import json
import subprocess
import time
import traceback
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

import DyberPet.settings as settings
from DyberPet.pet_chat import MCDPetChat

FEED_DIR = Path.home() / '.mcd-pet-feeder'
EVENTS_FILE = FEED_DIR / 'feed_events.jsonl'
HUNGER_FILE = FEED_DIR / 'pet_hunger.json'
REQUESTS_FILE = FEED_DIR / 'pet_requests.jsonl'
BRIDGE_STATE = FEED_DIR / 'bridge_state.json'

# satiety_delta → 内置食物映射（底座自带「喵当劳」系列，无需新增素材）
DELTA_ITEM = [(50, '汉堡'), (20, '薯条'), (0, '薯条')]


class MCDFeederBridge(QObject):
    """桌宠(事件消费者) + 饱食度快照提供者 + 点单需求收集者"""

    def __init__(self, pet_widget, parent=None):
        super().__init__(parent)
        self.p = pet_widget
        self._streak = 0
        self._last_fed = None
        self._last_state = 'calm'
        FEED_DIR.mkdir(parents=True, exist_ok=True)

        # 静音上游「App update available」更新提示（改造版版本号不随上游走）
        settings.UPDATE_NEEDED = False

        # 轮询断点：首次接入跳过历史事件，只消费新事件
        self._offset = self._load_offset()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll_events)
        self._timer.start()

        # 饱食度条（DP_HpBar）变化 → 写状态快照
        hp_bar = getattr(self.p, 'pet_hp', None)
        if hp_bar is not None and hasattr(hp_bar, 'hp_updated'):
            hp_bar.hp_updated.connect(self._on_hp_updated)
        self._write_hunger(self._current_hp())

        # 直聊模块（右键菜单 Chat 触发；点单意图经 order_requested 落盘联动 Skill）
        self.chat = MCDPetChat(state_provider=self._chat_state,
                               stats_provider=self._stats_summary)
        self.chat.reply_ready.connect(self._on_chat_reply)
        self.chat.order_requested.connect(self.submit_order_request)
        self.chat.bubble_emit = lambda text: self._bubble(
            f'mcd_chat_{uuid.uuid4().hex[:8]}', text)

    # ---------------- 轮询断点 ----------------

    def _load_offset(self):
        try:
            state = json.loads(BRIDGE_STATE.read_text(encoding='utf-8'))
            return int(state.get('events_offset', 0))
        except Exception:
            if EVENTS_FILE.exists():
                return EVENTS_FILE.stat().st_size  # 首次接入：跳过历史
            return 0

    def _save_offset(self, offset):
        try:
            BRIDGE_STATE.write_text(
                json.dumps({'events_offset': offset}), encoding='utf-8')
        except OSError:
            pass

    # ---------------- 直聊 ----------------

    def toggle_chat(self):
        self.chat.toggle(self.p.pos())

    def _chat_state(self):
        return {'satiety': self._current_hp() or 50,
                'state': self._last_state,
                'streak': self._streak,
                'last_fed': self._last_fed}

    def _on_chat_reply(self, reply):
        # 唯一 bubble_type 绕过同类型去重，保证连续对话每句都显示
        self._bubble(f'mcd_chat_{uuid.uuid4().hex[:8]}', f'🐼 {reply}')

    def submit_order_request(self, food: str):
        """点单联动：需求落盘 → 复制话术 → 拉起 WorkBuddy，由 Skill 完成真实下单"""
        try:
            ev = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                  'type': 'order_request', 'item': food,
                  'source': 'pet_chat'}
            with open(REQUESTS_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        except OSError:
            pass
        hint = (f'📋 已记下：想吃「{food}」\n'
                f'去 WorkBuddy 对饲养员说「帮它下单」就好～（话术已复制到剪贴板）')
        self._bubble(f'mcd_req_{uuid.uuid4().hex[:8]}', hint)
        try:
            subprocess.run(['pbcopy'],
                           input=f'饲养员，派蒙想吃「{food}」，帮它下单'.encode('utf-8'),
                           check=False)
        except Exception:
            pass
        try:
            subprocess.Popen(['open', '-a', 'WorkBuddy'])
        except Exception:
            pass

    def _stats_summary(self):
        """吃麦日记摘要（供桌宠聊天直接问答；人读版 diary.md 由 Skill 生成）"""
        if not EVENTS_FILE.exists():
            return '日记本还是空的，投喂第一单就开始记录啦~'
        orders = fed = spend = saved = 0
        streak = 0
        top = {}
        for line in EVENTS_FILE.read_text(encoding='utf-8').splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get('type')
            if t == 'ordered':
                orders += 1
                spend += float(ev.get('amount', 0) or 0)
                saved += float(ev.get('saved', 0) or 0)
                for it in ev.get('items', []):
                    name = it.get('name', '?')
                    top[name] = top.get(name, 0) + int(it.get('qty', 1))
            elif t == 'fed':
                fed += 1
                streak = max(streak, int(ev.get('streak', 0) or 0))
        if orders == 0:
            return '日记本还是空的，投喂第一单就开始记录啦~'
        top3 = '、'.join(f'{k}×{v}' for k, v in
                        sorted(top.items(), key=lambda x: -x[1])[:3]) or '—'
        return (f'📄 吃麦日记：下单 {orders} 单 / 进食 {fed} 次，'
                f'花费 ¥{spend:.0f}（券省 ¥{saved:.0f}），'
                f'最长连吃 {streak} 天，最爱：{top3}')

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
        self._last_state = state
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
            self._save_offset(self._offset)
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
        elif etype == 'paid':
            self._notify_paid(ev)
        elif etype == 'fed':
            self._apply_fed(ev)
        elif etype == 'cancelled':
            self._bubble(f'mcd_cancel_{uuid.uuid4().hex[:8]}',
                         '😢 麦门订单取消了…它眼巴巴地看着你')

    # ---------------- 动作实现 ----------------

    def _bubble(self, btype, message):
        try:
            self.p.bubble_manager.register_bubble.emit(
                {'bubble_type': btype, 'icon': 'system', 'message': message})
        except Exception:
            traceback.print_exc()

    def _notify_ordered(self, ev):
        """订单已创建【待支付】：提示去付款，含截止时间"""
        items = '、'.join(
            f"{i.get('name', '餐品')}×{i.get('qty', 1)}"
            for i in ev.get('items', [])) or '麦门好餐'
        amount = ev.get('amount')
        msg = f'🧾 订单已创建【待支付】：{items}'
        if amount is not None:
            msg += f' ¥{amount}'
        deadline = ev.get('pay_deadline')
        if deadline:
            msg += f'\n⏰ {deadline} 前完成支付，超时自动取消'
        msg += '\n付完款跟饲养员说声「支付完成」~'
        self._bubble(f'mcd_ordered_{uuid.uuid4().hex[:8]}', msg)

    def _notify_paid(self, ev):
        """支付成功：展示取餐码，引导取餐"""
        code = ev.get('pickup_code') or ev.get('order_id', '')
        store = ev.get('store_name', '')
        msg = f'✅ 支付成功！取餐码【{code}】'
        if store:
            msg += f'\n📍 {store}'
        msg += '\n取到餐开吃后说声「吃上了」~'
        self._bubble(f'mcd_paid_{uuid.uuid4().hex[:8]}', msg)

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
            self._bubble(f'mcd_fed_{uuid.uuid4().hex[:8]}',
                         '🍔 投喂收到！但当前宠物没有匹配的食物道具，先记一笔~')
        else:
            # 进食动画 + 饱食度/好感度/buff 全部走底座原生链路
            self.p.use_item(item)

        self._last_fed = ev.get('ts') or time.strftime('%Y-%m-%dT%H:%M:%S%z')
        streak = ev.get('streak')
        self._streak = streak if isinstance(streak, int) and streak > 0 else self._streak + 1
        self._write_hunger(self._current_hp())
