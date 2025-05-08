import sys
import os
import time
import argparse

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server.simpleServer import SimpleServer
from server.network.netStream import RpcProxy

def EXPOSED(func):
    func.__exposed__ = True
    return func

class GameServerEntity:
    EXPOSED_FUNC = {}
    
    def __init__(self, netstream, server):
        self.netstream = netstream
        self.caller = RpcProxy(self, netstream)
        self.server = server
        self.logger = server.logger
        self.id = server.generateEntityID()
        self.logger.info(f"创建新的游戏实体 ID: {self.id}")
        
    def destroy(self):
        self.logger.info(f"销毁游戏实体 ID: {self.id}")
        self.caller = None
        self.netstream = None
        
    @EXPOSED
    def hello_world_from_client(self, stat, msg):
        """处理来自客户端的问候"""
        self.logger.info(f'服务器收到客户端消息: stat={stat}, msg={msg}, 客户端ID: {self.id}')
        self.caller.remote_call("recv_msg_from_server", stat + 1, f"服务器已收到: {msg}")
        
    @EXPOSED
    def exit(self):
        """处理客户端的退出请求"""
        self.logger.info(f'服务器收到客户端退出请求, 客户端ID: {self.id}')
        self.caller.remote_call("exit_confirmed")

class MyGameServer(SimpleServer):
    _id_counter = 0
    
    def __init__(self):
        super(MyGameServer, self).__init__()
        # 使用单例日志系统
        from server.common.logger import logger_instance
        self.logger = logger_instance.get_logger('GameServer')
        self.log_file = logger_instance._log_files.get('GameServer', '')
        self.logger.info("游戏服务器初始化")
        self.clients = {}  # 存储客户端实体
        
        # 注册网络事件处理
        self.host.onConnected = self.on_client_connected
        self.host.onDisconnected = self.on_client_disconnected
        self.host.onData = self.on_client_data
    
    def generateEntityID(self):
        self._id_counter += 1
        self.logger.debug(f"生成新的实体ID: {self._id_counter}")
        return self._id_counter
        
    def on_client_connected(self, client_id, client_stream):
        """处理客户端连接事件"""
        self.logger.info(f"新客户端连接: {client_id}")
        # 创建客户端实体
        client_entity = GameServerEntity(client_stream, self)
        self.clients[client_id] = client_entity
        
    def on_client_disconnected(self, client_id):
        """处理客户端断开连接事件"""
        self.logger.info(f"客户端断开连接: {client_id}")
        if client_id in self.clients:
            self.clients[client_id].destroy()
            del self.clients[client_id]
            
    def on_client_data(self, client_id, data):
        """处理来自客户端的数据"""
        if client_id in self.clients:
            try:
                self.logger.debug(f"收到客户端 {client_id} 数据: {len(data)} 字节")
                # 获取客户端实体
                client_entity = self.clients[client_id]
                # 确保实体和RPC代理有效
                if client_entity and client_entity.caller:
                    client_entity.caller.parse_rpc(data)
                else:
                    self.logger.warning(f"客户端 {client_id} 的实体或RPC代理无效")
            except Exception as e:
                self.logger.error(f"处理客户端 {client_id} 数据时出错: {str(e)}")
                import traceback
                self.logger.error(traceback.format_exc())

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='游戏服务器')
    parser.add_argument('--port', type=int, default=2000, help='监听端口 (默认: 2000)')
    args = parser.parse_args()
    
    # 创建并初始化服务器
    server = MyGameServer()
    
    # 启动网络服务
    server.host.startup(args.port)
    server.logger.info(f"服务器已启动，正在监听端口 {args.port}...")
    
    try:
        # 主循环
        tick_count = 0
        start_time = time.time()
        
        while True:
            server.tick()
            tick_count += 1
            
            # 每1000次tick记录一次性能统计
            if tick_count % 1000 == 0:
                current_time = time.time()
                elapsed = current_time - start_time
                tps = 1000 / elapsed if elapsed > 0 else 0
                server.logger.info(f"服务器运行状态: {tick_count}次tick, {tps:.2f} TPS")
                start_time = current_time
                
            time.sleep(0.01)  # 控制循环速度
    except KeyboardInterrupt:
        server.logger.info("接收到关闭信号，服务器正在关闭...")
        server.host.shutdown()
        server.logger.info("服务器已关闭。")