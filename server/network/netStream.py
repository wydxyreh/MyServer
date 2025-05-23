# -*- coding: utf-8 -*-

#=========================================================================
#
# NOTE: We use 4 bytes little endian (x86) by default.
# If you choose a different endian, you may have to modify header length.
#
#=========================================================================

from server.common import conf
import errno
import socket
import struct
import json

from server.common.logger import logger_instance

class RpcProxy(object):
	"""远程过程调用代理，负责RPC调用的序列化和反序列化"""
	def __init__(self, entity, netstream):
		super(RpcProxy, self).__init__()
		
		# 初始化日志记录器
		self.logger = logger_instance.get_logger('RpcProxy')
		
		if hasattr(entity, 'EXPOSED_FUNC'):
			entity.EXPOSED_FUNC = {} # 初始化EXPOSED_FUNC
			
		# 获取所有标记为exposed的方法
		for name in dir(entity):
			method = getattr(entity, name)
			if hasattr(method, '__exposed__') and method.__exposed__:
				entity.EXPOSED_FUNC[name] = method
		
		self.entity = entity
		self.netstream = netstream
		self._is_closed = False

	def close(self):
		"""关闭RPC代理，清理引用"""
		self._is_closed = True
		self.entity = None
		self.netstream = None

	def remote_call(self, funcname, *args):
		try:
			# 检查RPC代理是否已关闭
			if self._is_closed:
				self.logger.warning(f"尝试通过已关闭的RPC代理调用方法: {funcname}")
				return
				
			# 直接使用netstream对象
			netstream = self.netstream
			if not netstream or netstream.status() != conf.NET_STATE_ESTABLISHED:
				self.logger.warning(f"尝试对未连接或已关闭的socket调用RPC: {funcname}")
				return
				
			info = b""  # 确保使用字节串
			if netstream.use_protobuf:
				try:
					from server.proto import sample_pb2
					entity_message = sample_pb2.EntityMessage()
					entity_message.funcname = funcname
					entity_message.funcargs = json.dumps(args)
					info = entity_message.SerializeToString()
					self.logger.debug(f"使用ProtoBuf序列化RPC调用: {funcname}, 参数: {args}")
				except Exception as e:
					self.logger.error(f"ProtoBuf序列化失败: {str(e)}")
					# 回退到JSON格式
					data = {
						'method': funcname,
						'args': args,
					}
					info = json.dumps(data).encode('utf-8')
			else:
				# 使用JSON格式
				data = {
					'method': funcname,
					'args': args,
				}
				info = json.dumps(data).encode('utf-8')
				self.logger.debug(f"使用JSON序列化RPC调用: {funcname}, 参数: {args}")
				
			self.logger.debug(f"发送RPC调用: {funcname}, 数据长度: {len(info)}")
			netstream.send(info)
		except Exception as e:
			self.logger.error(f"RPC调用失败 {funcname}: {str(e)}")
			import traceback
			traceback.print_exc()

	def parse_rpc(self, data):
		try:
			# 检查RPC代理是否已关闭
			if self._is_closed:
				self.logger.warning("尝试通过已关闭的RPC代理解析数据")
				return
			
			func = None
			args = []
			entity = self.entity  # 修正: owner -> entity
			netstream = self.netstream
			
			if not entity or not netstream:
				self.logger.warning("尝试解析RPC时，发现对象已被回收")
				return
				
			if netstream.use_protobuf:
				try:
					from server.proto import sample_pb2
					entity_message = sample_pb2.EntityMessage()
					entity_message.ParseFromString(data)
					method = entity_message.funcname
					args = json.loads(entity_message.funcargs)
				except Exception as e:
					self.logger.error(f"ProtoBuf解析失败: {str(e)}")
					return
					
				# 修正: 使用entity替代owner
				if hasattr(entity, 'EXPOSED_FUNC') and method in entity.EXPOSED_FUNC:
					func = entity.EXPOSED_FUNC[method]
				else:
					func = getattr(entity, method, None)
			else:
				try:
					info = json.loads(data.decode('utf-8'))
					method = info.get('method', None)
					if method is None:
						self.logger.warning("收到无效RPC请求: 缺少method字段")
						return
					args = info.get("args", [])
				except json.JSONDecodeError as e:
					self.logger.error(f"JSON解析失败: {str(e)}")
					return
				
				# 修正: 使用entity替代owner
				if hasattr(entity, 'EXPOSED_FUNC') and method in entity.EXPOSED_FUNC:
					func = entity.EXPOSED_FUNC[method]
				else:
					func = getattr(entity, method, None)
				
			if func:
				if hasattr(entity, 'EXPOSED_FUNC') and method in entity.EXPOSED_FUNC or getattr(func, '__exposed__', False):
					try:
						func(*args)
					except Exception as e:
						self.logger.error(f"RPC方法 {method} 执行失败: {str(e)}")
				else:
					self.logger.warning(f'无效RPC调用，未授权方法: {method}')
			else:
				self.logger.warning(f'无效RPC调用，方法不存在: {method}')
		except Exception as e:
			self.logger.error(f"RPC解析过程中发生错误: {str(e)}")
			import traceback
			traceback.print_exc()


