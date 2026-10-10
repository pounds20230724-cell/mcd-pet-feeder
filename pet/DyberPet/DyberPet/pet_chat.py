"""
麦门饲养员·桌宠直聊模块 v2（mcd-pet-feeder）
=============================================
正经的聊天面板：历史记录、快捷指令、真实点单联动。

交互设计：
- 入口收敛为「右键菜单 → Chat」（双击留给拖拽/互动语义，避免误触）
- 面板 = 头像标题栏 + 气泡历史区 + 快捷按钮（今天吃什么/帮我点餐）+ 输入行
- 点单意图（"点个汉堡"）：写入 pet_requests.jsonl 交给 Skill 处理，
  并复制话术 + 拉起 WorkBuddy，形成 桌宠→Skill→MCP→桌宠 完整闭环
- 聊天后端：OpenAI 兼容接口（MCD_PET_LLM_* 环境变量）> 规则人格兜底
"""
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

FEED_DIR = Path.home() / '.mcd-pet-feeder'
REQUESTS_FILE = FEED_DIR / 'pet_requests.jsonl'
WB_APP_NAME = 'WorkBuddy'

STATE_DESC = {'calm': '很满足', 'craving': '有点馋', 'hungry': '饿了', 'alert': '饿扁了'}

SYSTEM_PROMPT = (
    '你是「麦门饲养员」里的麦当劳主题桌宠。性格：活泼、嘴馋、偶尔讨食、有梗。'
    '用中文回答，1-2 句话，口语化，可以带 emoji，总长不超过 40 字。'
    '当前状态：饱食度 {satiety}/100（{state_desc}），连吃麦门 {streak} 天。'
    '用户想吃东西时可以给出具体单品建议，并提示「右键点餐或对我说『点个XX』」。'
)

# 供点单意图识别的食物词表（与桌宠道具/Skill 档位对齐）
FOOD_WORDS = ['汉堡', '薯条', '炸鸡', '可乐', '套餐', '巨无霸', '板烧', '麦辣', '麦乐鸡',
              '吉士蛋', '麦满分', '麦旋风', '派', '咖啡']
ORDER_PAT = re.compile(r'(点|来|下单|要|吃).{0,6}(' + '|'.join(FOOD_WORDS) + ')')

TIME_SLOTS = [
    (5, 10.5, '早餐档：吉士蛋麦满分 + 大杯咖啡，一分钟取餐不迟到'),
    (10.5, 14, '午餐档：板烧鸡腿堡套餐 🍔 403kcal，或巨无霸套餐管饱'),
    (14, 17, '下午茶档：麦乐鸡 5 块 + 零卡可乐，小饿不背锅'),
    (17, 21, '晚餐档：巨无霸套餐，今天值得一顿好的'),
    (21, 24, '夜宵档：麦辣鸡翅 + 薯条，快乐不打烊'),
    (0, 5, '宵夜档：这个点还醒着？派蒙陪你，垫个派就好'),
]


def rule_reply(text: str, state: dict) -> str:
    low = text.lower()
    satiety = state.get('satiety', '?')
    if ORDER_PAT.search(low):
        m = ORDER_PAT.search(low)
        return f'__ORDER__:{m.group(2)}'
    if any(k in low for k in ['什么吃', '吃什么', '建议', '推荐']):
        return meal_suggestion() + '（对我说「点个XX」我就记下来）'
    if any(k in low for k in ['饿', '吃', '麦麦', '麦当劳', '麦门']):
        if isinstance(satiety, (int, float)) and satiety < 35:
            return f'咕噜噜…饱食度只剩 {satiety} 了，快让饲养员安排！（说「点个汉堡」就行）'
        return f'{"还好还好，饱食度 " + str(satiety) + "，暂时稳住。" if satiety != "?" else "嘿嘿，想吃麦门。"}（想吃就说「点个XX」）'
    if any(k in low for k in ['你是谁', '名字', '你叫', '什么宠物']):
        return '我是麦门饲养员的招牌宠！应急食品…啊不，心灵伙伴！'
    if any(k in low for k in ['你好', '在吗', 'hi', 'hello', '嗨']):
        return random.choice(['在呢在呢~', '呜嘎！叫我？', '嗨！今天也想吃麦门。'])
    if any(k in low for k in ['牛', '厉害', '乖']):
        return '嘿嘿，那今晚加个鸡腿？（悄悄说：薯条也行）'
    if any(k in low for k in ['再见', '拜拜', '忙']):
        return '去吧去吧，我蹲 here 等你投喂~'
    return random.choice(['（歪头看你）', '今天也想吃麦门。', '戳我干嘛，戳饿了可要你负责！',
                          '陪聊业务：已开通。投喂业务：随时恭候。'])


