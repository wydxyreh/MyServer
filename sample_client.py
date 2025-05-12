# -*- coding: utf-8 -*-
import sys
import os
import time
import argparse
import socket
import threading
import json
import traceback
import signal
from server.common import conf
from server.network.netStream import NetStream, RpcProxy
from server.common.logger import logger_instance
from server.common.timer import TimerManager

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def EXPOSED(func):
    """装饰器，标记可被RPC调用的函数"""
    func.__exposed__ = True
    return func

def log_function(func):
    """装饰器，记录函数调用的名称、参数和执行完成情况，包括详细的参数内容和返回值"""
    def wrapper(*args, **kwargs):
        instance = args[0] if args else None
        logger = instance.logger if hasattr(instance, 'logger') else logger_instance.get_logger('FunctionLogger')
        
        # 记录函数调用信息
        args_repr = [repr(a) for a in args[1:]]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        
        # 获取调用者信息
        caller_info = ""
        if hasattr(instance, 'id'):
            caller_info += f"客户端ID={instance.id} "
        if hasattr(instance, 'username') and instance.username:
            caller_info += f"用户={instance.username} "
        
        # 获取调用位置信息
        import inspect
        frame = inspect.currentframe().f_back
        filename = frame.f_code.co_filename
        line_number = frame.f_lineno
        caller_location = f"{filename}:{line_number}"
        
        logger.info(f"{caller_info}调用函数 {func.__name__}({signature}) 位置:{caller_location}")
        
        # 执行函数
        import time
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = (time.time() - start_time) * 1000  # 毫秒
        
        # 记录返回值（如果不是None且不是特别大）
        result_info = ""
        if result is not None:
            result_str = repr(result)
            if len(result_str) > 500:  # 限制返回值日志长度
                result_str = result_str[:500] + "..."
            result_info = f", 返回值: {result_str}"
        
        # 记录函数执行完成
        logger.info(f"{caller_info}函数 {func.__name__} 执行完成，耗时: {execution_time:.2f}ms{result_info}")
        
        return result
    return wrapper

