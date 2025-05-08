# -*- coding: utf-8 -*-
from server.common import conf
from server.common.header import SimpleHeader

class MsgCSLogin(SimpleHeader):
	def __init__(self, name = '', icon = -1):
		super().__init__(conf.MSG_CS_LOGIN)
		self.appendParam('name', name, 's')
		self.appendParam('icon', icon, 'i')

class MsgSCConfirm(SimpleHeader):
	def __init__(self, uid = 0, result = 0):
		super().__init__(conf.MSG_SC_CONFIRM)
		self.appendParam('uid', uid, 'i')
		self.appendParam('result', result, 'i')

class MsgCSMoveto(SimpleHeader):
	def __init__ (self, x = 0, y = 0):
		super().__init__(conf.MSG_CS_MOVETO)
		self.appendParam('x', x, 'i')
		self.appendParam('y', y, 'i')

class MsgSCCMoveto(SimpleHeader):
	def __init__ (self, uid = 0, x = 0, y = 0):
		super().__init__(conf.MSG_SC_MOVETO)
		self.appendParam('uid', uid, 'i')
		self.appendParam('x', x, 'i')
		self.appendParam('y', y, 'i')