# -*- coding: utf-8 -*-
import sys
import os
import time
import argparse
import socket
import threading
import queue
import weakref
import select
import json
import getpass
import traceback
import signal

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
 
from server.common import conf
from server.network.netStream import NetStream, RpcProxy
from server.common.logger import logger_instance
from server.common.timer import TimerManager

def EXPOSED(func):
    func.__exposed__ = True
    return func

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
        self.command_queue = queue.Queue()
        self.pending_messages = []  # 待处理消息队列
        
        # 认证相关
        self.authenticated = False
        self.username = self.DEFAULT_USERNAME
        self.password = self.DEFAULT_PASSWORD
        self.token = None
        self.login_in_progress = False
        self.login_attempts = 0  # 添加登录尝试计数器
        
        # 用户数据
        self.user_data = {}
        
        # 初始化命令处理器映射
        self._init_command_handlers()
        # 命令处理回调
        self.on_command_input = None
        
        # 设置定时器
        self._setup_timers()
        
    def _init_command_handlers(self):
        """初始化命令处理器映射"""
        self.command_handlers = {
            'send': self.handle_send_command,
            'exit': self.handle_exit_command,
            'help': self.handle_help_command,
            'status': self.handle_status_command,
            'reconnect': self.handle_reconnect_command,
            'login': self.handle_login_command,
            'save': self.handle_save_command,
            'load': self.handle_load_command,
            'data': self.handle_data_command,
            'set': self.handle_set_command
        }
        
    def _setup_timers(self):
        """设置定时器"""
        # 10ms定时器 - 处理网络
        TimerManager.addRepeatTimer(0.01, self.process_network)
        
        # 100ms定时器 - 处理消息
        TimerManager.addRepeatTimer(0.1, self.process_messages)
        
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

    def _print_prompt(self, message=None):
        """打印提示消息和命令提示符"""
        if message:
            print(f"\n{message}")
        print("\n> ", end='', flush=True)

    def handle_send_command(self, args=None):
        """处理发送消息命令"""
        # 如果有提供消息参数，直接使用
        if args and len(args) > 0:
            message = ' '.join(args)
            if message.strip():  # 检查消息是否为空
                self._send_message(message)
            else:
                self._print_prompt("消息不能为空，请重新输入")
        else:
            # 提示用户输入消息
            print("请输入要发送的消息: ", end='', flush=True)
            # 设置回调以处理用户输入的消息
            self.on_command_input = self._send_message_with_check
        
    def _send_message_with_check(self, message):
        """检查消息内容并发送"""
        if not message.strip():  # 检查消息是否为空
            print("消息不能为空，请重新输入: ", end='', flush=True)
            # 保持回调，让用户重新输入
            return
            
        self._send_message(message)
        
    def _send_message(self, message):
        """发送消息到服务器"""
        self.logger.info(f'客户端发送新消息: {message}')
        self.caller.remote_call("hello_world_from_client", self.stat + 1, message)
        # 重置回调
        self.on_command_input = None
        self._print_prompt()
    
    def handle_exit_command(self, args=None):
        """处理退出命令"""
        self.logger.info('客户端发送退出请求')
        self.caller.remote_call("exit")
    
    def handle_reconnect_command(self, args=None):
        """处理重连命令"""
        print("\n正在尝试重新连接到服务器...")
        if self.network_manager.connected:
            self.network_manager.close()
        
        if self.network_manager.connect():
            self._print_prompt("重新连接成功!")
        else:
            self._print_prompt("重新连接失败，将继续尝试自动重连")
    
    def handle_help_command(self, args=None):
        """显示帮助信息"""
        help_text = """
        可用命令:
        send <消息>  - 发送消息到服务器
        exit         - 退出客户端
        status       - 显示连接状态
        reconnect    - 重新连接服务器
        login        - 登录账户
        save         - 保存用户数据
        load         - 加载用户数据
        data         - 显示当前用户数据
        set <键> <值> - 设置用户数据
        help         - 显示此帮助信息
        """
        self._print_prompt(help_text)
    
    def handle_status_command(self, args=None):
        """显示连接状态"""
        if self.network_manager.connected:
            uptime = int(time.time() - self.network_manager.setup_time)
            avg_latency = sum(self.network_manager.latency_samples) / len(self.network_manager.latency_samples) if self.network_manager.latency_samples else 0
            
            status_info = f"""
            连接状态: 已连接
            服务器地址: {self.network_manager.host}:{self.network_manager.port}
            连接时长: {uptime}秒
            心跳次数: {self.network_manager.heartbeat_count}
            平均网络延迟: {avg_latency:.2f}ms
            已发送: {self.network_manager.bytes_sent} 字节
            已接收: {self.network_manager.bytes_received} 字节
            """
        else:
            status_info = "\n连接状态: 未连接"
            if self.network_manager.reconnect_attempts > 0:
                status_info += f"\n正在尝试重连 ({self.network_manager.reconnect_attempts}/{self.network_manager.max_reconnect_attempts})"
        
        self._print_prompt(status_info)
    
    def process_command(self, command_line):
        """处理用户输入命令"""
        try:
            # 如果有回调函数在等待输入，则调用它
            if self.on_command_input:
                callback = self.on_command_input
                # 注意：不立即清除回调，让回调函数决定是否清除
                callback(command_line)
                return True
                
            # 命令行为空，仅显示提示符
            if not command_line.strip():
                self._print_prompt()
                return True
                
            # 否则按正常命令处理
            parts = command_line.strip().split(maxsplit=1)
            command = parts[0].lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []
            
            if command in self.command_handlers:
                self.command_handlers[command](args)
            elif command:
                self._print_prompt(f"未知命令: {command}，输入 'help' 查看可用命令")
            
            return True
        except Exception as e:
            self.logger.error(f"处理命令时出错: {str(e)}")
            self.logger.error(traceback.format_exc())
            self._print_prompt()
            return False
            
    def _handle_auth_action(self, action_name, auth_required=True, callback=None):
        """处理需要认证的操作"""
        if auth_required and not self.authenticated:
            self._print_prompt("请先登录")
            return False
            
        if callback:
            callback()
        return True
    
    def handle_login_command(self, args=None):
        """处理登录命令"""
        if self.authenticated:
            self._print_prompt(f"您已经以 {self.username} 登录")
            return
            
        if not args:
            # 交互式登录
            print("\n请输入用户名: ", end='', flush=True)
            self.on_command_input = self._get_username_input
        else:
            # 直接登录
            self.username = args[0]
            print("请输入密码: ", end='', flush=True)
            self.on_command_input = self._get_password_input
    
    def _get_username_input(self, username):
        """处理用户名输入"""
        if not username.strip():
            print("用户名不能为空，请重新输入: ", end='', flush=True)
            # 保持回调，等待重新输入
            return
            
        self.username = username
        print("请输入密码: ", end='', flush=True)
        self.on_command_input = self._get_password_input
    
    def _get_password_input(self, password):
        """处理密码输入"""
        if not password.strip():
            print("密码不能为空，请重新输入: ", end='', flush=True)
            # 保持回调，等待重新输入
            return
            
        self.password = password
        self._perform_login()
        self.on_command_input = None  # 清除回调
        
    def _perform_login(self):
        """执行登录操作"""
        print(f"\n正在登录账号 {self.username}...")
        self.login_in_progress = True
        
        if self.network_manager.connected:
            self.caller.remote_call("client_login", self.username, self.password)
        else:
            self._print_prompt("未连接到服务器，无法登录")
    
    def _handle_user_data_command(self, command_name, callback):
        """处理用户数据相关命令"""
        if not self.authenticated:
            self._print_prompt("请先登录")
            return
            
        self._print_prompt(f"正在{command_name}用户数据...")
        callback()
    
    def handle_save_command(self, args=None):
        """处理保存用户数据命令"""
        self._handle_user_data_command("保存", 
            lambda: self.caller.remote_call("userdata_save", json.dumps(self.user_data)))
    
    def handle_load_command(self, args=None):
        """处理加载用户数据命令"""
        self._handle_user_data_command("加载", 
            lambda: self.caller.remote_call("userdata_load"))
    
    def handle_data_command(self, args=None):
        """显示当前用户数据"""
        if not self.authenticated:
            self._print_prompt("请先登录")
            return
            
        data_info = "\n当前用户数据:"
        for key, value in self.user_data.items():
            data_info += f"\n  {key}: {value}"
        self._print_prompt(data_info)
    
    def handle_set_command(self, args=None):
        """设置用户数据属性"""
        if not self.authenticated:
            self._print_prompt("请先登录")
            return
            
        if not args or not args[0].strip() or len(args[0].split()) < 2:
            self._print_prompt("格式错误：set <key> <value>")
            return
            
        parts = args[0].split(maxsplit=1)
        key = parts[0].strip()
        value = parts[1].strip()
        
        if not key or not value:
            self._print_prompt("键和值都不能为空")
            return
        
        # 尝试将值转换为数字类型
        try:
            value = int(value)
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                pass  # 保持为字符串类型
                
        # 更新数据
        self.user_data[key] = value
        self._print_prompt(f"已设置 {key} = {value}")
            
    @EXPOSED
    def login_required(self):
        """服务器要求登录"""
        try:
            self.logger.info("服务器请求登录")
            if not self.login_in_progress:
                self._print_prompt("服务器要求登录，使用默认凭据进行登录...")
                self._perform_login()
        except Exception as e:
            self.logger.error(f"处理登录请求时出错: {str(e)}")
    
    @EXPOSED
    def login_success(self, token):
        """登录成功回调"""
        try:
            if not token:
                self.logger.warning("收到空token")
                self.authenticated = False
                self._print_prompt("登录异常: 服务器返回无效token")
                return
                
            self.authenticated = True
            self.token = token
            self.login_in_progress = False
            self.login_attempts = 0  # 重置登录尝试次数
            
            # 不在日志中显示完整的token，只显示部分
            masked_token = token[:5] + "..." + token[-5:] if len(token) > 10 else "***"
            self.logger.info(f"登录成功，获取token: {masked_token}")
            
            self._print_prompt(f"登录成功! 欢迎, {self.username}")
        except Exception as e:
            self.logger.error(f"处理登录成功回调时出错: {str(e)}")
    
    @EXPOSED
    def kicked(self, reason):
        """被踢下线的回调"""
        self.authenticated = False
        self.token = None
        self.logger.warning(f"您的账号在其他设备登录，被迫下线: {reason}")
        self._print_prompt(f"您已被服务器踢下线: {reason}")
    
    @EXPOSED
    def server_shutdown(self, message):
        """服务器关闭的回调"""
        self.logger.warning(f"服务器关闭通知: {message}")
        self._print_prompt(f"服务器通知: {message}\n服务器即将关闭，客户端将在5秒后退出...")
        
        # 设置定时器在5秒后关闭客户端
        threading.Timer(5.0, self.handle_exit_command).start()
    
    @EXPOSED
    def login_failed(self, reason):
        """登录失败回调"""
        try:
            self.authenticated = False
            self.token = None
            self.login_in_progress = False
            
            self.logger.warning(f"登录失败: {reason}")
            
            message = f"登录失败: {reason}"
            
            # 如果多次尝试登录失败，给出建议
            if self.login_attempts > 2:
                message += "\n提示: 请检查您的用户名和密码是否正确"
                self.login_attempts = 0  # 重置计数
            else:
                self.login_attempts += 1
                
            self._print_prompt(message)
        except Exception as e:
            self.logger.error(f"处理登录失败回调时出错: {str(e)}")
    
    @EXPOSED
    def userdata_update(self, data_json):
        """接收用户数据更新"""
        try:
            self.user_data = json.loads(data_json)
            self.logger.info("已接收用户数据")
            self._print_prompt("已加载用户数据")
        except Exception as e:
            self.logger.error(f"解析用户数据时出错: {str(e)}")
            self._print_prompt(f"加载用户数据时出错: {str(e)}")
    
    @EXPOSED
    def save_success(self):
        """保存数据成功回调"""
        self.logger.info("数据保存成功")
        self._print_prompt("数据保存成功")
    
    @EXPOSED
    def data_error(self, message):
        """数据操作错误回调"""
        self.logger.warning(f"数据操作错误: {message}")
        self._print_prompt(f"数据操作错误: {message}")
    
    @EXPOSED
    def auth_error(self, message):
        """认证错误回调"""
        self.logger.warning(f"认证错误: {message}")
        self._print_prompt(f"认证错误: {message}")
            
    @EXPOSED
    def recv_msg_from_server(self, stat, msg):
        """接收服务器消息的回调函数"""
        self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
        self.stat = stat
        
        # 更新网络延迟信息
        self.network_manager.update_latency(time.time())
        
        self._print_prompt(f"服务器消息: {msg}")
    
    @EXPOSED
    def exit_confirmed(self):
        """服务器确认退出的回调函数"""
        self.logger.info('服务器确认客户端退出')
        self.running = False

def input_thread_function(client_entity, logger):
    """用于处理用户输入的线程函数"""
    logger.debug("输入处理线程启动")
    print("> ", end='', flush=True)
    
    while client_entity.running:
        try:
            cmd = input()
            # 将命令发送到主线程处理
            client_entity.process_command(cmd)
        except EOFError:
            logger.info("输入流已关闭")
            break
        except Exception as e:
            logger.error(f"输入处理错误: {e}")
            
    logger.debug("输入处理线程结束")

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='客户端')
    parser.add_argument('--host', default='127.0.0.1', help='服务器地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=2000, help='服务器端口 (默认: 2000)')
    parser.add_argument('--username', default=None, help='登录用户名 (默认: netease1)')
    parser.add_argument('--password', default=None, help='登录密码 (默认: 123)')
    parser.add_argument('--no-reconnect', action='store_true', help='禁用自动重连')
    args = parser.parse_args()
    
    # 设置日```python
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
        
        # 显示帮助信息
        client_entity.handle_help_command()
        
        # 创建并启动输入处理线程
        input_thread = threading.Thread(
            target=input_thread_function, 
            args=(client_entity, logger),
            daemon=True  # 设置为守护线程，这样主线程退出时它会自动结束
        )
        input_thread.start()
        
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