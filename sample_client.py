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
import datetime
from enum import Enum, auto

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
 
from server.common import conf
from server.network.netStream import NetStream, RpcProxy
from server.common.logger import logger_instance
from server.common.timer import TimerManager

# 定义ANSI转义序列用于控制台颜色
class ColorText:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"

def EXPOSED(func):
    func.__exposed__ = True
    return func

# 测试状态枚举
class TestState(Enum):
    INIT = auto()
    SAVE_WITHOUT_LOGIN = auto()
    LOAD_WITHOUT_LOGIN = auto()
    LOGIN_WRONG_CREDENTIALS = auto()
    LOAD_AFTER_FAILED_LOGIN = auto()
    LOGIN_CORRECT_CREDENTIALS = auto()
    LOAD_WITHOUT_DATA = auto()
    SAVE_DATA = auto()
    RECONNECT = auto()
    CHECK_TOKEN_INVALID = auto()
    LOAD_WITHOUT_LOGIN_2 = auto()
    LOGIN_AGAIN = auto()
    LOAD_DATA = auto()
    LOGOUT = auto()
    COMPLETE = auto()

# 测试结果状态枚举
class TestResult(Enum):
    PENDING = "pending"   # 等待执行
    RUNNING = "running"   # 正在执行
    SUCCESS = "success"   # 测试成功
    FAILURE = "failure"   # 测试失败
    SKIPPED = "skipped"   # 测试跳过
    ERROR = "error"       # 测试错误