class ClientNetworkManager:
    """客户端网络管理器，整合了NetworkSocket的心跳机制和NetStream的RPC功能"""
    @log_function
    def __init__(self, host='127.0.0.1', port=2000):
        self.host = host
        self.port = port
        self.socket = NetStream()
        self.logger = logger_instance.get_logger('ClientNetwork')
        self.connected = False
        self.heartbeat_interval = 5.0  # 心跳间隔(秒)
        self.last_heartbeat_time = 0
        self.setup_time = 0
        self.heartbeat_count = 0
        self.recv_buffer = []  # 缓存接收到的数据
        self.send_buffer = []  # 缓存发送请求
        self.connection_state = "disconnected"  # 连接状态: disconnected, connecting, connected
        
        # 网络统计
        self.bytes_sent = 0
        self.bytes_received = 0
        self.latency_samples = []
        self.last_ping_time = 0
        
    @log_function
    def connect(self):
        """连接到服务器"""
        if self.connection_state == "connecting":
            return False
            
        try:
            self.connection_state = "connecting"
            self.logger.info(f"尝试连接服务器: {self.host}:{self.port}")
            
            # 尝试DNS解析和连接
            try:
                socket.getaddrinfo(self.host, self.port)
                self.socket.connect(self.host, self.port)
            except (socket.gaierror, socket.error, OSError) as e:
                self.logger.error(f"连接失败: {str(e)}")
                self.connection_state = "disconnected"
                return False
                
            self.socket.nodelay(1)  # 启用TCP_NODELAY
            self.setup_time = time.time()
            self.last_heartbeat_time = self.last_ping_time = self.setup_time
            
            # 等待连接建立
            connection_timeout = time.time() + 5.0  # 5秒连接超时
            while time.time() < connection_timeout:
                self.socket.process()
                if self.socket.status() == conf.NET_STATE_ESTABLISHED:
                    self._on_connection_established()
                    return True
                time.sleep(0.01)
            
            self.logger.error("连接超时")
            self._close_socket()
            return False
            
        except Exception as e:
            self.logger.error(f"连接服务器时出错: {str(e)}")
            self.logger.error(traceback.format_exc())
            self.connection_state = "disconnected"
            return False
    
    @log_function
    def _on_connection_established(self):
        """连接建立成功后的处理"""
        self.connected = True
        self.connection_state = "connected"
        self.logger.info(f"已成功连接到服务器 {self.host}:{self.port}")
    
    @log_function
    def _close_socket(self):
        """安全关闭套接字"""
        try:
            self.socket.close()
        except Exception as e:
            self.logger.warning(f"关闭套接字时发生异常: {str(e)}")
        finally:
            self.connection_state = "disconnected"
    
    def process(self):
        """处理网络事件，返回接收到的数据"""
        if not self.connected:
            # 已断开连接，不再尝试重连
            return None
            
        try:
            self.socket.process()
            
            # 检查连接状态
            if self.socket.status() != conf.NET_STATE_ESTABLISHED:
                self.logger.warning("连接已断开")
                self.connected = False
                self.connection_state = "disconnected"
                return None
                
            # 发送心跳包
            self.send_heartbeat()
            
            # 处理发送缓冲区和接收数据
            self._process_send_buffer()
            data = self.socket.recv()
            if data:
                self.bytes_received += len(data)
                self.recv_buffer.append(data)
                
            # 返回并清空接收缓冲区
            if self.recv_buffer:
                result = self.recv_buffer
                self.recv_buffer = []
                return result
                
            return None
            
        except Exception as e:
            self.logger.error(f"处理网络事件时出错: {str(e)}")
            self.connected = False
            self.connection_state = "disconnected"
            return None
    
    def _process_send_buffer(self):
        """处理发送缓冲区中的数据"""
        if not self.send_buffer:
            return
            
        try:
            while self.send_buffer:
                data = self.send_buffer.pop(0)
                self.socket.send(data)
                self.bytes_sent += len(data)
        except Exception as e:
            self.logger.error(f"发送数据出错: {str(e)}")
            if 'data' in locals():
                self.send_buffer.insert(0, data)
    
    @log_function
    def send(self, data):
        """发送数据到服务器"""
        if not self.connected:
            self.logger.warning("尝试发送数据但未连接到服务器，将数据加入发送缓冲区")
            self.send_buffer.append(data)
            return False
            
        self.send_buffer.append(data)
        return True
    
    def send_heartbeat(self):
        """发送心跳包以保持连接活跃"""
        cur_time = time.time()
        if cur_time - self.last_heartbeat_time >= self.heartbeat_interval:
            self.last_heartbeat_time = cur_time
            self.heartbeat_count += 1
            self.last_ping_time = cur_time
            return True
        return False
    
    @log_function
    def update_latency(self, server_time):
        """更新延迟信息"""
        if self.last_ping_time > 0:
            latency = (time.time() - self.last_ping_time) * 1000  # 毫秒
            self.latency_samples.append(latency)
            # 只保留最近10个延迟样本
            if len(self.latency_samples) > 10:
                self.latency_samples.pop(0)
            
            # 每10次心跳记录一次平均延迟
            if self.heartbeat_count % 10 == 0:
                avg_latency = sum(self.latency_samples) / len(self.latency_samples) if self.latency_samples else 0
                self.logger.debug(f"网络延迟: {avg_latency:.2f}ms")
    
    @log_function
    def disconnect(self):
        """主动断开连接"""
        self.logger.info("客户端主动断开连接")
        self._close_socket()
        self.connected = False
        self.connection_state = "disconnected"
    
    @log_function
    def close(self):
        """关闭连接并清理资源"""
        if self.socket:
            self.logger.info("关闭网络连接")
            self._close_socket()
            self.connected = False

