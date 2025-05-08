# -*- coding: utf-8 -*-

import unittest
import time
import os
import sys
import logging
import tracemalloc

# 启用tracemalloc来跟踪资源分配
tracemalloc.start()

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server.common import conf
from server.common.events import MsgCSLogin, MsgCSMoveto
from server.dispatcher import Service, Dispatcher
from server.network.netStream import NetStream, RpcProxy
from server.network.simpleHost import SimpleHost
from server.common.timer import TimerManager
from server.common.logger import logger_instance, Logger


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
	def __init__(self, netstream, is_client=False):
		self.netstream = netstream
		# 传递self对象而不是函数引用
		self.caller = RpcProxy(self, netstream)
		self.stat = 0
		self.is_client = is_client
		self.logger = logger_instance.get_logger('GameEntity')
		self.entity_type = "Client" if is_client else "Server"

	def destroy(self):
		self.logger.info(f"{self.entity_type} Entity被销毁")
		self.caller = None
		self.netstream = None

	# CLIENT CODE
	@EXPOSED
	def recv_msg_from_server(self, stat, msg):
		self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
		self.stat = stat
		self.logger.info(f'客户端状态更新为: {stat}')
		self.logger.info('客户端发送退出请求')
		self.caller.remote_call("exit")
	###

	# SERVER CODE
	@EXPOSED
	def hello_world_from_client(self, stat, msg):
		self.logger.info(f'服务器收到客户端消息: stat={stat}, msg={msg}')
		self.stat = stat + 1
		self.logger.info(f'服务器状态更新为: {self.stat}')
		self.logger.info('服务器发送响应消息回客户端')
		self.caller.remote_call("recv_msg_from_server", self.stat, msg)

	@EXPOSED
	def exit(self):
		self.logger.info('服务器收到客户端退出请求')
		self.stat = -1
		self.logger.info('服务器状态设置为: -1')
	###

