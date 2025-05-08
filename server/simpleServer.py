# -*- coding: utf-8 -*-

from server.network.simpleHost import SimpleHost
from server.dispatcher import Dispatcher
from server.common import conf

class SimpleServer(object):
	
	def __init__(self):
		super(SimpleServer, self).__init__()

		self.entities = {}
		self.host = SimpleHost()
		self.dispatcher = Dispatcher()

		return

	def generateEntityID(self):
		raise NotImplementedError

	def registerEntity(self, entity):
		eid = self.generateEntityID()
		entity.id = eid

		self.entities[eid] = entity

		return

	def tick(self):
		# 处理网络事件
		self.host.process()
		
		# 处理消息队列
		while True:
			event_type, hid, data = self.host.read()
			if event_type == -1:
				break
				
			# 处理连接事件
			if event_type == conf.NET_CONNECTION_NEW and self.host.onConnected:
				self.host.onConnected(hid, self.host.clients[hid & conf.MAX_HOST_CLIENTS_INDEX])
				
			# 处理断开连接事件
			elif event_type == conf.NET_CONNECTION_LEAVE and self.host.onDisconnected:
				self.host.onDisconnected(hid)
				
			# 处理数据事件
			elif event_type == conf.NET_CONNECTION_DATA and self.host.onData:
				self.host.onData(hid, data)
				
		# 更新实体
		for eid, entity in self.entities.items():
			# Note: you can not delete entity in tick.
			# you may cache delete items and delete in next frame
			# or just use items.
			entity.tick()