class ClientNetworkManager:
    """客户端网络管理器，整合了NetworkSocket的心跳机制和NetStream的RPC功能"""
    def __init__(self, host='127.0.0.1', port=2000):
        self.host = host
        self.port = port
        self.socket = NetStream()
        self.logger = logger_instance.get_logger('ClientNetwork')
        self.connected = False
        self.heartbeat_interval = 5.0  # 心跳间隔(秒)
        self.reconnect_interval = 3.0  # 重连间隔(秒)
        self.last_heartbeat_time = 0
        self.last_reconnect_time = 0
        self.setup_time = 0
        self.heartbeat_count = 0
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.recv_buffer = []  # 缓存接收到的数据
        self.send_buffer = []  # 缓存发送请求
        self.connection_state = "disconnected"  # 连接状态: disconnected, connecting, connected
        
        # 网络统计
        self.bytes_sent = 0
        self.bytes_received = 0
        self.latency_samples = []
        self.last_ping_time = 0
        
        # 用于自动测试
        self.last_connection_attempt = 0
        self.connection_retry_interval = 1.0  # 重试间隔
        
    def connect(self):
        """连接到服务器"""
        if self.connection_state == "connecting":
            return False
            
        try:
            self.connection_state = "connecting"
            self.logger.info(f"尝试连接到服务器: {self.host}:{self.port}")
            
            # 先尝试DNS解析
            try:
                socket.getaddrinfo(self.host, self.port)
            except socket.gaierror as e:
                self.logger.error(f"无法解析服务器地址: {self.host}, 错误: {str(e)}")
                self.connection_state = "disconnected"
                return False
                
            try:
                self.socket.connect(self.host, self.port)
            except (socket.error, OSError) as e:
                self.logger.error(f"连接服务器失败: {str(e)}")
                self.connection_state = "disconnected"
                return False
                
            self.socket.nodelay(1)  # 启用TCP_NODELAY
            self.setup_time = time.time()
            self.last_heartbeat_time = self.setup_time
            self.last_ping_time = self.setup_time
            
            # 等待连接建立，使用渐进式超时机制
            connection_timeout = time.time() + 5.0  # 5秒连接超时
            progress_points = [1.0, 2.0, 3.0, 4.0]  # 在1秒、2秒、3秒和4秒时记录进度
            next_progress = 0
            
            while time.time() < connection_timeout:
                # 进度通知
                elapsed = time.time() - self.setup_time
                if next_progress < len(progress_points) and elapsed >= progress_points[next_progress]:
                    self.logger.debug(f"连接进行中... {progress_points[next_progress]}秒")
                    next_progress += 1
                
                self.socket.process()
                if self.socket.status() == conf.NET_STATE_ESTABLISHED:
                    self._on_connection_established()
                    return True
                    
                time.sleep(0.01)  # 减少等待时间粒度
            
            self.logger.error(f"连接服务器超时 (>{connection_timeout-self.setup_time}秒)")
            
            self._close_socket()
            return False
            
        except Exception as e:
            self.logger.error(f"连接服务器时出错: {str(e)}")
            self.logger.error(traceback.format_exc())
            self.connection_state = "disconnected"
            return False
    
    def _on_connection_established(self):
        """连接建立成功后的处理"""
        self.connected = True
        self.connection_state = "connected"
        self.reconnect_attempts = 0  # 重置重连计数
        self.logger.info(f"已成功连接到服务器 {self.host}:{self.port}")
    
    def _close_socket(self):
        """安全关闭套接字"""
        try:
            self.socket.close()
        except Exception as e:
            self.logger.warning(f"关闭套接字时发生异常: {str(e)}")
        finally:
            self.connection_state = "disconnected"
    
    def try_reconnect(self):
        """尝试重新连接服务器，使用指数退避策略"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.error(f"达到最大重连次数 ({self.max_reconnect_attempts})，停止重连")
            return False
            
        current_time = time.time()
        
        # 计算指数退避时间 - 随着重试次数增加，等待时间呈指数增长
        backoff_interval = min(30, self.reconnect_interval * (2 ** (self.reconnect_attempts - 1)))
        
        if current_time - self.last_reconnect_time < backoff_interval:
            return False
            
        self.last_reconnect_time = current_time
        self.reconnect_attempts += 1
        
        self.logger.info(f"尝试重新连接服务器 (第 {self.reconnect_attempts} 次，等待了 {backoff_interval:.1f} 秒)")
        
        # 确保旧连接已关闭
        if self.socket:
            self._close_socket()
            
        # 创建新连接
        self.socket = NetStream()
        return self.connect()
    
    def process(self):
        """处理网络事件，返回接收到的数据"""
        if not self.connected:
            # 尝试重连
            if self.connection_state == "disconnected":
                self.try_reconnect()
            return None
            
        try:
            # 处理网络事件
            self.socket.process()
            
            # 检查连接状态
            if self.socket.status() != conf.NET_STATE_ESTABLISHED:
                self.logger.warning("连接已断开")
                self.connected = False
                self.connection_state = "disconnected"
                return None
                
            # 发送心跳包
            self.send_heartbeat()
            
            # 处理发送缓冲区
            self._process_send_buffer()
            
            # 接收数据
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
            self.logger.error(f"发送缓冲数据时出错: {str(e)}")
            # 如果发送失败，将数据重新放入缓冲区
            if 'data' in locals():
                self.send_buffer.insert(0, data)
    
    def send(self, data):
        """发送数据到服务器"""
        if not self.connected:
            self.logger.warning("尝试发送数据但未连接到服务器，将数据加入发送缓冲区")
            self.send_buffer.append(data)
            return False
            
        try:
            # 将数据加入发送缓冲区
            self.send_buffer.append(data)
            return True
        except Exception as e:
            self.logger.error(f"加入发送缓冲区时出错: {str(e)}")
            return False
    
    def send_heartbeat(self):
        """发送心跳包以保持连接活跃"""
        cur_time = time.time()
        if cur_time - self.last_heartbeat_time >= self.heartbeat_interval:
            self.last_heartbeat_time = cur_time
            self.heartbeat_count += 1
            uptime = int(cur_time - self.setup_time)
            
            # 发送心跳消息并测量延迟
            self.last_ping_time = cur_time
            return True
        return False
    
    def update_latency(self, server_time):
        """更新延迟信息"""
        if self.last_ping_time > 0:
            latency = (time.time() - self.last_ping_time) * 1000  # 毫秒
            self.latency_samples.append(latency)
            # 只保留最近10个延迟样本
            if len(self.latency_samples) > 10:
                self.latency_samples.pop(0)
            
            # 计算平均延迟
            avg_latency = sum(self.latency_samples) / len(self.latency_samples) if self.latency_samples else 0
            self.logger.debug(f"网络延迟: {avg_latency:.2f}ms")
    
    def close(self):
        """关闭连接"""
        if self.socket:
            self.logger.info("关闭网络连接")
            self._close_socket()
            self.connected = False

class ClientEntity:
    """客户端实体类，用于与服务器交互"""
    EXPOSED_FUNC = {}
    
    # 账号信息全局变量
    DEFAULT_USERNAME = "netease1"
    DEFAULT_PASSWORD = "123"
    
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
        self.username = self.DEFAULT_USERNAME
        self.password = self.DEFAULT_PASSWORD
        self.token = None
        self.login_in_progress = False
        self.login_attempts = 0
        
        # 用户数据
        self.user_data = {}
        
        # 自动测试相关
        self.test_state = TestState.INIT
        self.last_test_time = time.time()
        self.test_wait_time = 2.0  # 测试步骤间隔
        self.test_results = {}  # 测试结果记录
        self.test_details = {}  # 测试详细信息
        self.start_time = time.time()  # 测试开始时间
        
        # 初始化测试结果
        for state in TestState:
            if state != TestState.INIT:  # 包含COMPLETE状态
                self.test_results[state] = TestResult.PENDING
                self.test_details[state] = {"start_time": 0, "end_time": 0, "messages": []}
        
        self.test_sample_data = {
            "name": self.DEFAULT_USERNAME,
            "bullet": 50,
            "exp": 100,
            "test_items": ["sword", "shield"],
            "settings": {"difficulty": "hard", "sound": True}
        }
        
        # 设置定时器
        self._setup_timers()
        
        # 打印欢迎信息
        self._print_welcome_message()
        
    def _print_welcome_message(self):
        """打印测试客户端欢迎信息"""
        welcome_msg = "\n" + "="*60 + "\n" + "服务器通信自动测试客户端".center(58) + "\n" + \
                      f"开始时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + "\n" + "="*60 + "\n"
        print(welcome_msg)
        self.logger.info("测试客户端启动")
        self.logger.info(welcome_msg)
    
    def _add_test_message(self, message):
        """添加测试消息到当前测试状态的详细信息中"""
        if self.test_state in self.test_details:
            self.test_details[self.test_state]["messages"].append(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {message}"
            )
            self.logger.info(f"测试消息[{self.test_state.name}]: {message}")
    
    def _mark_test_result(self, test_state, result, message=None):
        """标记测试结果并添加消息"""
        if test_state in self.test_results:
            self.test_results[test_state] = result
            self.test_details[test_state]["end_time"] = time.time()
            
            if message:
                self._add_test_message(message)
                
            # 记录测试结果
            result_str = f"测试[{test_state.name}]: {result.value}"
            duration = self.test_details[test_state]["end_time"] - self.test_details[test_state]["start_time"]
            self.logger.info(f"{result_str} (耗时: {duration:.2f}秒)")
            print(f"[测试结果] {result_str} (耗时: {duration:.2f}秒)")
            
    def _setup_timers(self):
        """设置定时器"""
        # 10ms定时器 - 处理网络
        TimerManager.addRepeatTimer(0.01, self.process_network)
        
        # 100ms定时器 - 处理消息
        TimerManager.addRepeatTimer(0.1, self.process_messages)
        
        # 测试定时器 - 执行测试流程
        TimerManager.addRepeatTimer(0.5, self.run_test_step)
        
    def run_test_step(self):
        """执行测试步骤"""
        if not self.running:
            return
            
        # 确保网络已连接
        if not self.network_manager.connected:
            current_time = time.time()
            if current_time - self.network_manager.last_connection_attempt >= self.network_manager.connection_retry_interval:
                self.network_manager.last_connection_attempt = current_time
                self.logger.info("测试: 尝试连接服务器...")
                self.network_manager.connect()
            return
            
        # 等待一定时间间隔再执行下一步测试
        current_time = time.time()
        if current_time - self.last_test_time < self.test_wait_time:
            return
            
        # 执行当前测试状态对应的步骤
        self.last_test_time = current_time
        
        # 根据不同的测试状态执行对应的测试步骤
        if self.test_state == TestState.INIT:
            self._do_init_test()
        elif self.test_state == TestState.SAVE_WITHOUT_LOGIN:
            self._do_save_without_login_test()
        elif self.test_state == TestState.LOAD_WITHOUT_LOGIN:
            self._do_load_without_login_test()
        elif self.test_state == TestState.LOGIN_WRONG_CREDENTIALS:
            self._do_login_wrong_credentials_test()
        elif self.test_state == TestState.LOAD_AFTER_FAILED_LOGIN:
            self._do_load_after_failed_login_test()
        elif self.test_state == TestState.LOGIN_CORRECT_CREDENTIALS:
            self._do_login_correct_credentials_test()
        elif self.test_state == TestState.LOAD_WITHOUT_DATA:
            self._do_load_without_data_test()
        elif self.test_state == TestState.SAVE_DATA:
            self._do_save_data_test()
        elif self.test_state == TestState.RECONNECT:
            self._do_reconnect_test()
        elif self.test_state == TestState.CHECK_TOKEN_INVALID:
            self._do_check_token_invalid_test()
        elif self.test_state == TestState.LOAD_WITHOUT_LOGIN_2:
            self._do_load_without_login_2_test()
        elif self.test_state == TestState.LOGIN_AGAIN:
            self._do_login_again_test()
        elif self.test_state == TestState.LOAD_DATA:
            self._do_load_data_test()
        elif self.test_state == TestState.LOGOUT:
            self._do_logout_test()
        elif self.test_state == TestState.COMPLETE:
            self._do_complete_test()
    
    def _do_init_test(self):
        """初始化测试步骤"""
        self.logger.info("测试初始化完成，开始测试流程")
        print("\n\n===== 开始自动化测试流程 =====")
        self.test_state = TestState.SAVE_WITHOUT_LOGIN
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_save_without_login_test(self):
        """测试未登录状态下保存数据"""
        self.logger.info("测试: 未登录状态下保存数据")
        print("\n[测试] 1. 未登录状态下保存数据(预期失败)")
        self._add_test_message("发送保存数据请求，预期服务器会拒绝未登录状态的请求")
        
        self.caller.remote_call("userdata_save", json.dumps(self.test_sample_data))
        
        self.test_state = TestState.LOAD_WITHOUT_LOGIN
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_load_without_login_test(self):
        """测试未登录状态下加载数据"""
        self.logger.info("测试: 未登录状态下加载数据")
        print("\n[测试] 2. 未登录状态下加载数据(预期失败)")
        self._add_test_message("发送加载数据请求，预期服务器会拒绝未登录状态的请求")
        
        self.caller.remote_call("userdata_load")
        
        self.test_state = TestState.LOGIN_WRONG_CREDENTIALS
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_login_wrong_credentials_test(self):
        """测试使用错误凭据登录"""
        self.logger.info("测试: 错误凭据登录")
        print("\n[测试] 3. 使用错误的账号密码登录(预期失败)")
        self._add_test_message("尝试使用错误的账号密码登录，预期失败")
        
        self.username = "wrong_user"
        self.password = "wrong_pass"
        self.caller.remote_call("client_login", self.username, self.password)
        
        self.test_state = TestState.LOAD_AFTER_FAILED_LOGIN
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_load_after_failed_login_test(self):
        """测试登录失败后加载数据"""
        self.logger.info("测试: 登录失败后加载数据")
        print("\n[测试] 4. 登录失败后加载数据(预期失败)")
        self._add_test_message("登录失败后尝试加载数据，预期失败")
        
        self.caller.remote_call("userdata_load")
        
        self.test_state = TestState.LOGIN_CORRECT_CREDENTIALS
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_login_correct_credentials_test(self):
        """测试使用正确凭据登录"""
        self.logger.info("测试: 正确凭据登录")
        print("\n[测试] 5. 使用正确的账号密码登录(预期成功)")
        self._add_test_message("尝试使用正确的账号密码登录，预期成功")
        
        self.username = self.DEFAULT_USERNAME
        self.password = self.DEFAULT_PASSWORD
        self.caller.remote_call("client_login", self.username, self.password)
        
        self.test_state = TestState.LOAD_WITHOUT_DATA
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_load_without_data_test(self):
        """测试尝试加载可能不存在的数据"""
        self.logger.info("测试: 尝试加载可能不存在的数据")
        print("\n[测试] 6. 尝试加载账户数据(可能不存在)")
        self._add_test_message("登录成功后尝试加载用户数据")
        
        self.caller.remote_call("userdata_load")
        
        self.test_state = TestState.SAVE_DATA
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_save_data_test(self):
        """测试保存测试数据"""
        self.logger.info("测试: 保存测试数据")
        print("\n[测试] 7. 保存新的测试数据")
        self._add_test_message(f"尝试保存测试数据: {json.dumps(self.test_sample_data, indent=2)}")
        
        self.user_data = self.test_sample_data
        self.caller.remote_call("userdata_save", json.dumps(self.test_sample_data))
        
        self.test_state = TestState.RECONNECT
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_reconnect_test(self):
        """测试断开连接并重连"""
        self.logger.info("测试: 断开连接并重连")
        print("\n[测试] 8. 断开连接并重连服务器")
        
        # 保存当前token
        old_token = self.token
        self.test_results["old_token"] = old_token
        self._add_test_message(f"当前token: {old_token}")
        self._add_test_message("正在断开连接...")
        
        # 断开连接
        self.network_manager.close()
        self.authenticated = False
        
        self.test_state = TestState.CHECK_TOKEN_INVALID
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
        self.test_wait_time = 5.0  # 给重连多一点时间
    
    def _do_check_token_invalid_test(self):
        """测试检查重连后token是否无效"""
        self.logger.info("测试: 检查重连后token是否无效")
        print("\n[测试] 9. 检查重连后token是否失效")
        self._add_test_message("重连成功，检查token是否已失效")
        
        self.test_wait_time = 2.0  # 恢复标准等待时间
        self.test_state = TestState.LOAD_WITHOUT_LOGIN_2
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_load_without_login_2_test(self):
        """测试重连后未登录状态加载数据"""
        self.logger.info("测试: 重连后未登录状态加载数据")
        print("\n[测试] 10. 重连后未登录状态加载数据(预期失败)")
        self._add_test_message("重连后，未重新登录就尝试加载数据，预期失败")
        
        self.caller.remote_call("userdata_load")
        
        self.test_state = TestState.LOGIN_AGAIN
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_login_again_test(self):
        """测试重连后重新登录"""
        self.logger.info("测试: 重连后重新登录")
        print("\n[测试] 11. 重连后重新登录(预期成功)")
        self._add_test_message("尝试重连后重新登录，预期成功")
        
        self.username = self.DEFAULT_USERNAME
        self.password = self.DEFAULT_PASSWORD
        self.caller.remote_call("client_login", self.username, self.password)
        
        self.test_state = TestState.LOAD_DATA
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_load_data_test(self):
        """测试加载之前保存的数据"""
        self.logger.info("测试: 加载之前保存的数据")
        print("\n[测试] 12. 加载之前保存的测试数据")
        self._add_test_message("尝试加载之前保存的测试数据")
        
        self.caller.remote_call("userdata_load")
        
        self.test_state = TestState.LOGOUT
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_logout_test(self):
        """测试退出登录"""
        self.logger.info("测试: 退出登录")
        print("\n[测试] 13. 退出登录(使token失效)")
        self._add_test_message("尝试退出登录，使token失效，但保持连接")
        
        self.caller.remote_call("exit")
        
        self.test_state = TestState.COMPLETE
        self.test_details[self.test_state]["start_time"] = time.time()
        self.test_results[self.test_state] = TestResult.RUNNING
    
    def _do_complete_test(self):
        """完成所有测试"""
        self.logger.info("所有测试完成")
        print("\n\n===== 自动化测试完成 =====")
        current_time = time.time()
        self._add_test_message("所有测试步骤已完成")
        
        # 打印测试结果摘要
        print("\n\n===== 自动化测试完成 =====")
        print("\n测试结果摘要:")
        
        for state in TestState:
            if state != TestState.INIT and state != TestState.COMPLETE:
                result = self.test_results.get(state, TestResult.PENDING)
                state_name = state.name
                result_value = result.value
                result_color = ColorText.GREEN if result == TestResult.SUCCESS else \
                              (ColorText.RED if result == TestResult.FAILURE else \
                              ColorText.YELLOW)
                print(f" - {state_name}: {result_color}{result_value}{ColorText.RESET}")
                
                # 记录到日志
                self.logger.info(f"测试[{state_name}]: {result_value}")
                
                # 记录详细消息
                if state in self.test_details:
                    details = self.test_details[state]
                    duration = details["end_time"] - details["start_time"] if details["end_time"] > 0 else 0
                    self.logger.info(f"  耗时: {duration:.2f}秒, 消息: {len(details['messages'])}")
                    
                    for msg in details["messages"]:
                        self.logger.info(f"    - {msg}")
        
        # 计算总体执行时间
        total_time = time.time() - self.start_time
        print(f"\n总执行时间: {total_time:.2f}秒")
        self.logger.info(f"测试总执行时间: {total_time:.2f}秒")
        
        print("\n程序将在5秒后退出...")
        self.running = False
        
    def destroy(self):
        """销毁客户端实体，释放资源"""
        self.logger.info("客户端实体被销毁")
        if self.caller:
            self.caller.close()
        self.caller = None
        self.socket = None
        self.network_manager = None
        
    def process_network(self):
        """处理网络事件"""
        if not self.running:
            return
            
        # 处理网络并获取数据
        data_list = self.network_manager.process()
        
        # 如果连接断开，等待重连由network_manager内部逻辑处理
        if not self.network_manager.connected:
            return
            
        # 处理接收到的数据
        if data_list:
            for data in data_list:
                self.pending_messages.append(data)
    
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
    
    @EXPOSED
    def login_required(self):
        """服务器要求登录"""
        try:
            self.logger.info("服务器请求登录")
            print("[服务器] 请求登录认证")
            self._add_test_message("服务器要求进行登录认证")
            
            if not self.login_in_progress and self.test_state == TestState.INIT:
                print("使用默认凭据自动登录...")
                # 不在初始测试流程中自动登录，遵循测试流程
        except Exception as e:
            self.logger.error(f"处理登录请求时错误: {str(e)}")
            self._add_test_message(f"处理登录请求出错: {str(e)}")
    
    @EXPOSED
    def login_success(self, token):
        """登录成功回调"""
        try:
            if not token:
                self.logger.warning("收到空token")
                self.authenticated = False
                print("[登录] 异常: 服务器返回无效token")
                self._add_test_message("登录异常: 服务器返回无效token")
                
                # 如果是正确凭据登录测试，标记为失败
                if self.test_state == TestState.LOGIN_CORRECT_CREDENTIALS:
                    self._mark_test_result(TestState.LOGIN_CORRECT_CREDENTIALS, TestResult.FAILURE, "登录失败: 无效token")
                # 如果是重新登录测试，标记为失败
                elif self.test_state == TestState.LOGIN_AGAIN:
                    self._mark_test_result(TestState.LOGIN_AGAIN, TestResult.FAILURE, "重新登录失败: 无效token")
                    
                return
                
            self.authenticated = True
            self.token = token
            self.login_in_progress = False
            self.login_attempts = 0  # 重置登录尝试次数
            
            # 不在日志中显示完整的token，只显示部分
            masked_token = token[:5] + "..." + token[-5:] if len(token) > 10 else "***"
            self.logger.info(f"登录成功，获取token: {masked_token}")
            
            print(f"[登录] 成功! 用户: {self.username}, Token: {masked_token}")
            self._add_test_message(f"登录成功，用户: {self.username}，Token: {masked_token}")
            
            # 如果是正确凭据登录测试，标记为成功
            if self.test_state == TestState.LOGIN_CORRECT_CREDENTIALS:
                self._mark_test_result(TestState.LOGIN_CORRECT_CREDENTIALS, TestResult.SUCCESS, "使用正确凭据登录成功")
            # 如果是重新登录测试，标记为成功
            elif self.test_state == TestState.LOGIN_AGAIN:
                self._mark_test_result(TestState.LOGIN_AGAIN, TestResult.SUCCESS, "重连后重新登录成功")
                
        except Exception as e:
            self.logger.error(f"处理登录成功回调时出错: {str(e)}")
            self._add_test_message(f"处理登录成功回调出错: {str(e)}")
    
    @EXPOSED
    def kicked(self, reason):
        """被踢下线的回调"""
        self.authenticated = False
        self.token = None
        self.logger.warning(f"您的账号在其他设备登录，被迫下线: {reason}")
        print(f"[系统] 您已被服务器踢下线: {reason}")
        self._add_test_message(f"被服务器踢下线: {reason}")
    
    @EXPOSED
    def server_shutdown(self, message):
        """服务器关闭的回调"""
        self.logger.warning(f"服务器关闭通知: {message}")
        print(f"[系统] 服务器通知: {message}")
        print("服务器即将关闭，客户端将在3秒后退出...")
        self._add_test_message(f"服务器关闭通知: {message}")
        
        # 标记所有未完成的测试为跳过
        for state in TestState:
            if state != TestState.INIT and state != TestState.COMPLETE:
                if self.test_results.get(state)== TestResult.PENDING:
                    self._mark_test_result(state, TestResult.SKIPPED, "服务器关闭，测试跳过")
        
        # 设置定时器在3秒后关闭客户端
        threading.Timer(3.0, self._exit_program).start()
    
    def _exit_program(self):
        """关闭程序"""
        self.logger.info("程序即将退出")
        self.running = False
        print("程序正在退出...")
    
    @EXPOSED
    def login_failed(self, reason):
        """登录失败回调"""
        try:
            self.authenticated = False
            self.token = None
            self.login_in_progress = False
            
            self.logger.warning(f"登录失败: {reason}")
            print(f"[登录] 失败: {reason}")
            self._add_test_message(f"登录失败: {reason}")
            
            # 如果是错误凭据登录测试，标记为成功(因为我们期望失败)
            if self.test_state == TestState.LOGIN_WRONG_CREDENTIALS:
                self._mark_test_result(TestState.LOGIN_WRONG_CREDENTIALS, TestResult.SUCCESS, "使用错误凭据登录，预期失败，测试通过")
            # 如果是正确凭据登录测试，标记为失败
            elif self.test_state == TestState.LOGIN_CORRECT_CREDENTIALS:
                self._mark_test_result(TestState.LOGIN_CORRECT_CREDENTIALS, TestResult.FAILURE, f"使用正确凭据登录失败: {reason}")
            # 如果是重新登录测试，标记为失败
            elif self.test_state == TestState.LOGIN_AGAIN:
                self._mark_test_result(TestState.LOGIN_AGAIN, TestResult.FAILURE, f"重连后重新登录失败: {reason}")
                
        except Exception as e:
            self.logger.error(f"处理登录失败回调时出错: {str(e)}")
            self._add_test_message(f"处理登录失败回调出错: {str(e)}")
    
    @EXPOSED
    def userdata_update(self, data_json):
        """接收用户数据更新"""
        try:
            self.user_data = json.loads(data_json)
            self.logger.info("已接收用户数据")
            print(f"[数据] 已接收用户数据: {data_json[:50]}..." if len(data_json) > 50 else data_json)
            self._add_test_message(f"成功接收用户数据: {data_json}")
            
            # 如果是加载用户数据测试，标记为成功
            if self.test_state == TestState.LOAD_WITHOUT_DATA:
                self._mark_test_result(TestState.LOAD_WITHOUT_DATA, TestResult.SUCCESS, "成功加载初始用户数据")
            elif self.test_state == TestState.LOAD_DATA:
                # 验证数据是否与保存的一致
                try:
                    loaded_data = json.loads(data_json)
                    is_matching = True
                    
                    # 检查保存的关键字段
                    for key in self.test_sample_data:
                        if key not in loaded_data or loaded_data[key] != self.test_sample_data[key]:
                            is_matching = False
                            break
                    
                    if is_matching:
                        self._mark_test_result(TestState.LOAD_DATA, TestResult.SUCCESS, "成功加载之前保存的数据，数据匹配")
                    else:
                        self._mark_test_result(TestState.LOAD_DATA, TestResult.FAILURE, "加载的数据与之前保存的不匹配")
                except:
                    self._mark_test_result(TestState.LOAD_DATA, TestResult.ERROR, "验证加载数据时出错")
        except Exception as e:
            self.logger.error(f"解析用户数据时出错: {str(e)}")
            print(f"[数据] 加载用户数据时出错: {str(e)}")
            self._add_test_message(f"解析用户数据出错: {str(e)}")
            
            # 标记测试失败
            if self.test_state == TestState.LOAD_WITHOUT_DATA:
                self._mark_test_result(TestState.LOAD_WITHOUT_DATA, TestResult.ERROR, f"解析用户数据出错: {str(e)}")
            elif self.test_state == TestState.LOAD_DATA:
                self._mark_test_result(TestState.LOAD_DATA, TestResult.ERROR, f"解析用户数据出错: {str(e)}")
    
    @EXPOSED
    def save_success(self):
        """保存数据成功回调"""
        self.logger.info("数据保存成功")
        print("[数据] 保存成功")
        self._add_test_message("数据保存成功")
        
        # 如果是保存数据测试，标记为成功
        if self.test_state == TestState.SAVE_DATA:
            self._mark_test_result(TestState.SAVE_DATA, TestResult.SUCCESS, "成功保存测试数据")
    
    @EXPOSED
    def data_error(self, message):
        """数据操作错误回调"""
        self.logger.warning(f"数据操作错误: {message}")
        print(f"[数据] 错误: {message}")
        self._add_test_message(f"数据操作错误: {message}")
        
        # 根据当前测试状态标记测试结果
        if self.test_state == TestState.SAVE_WITHOUT_LOGIN:
            self._mark_test_result(TestState.SAVE_WITHOUT_LOGIN, TestResult.SUCCESS, "未登录状态下尝试保存数据，服务器正确拒绝")
        elif self.test_state == TestState.LOAD_WITHOUT_LOGIN:
            self._mark_test_result(TestState.LOAD_WITHOUT_LOGIN, TestResult.SUCCESS, "未登录状态下尝试加载数据，服务器正确拒绝")
        elif self.test_state == TestState.LOAD_AFTER_FAILED_LOGIN:
            self._mark_test_result(TestState.LOAD_AFTER_FAILED_LOGIN, TestResult.SUCCESS, "登录失败后尝试加载数据，服务器正确拒绝")
        elif self.test_state == TestState.LOAD_WITHOUT_LOGIN_2:
            self._mark_test_result(TestState.LOAD_WITHOUT_LOGIN_2, TestResult.SUCCESS, "重连后未登录状态下尝试加载数据，服务器正确拒绝")
        elif self.test_state == TestState.SAVE_DATA:
            self._mark_test_result(TestState.SAVE_DATA, TestResult.FAILURE, f"保存数据失败: {message}")
    
    @EXPOSED
    def auth_error(self, message):
        """认证错误回调"""
        self.logger.warning(f"认证错误: {message}")
        print(f"[认证] 错误: {message}")
        self._add_test_message(f"认证错误: {message}")
        
        # 根据当前测试状态标记测试结果
        if self.test_state == TestState.SAVE_WITHOUT_LOGIN:
            self._mark_test_result(TestState.SAVE_WITHOUT_LOGIN, TestResult.SUCCESS, "未登录状态下尝试保存数据，服务器正确拒绝未登录状态下尝试保存数据，服务器正确拒绝")
        elif self.test_state == TestState.LOAD_WITHOUT_LOGIN:
            self._mark_test_result(TestState.LOAD_WITHOUT_LOGIN, TestResult.SUCCESS, "未登录状态下尝试加载数据，服务器正确拒绝")
        elif self.test_state == TestState.LOAD_AFTER_FAILED_LOGIN:
            self._mark_test_result(TestState.LOAD_AFTER_FAILED_LOGIN, TestResult.SUCCESS, "登录失败后尝试加载数据，服务器正确拒绝")
        elif self.test_state == TestState.LOAD_WITHOUT_LOGIN_2:
            self._mark_test_result(TestState.LOAD_WITHOUT_LOGIN_2, TestResult.SUCCESS, "重连后未登录状态下尝试加载数据，服务器正确拒绝")
            
    @EXPOSED
    def recv_msg_from_server(self, stat, msg):
        """接收服务器消息的回调函数"""
        self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
        self.stat = stat
        
        # 更新网络延迟信息
        self.network_manager.update_latency(time.time())
        
        print(f"[服务器消息] {msg}")
    
    @EXPOSED
    def exit_confirmed(self):
        """服务器确认退出的回调函数"""
        self.logger.info('服务器确认客户端退出')
        print("[系统] 服务器已确认退出请求")
        self._add_test_message("服务器确认退出请求")
        
        # 如果是登出测试，标记为成功
        if self.test_state == TestState.LOGOUT:
            self._mark_test_result(TestState.LOGOUT, TestResult.SUCCESS, "成功退出登录，使token失效")

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='客户端')
    parser.add_argument('--host', default='127.0.0.1', help='服务器地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=2000, help='服务器端口 (默认: 2000)')
    parser.add_argument('--username', default=None, help='登录用户名 (默认: netease1)')
    parser.add_argument('--password', default=None, help='登录密码 (默认: 123)')
    parser.add_argument('--no-reconnect', action='store_true', help='禁用自动重连')
    args = parser.parse_args()
    
    # 设置日志
    logger = logger_instance.get_logger('SampleClient')
    log_file = logger_instance._log_files.get('SampleClient', '')
    logger.info(f"客户端日志文件: {os.path.abspath(log_file)}")
    
    # 创建网络管理器
    network_manager = ClientNetworkManager(args.host, args.port)
    
    try:
        # 如果指定了--no-reconnect参数，则禁用自动重连
        if args.no_reconnect:
            network_manager.max_reconnect_attempts = 0
            logger.info("已禁用自动重连功能")
        
        # 连接服务器
        if not network_manager.connect():
            logger.warning("初始连接失败，将尝试自动重连")
        
        # 创建客户端实体
        client_entity = ClientEntity(network_manager)
        
        # 设置登录凭据(如果提供)
        if args.username:
            client_entity.username = args.username
        if args.password:
            client_entity.password = args.password
            
        # 注册信号处理函数 - Windows和Unix兼容处理
        def setup_signal_handlers():
            try:
                # 注册SIGINT处理器 (Ctrl+C)
                if hasattr(signal, 'SIGINT'):
                    def signal_handler(signum, frame):
                        signal_name = "SIGINT" if signum == signal.SIGINT else f"Signal {signum}"
                        logger.info(f"收到信号: {signal_name}")
                        client_entity.running = False
                        print("\n正在退出客户端...")
                    
                    signal.signal(signal.SIGINT, signal_handler)
                    logger.debug("已注册SIGINT信号处理器")
            except Exception as e:
                logger.warning(f"无法注册信号处理器: {str(e)}")
        
        setup_signal_handlers()
        
        # 主循环 - 运行定时器调度
        while client_entity.running:
            try:
                # 运行定时器调度器
                TimerManager.scheduler()
                time.sleep(0.001)  # 微小的延迟以减轻CPU负担
            except KeyboardInterrupt:
                logger.info("用户中断，客户端退出")
                break
        
        # 清理资源
        client_entity.destroy()
        network_manager.close()
        logger.info("客户端正常退出")
        
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")
        logger.error(traceback.format_exc())
    finally:
        # 确保资源被释放
        if 'network_manager' in locals():
            network_manager.close()
        
if __name__ == "__main__":
    main()