class ClientEntity:
    """客户端实体类，用于与服务器交互"""
    EXPOSED_FUNC = {}
    
    @log_function
    def __init__(self, network_manager):
        self.network_manager = network_manager
        self.socket = network_manager.socket
        self.caller = RpcProxy(self, self.socket)
        self.stat = 0
        self.logger = logger_instance.get_logger('SampleClient')
        self.running = True
        self.pending_messages = []  # 待处理消息队列
        
        # 认证相关
        self.authenticated = False
        self.username = ""
        self.password = ""
        self.token = None  # 只在内存中临时存储token
        self.login_in_progress = False
        self.login_attempts = 0
        self.auth_status = {
            "is_logged_in": False,
            "login_time": None,
            "last_token_refresh": None,
            "login_error": None
        }
        
        # 用户数据
        self.user_data = {}
        self.user_data_exists = False  # 标记用户数据是否存在
        
        # 数据操作状态记录
        self.data_operations = {
            "save": {
                "last_attempt_time": None,
                "last_success_time": None,
                "success": False,
                "error_message": None,
                "pending": False
            },
            "load": {
                "last_attempt_time": None,
                "last_success_time": None,
                "success": False,
                "error_message": None,
                "pending": False
            }
        }
        
        # RPC超时设置
        self.pending_rpc_calls = {}  # 存储待响应的RPC调用 {call_id: (timestamp, func_name)}
        self.rpc_timeout = 10.0  # RPC调用超时时间(秒)
        
        # 广播消息相关
        self.achievement_broadcast_received = False  # 标志位：是否接收到成就广播
        self.last_achievement_broadcast = None  # 最近接收到的成就广播内容
        self.achievement_broadcasts_history = []  # 存储历史广播消息
    
    @log_function
    def disconnect(self):
        """主动断开与服务器的连接
        Returns:
            bool: 是否成功发送断开请求
        """
        try:
            # 先向服务器发送退出请求
            if self.network_manager.connected and self.caller:
                self.logger.info("向服务器发送退出请求")
                self.caller.remote_call("exit")
                print("[系统] 已发送断开连接请求到服务器")
                
            # 然后断开网络连接
            if self.network_manager:
                self.network_manager.disconnect()
                return True
        except Exception as e:
            self.logger.error(f"断开连接时出错: {str(e)}")
            return False
    
    @log_function
    def destroy(self):
        """销毁客户端实体，释放资源"""
        self.logger.info("客户端实体被销毁")
        if self.caller:
            self.caller.close()
        self.caller = self.socket = self.network_manager = None
    
    @log_function
    def process_network(self):
        """处理网络事件"""
        if not self.running:
            return
            
        # 处理网络并获取数据
        data_list = self.network_manager.process()
        
        # 如果连接断开，等待重连由network_manager内部逻辑处理
        if not self.network_manager.connected:
            # 连接断开时清除token，确保下次使用账密登录
            if self.token:
                self.logger.info("连接断开，清除token")
                self.token = None
            return
            
        # 处理接收到的数据
        if data_list:
            self.pending_messages.extend(data_list)
    
    @log_function
    def process_messages(self):
        """处理消息队列"""
        if not self.pending_messages:
            return
            
        # 批量处理消息
        for data in self.pending_messages:
            try:
                self.caller.parse_rpc(data)
            except Exception as e:
                self.logger.error(f"解析RPC数据时出错: {str(e)}")
                
        # 清空消息队列
        self.pending_messages = []
    
    @log_function
    def login(self):
        """执行登录操作
        
        尝试使用以下优先级登录:
        1. 如果有token，先尝试使用token登录
        2. 否则，使用用户名和密码登录
        """
        if not self.network_manager.connected:
            self.logger.warning("尝试登录但未连接到服务器")
            print("[登录] 错误: 未连接到服务器")
            return False
            
        if self.login_in_progress:
            self.logger.warning("登录操作已在进行中")
            print("[登录] 请稍候，登录操作正在进行中...")
            return False
            
        self.login_in_progress = True
        
        try:
            # 先尝试使用token登录（如果有）
            if self.token:
                self.logger.info("使用token尝试登录")
                self.caller.remote_call("client_login", None, None, self.token)
            else:
                # 使用账号密码登录
                self.logger.info(f"使用账号密码尝试登录: {self.username}")
                self.caller.remote_call("client_login", self.username, self.password)
                
            return True
        except Exception as e:
            self.logger.error(f"发送登录请求时出错: {str(e)}")
            self.login_in_progress = False
            print(f"[登录] 错误: {str(e)}")
            return False
    
    @log_function
    def logout(self):
        """执行登出操作"""
        if not self.authenticated:
            self.logger.warning("尝试登出但未登录")
            print("[登出] 错误: 您尚未登录")
            return False
            
        try:
            self.logger.info("发送登出请求")
            self.caller.remote_call("client_logout")
            return True
        except Exception as e:
            self.logger.error(f"发送登出请求时出错: {str(e)}")
            print(f"[登出] 错误: {str(e)}")
            return False
    
    @EXPOSED
    @log_function
    def login_success(self, token):
        """登录成功回调"""
        try:
            if not token:
                self.logger.warning("认证成功但收到空token！这是服务端错误！")
                self.authenticated = False
                
                # 更新认证状态
                self.auth_status = {
                    "is_logged_in": False,
                    "login_time": None,
                    "last_token_refresh": None,
                    "login_error": "服务器返回无效token"
                }
                
                print(f"[登录] 异常: 服务器返回无效token")
                return
                
            self.authenticated = True
            self.token = token
            self.login_in_progress = False
            self.login_attempts = 0  # 重置登录尝试次数
            
            # 更新认证状态
            current_time = time.time()
            self.auth_status = {
                "is_logged_in": True,
                "login_time": current_time,
                "last_token_refresh": current_time,
                "login_error": None
            }
            
            # 不在日志中显示完整的token，只显示部分
            masked_token = token[:5] + "..." + token[-5:] if len(token) > 10 else "***"
            self.logger.info(f"认证成功，获取有效token: {masked_token}")
            
            print(f"[登录] 成功! 用户: {self.username}")
                
        except Exception as e:
            self.logger.error(f"处理登录成功回调时出错: {str(e)}")
            # 更新认证失败状态
            self.auth_status["login_error"] = str(e)
    
    @EXPOSED
    @log_function
    def token_invalid(self, reason):
        """token无效的回调"""
        self.logger.warning(f"Token无效: {reason}")
        print(f"[登录] Token无效: {reason}")
        self.token = None  # 清除无效token
        
        # 尝试使用账号密码重新登录
        print("[登录] 正在使用账号密码重新登录...")
        self.login_in_progress = False
        self.login()
    
    @EXPOSED
    @log_function
    def process_existing_session(self, message):
        """处理已存在会话的回调"""
        self.logger.info(f"服务器正在处理旧会话: {message}")
        print(f"[登录] {message}")
    
    @EXPOSED
    @log_function
    def logout_success(self):
        """登出成功回调"""
        self.authenticated = False
        self.token = None
        self.logger.info("登出成功")
        print("[登出] 成功!")
    
    @EXPOSED
    @log_function
    def forced_logout(self, reason):
        """被强制登出的回调"""
        self.authenticated = False
        self.token = None  # 清除token
        self.logger.warning(f"您被强制登出: {reason}")
        print(f"[系统] 您被强制登出: {reason}")
    
    @EXPOSED
    @log_function
    def server_shutdown(self, message):
        """服务器关闭的回调"""
        self.logger.warning(f"服务器关闭通知: {message}")
        print(f"[系统] 服务器通知: {message}")
        print("服务器即将关闭，客户端将在3秒后退出...")
        
        # 设置定时器在3秒后关闭客户端
        threading.Timer(3.0, self._exit_program).start()
    
    @log_function
    def _exit_program(self):
        """关闭程序"""
        self.logger.info("程序即将退出")
        self.running = False
        print("程序正在退出...")
    
    @EXPOSED
    @log_function
    def login_failed(self, reason):
        """登录失败回调"""
        self.authenticated = False
        self.token = None
        self.login_in_progress = False
        
        # 更新认证状态
        self.auth_status = {
            "is_logged_in": False,
            "login_time": None,
            "last_token_refresh": None,
            "login_error": reason
        }
        
        self.logger.warning(f"登录失败: {reason}")
        print(f"[登录] 失败: {reason}")
    
    @EXPOSED
    @log_function
    def userdata_update(self, data_json):
        """接收用户数据更新"""
        current_time = time.time()
        
        try:
            self.user_data = json.loads(data_json)
            self.user_data_exists = True  # 标记数据已存在
            
            # 更新加载成功状态
            self.data_operations["load"] = {
                "last_attempt_time": self.data_operations["load"]["last_attempt_time"],
                "last_success_time": current_time,
                "success": True,
                "error_message": None,
                "pending": False
            }
            
            self.logger.info("接收用户数据")
            # 截断显示数据，避免过长输出
            data_preview = data_json[:50] + ("..." if len(data_json) > 50 else "")
            print(f"[数据] 已接收用户数据: {data_preview}")
        except Exception as e:
            self.logger.error(f"解析数据失败: {str(e)}")
            print(f"[数据] 加载失败: {str(e)}")
            
            # 更新加载失败状态
            self.data_operations["load"]["error_message"] = f"解析数据失败: {str(e)}"
            self.data_operations["load"]["success"] = False
            self.data_operations["load"]["pending"] = False
    
    @EXPOSED
    @log_function
    def save_success(self):
        """保存数据成功回调"""
        current_time = time.time()
        
        # 更新保存操作状态
        self.data_operations["save"] = {
            "last_attempt_time": self.data_operations["save"]["last_attempt_time"],
            "last_success_time": current_time,
            "success": True,
            "error_message": None,
            "pending": False
        }
        
        self.logger.info("数据保存成功")
        print("[数据] 保存成功")
        
        # 如果是被强制登出状态，则在数据保存成功后断开连接
        if not self.authenticated and hasattr(self, 'token') and self.token is None:
            self.logger.info("强制登出状态下数据保存成功，现在断开连接")
            print("[系统] 您的数据已保存，正在断开连接...")
            self.disconnect()
        
    @log_function
    def save_user_data(self, data_json):
        """向服务器保存用户数据
        
        Args:
            data_json: JSON格式的数据，可以是字符串或JavaScript对象
        """
        current_time = time.time()
        
        # 更新保存尝试状态
        self.data_operations["save"] = {
            "last_attempt_time": current_time,
            "last_success_time": self.data_operations["save"]["last_success_time"],
            "success": self.data_operations["save"]["success"],
            "error_message": None,
            "pending": True
        }
        
        if not self.authenticated:
            self.logger.warning("尝试保存数据但未认证")
            print("[数据] 错误: 请先登录")
            
            # 更新保存失败状态
            self.data_operations["save"]["error_message"] = "未认证，请先登录"
            self.data_operations["save"]["pending"] = False
            self.data_operations["save"]["success"] = False
            
            return False
            
        try:
            self.logger.info("发送数据保存请求到服务器")
            # 直接将JSON数据传递给服务器，不做额外转换
            self.caller.remote_call("userdata_save", data_json)
            return True
        except Exception as e:
            self.logger.error(f"保存数据时出错: {str(e)}")
            print(f"[数据] 保存错误: {str(e)}")
            
            # 更新保存失败状态
            self.data_operations["save"]["error_message"] = str(e)
            self.data_operations["save"]["pending"] = False
            self.data_operations["save"]["success"] = False
            
            return False
            
    @log_function
    def load_user_data(self):
        """从服务器加载最新的用户数据，结果通过userdata_update回调获取"""
        current_time = time.time()
        
        # 更新加载尝试状态
        self.data_operations["load"] = {
            "last_attempt_time": current_time,
            "last_success_time": self.data_operations["load"]["last_success_time"],
            "success": self.data_operations["load"]["success"],
            "error_message": None,
            "pending": True
        }
        
        if not self.authenticated:
            self.logger.warning("尝试加载数据但未认证")
            print("[数据] 错误: 请先登录")
            
            # 更新加载失败状态
            self.data_operations["load"]["error_message"] = "未认证，请先登录"
            self.data_operations["load"]["pending"] = False
            self.data_operations["load"]["success"] = False
            
            return False
            
        try:
            self.logger.info("发送数据加载请求到服务器")
            self.caller.remote_call("userdata_load")
            return True
        except Exception as e:
            self.logger.error(f"请求加载数据时出错: {str(e)}")
            print(f"[数据] 加载错误: {str(e)}")
            
            # 更新加载失败状态
            self.data_operations["load"]["error_message"] = str(e)
            self.data_operations["load"]["pending"] = False
            self.data_operations["load"]["success"] = False
            
            return False
    
    @EXPOSED
    @log_function
    def data_error(self, message):
        """数据操作错误回调"""
        self.logger.warning(f"数据操作错误: {message}")
        print(f"[数据] 错误: {message}")
        
        # 检查哪个操作正在等待结果
        if self.data_operations["save"]["pending"]:
            self.data_operations["save"] = {
                "last_attempt_time": self.data_operations["save"]["last_attempt_time"],
                "last_success_time": self.data_operations["save"]["last_success_time"],
                "success": False,
                "error_message": message,
                "pending": False
            }
            
        if self.data_operations["load"]["pending"]:
            self.data_operations["load"] = {
                "last_attempt_time": self.data_operations["load"]["last_attempt_time"],
                "last_success_time": self.data_operations["load"]["last_success_time"],
                "success": False,
                "error_message": message,
                "pending": False
            }
        
    @EXPOSED
    @log_function
    def data_not_found(self, message):
        """数据不存在回调"""
        self.logger.info(f"数据不存在: {message}")
        print(f"[数据] 提示: {message}")
        # 可以在这里设置一个标志，让用户知道需要创建新数据
        self.user_data_exists = False
        print("[数据] 您需要创建新的用户数据")
        
        # 更新加载结果状态
        if self.data_operations["load"]["pending"]:
            current_time = time.time()
            self.data_operations["load"] = {
                "last_attempt_time": self.data_operations["load"]["last_attempt_time"],
                "last_success_time": current_time,  # 虽然没找到数据，但操作本身成功了
                "success": True,
                "error_message": "数据不存在，需要创建新数据",
                "pending": False,
                "data_exists": False
            }
    
    @EXPOSED
    @log_function
    def auth_error(self, message):
        """认证错误回调"""
        self.logger.warning(f"认证错误: {message}")
        print(f"[认证] 错误: {message}")
            
    @EXPOSED
    @log_function
    def recv_msg_from_server(self, stat, msg):
        """接收服务器消息的回调函数"""
        self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
        self.stat = stat
        self.network_manager.update_latency(time.time())
        print(f"[服务器消息] {msg}")
    
    @EXPOSED
    @log_function
    def exit_confirmed(self):
        """服务器确认退出的回调函数"""
        self.logger.info('服务器确认客户端退出')
        print("[系统] 服务器已确认退出请求")
    
    @EXPOSED
    @log_function
    def on_save_data_request(self):
        """响应服务器的数据保存请求"""
        self.logger.info("服务器请求保存用户数据")
        print("[数据] 服务器请求保存您的数据...")
    
        if hasattr(self, 'user_data') and self.user_data:
            try:
                data_json = json.dumps(self.user_data)
                self.caller.remote_call("userdata_save", data_json)
                self.logger.info("已响应服务器请求，发送数据保存请求")
                print("[数据] 已发送数据到服务器")
            except Exception as e:
                self.logger.error(f"响应服务器保存数据请求时失败: {str(e)}")
                print(f"[数据] 保存失败: {str(e)}")
        else:
            self.logger.warning("服务器请求保存数据，但客户端没有可保存的数据")
            print("[数据] 没有可保存的数据")
    
    @log_function
    def get_recent_achievement_broadcast(self, clear_flag=False):
        """获取最近的成就广播内容，可选择是否清除标志位
        
        Args:
            clear_flag: 是否在获取后清除接收标志位，默认为False
            
        Returns:
            dict/None: 最近的广播内容，如果没有则返回None
        """
        broadcast_content = None
        
        if self.achievement_broadcast_received and self.last_achievement_broadcast:
            broadcast_content = self.last_achievement_broadcast
            
            # 如果需要清除标志位
            if clear_flag:
                self.achievement_broadcast_received = False
                
        return broadcast_content
    
    @log_function
    def has_new_achievement_broadcast(self):
        """检查是否有新的成就广播
        
        Returns:
            bool: 是否有新的未处理的成就广播
        """
        return self.achievement_broadcast_received
    
    @EXPOSED
    @log_function
    def connection_closed(self, reason):
        """服务器主动断开连接的回调"""
        self.logger.warning(f"服务器主动断开连接: {reason}")
        print(f"[系统] 服务器已断开连接: {reason}")
    
        # 在断开连接前尝试保存数据
        if hasattr(self, 'user_data') and self.user_data:
            self.logger.info("连接断开前尝试保存用户数据")
            print("[数据] 连接断开前尝试保存数据...")
            try:
                data_json = json.dumps(self.user_data)
                self.caller.remote_call("userdata_save", data_json)
                self.logger.info("连接断开前已发送数据保存请求")
            except Exception as e:
                self.logger.error(f"连接断开前保存数据失败: {str(e)}")
                print(f"[数据] 保存失败: {str(e)}")
    
        # 短暂延迟后主动关闭连接，确保有时间发送最后的消息
        import threading
        threading.Timer(0.5, self.disconnect).start()
        self.logger.info("计划在0.5秒后关闭客户端连接")
    
    @EXPOSED
    @log_function
    def pong_response(self, message):
        """服务器ping测试响应"""
        self.logger.info(f"收到服务器ping响应: {message}")
        print(f"[连接测试] {message}")
        
    @EXPOSED
    @log_function
    def achievement_broadcast(self, username, threshold_title, threshold_value):
        """接收服务器广播的成就通知
        
        Args:
            username: 达到阈值的用户名
            threshold_title: 阈值的名称/标题
            threshold_value: 达到的具体阈值值
        """
        # 记录成就广播
        self.logger.info(f"收到成就广播: 用户 {username} 达到 {threshold_title} ({threshold_value})")
        
        # 判断是否是自己的成就
        is_own_achievement = self.username == username
        
        # 构建广播内容
        broadcast_content = {
            "username": username,
            "threshold_title": threshold_title,
            "threshold_value": threshold_value,
            "timestamp": time.time(),
            "is_own_achievement": is_own_achievement
        }
        
        # 更新广播接收标志位和内容
        self.achievement_broadcast_received = True
        self.last_achievement_broadcast = broadcast_content
        
        # 添加到历史广播记录
        self.achievement_broadcasts_history.append(broadcast_content)
        # 保持历史记录在合理范围内（最多保存10条）
        if len(self.achievement_broadcasts_history) > 10:
            self.achievement_broadcasts_history.pop(0)
        
        # 构建显示信息
        if is_own_achievement:
            message = f"[成就] 恭喜! 您已达到 '{threshold_title}' 成就! 击杀数: {threshold_value}"
        else:
            message = f"[系统公告] 玩家 {username} 已达到 '{threshold_title}' 成就! 击杀数: {threshold_value}"
        
        # 显示成就通知，可以在此处添加UI通知逻辑
        print(message)

