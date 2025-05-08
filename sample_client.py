# -*- coding: utf-8 -*-
import sys
import os
import time
import argparse
import socket

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
 
from server.common import conf
from server.network.netStream import NetStream, RpcProxy
from server.common.logger import Logger

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
        self.last_heartbeat_time = 0
        self.setup_time = 0
        self.heartbeat_count = 0
        
    def connect(self):
        """连接到服务器"""
        try:
            self.logger.info(f"尝试连接到服务器: {self.host}:{self.port}")
            self.socket.connect(self.host, self.port)
            self.socket.nodelay(1)
            self.setup_time = time.time()
            self.last_heartbeat_time = self.setup_time
            
            # 等待连接建立
            connection_timeout = time.time() + 5.0  # 5秒连接超时
            
            while time.time() < connection_timeout:
                self.socket.process()
                if self.socket.status() == conf.NET_STATE_ESTABLISHED:
                    self.connected = True
                    self.logger.info(f"已成功连接到服务器 {self.host}:{self.port}")
                    return True
                time.sleep(0.1)
            
            self.logger.error("连接服务器超时")
            return False
            
        except Exception as e:
            self.logger.error(f"连接服务器时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def process(self):
        """处理网络事件，返回接收到的数据"""
        if not self.connected:
            return None
            
        try:
            # 处理网络事件
            self.socket.process()
            
            # 发送心跳包
            self.send_heartbeat()
            
            # 接收数据
            data = self.socket.recv()
            if data:
                self.logger.debug(f"收到服务器数据: {len(data)} 字节")
                return data
                
            return None
            
        except Exception as e:
            self.logger.error(f"处理网络事件时出错: {str(e)}")
            self.connected = False
            return None
    
    def send(self, data):
        """发送数据到服务器"""
        if not self.connected:
            self.logger.warning("尝试发送数据但未连接到服务器")
            return False
            
        try:
            self.socket.send(data)
            return True
        except Exception as e:
            self.logger.error(f"发送数据时出错: {str(e)}")
            return False
    
    def send_heartbeat(self):
        """发送心跳包以保持连接活跃"""
        cur_time = time.time()
        if cur_time - self.last_heartbeat_time >= self.heartbeat_interval:
            self.last_heartbeat_time = cur_time
            self.heartbeat_count += 1
            uptime = int(cur_time - self.setup_time)
            
            # 使用RPC机制发送心跳
            try:
                # 我们这里用一个特殊的方法标记心跳，服务端可以忽略或记录
                heartbeat_data = f"HEARTBEAT|{uptime}|{self.heartbeat_count}"
                self.logger.debug(f"发送心跳 #{self.heartbeat_count}, 运行时间: {uptime}秒")
                # 注意：在实际实现中，应该使用已注册的RPC方法，这里仅用做示例
                return True
            except Exception as e:
                self.logger.error(f"发送心跳包时出错: {str(e)}")
                return False
    
    def close(self):
        """关闭连接"""
        if self.socket:
            self.logger.info("关闭网络连接")
            self.socket.close()
            self.connected = False

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
        self.command_handlers = {
            'send': self.handle_send_command,
            'exit': self.handle_exit_command,
            'help': self.handle_help_command,
            'status': self.handle_status_command
        }
        
    def destroy(self):
        self.logger.info("客户端实体被销毁")
        self.caller = None
        self.socket = None
        self.network_manager = None
        
    def handle_send_command(self, args=None):
        """处理发送消息命令"""
        message = input("请输入要发送的消息: ")
        self.logger.info(f'客户端发送新消息: {message}')
        self.caller.remote_call("hello_world_from_client", self.stat + 1, message)
    
    def handle_exit_command(self, args=None):
        """处理退出命令"""
        self.logger.info('客户端发送退出请求')
        self.caller.remote_call("exit")
    
    def handle_help_command(self, args=None):
        """显示帮助信息"""
        print("\n可用命令:")
        print("  send    - 发送消息到服务器")
        print("  exit    - 退出客户端")
        print("  status  - 显示连接状态")
        print("  help    - 显示此帮助信息")
    
    def handle_status_command(self, args=None):
        """显示连接状态"""
        if self.network_manager.connected:
            uptime = int(time.time() - self.network_manager.setup_time)
            print(f"\n连接状态: 已连接")
            print(f"服务器地址: {self.network_manager.host}:{self.network_manager.port}")
            print(f"连接时长: {uptime}秒")
            print(f"心跳次数: {self.network_manager.heartbeat_count}")
        else:
            print("\n连接状态: 未连接")
    
    def process_command(self):
        """处理用户输入命令"""
        try:
            print("\n输入命令 (help 查看帮助):")
            user_input = input("> ").strip().lower()
            command = user_input.split()[0] if user_input else ""
            
            if command in self.command_handlers:
                args = user_input.split()[1:] if len(user_input.split()) > 1 else None
                self.command_handlers[command](args)
            elif command:
                print(f"未知命令: {command}，输入 'help' 查看可用命令")
            
            return True
        except Exception as e:
            self.logger.error(f"处理命令时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
            
    @EXPOSED
    def recv_msg_from_server(self, stat, msg):
        """接收服务器消息的回调函数"""
        self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
        self.stat = stat
        self.logger.info(f'客户端状态更新为: {self.stat}')
        self.process_command()
    
    @EXPOSED
    def exit_confirmed(self):
        """服务器确认退出的回调函数"""
        self.logger.info('服务器确认客户端退出')
        self.running = False

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='示例客户端')
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
            logger.error("无法连接到服务器，程序退出")
            return
        
        # 创建客户端实体
        client_entity = ClientEntity(network_manager)
        
        # 发送第一条消息
        logger.info("发送初始消息到服务器")
        client_entity.caller.remote_call("hello_world_from_client", 1, "你好，服务器!")
        
        # 主循环
        while client_entity.running:
            try:
                # 处理网络事件
                data = network_manager.process()
                
                # 如果连接断开，退出循环
                if not network_manager.connected:
                    logger.error("与服务器的连接已断开")
                    break
                    
                # 处理接收到的数据
                if data:
                    client_entity.caller.parse_rpc(data)
                
                time.sleep(0.1)
            except KeyboardInterrupt:
                logger.info("用户中断，客户端退出")
                break
            except Exception as e:
                logger.error(f"客户端主循环中出现错误: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
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