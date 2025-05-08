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
		self.stat = 0
		self.is_client = is_client
		self.logger = logger_instance.get_logger('GameEntity')
		self.entity_type = "Client" if is_client else "Server"
		
		# 显式关闭ProtoBuf，使用JSON传输
		if netstream:
			netstream.use_protobuf = False
			
		# 创建RPC代理
		self.caller = RpcProxy(self, netstream)
		
		self.logger.info(f"创建{self.entity_type} Entity, use_protobuf={False if netstream else 'N/A'}")

	def destroy(self):
		self.logger.info(f"{self.entity_type} Entity被销毁")
		if self.caller:
			self.caller.close()
			self.caller = None
		self.netstream = None

	# CLIENT CODE
	@EXPOSED
	def recv_msg_from_server(self, stat, msg):
		self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
		self.stat = stat
		self.logger.info(f'客户端状态更新为: {stat}')
		self.logger.info('客户端发送退出请求')
		if self.caller:
			self.caller.remote_call("exit")
	###

	# SERVER CODE
	@EXPOSED
	def hello_world_from_client(self, stat, msg):
		self.logger.info(f'服务器收到客户端消息: stat={stat}, msg={msg}')
		self.stat = stat + 1
		self.logger.info(f'服务器状态更新为: {self.stat}')
		self.logger.info('服务器发送响应消息回客户端')
		if self.caller:
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
			
			# 设置全局测试超时
			global_timeout = time.time() + 10.0  # 10秒全局超时
			last_activity_time = time.time()
			
			while not test_complete:
				current_time = time.time()
				# 添加短暂的休眠以减轻CPU负担
				time.sleep(0.05)
				
				# 检查全局超时
				if current_time > global_timeout:
					self.logger.error("测试全局超时！")
					break
					
				# 检查连接超时
				if current_time > connection_timeout and stat == 0:
					self.logger.error("连接超时！")
					break
					
				# 检测活动超时（如果5秒内没有任何活动）
				if current_time - last_activity_time > 5.0:
					self.logger.error("测试活动超时！5秒内没有活动")
					break
					
				### CLIENT SECTION
				sock.process()
				if stat == 0:
					if sock.status() == conf.NET_STATE_ESTABLISHED:
						stat = 1
						self.logger.info("客户端连接成功建立")
						client_entity = GameEntity(sock, is_client=True)
						
						# 等待一小段时间以确保连接稳定
						time.sleep(0.1)
						
						self.logger.info("客户端准备发送RPC调用")
						client_entity.caller.remote_call("hello_world_from_client", stat, 'Hello, world !!')
						self.logger.info("客户端发送RPC调用: hello_world_from_client")
						last = time.time()
						last_activity_time = time.time()
				else:
					recv_data = sock.recv()
					if len(recv_data) > 0:
						self.logger.info(f"客户端收到数据: {len(recv_data)} 字节")
						try:
							client_entity.caller.parse_rpc(recv_data)
							last_activity_time = time.time()  # 更新活动时间
						except Exception as e:
							self.logger.error(f"客户端解析RPC数据失败: {str(e)}")
				####

				### SERVER SECTION
				self.logger.info(f"SERVER SECTION - 开始处理服务器事件")
				host.process()
				self.logger.info(f"host.process() 完成，准备读取事件")
				
				# 检查是否有事件在队列中
				queue_size = len(host.queue)
				self.logger.info(f"事件队列长度: {queue_size}")
				
				event, wparam, data = host.read()
				self.logger.info(f"读取到事件: {event}, wparam: {wparam}, 数据长度: {len(data) if isinstance(data, bytes) else 'N/A'}")
				
				if event >= 0:
					last_activity_time = time.time()  # 更新活动时间
					
				if event < 0:
					continue
				
				if event == conf.NET_CONNECTION_NEW:
					self.logger.info("服务器接收到新连接")
					code, client_netstream = host.getClient(wparam)
					if code >= 0:
						self.logger.info(f"服务器创建客户端连接, 连接ID: {wparam}")
						server_entity = GameEntity(client_netstream)
					else:
						self.logger.error(f"获取客户端连接失败，错误码: {code}")

				elif event == conf.NET_CONNECTION_DATA:
					if isinstance(data, bytes):
						self.logger.info(f"服务器收到数据: {len(data)} 字节")
						try:
							if server_entity:
								server_entity.caller.parse_rpc(data)
								
								# 检查服务器状态，如果收到退出信号则结束测试
								if server_entity.stat == -1:
									self.logger.info("服务器接收到客户端退出信号")
									server_entity.destroy()
									host.closeClient(wparam)
									self.logger.info("关闭客户端连接并关闭服务器")
									test_complete = True
									break
							else:
								self.logger.error("服务器实体未初始化，无法处理数据")
						except Exception as e:
							self.logger.error(f"服务器解析RPC数据失败: {str(e)}")
							import traceback
							traceback.print_exc()
					else:
						self.logger.error(f"服务器收到非字节类型数据: {type(data)}")
						
				elif event == conf.NET_CONNECTION_LEAVE:
					self.logger.info(f"客户端离开, ID: {wparam}")
					if server_entity:
						server_entity.destroy()
						server_entity = None
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
						if sock.status() != conf.NET_STATE_STOP:
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
				
				# 强制垃圾回收以释放资源
				import gc
				gc.collect()
					
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