@log_function
def setup_timers(client_entity, network_manager):
    """设置客户端定时器，处理网络事件和消息队列
    
    Args:
        client_entity: 客户端实体实例
        network_manager: 网络管理器实例
    """
    logger = logger_instance.get_logger('SampleClient')
    
    # 1. 添加定时器调度器任务 - 最高优先级
    TimerManager.addRepeatTimer(0.001, TimerManager.scheduler)
    
    # 2. 添加10ms定时器处理网络事件
    def process_network():
        if client_entity.running:
            # 处理网络并获取数据
            data_list = network_manager.process()
            
            # 如果连接断开，处理token清除
            if not network_manager.connected and client_entity.token:
                logger.info("连接断开，清除token")
                client_entity.token = None
                return
                
            # 处理接收到的数据
            if data_list:
                client_entity.pending_messages.extend(data_list)
                
    TimerManager.addRepeatTimer(0.01, process_network)  # 10ms定时器
    
    # 3. 添加100ms定时器处理消息队列
    def process_messages():
        if not client_entity.running or not client_entity.pending_messages:
            return
            
        # 批量处理消息
        for data in client_entity.pending_messages:
            try:
                client_entity.caller.parse_rpc(data)
            except Exception as e:
                logger.error(f"解析RPC数据时出错: {str(e)}")
                
        # 清空消息队列
        client_entity.pending_messages = []
        
    TimerManager.addRepeatTimer(0.1, process_messages)  # 100ms定时器
    
    # 4. 添加退出检查定时器
    def check_exit():
        if not client_entity.running:
            # 清理资源
            client_entity.destroy()
            network_manager.close()
            logger.info("客户端正常退出")
            # 停止所有定时器
            TimerManager.clear_all_timers()
            # 退出程序
            os._exit(0)
    
    TimerManager.addRepeatTimer(0.5, check_exit)

