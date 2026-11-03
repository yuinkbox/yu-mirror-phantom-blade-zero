"""User run intent survives temporary focus/menu interruptions, but never a manual stop."""
import math


class SessionControl:
    def __init__(self):
        self.enabled=False
        self.identity=None
        self.last_time=None
        self.advanced_at=None
        self.clear_since=None
        self.menu_until=0
        self.reason='已暂停'

    def start(self, now, state):
        self.enabled=True
        self.identity=(state.player,state.component,state.world)
        self.last_time=state.game_time
        self.advanced_at=now
        self.clear_since=None
        self.reason='已启用，等待游戏恢复操作'

    def stop(self, reason='已暂停'):
        self.enabled=False
        self.clear_since=None
        self.reason=reason

    def update(self, now, foreground, state, menu_key=False):
        if not self.enabled:return False
        if not state.valid or not math.isfinite(state.hp) or state.hp<=0:
            self.stop(state.reason if not state.valid else '角色死亡，请按 F8 重新开始')
            return False
        if self.identity!=(state.player,state.component,state.world) or not math.isfinite(state.game_time):
            self.stop('角色或场景已变化，请按 F8 重新开始')
            return False
        if state.game_time < self.last_time-.001:
            self.stop('场景时间已变化，请按 F8 重新开始')
            return False
        if state.game_time>self.last_time+.00001:self.advanced_at=now
        self.last_time=state.game_time
        if menu_key:self.menu_until=now+.35
        reason = ('已切出游戏，返回后自动恢复' if not foreground else
                  '菜单或操作受限，关闭后自动恢复' if getattr(state,'input_blocked',False) or now<self.menu_until else
                  '游戏已暂停，继续游戏后自动恢复' if now-self.advanced_at>.18 else '')
        if reason:
            self.clear_since=None
            self.reason=reason
            return False
        if self.clear_since is None:self.clear_since=now
        if now-self.clear_since<.35:
            self.reason='游戏已恢复，正在接续辅助…'
            return False
        self.reason='运行中'
        return True
