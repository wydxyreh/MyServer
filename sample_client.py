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

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
 
from server.common import conf
from server.network.netStream import NetStream, RpcProxy
from server.common.logger import Logger
from server.common.timer import TimerManager

def EXPOSED(func):
    func.__exposed__ = True
    return func

class ClientNetworkManager(object):
    """客户端网络管理器，整合了NetworkSocket的心跳机制和NetStream的RPC功能"""
    def __init__(self, host='127.0.0.1', port=2000):
        self.host = host
        self.port = port
        self.socket = NetStream()
        self.logger, _ = Logger.setup_logger('ClientNetwork')
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
            self.socket.connect(self.host, self.port)
            self.socket.nodelay(1)  # 启用TCP_NODELAY
            self.setup_time = time.time()
            self.last_heartbeat_time = self.setup_time
            self.last_ping_time = self.setup_time
            
            # 等待连接建立
            connection_timeout = time.time() + 5.0  # 5秒连接超时
            
            while time.time() < connection_timeout:
                self.socket.process()
                if self.socket.status() == conf.NET_STATE_ESTABLISHED:
                    self.connected = True
                    self.connection_state = "connected"
                    self.reconnect_attempts = 0  # 重置重连计数
                    self.logger.info(f"已成功连接到服务器 {self.host}:{self.port}")
                    return True
                time.sleep(0.01)  # 减少等待时间粒度
            
            self.logger.error("连接服务器超时")
            self.connection_state = "disconnected"
            return False
            
        except Exception as e:
            self.logger.error(f"连接服务器时出错: {str(e)}")
            self.connection_state = "disconnected"
            return False
    
    def try_reconnect(self):
        """尝试重新连接服务器"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.error(f"达到最大重连次数 ({self.max_reconnect_attempts})，停止重连")
            return False
            
        current_time = time.time()
        if current_time - self.last_reconnect_time < self.reconnect_interval:
            return False
            
        self.last_reconnect_time = current_time
        self.reconnect_attempts += 1
        
        self.logger.info(f"尝试重新连接服务器 (第 {self.reconnect_attempts} 次)")
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            
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
            # 处理待发送队列中的所有消息
            while self.send_buffer:
                data = self.send_buffer.pop(0)
                self.socket.send(data)
                self.bytes_sent += len(data)
        except Exception as e:
            self.logger.error(f"发送缓冲数据时出错: {str(e)}")
            # 如果发送失败，将数据重新放入缓冲区
            if data:
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
    
    def update_latency(self, server_time):
        """更新延迟信息"""
        if self.last_ping_time > 0:
            latency = (time.time() - self.last_ping_time) * 1000  # 毫秒
            self.latency_samples.append(latency)
            # 只保留最近10个延迟样本
            if len(self.latency_samples) > 10:
                self.latency_samples.pop(0)
            
            # 计算平均延迟
            avg_latency = sum(self.latency_samples) / len(self.latency_samples)
            self.logger.debug(f"网络延迟: {avg_latency:.2f}ms")
    
    def close(self):
        """关闭连接"""
        if self.socket:
            self.logger.info("关闭网络连接")
            self.socket.close()
            self.connected = False
            self.connection_state = "disconnected"

class ClientEntity(object):
    """客户端实体类，用于与服务器交互"""
    EXPOSED_FUNC = {}
    
    def __init__(self, network_manager):
        self.network_manager = network_manager
        self.socket = network_manager.socket
        self.caller = RpcProxy(self, self.socket)
        self.stat = 0
        self.logger, _ = Logger.setup_logger('SampleClient')
        self.running = True
        self.command_queue = queue.Queue()
        self.pending_messages = []  # 待处理消息队列
        self.command_handlers = {
            'send': self.handle_send_command,
            'exit': self.handle_exit_command,
            'help': self.handle_help_command,
            'status': self.handle_status_command,
            'reconnect': self.handle_reconnect_command
        }
        # 命令处理回调
        self.on_command_input = None
        
        # 设置定时器
        self._setup_timers()
        
    def _setup_timers(self):
        """设置定时器"""
        # 10ms定时器 - 处理网络
        TimerManager.addRepeatTimer(0.01, self.process_network)
        
        # 100ms定时器 - 处理消息
        TimerManager.addRepeatTimer(0.1, self.process_messages)
        
    def destroy(self):
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
        
        # 如果连接断开，尝试重连
        if not self.network_manager.connected:
            # 等待重连，由network_manager内部逻辑处理
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
        
    def handle_send_command(self, args=None):
        """处理发送消息命令"""
        # 如果有提供消息参数，直接使用
        if args and len(args) > 0:
            message = ' '.join(args)
        else:
            # 提示用户输入消息
            print("请输入要发送的消息: ", end='', flush=True)
            # 设置回调以处理用户输入的消息
            self.on_command_input = lambda msg: self._send_message(msg)
            return
            
        self._send_message(message)
        
    def _send_message(self, message):
        """发送消息到服务器"""
        self.logger.info(f'客户端发送新消息: {message}')
        self.caller.remote_call("hello_world_from_client", self.stat + 1, message)
        # 重置回调
        self.on_command_input = None
        # 重新显示命令提示符
        print("\n> ", end='', flush=True)
    
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
            print("重新连接成功!")
        else:
            print("重新连接失败，将继续尝试自动重连")
        print("\n> ", end='', flush=True)
    
    def handle_help_command(self, args=None):
        """显示帮助信息"""
        print("\n可用命令:")
        print("  send <消息>  - 发送消息到服务器")
        print("  exit         - 退出客户端")
        print("  status       - 显示连接状态")
        print("  reconnect    - 重新连接服务器")
        print("  help         - 显示此帮助信息")
        print("\n> ", end='', flush=True)
    
    def handle_status_command(self, args=None):
        """显示连接状态"""
        if self.network_manager.connected:
            uptime = int(time.time() - self.network_manager.setup_time)
            avg_latency = sum(self.network_manager.latency_samples) / len(self.network_manager.latency_samples) if self.network_manager.latency_samples else 0
            
            print(f"\n连接状态: 已连接")
            print(f"服务器地址: {self.network_manager.host}:{self.network_manager.port}")
            print(f"连接时长: {uptime}秒")
            print(f"心跳次数: {self.network_manager.heartbeat_count}")
            print(f"平均网络延迟: {avg_latency:.2f}ms")
            print(f"已发送: {self.network_manager.bytes_sent} 字节")
            print(f"已接收: {self.network_manager.bytes_received} 字节")
        else:
            print("\n连接状态: 未连接")
            if self.network_manager.reconnect_attempts > 0:
                print(f"正在尝试重连 ({self.network_manager.reconnect_attempts}/{self.network_manager.max_reconnect_attempts})")
        print("\n> ", end='', flush=True)
    
    def process_command(self, command_line):
        """处理用户输入命令"""
        try:
            # 如果有回调函数在等待输入，则调用它
            if self.on_command_input:
                callback = self.on_command_input
                self.on_command_input = None
                callback(command_line)
                return True
                
            # 否则按正常命令处理
            parts = command_line.strip().split(maxsplit=1)
            command = parts[0].lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []
            
            if command in self.command_handlers:
                self.command_handlers[command](args)
            elif command:
                print(f"未知命令: {command}，输入 'help' 查看可用命令")
                print("\n> ", end='', flush=True)
            
            return True
        except Exception as e:
            self.logger.error(f"处理命令时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            print("\n> ", end='', flush=True)
            return False
            
    @EXPOSED
    def recv_msg_from_server(self, stat, msg):
        """接收服务器消息的回调函数"""
        self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
        self.stat = stat
        
        # 更新网络延迟信息
        self.network_manager.update_latency(time.time())
        
        # 显示命令提示符
        print(f"\n服务器消息: {msg}")
        print("> ", end="", flush=True)
    
    @EXPOSED
    def exit_confirmed(self):
        """服务器确认退出的回调函数"""
        self.logger.info('服务器确认客户端退出')
        self.running = False

# 用于处理用户输入的线程函数
def input_thread_function(client_entity, logger):
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
    parser = argparse.ArgumentParser(description='优化的客户端')
    parser.add_argument('--host', default='127.0.0.1', help='服务器地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=2000, help='服务器端口 (默认: 2000)')
    args = parser.parse_args()
    
    # 设置日志
    logger, log_file = Logger.setup_logger('SampleClient')
    logger.info(f"客户端日志文件: {os.path.abspath(log_file)}")
    
    # 创建网络管理器
    network_manager = ClientNetworkManager(args.host, args.port)
    
    try:
        # 连接服务器
        if not network_manager.connect():
            logger.warning("初始连接失败，将尝试自动重连")
        
        # 创建客户端实体
        client_entity = ClientEntity(network_manager)
        
        if network_manager.connected:
            # 发送第一条消息
            logger.info("发送初始消息到服务器")
            client_entity.caller.remote_call("hello_world_from_client", 1, "你好，服务器!")
        
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
    finally:
        # 确保资源被释放
        if 'network_manager' in locals():
            network_manager.close()
        
if __name__ == "__main__":
    main()