@log_function
def main():
    """客户端主函数，初始化网络并设置定时器处理事件"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='客户端')
    parser.add_argument('--host', default='127.0.0.1', help='服务器地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=2000, help='服务器端口 (默认: 2000)')
    parser.add_argument('--username', default=None, help='登录用户名')
    parser.add_argument('--password', default=None, help='登录密码')
    args = parser.parse_args()
    
    # 设置日志
    logger = logger_instance.get_logger('SampleClient')
    log_file = logger_instance._log_files.get('SampleClient', '')
    logger.info(f"客户端日志文件: {os.path.abspath(log_file)}")
    
    # 创建网络管理器
    network_manager = ClientNetworkManager(args.host, args.port)
    
    try:
        # 连接服务器
        connected = False
        while not connected:
            if network_manager.connect():
                connected = True
                logger.info("成功连接到服务器")
            else:
                logger.warning("连接服务器失败，3秒后重试...")
                print("[系统] 连接服务器失败，正在尝试重连...")
                time.sleep(2)  # 等待3秒后重试
        
        # 创建客户端实体
        client_entity = ClientEntity(network_manager)
        
        # 设置登录凭据(如果提供)
        if args.username:
            client_entity.username = args.username
        if args.password:
            client_entity.password = args.password
        
        # 注册信号处理函数
        def setup_signal_handlers():
            try:
                if hasattr(signal, 'SIGINT'):
                    def signal_handler(signum, frame):
                        logger.info(f"收到退出信号")
                        client_entity.running = False
                        print("\n正在退出客户端...")
                    
                    signal.signal(signal.SIGINT, signal_handler)
                    logger.debug("已注册SIGINT信号处理器")
            except Exception as e:
                logger.warning(f"无法注册信号处理器: {str(e)}")
        
        setup_signal_handlers()
        
        # 设置定时器
        setup_timers(client_entity, network_manager)
        
        # 主循环，确保定时器能被执行
        try:
            while client_entity.running:
                # 每次循环都调度定时器任务
                TimerManager.scheduler()
                time.sleep(0.001)  # 释放CPU时间片
        except KeyboardInterrupt:
            client_entity.running = False
            logger.info("用户中断，客户端退出")
            
    except KeyboardInterrupt:
        logger.info("用户中断，客户端退出")
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")
        logger.error(traceback.format_exc())
    finally:
        # 确保资源被释放
        if 'network_manager' in locals():
            network_manager.close()
        
if __name__ == "__main__":
    main()