def meal_suggestion() -> str:
    hour = time.localtime().tm_hour + time.localtime().tm_min / 60.0
    for start, end, tip in TIME_SLOTS:
        if start <= hour < end:
            return tip
    return '任意时刻：巨无霸永远是对的'


def llm_reply(text: str, state: dict):
    base = os.environ.get('MCD_PET_LLM_BASE_URL')
    if not base:
        return None
    model = os.environ.get('MCD_PET_LLM_MODEL', 'qwen2.5:3b')
    key = os.environ.get('MCD_PET_LLM_API_KEY', 'EMPTY')
    url = base.rstrip('/') + '/chat/completions'
    prompt = SYSTEM_PROMPT.format(
        satiety=state.get('satiety', '?'),
        state_desc=STATE_DESC.get(state.get('state', 'calm'), ''),
        streak=state.get('streak', 0))
    payload = json.dumps({
        'model': model,
        'messages': [{'role': 'system', 'content': prompt},
                     {'role': 'user', 'content': text}],
        'max_tokens': 120,
        'temperature': 0.8,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {key}'})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            content = data['choices'][0]['message']['content'].strip()
            return content or None
    except Exception:
        return None


class MCDPetChat(QWidget):
    """桌宠聊天面板：气泡历史 + 快捷点单 + LLM/规则人格"""

    reply_ready = Signal(str)
    order_requested = Signal(str)   # 识别到点单意图 → 交给桥接层落盘并引导 WorkBuddy

    def __init__(self, state_provider, stats_provider=None, parent=None):
        super().__init__(parent)
        self.state_provider = state_provider
        self.stats_provider = stats_provider  # callable -> 吃麦日记摘要文本
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('麦门饲养员 · 派蒙')

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # 标题栏
        head = QHBoxLayout()
        title = QLabel('🍔 派蒙 · 麦门饲养员')
        title.setStyleSheet('font-weight:bold; font-size:13px; color:#333;')
        close_btn = QPushButton('×')
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            'QPushButton{border:none;background:transparent;color:#999;font-size:14px;}'
            'QPushButton:hover{color:#e53935;}')
        close_btn.clicked.connect(self.hide)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close_btn)
        root.addLayout(head)

        # 气泡历史区
        self.history = QTextBrowser()
        self.history.setMinimumSize(300, 190)
        self.history.setOpenExternalLinks(False)
        self.history.setStyleSheet(
            'QTextBrowser{border:none;background:transparent;}')
        root.addWidget(self.history)

        # 快捷按钮
        quick = QHBoxLayout()
        for label, handler in (('🍟 今天吃什么', self._suggest),
                               ('📝 帮我点餐', self._quick_order)):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                'QPushButton{border:1px solid #ffbc0d;border-radius:10px;'
                'background:#fff8e1;color:#7a5c00;padding:4px 10px;font-size:12px;}'
                'QPushButton:hover{background:#ffecb3;}')
            btn.clicked.connect(handler)
            quick.addWidget(btn)
        quick.addStretch(1)
        root.addLayout(quick)

        # 输入行
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText('说点什么…（回车发送，说「点个汉堡」直接下单）')
        row.addWidget(self.input, 1)
        send_btn = QPushButton('发送')
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet(
            'QPushButton{border:none;background:#ffbc0d;border-radius:10px;'
            'padding:6px 14px;font-weight:bold;color:#5d4037;}'
            'QPushButton:hover{background:#ffb300;}')
        send_btn.clicked.connect(self._send)
        row.addWidget(send_btn)
        root.addLayout(row)

        self.setStyleSheet(
            'QWidget{background:#ffffff;border:1px solid #eee;border-radius:12px;}')

        self.reply_ready.connect(self._on_reply_ready)
        self._append('pet', '呜嘎！我是派蒙…啊不，麦门饲养员的招牌宠！右键还能唤出菜单哦~')

    # ---- 对外 ----
    def toggle(self, pet_pos=None):
        if self.isVisible():
            self.hide()
            return
        if pet_pos is not None:
            x = pet_pos.x() - self.width() + 60
            y = pet_pos.y() - self.height() - 14
            self.move(max(8, x), max(8, y))
        self.show()
        self.raise_()
        self.input.setFocus()

    def push_demo(self, text='今天吃什么？'):
        """演示模式：自动发一句话（自测/GIF 录制）"""
        self.show()
        self.input.setText(text)
        self._send()

    # ---- 界面动作 ----
    def _suggest(self):
        self._append('user', '今天吃什么？')
        self._append('pet', meal_suggestion())
        self._bubble_after('🍽️ ' + meal_suggestion())

    def _quick_order(self):
        self._append('user', '帮我点餐')
        self.input.setText('点个汉堡')
        self._send()

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._append('user', text)
        low = text.lower()
        # 吃麦日记直查（数据沉淀可随时问答）
        if any(k in low for k in ['日记', '账单', '吃了多少', '花']):
            if self.stats_provider:
                self._append('pet', self.stats_provider())
                self._bubble_after('📄 ' + self.stats_provider())
            else:
                self._append('pet', '日记本还没开通~')
            return
        m = ORDER_PAT.search(low)
        if m:
            food = m.group(2)
            self._append('pet', f'好耶！「{food}」已记到饲养员的小本本上 📋')
            self.order_requested.emit(food)
            return
        state = {}
        try:
            state = dict(self.state_provider() or {})
        except Exception:
            pass
        threading.Thread(target=self._worker, args=(text, state), daemon=True).start()

    # ---- 后端 ----
    def _worker(self, text, state):
        reply = llm_reply(text, state)
        if not reply:
            reply = rule_reply(text, state)
        self.reply_ready.emit(reply)

    def _on_reply_ready(self, reply):
        if reply.startswith('__ORDER__:'):
            food = reply.split(':', 1)[1]
            self._append('pet', f'好耶！「{food}」已记到饲养员的小本本上 📋')
            self.order_requested.emit(food)
            return
        self._append('pet', reply)
        self._bubble_after('🐼 ' + reply)

    def _bubble_after(self, text):
        # 延迟一拍再弹气泡，避免与面板内容重叠观感
        threading.Timer(0.4, lambda: self._emit_bubble(text)).start()

    def _emit_bubble(self, text):
        try:
            self.bubble_emit(text)
        except Exception:
            pass

    bubble_emit = None  # 由桥接层注入: callable(str)

    # ---- 历史渲染 ----
    def _append(self, who: str, text: str):
        color = '#ffbc0d' if who == 'user' else '#ffffff'
        name = '你' if who == 'user' else '🐼'
        align = 'right' if who == 'user' else 'left'
        bg = '#fff3cd' if who == 'user' else '#f5f5f5'
        html = (f'<div style="text-align:{align};margin:3px 0;">'
                f'<span style="background:{bg};color:#333;border-radius:8px;'
                f'padding:4px 8px;display:inline-block;max-width:85%;">'
                f'<b style="color:#e65100;font-size:11px;">{name}</b> '
                f'{text}</span></div>')
        self.history.append(html)
        sb = self.history.verticalScrollBar()
        sb.setValue(sb.maximum())
