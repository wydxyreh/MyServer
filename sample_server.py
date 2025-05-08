import sys
import os
import time
import argparse
import gc
import weakref
from collections import defaultdict
from functools import partial

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server.simpleServer import SimpleServer
from server.network.netStream import RpcProxy
from server.common.timer import TimerManager
from server.common import conf

def EXPOSED(func):
    func.__exposed__ = True
    return func

class GameServerEntity:
    EXPOSED_FUNC = {}
    
    def __init__(self, netstream, server):
        self.netstream = netstream
        self.caller = RpcProxy(self, netstream)
        # 使用弱引用避免循环引用
        self.server = weakref.proxy(server)
        self.logger = server.logger
        self.id = server.generateEntityID()
        self.last_activity_time = time.time()
        # 用于批量处理的消息队列
        self.pending_messages = []
        self.logger.info(f"创建新的游戏实体 ID: {self.id}")
        
    def destroy(self):
        self.logger.info(f"销毁游戏实体 ID: {self.id}")
        if self.caller:
            self.caller.close()  # 确保正确关闭RPC代理
        self.caller = None
        self.netstream = None
        
    @EXPOSED
    def hello_world_from_client(self, stat, msg):
        """处理来自客户端的问候"""
        self.last_activity_time = time.time()
        # 将消息添加到队列，而不是立即处理
        self.pending_messages.append(("hello", stat, msg))
        
    def process_messages(self):
        """批量处理积累的消息"""
        if not self.pending_messages:
            return
            
        for msg_type, *args in self.pending_messages:
            if msg_type == "hello":
                stat, msg = args
                self.logger.info(f'服务器收到客户端消息: stat={stat}, msg={msg}, 客户端ID: {self.id}')
                self.caller.remote_call("recv_msg_from_server", stat + 1, f"服务器已收到: {msg}")
                
        # 清空消息队列
        self.pending_messages = []
        
    @EXPOSED
    def exit(self):
        """处理客户端的退出请求"""
        self.last_activity_time = time.time()
        self.logger.info(f'服务器收到客户端退出请求, 客户端ID: {self.id}')
        self.caller.remote_call("exit_confirmed")
        # 将客户端标记为待移除
        self.server.mark_client_for_removal(self.netstream.hid)

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
        self.clients_to_remove = set()  # 存储待移除的客户端ID
        
        # 性能监控
        self.tick_count = 0
        self.start_time = time.time()
        self.last_stats_time = self.start_time
        self.network_stats = {
            'bytes_received': 0,
            'bytes_sent': 0,
            'msgs_received': 0,
            'msgs_sent': 0
        }
        
        # 注册网络事件处理
        self.host.onConnected = self.on_client_connected
        self.host.onDisconnected = self.on_client_disconnected
        self.host.onData = self.on_client_data
        
        # 设置多级定时器
        self._setup_timers()
        
    def _setup_timers(self):
        """设置三个不同频率的定时器"""
        # 1ms高频定时器 - 只处理紧急的网络事件
        TimerManager.addRepeatTimer(0.001, self.on_high_frequency_tick)
        
        # 10ms中频定时器 - 处理消息队列和实体状态更新
        TimerManager.addRepeatTimer(0.01, self.on_medium_frequency_tick)
        
        # 100ms低频定时器 - 处理统计信息和垃圾回收等任务
        TimerManager.addRepeatTimer(0.1, self.on_low_frequency_tick)
    
    def generateEntityID(self):
        self._id_counter += 1
        return self._id_counter
        
    def mark_client_for_removal(self, client_id):
        """标记客户端待移除，避免在迭代过程中修改clients字典"""
        self.clients_to_remove.add(client_id)
        
    def on_client_connected(self, client_id, client_stream):
        """处理客户端连接事件"""
        self.logger.info(f"新客户端连接: {client_id}")
        # 创建客户端实体
        client_entity = GameServerEntity(client_stream, self)
        self.clients[client_id] = client_entity
        
    def on_client_disconnected(self, client_id):
        """处理客户端断开连接事件"""
        self.logger.info(f"客户端断开连接: {client_id}")
        self.mark_client_for_removal(client_id)
            
    def on_client_data(self, client_id, data):
        """处理来自客户端的数据"""
        if client_id in self.clients:
            try:
                data_size = len(data)
                self.network_stats['bytes_received'] += data_size
                self.network_stats['msgs_received'] += 1
                
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
    
    def on_high_frequency_tick(self):
        """1ms高频定时器回调 - 只处理网络IO"""
        from server.common import conf
        
        # 处理网络事件
        self.host.process()
        
        # 处理消息队列
        events_count = 0
        while events_count < 10:  # 限制每次处理的事件数量
            event_type, hid, data = self.host.read()
            if event_type == -1:
                break
                
            # 处理连接事件
            if event_type == conf.NET_CONNECTION_NEW and self.host.onConnected:
                self.host.onConnected(hid, self.host.clients[hid & conf.MAX_HOST_CLIENTS_INDEX])
                
            # 处理断开连接事件
            elif event_type == conf.NET_CONNECTION_LEAVE and self.host.onDisconnected:
                self.host.onDisconnected(hid)
                
            # 处理数据事件
            elif event_type == conf.NET_CONNECTION_DATA and self.host.onData:
                self.host.onData(hid, data)
            
            events_count += 1
    
    def on_medium_frequency_tick(self):
        """10ms中频定时器回调 - 处理消息和实体状态"""
        # 处理实体的消息队列
        for client_id, entity in list(self.clients.items()):
            try:
                entity.process_messages()
            except Exception as e:
                self.logger.error(f"处理实体 {client_id} 消息时出错: {str(e)}")
        
        # 移除标记为待删除的客户端
        if self.clients_to_remove:
            for client_id in self.clients_to_remove:
                if client_id in self.clients:
                    self.clients[client_id].destroy()
                    del self.clients[client_id]
            self.clients_to_remove.clear()
    
    def on_low_frequency_tick(self):
        """100ms低频定时器回调 - 处理统计和其他非紧急任务"""
        # 更新性能统计
        self.tick_count += 1
        current_time = time.time()
        if current_time - self.last_stats_time >= 10.0:  # 每10秒记录一次详细的统计信息
            elapsed = current_time - self.last_stats_time
            self.last_stats_time = current_time
            
            # 计算网络统计
            bytes_recv_rate = self.network_stats['bytes_received'] / elapsed
            bytes_sent_rate = self.network_stats['bytes_sent'] / elapsed
            msgs_recv_rate = self.network_stats['msgs_received'] / elapsed
            msgs_sent_rate = self.network_stats['msgs_sent'] / elapsed
            
            self.logger.info(f"服务器运行状态: {len(self.clients)}个客户端, "
                             f"接收速率: {bytes_recv_rate:.2f}B/s ({msgs_recv_rate:.2f}条/s), "
                             f"发送速率: {bytes_sent_rate:.2f}B/s ({msgs_sent_rate:.2f}条/s)")
            
            # 重置统计数据
            self.network_stats = {
                'bytes_received': 0,
                'bytes_sent': 0,
                'msgs_received': 0,
                'msgs_sent': 0
            }
            
        # 检测不活跃的客户端
        current_time = time.time()
        inactive_threshold = 300  # 5分钟不活跃则断开
        for client_id, entity in list(self.clients.items()):
            if current_time - entity.last_activity_time > inactive_threshold:
                self.logger.warning(f"客户端 {client_id} 长时间不活跃，断开连接")
                self.mark_client_for_removal(client_id)
                
        # 执行垃圾回收
        gc.collect()
    
    def tick(self):
        """重写tick方法，使其更轻量级 - 主要流程由定时器处理"""
        # 运行定时器调度器
        TimerManager.scheduler()

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='优化的游戏服务器')
    parser.add_argument('--port', type=int, default=2000, help='监听端口 (默认: 2000)')
    args = parser.parse_args()
    
    # 创建并初始化服务器
    server = MyGameServer()
    
    # 启动网络服务
    server.host.startup(args.port)
    server.logger.info(f"服务器已启动，正在监听端口 {args.port}...")
    
    try:
        # 导入需要的模块
        from server.common import conf
        
        # 主循环 - 极简设计，只运行定时器调度
        while True:
            server.tick()
            time.sleep(0.001)  # 微小的延迟以减轻CPU负担
    except KeyboardInterrupt:
        server.logger.info("接收到关闭信号，服务器正在关闭...")
        server.host.shutdown()
        server.logger.info("服务器已关闭。")