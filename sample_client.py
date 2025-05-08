# -*- coding: utf-8 -*-
import sys
import os
import time
import argparse

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
 
from server.common import conf
from server.network.netStream import NetStream, RpcProxy
from server.common.logger import Logger

def EXPOSED(func):
    func.__exposed__ = True
    return func

class ClientEntity(object):
    """客户端实体类，用于与服务器交互"""
    EXPOSED_FUNC = {}
    
    def __init__(self, netstream):
        self.netstream = netstream
        self.caller = RpcProxy(self, netstream)
        self.stat = 0
        self.logger, _ = Logger.setup_logger('SampleClient')
        self.running = True
        
    def destroy(self):
        self.logger.info("客户端实体被销毁")
        self.caller = None
        self.netstream = None
        
    @EXPOSED
    def recv_msg_from_server(self, stat, msg):
        """接收服务器消息的回调函数"""
        self.logger.info(f'客户端收到服务器消息: stat={stat}, msg={msg}')
        self.stat = stat
        self.logger.info(f'客户端状态更新为: {self.stat}')
        
        # 在实际应用中可以在这里处理服务器发来的各种消息
        # 例如更新游戏状态、显示聊天消息等
        
        print("\n可用命令: [send] 发送新消息, [exit] 退出")
        user_input = input("请输入命令: ")
        if user_input.lower() == 'exit':
            self.logger.info('客户端发送退出请求')
            self.caller.remote_call("exit")
        elif user_input.lower() == 'send':
            message = input("请输入要发送的消息: ")
            self.logger.info(f'客户端发送新消息: {message}')
            self.caller.remote_call("hello_world_from_client", self.stat + 1, message)
        else:
            self.logger.info('未知命令，使用默认消息')
            self.caller.remote_call("hello_world_from_client", self.stat + 1, "这是一条默认消息")
    
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
    
    # 创建网络连接
    sock = NetStream()
    logger.info(f"尝试连接到服务器: {args.host}:{args.port}")
    
    try:
        # 连接服务器
        sock.connect(args.host, args.port)
        sock.nodelay(1)
        
        # 等待连接建立
        connection_timeout = time.time() + 5.0  # 5秒连接超时
        connected = False
        
        while not connected and time.time() < connection_timeout:
            sock.process()
            if sock.status() == conf.NET_STATE_ESTABLISHED:
                connected = True
                logger.info("已成功连接到服务器")
                break
            time.sleep(0.1)
        
        if not connected:
            logger.error("连接服务器超时")
            return
        
        # 创建客户端实体
        client_entity = ClientEntity(sock)
        
        # 发送第一条消息
        logger.info("发送初始消息到服务器")
        client_entity.caller.remote_call("hello_world_from_client", 1, "你好，服务器!")
        
        # 主循环
        while client_entity.running:
            # 处理网络事件
            sock.process()
            
            # 接收数据
            recv_data = sock.recv()
            if len(recv_data) > 0:
                logger.info(f"收到服务器数据: {len(recv_data)} 字节")
                client_entity.caller.parse_rpc(recv_data)
            
            time.sleep(0.1)
        
        # 清理资源
        client_entity.destroy()
        logger.info("客户端正常退出")
        
    except KeyboardInterrupt:
        logger.info("用户中断，客户端退出")
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")
    finally:
        # 确保资源被释放
        if 'sock' in locals():
            sock.close()
        
if __name__ == "__main__":
    main()