class ServerTest(unittest.TestCase):
	def setUp(self):
		# 设置日志记录器
		self.logger = logger_instance.get_logger('UnitTest')
		self.log_file = logger_instance._log_files.get('UnitTest', '')
		self.logger.info("====== 测试开始 ======")
		
		self._head1 = MsgCSLogin('test', 0)
		self._head2 = MsgCSMoveto(3, 5)
		self.logger.info(f"初始化消息头 - Login: name={self._head1.name}, icon={self._head1.icon}")
		self.logger.info(f"初始化消息头 - MoveTo: x={self._head2.x}, y={self._head2.y}")

		self._dispatcher = Dispatcher()
		self._dispatcher.register(100, TestService())
		self.logger.info("初始化Dispatcher并注册TestService")

		self.count = 0
		self.logger.info("计数器初始化为0")

	def tearDown(self):
		self._head1 = None
		self._head2 = None
		self._dispatcher = None
		self.count = 0
		
		# 显示tracemalloc统计信息
		current, peak = tracemalloc.get_traced_memory()
		self.logger.info(f"当前内存使用: {current / 10**6:.1f}MB; 峰值: {peak / 10**6:.1f}MB")
		
		self.logger.info("====== 测试结束 ======")
		self.logger.info(f"完整日志保存在: {os.path.abspath(self.log_file)}")

	def addCount(self):
		self.count += 1

	def test_Parser(self):
		self.logger.info("开始测试解析器...")
		# test header
		self.logger.info("测试消息头解析...")
		data = self._head1.marshal()
		head = MsgCSLogin().unmarshal(data)
		self.assertEqual(self._head1.name, head.name)
		self.assertEqual(self._head1.icon, head.icon)
		self.logger.info(f"Login消息解析成功: name={head.name}, icon={head.icon}")

		data = self._head2.marshal()
		head = MsgCSMoveto().unmarshal(data)
		self.assertEqual(self._head2.x, head.x)
		self.assertEqual(self._head2.y, head.y)
		self.logger.info(f"MoveTo消息解析成功: x={head.x}, y={head.y}")

		# test dispatcher
		self.logger.info("测试消息分发器...")
		msg = MsgService()
		msg.sid = 100
		msg.cid = 10
		result = self._dispatcher.dispatch(msg, 'client1')
		self.assertEqual(result, 'client1')
		self.logger.info(f"分发消息(sid=100,cid=10)成功, 结果: {result}")
		
		msg.cid = 20
		result = self._dispatcher.dispatch(msg, 'client2')
		self.assertEqual(result, 'client2')
		self.logger.info(f"分发消息(sid=100,cid=20)成功, 结果: {result}")

		# 设置测试环境
		host = None
		sock = None
		client_entity = None
		server_entity = None
		
		try:
			# test network
			self.logger.info("开始测试网络功能...")
			host = SimpleHost()
			host.startup(2000)
			self.logger.info("服务器启动在端口2000")
			
			sock = NetStream()
			last = time.time()
			sock.connect('127.0.0.1', 2000)
			self.logger.info("客户端尝试连接到127.0.0.1:2000")

			stat = 0
			last = time.time()
			sock.nodelay(1)
			self.logger.info("设置客户端socket为nodelay模式")

			connection_timeout = time.time() + 5.0  # 5秒连接超时
			test_complete = False
			
			while not test_complete:
				time.sleep(0.1)
				
				# 检查连接超时
				if time.time() > connection_timeout and stat == 0:
					self.logger.error("连接超时！")
					break
					
				### CLIENT SECTION
				sock.process()
				if stat == 0:
					if sock.status() == conf.NET_STATE_ESTABLISHED:
						stat = 1
						self.logger.info("客户端连接成功建立")
						client_entity = GameEntity(sock, is_client=True)
						client_entity.caller.remote_call("hello_world_from_client", stat, 'Hello, world !!')
						self.logger.info("客户端发送RPC调用: hello_world_from_client")
						last = time.time()
				else:
					recv_data = sock.recv()
					if len(recv_data) > 0:
						self.logger.info(f"客户端收到数据: {len(recv_data)} 字节")
						client_entity.caller.parse_rpc(recv_data)
				####

				### SERVER SECTION
				host.process()
				event, wparam, data = host.read()
				if event < 0:
					continue
				
				if event == conf.NET_CONNECTION_NEW:
					self.logger.info("服务器接收到新连接")
					code, client_netstream = host.getClient(wparam)
					self.assertGreaterEqual(code, 0)
					self.logger.info(f"服务器创建客户端连接, 连接ID: {wparam}")
					server_entity = GameEntity(client_netstream)

				elif event == conf.NET_CONNECTION_DATA:
					self.logger.info(f"服务器收到数据: {len(data)} 字节")
					server_entity.caller.parse_rpc(data)
					
					if server_entity.stat == -1:
						self.logger.info("服务器接收到客户端退出信号")
						server_entity.destroy()
						host.closeClient(wparam)
						self.logger.info("关闭客户端连接并关闭服务器")
						test_complete = True
						break
				###

			# test timer
			self.logger.info("开始测试定时器...")
			TimerManager.addRepeatTimer(0.15, self.addCount)
			self.logger.info("添加重复定时器，间隔0.15秒")
			last = time.time()
			while 1:
				time.sleep(0.01)
				TimerManager.scheduler()

				if time.time() - last > 1.0:
					break

			self.assertEqual(self.count, 6)
			self.logger.info(f"定时器测试完成，计数器值: {self.count}，预期值: 6")
		
		finally:
			# 确保所有资源都被正确关闭
			self.logger.info("清理测试资源...")
			try:
				# 先关闭实体
				if client_entity:
					client_entity.destroy()
					client_entity = None
				if server_entity:
					server_entity.destroy()
					server_entity = None
				
				# 关闭客户端socket
				if sock:
					try:
						if sock.status() != conf.NET_STATE_STOP:  # 使用已知的NET_STATE_STOP代替NET_STATE_CLOSED
							sock.close()
							self.logger.info("客户端socket已关闭")
					except Exception as e:
						self.logger.error(f"关闭客户端socket时出错: {str(e)}")
					sock = None
				
				# 关闭服务器
				if host:
					try:
						host.shutdown()
						self.logger.info("服务器已关闭")
					except Exception as e:
						self.logger.error(f"关闭服务器时出错: {str(e)}")
					host = None
					
			except Exception as e:
				self.logger.error(f"清理资源时发生错误: {str(e)}")

if __name__ == '__main__':
	try:
		# 设置unittest的输出级别，使其显示更详细的信息
		unittest.main(verbosity=2)
	except KeyboardInterrupt:
		print("\n用户中断测试，正在清理资源...")
		# 使用单例日志记录器来记录清理操作
		cleanup_logger = logger_instance.get_logger('Cleanup')
		cleanup_logger.info("正在清理全局资源...")
		
		# 尝试使用更安全的方法来清理资源
		try:
			# 通过垃圾回收帮助回收未引用的socket
			import gc
			gc.collect()  # 强制垃圾回收
			# 调用单例的清理方法
			logger_instance.cleanup_resources()
		except Exception as e:
			cleanup_logger.error(f"垃圾回收时出错: {str(e)}")