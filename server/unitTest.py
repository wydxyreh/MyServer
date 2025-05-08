# -*- coding: utf-8 -*-

import unittest
import time


from server.common import conf
from server.common.events import MsgCSLogin, MsgCSMoveto
from server.dispatcher import Service, Dispatcher
from server.network.netStream import NetStream, RpcProxy
from server.network.simpleHost import SimpleHost
from server.common.timer import TimerManager


class TestService(Service):
	def __init__(self, sid = 0):
		super(TestService, self).__init__(sid)
		commands = {
			10 : self.f,
			20 : self.f,
		}
		self.registers(commands)
	
	def f(self, msg, owner):
		return owner

class MsgService(object):
	pass


def EXPOSED(func):
	func.__exposed__ = True
	return func

class GameEntity(object):
	EXPOSED_FUNC = {}
	def __init__(self, netstream):
		self.netstream = netstream
		self.caller = RpcProxy(self, netstream)
		self.stat = 0

	def destroy(self):
		self.caller = None
		self.netstream = None


	# CLIENT CODE
	@EXPOSED
	def recv_msg_from_server(self, stat, msg):
		print('client recv msg from server:', stat, msg)
		self.stat = stat

		self.caller.remote_call("exit")
	###

	# SERVER CODE
	@EXPOSED
	def hello_world_from_client(self, stat, msg):
		print('server recv msg from client:', stat, msg)
		self.stat = stat + 1
		self.caller.remote_call("recv_msg_from_server", self.stat, msg)

	@EXPOSED
	def exit(self):
		print('client exit, set stat to -1:')
		self.stat = -1
	###

class ServerTest(unittest.TestCase):
	def setUp(self):
		self._head1 = MsgCSLogin('test', 0)
		self._head2 = MsgCSMoveto(3, 5)

		self._dispatcher = Dispatcher()
		self._dispatcher.register(100, TestService())

		self.count = 0

	def tearDown(self):
		self._head1 = None
		self._head2 = None

		self._dispatcher = None

		self.count = 0

	def addCount(self):
		self.count += 1

	def test_Parser(self):
		# test header
		
		data = self._head1.marshal()
		head = MsgCSLogin().unmarshal(data)
		self.assertEqual(self._head1.name, head.name)
		self.assertEqual(self._head1.icon, head.icon)

		data = self._head2.marshal()
		head = MsgCSMoveto().unmarshal(data)
		self.assertEqual(self._head2.x, head.x)
		self.assertEqual(self._head2.y, head.y)

		# test dispatcher
		msg = MsgService()
		msg.sid = 100
		msg.cid = 10
		self.assertEqual(self._dispatcher.dispatch(msg, 'client1'), 'client1')
		msg.cid = 20
		self.assertEqual(self._dispatcher.dispatch(msg, 'client2'), 'client2')

		# test network
		host = SimpleHost()
		host.startup(2000)
		sock = NetStream()
		last = time.time()
		sock.connect('127.0.0.1', 2000)

		stat = 0
		last = time.time()
		sock.nodelay(1)

		client_entity = None
		server_entity = None

		while 1:
			time.sleep(0.1)
			
			### CLIENT SECTION
			sock.process()
			if stat == 0:
				if sock.status() == conf.NET_STATE_ESTABLISHED:
					stat = 1
					client_entity = GameEntity(sock)
					client_entity.caller.remote_call("hello_world_from_client", stat, 'Hello, world !!')
					last = time.time()
			else:
				recv_data = sock.recv()
				if len(recv_data) > 0:
					client_entity.caller.parse_rpc(recv_data)
			####

			### SERVER SECTION
			host.process()
			event, wparam, data = host.read()
			if event < 0:
				continue
			
			if event == conf.NET_CONNECTION_NEW:
				code, client_netstream = host.getClient(wparam)
				self.assertGreaterEqual(code, 0)
				server_entity = GameEntity(client_netstream)


			elif event == conf.NET_CONNECTION_DATA:
				server_entity.caller.parse_rpc(data)
				
				if server_entity.stat == -1:
					server_entity.destroy()
					host.closeClient(wparam)
					host.shutdown()
					break
			###

			
		# test timer
		TimerManager.addRepeatTimer(0.15, self.addCount)
		last = time.time()
		while 1:
			time.sleep(0.01)
			TimerManager.scheduler()

			if time.time() - last > 1.0:
				break

		self.assertEqual(self.count, 6)

		return

if __name__ == '__main__':
	unittest.main()