class NetStream(object):
	def __init__(self):
		super(NetStream, self).__init__()
		
		self.logger = logger_instance.get_logger('NetStream')
		self.sock = None		# socket object
		self.send_buf = b''		# send buffer (bytes)
		self.recv_buf = b''		# recv buffer (bytes)

		self.state = conf.NET_STATE_STOP
		self.errd = (errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK)
		self.conn = (errno.EISCONN, 10057, 10053)
		self.errc = 0

		self.use_protobuf = True  # use google protobuf or not, default is True
		
		return

	def status(self):
		return self.state

	# connect the remote server
	def connect(self, address, port):
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.setblocking(0)
		self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
		self.sock.connect_ex((address, port))
		self.state = conf.NET_STATE_CONNECTING
		self.send_buf = b''
		self.recv_buf = b''
		self.errc = 0

		return 0

	# close connection
	def close(self):
		self.state = conf.NET_STATE_STOP

		if not self.sock:
			return 0
		try:
			# 确保socket被关闭前先关闭所有方向的数据传输
			try:
				self.sock.shutdown(socket.SHUT_RDWR)
			except:
				pass
			self.sock.close()
		except Exception as e:
			import sys
			print(f"关闭socket时出错: {str(e)}", file=sys.stderr)

		self.sock = None
		self.send_buf = b''
		self.recv_buf = b''

		return 0
	
	# assign a socket to netstream
	def assign(self, sock):
		self.close()
		self.sock = sock
		self.sock.setblocking(0)
		self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
		self.state = conf.NET_STATE_ESTABLISHED
		
		self.send_buf = b''
		self.recv_buf = b''

		return 0
	
	# set tcp nodelay flag
	def nodelay(self, nodelay = 0):
		if not 'TCP_NODELAY' in socket.__dict__:
			return -1
		if self.state != 2:
			return -2

		self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, nodelay)

		return 0

	# update
	def process(self):
		if self.state == conf.NET_STATE_STOP:
			return 0
		if self.state == conf.NET_STATE_CONNECTING:
			self.__tryConnect()
		if self.state == conf.NET_STATE_ESTABLISHED:
			self.__tryRecv()
		if self.state == conf.NET_STATE_ESTABLISHED:
			self.__trySend()

		return 0

	def __tryConnect(self):
		if (self.state == conf.NET_STATE_ESTABLISHED):
			return 1
		if (self.state != conf.NET_STATE_CONNECTING):
			return -1
		try:
			self.sock.recv(0)
		except socket.error as e:
			code = e.errno
			if code in self.conn:
				return 0
			if code in self.errd:
				self.state = conf.NET_STATE_ESTABLISHED
				self.recv_buf = b''
				return 1
			
			self.close()
			return -1

		self.state = conf.NET_STATE_ESTABLISHED

		return 1

	# append data into send_buf with a size header
	def send(self, data):
		size = len(data) + conf.NET_HEAD_LENGTH_SIZE
		wsize = struct.pack(conf.NET_HEAD_LENGTH_FORMAT, size)
		self.__sendRaw(wsize + data)

		return 0

	# append data to send_buf then try to send it out (__try_send)
	def __sendRaw(self, data):
		self.send_buf = self.send_buf + data
		self.process()

		return 0

	# send data from send_buf until block (reached system buffer limit)
	def __trySend(self):
		wsize = 0
		if (len(self.send_buf) == 0):
			return 0

		try:
			wsize = self.sock.send(self.send_buf)
		except socket.error as e:
			code = e.errno
			if not code in self.errd:
				self.errc = code
				self.close()

				return -1

		self.send_buf = self.send_buf[wsize:]
		return wsize

	# recv an entire message from recv_buf
	def recv(self):
		rsize = self.__peekRaw(conf.NET_HEAD_LENGTH_SIZE)
		if (len(rsize) < conf.NET_HEAD_LENGTH_SIZE):
			return b''

		size = struct.unpack(conf.NET_HEAD_LENGTH_FORMAT, rsize)[0]
		if size <= 0:
			# 处理无效的大小值
			self.__recvRaw(conf.NET_HEAD_LENGTH_SIZE)  # 清除无效头
			return b''
			
		if (len(self.recv_buf) < size):
			return b''

		self.__recvRaw(conf.NET_HEAD_LENGTH_SIZE)

		return self.__recvRaw(size - conf.NET_HEAD_LENGTH_SIZE)

	# try to receive all the data into recv_buf
	def __tryRecv(self):
		rdata = b''
		while 1:
			text = b''
			try:
				text = self.sock.recv(1024)
				if not text:
					self.errc = 10000
					self.close()

					return -1
			except socket.error as e:
				code = e.errno
				if not code in self.errd:
					self.errc = code
					self.close()
					return -1
			if text == b'':
				break

			rdata = rdata + text

		self.recv_buf = self.recv_buf + rdata
		return len(rdata)
	
	# peek data from recv_buf (read without delete it)
	def __peekRaw(self, size):
		self.process()
		if len(self.recv_buf) == 0:
			return b''

		if size > len(self.recv_buf):
			size = len(self.recv_buf)
		rdata = self.recv_buf[0:size]

		return rdata
	
	# read data from recv_buf (read and delete it from recv_buf)
	def __recvRaw(self, size):
		rdata = self.__peekRaw(size)
		size = len(rdata)
		self.recv_buf = self.recv_buf[size:]

		return rdata