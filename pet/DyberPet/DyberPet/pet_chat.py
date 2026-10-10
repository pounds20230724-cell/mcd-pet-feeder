"""
麦门饲养员·桌宠直聊模块（mcd-pet-feeder）
==========================================
让桌宠可以直接对话，而不必经由 WorkBuddy：
- 双击桌宠 或 右键菜单「和它聊天」打开聊天输入框
- 后端优先级：
  1. OpenAI 兼容接口（环境变量 MCD_PET_LLM_BASE_URL / MCD_PET_LLM_API_KEY / MCD_PET_LLM_MODEL，
     可指向 Ollama: http://localhost:11434/v1 、DeepSeek、GLM 等）
  2. 内置「麦门人格」规则回复（离线兜底，结合饱食度/连吃状态）
- 回复以桌宠气泡呈现（复用 DyberPet 气泡系统）
"""
import json
import os
import random
import threading
import urllib.error
import urllib.request
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

STATE_DESC = {'calm': '很满足', 'craving': '有点馋', 'hungry': '饿了', 'alert': '饿扁了'}

SYSTEM_PROMPT = (
    '你是「麦门饲养员」里的麦当劳主题桌宠。性格：活泼、嘴馋、偶尔讨食、有梗。'
    '用中文回答，1-2 句话，口语化，可以带 emoji，总长不超过 40 字。'
    '当前状态：饱食度 {satiety}/100（{state_desc}），连吃麦门 {streak} 天。'
    '用户问吃/饿相关话题时结合状态回答，并可以提示用户让 WorkBuddy 里的饲养员 Skill 帮忙点餐。'
)

RULE_GREETS = ['在呢在呢~', '呜嘎！叫我？', '汪！哦不，呜嘎？']

RULE_RULES = [
    (['饿', '吃', '麦麦', '麦当劳', '麦门'],
     ['咕噜噜…确实有点饿了，饱食度只剩 {satiety} 了喂！',
      '让 WorkBuddy 里的饲养员给我点个喵当劳吧~',
      '汉堡！薯条！我全都要！（饱食度 {satiety}，你看着办）']),
    (['你是谁', '名字', '你叫', '什么宠物'],
     ['我是麦门饲养员的招牌宠！应急食品…不对，是心灵伙伴！',
      '派蒙！啊不，我是麦门限定版小家伙~']),
    (['你好', '在吗', 'hi', 'hello', '嗨'],
     RULE_GREETS),
    (['牛', '厉害', '乖'],
     ['嘿嘿，那今晚加个鸡腿？（悄悄说：薯条也行）']),
    (['再见', '拜拜', '忙'],
     ['去吧去吧，我蹲 here 等你投喂~']),
]

RULE_DEFAULT = ['（歪头看你）', '今天也想吃麦门。', '戳我干嘛，戳饿了可要你负责！',
                '陪聊业务：已开通。投喂业务：随时恭候。']


def rule_reply(text: str, state: dict) -> str:
    low = text.lower()
    for keys, pool in RULE_RULES:
        if any(k in low for k in keys):
            return random.choice(pool).format(satiety=state.get('satiety', '?'))
    return random.choice(RULE_DEFAULT)


def llm_reply(text: str, state: dict):
    """调用 OpenAI 兼容接口；未配置或失败时返回 None（由调用方回落到规则回复）"""
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
            return data['choices'][0]['message']['content'].strip() or None
    except Exception:
        return None


class MCDPetChat(QWidget):
    """桌宠聊天输入框：无边框小窗，回车发送，回复经信号交给桥接层弹气泡"""

    reply_ready = Signal(str)

    def __init__(self, state_provider, parent=None):
        super().__init__(parent)
        self.state_provider = state_provider  # callable -> {satiety, state, streak}
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('和它聊天')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.tip = QLabel('和它聊两句（回车发送）')
        self.tip.setStyleSheet('color:#888; font-size:11px;')
        self.input = QLineEdit()
        self.input.setPlaceholderText('比如：你饿了吗？')
        self.send_btn = QPushButton('发送')
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        layout.addWidget(self.tip)
        layout.addLayout(row)
        self.setStyleSheet(
            'QWidget{background:#fff;border:1px solid #ddd;border-radius:8px;}'
            'QLineEdit{border:1px solid #ddd;border-radius:6px;padding:4px 8px;}'
            'QPushButton{border:none;background:#ffbc0d;border-radius:6px;'
            'padding:4px 12px;font-weight:bold;}')
        self.resize(300, 64)

        self.input.returnPressed.connect(self._send)
        self.send_btn.clicked.connect(self._send)
        self.reply_ready.connect(self._noop)  # 保持信号活跃

    # Qt 信号需要连接才生效；此处桥接层已连接，占位避免警告
    def _noop(self, _):
        pass

    # ---- 对外 ----
    def toggle(self, pet_pos=None):
        if self.isVisible():
            self.hide()
            return
        if pet_pos is not None:
            x, y = pet_pos.x() - self.width() + 40, pet_pos.y() - self.height() - 16
            self.move(max(0, x), max(0, y))
        self.show()
        self.raise_()
        self.input.setFocus()

    def push_demo(self, text='你饿了吗？'):
        """演示模式：自动发一句话（用于 GIF 录制与自测）"""
        self.show()
        self.input.setText(text)
        self._send()

    # ---- 内部 ----
    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        state = {}
        try:
            state = dict(self.state_provider() or {})
        except Exception:
            pass
        threading.Thread(target=self._worker, args=(text, state), daemon=True).start()

    def _worker(self, text, state):
        reply = llm_reply(text, state)
        if not reply:
            reply = rule_reply(text, state)
        self.reply_ready.emit(reply)
