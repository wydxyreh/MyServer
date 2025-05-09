import sys
import os
import time
import argparse
import gc
import weakref
from collections import defaultdict
from functools import partial
import json
import threading
import signal
import traceback

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server.simpleServer import SimpleServer
from server.network.netStream import RpcProxy
from server.common.timer import TimerManager
from server.common import conf
from server.common.db_manager import db_manager
from server.common.logger import logger_instance

def EXPOSED(func):
    """标记允许远程调用的方法的装饰器"""
    func.__exposed__ = True
    return func

class GameServerEntity:
    """游戏服务器实体类，处理单个客户端连接和逻辑"""
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
        
        # 认证相关
        self.authenticated = False
        self.username = None
        self.token = None
        self.login_attempts = 0
        self.max_login_attempts = 5  # 最大登录尝试次数
        self.login_timeout = 15.0    # 登录超时秒数
        self.login_request_time = 0  # 登录请求时间戳
        
        # IP地址信息
        self.ip_address = self._get_ip_address(netstream)
        
        self.logger.info(f"创建新的游戏实体 ID: {self.id}, IP: {self.ip_address}")
        
        # 连接后强制客户端先登录
        self._request_login()
        
    def _get_ip_address(self, netstream):
        """获取客户端IP地址"""
        try:
            if hasattr(netstream, "peername") and netstream.peername:
                return netstream.peername[0]
        except (IndexError, TypeError):
            pass
        return "unknown"
        
    def _request_login(self):
        """请求客户端登录并设置登录超时检查"""
        self.login_request_time = time.time()
        try:
            # 使用安全的计时器来检查登录状态
            timer = threading.Timer(self.login_timeout, self._check_login_status)
            timer.daemon = True  # 设为守护线程，主程序退出时自动关闭
            timer.start()
            self.caller.remote_call("login_required")
            self.logger.debug(f"已向客户端 {self.id} 发送登录请求")
        except Exception as e:
            self._log_error("发送登录请求时出错", e)
        
    def _check_login_status(self):
        """检查登录状态，如果仍未登录则断开连接"""
        try:
            if not self.authenticated and hasattr(self, 'netstream') and self.netstream:
                elapsed = time.time() - self.login_request_time
                self.logger.warning(f"客户端 {self.id} 未在规定时间({elapsed:.1f}秒)内登录，断开连接")
                self._request_client_removal()
        except Exception as e:
            self._log_error("检查登录状态时出错", e)
    
    def _log_error(self, message, exception=None):
        """统一的错误日志记录方法"""
        error_msg = f"{message}: {str(exception)}" if exception else message
        self.logger.error(f"客户端 {self.id} {error_msg}")
        if exception:
            self.logger.error(traceback.format_exc())
    
        # 避免重复发送相同错误消息到客户端
        if hasattr(self, '_last_error_msg') and self._last_error_msg == error_msg:
            return
        
        self._last_error_msg = error_msg
    
    def _request_client_removal(self):
        """请求服务器移除此客户端"""
        if hasattr(self, 'server') and self.server and hasattr(self, 'netstream') and self.netstream:
            try:
                self.server.mark_client_for_removal(self.netstream.hid)
            except Exception as e:
                self._log_error("请求移除客户端时出错", e)

    def _send_client_response(self, method, *args):
        """安全地发送响应到客户端"""
        try:
            if hasattr(self, 'caller') and self.caller:
                self.caller.remote_call(method, *args)
                return True
        except Exception as e:
            self._log_error(f"发送响应 '{method}' 到客户端时出错", e)
        return False

    def destroy(self):
        """销毁实体并清理资源"""
        try:
            self.logger.info(f"销毁游戏实体 ID: {self.id}, IP: {self.ip_address}")
            
            # 若已认证，使token失效
            if self.authenticated and self.token:
                db_manager.invalidate_token(self.token)
                self.logger.debug(f"使用户 {self.username} 的令牌失效")
                
            if hasattr(self, 'caller') and self.caller:
                self.caller.close()  # 确保正确关闭RPC代理
            
            # 清理对象引用
            self.caller = None
            self.netstream = None
        except Exception as e:
            self._log_error("销毁游戏实体时出错", e)
    
    def update_activity_time(self):
        """更新最后活动时间"""
        self.last_activity_time = time.time()
    
    def _verify_token(self):
        """验证用户令牌"""
        if not self.token:
            self.logger.warning(f"客户端 {self.id} 请求操作但无token")
            return False
            
        # 通过数据库管理器验证token，同时验证client_id
        username = db_manager.validate_token(self.token, self.id)
        
        # 确保token对应的用户名与当前认证的用户名一致
        is_valid = username is not None and username == self.username
        
        if not is_valid and username is not None:
            self.logger.warning(f"客户端 {self.id} 提供了有效token但用户名不匹配: 期望={self.username}, 实际={username}")
            
        return is_valid
        
    def _verify_auth(self, operation_name):
        """验证用户是否已认证，如未认证则发送错误消息"""
        self.update_activity_time()
        if not self.authenticated:
            self.logger.warning(f"未认证客户端 {self.id} 尝试进行 {operation_name} 操作")
            self._send_client_response("auth_error", "请先登录")
            return False
        return True
        
    def _verify_auth_with_token(self, operation_name):
        """验证用户认证状态和令牌有效性"""
        if not self._verify_auth(operation_name):
            return False
        
        # 严格验证token - 确保每次操作前都验证token的有效性
        if not self._verify_token():
            self.authenticated = False  # 重置认证状态
            self.token = None  # 清除无效token
            self.logger.warning(f"客户端 {self.id} 的令牌无效或已过期")
            self._send_client_response("auth_error", "会话已过期，请重新登录")
            return False
        return True
    
    @EXPOSED
    def client_login(self, username, password):
        """处理客户端登录请求"""
        try:
            self.update_activity_time()
            
            # 安全检查 - 防止空值或非法值
            if not username or not isinstance(username, str) or not password or not isinstance(password, str):
                self.logger.warning(f"客户端 {self.id} 提供无效凭据格式")
                self._send_client_response("login_failed", "无效的用户名或密码格式")
                return
                
            # 输入长度验证
            if len(username) > 32 or len(password) > 64:
                self.logger.warning(f"客户端 {self.id} 提供超长的用户名或密码")
                self._send_client_response("login_failed", "用户名或密码格式错误")
                return
                
            # 记录登录尝试（不记录密码）
            self.logger.info(f"客户端 {self.id} 尝试认证: {username}")
            
            # 检查登录次数限制（防止暴力破解）
            if self.login_attempts >= self.max_login_attempts:
                self.logger.warning(f"客户端 {self.id} (IP: {self.ip_address}) 认证尝试次数过多")
                self._send_client_response("login_failed", "认证尝试次数过多，请稍后再试")
                self._request_client_removal()
                return
                
            # 增加登录尝试计数
            self.login_attempts += 1
            
            # 认证过程 - 确保只有认证成功才会获得token，并传入client_id
            # 使用self.id作为client标识，便于追踪token与客户端的关联
            token = db_manager.authenticate(username, password, self.id)
            
            if token:
                # 处理认证成功
                self._handle_successful_login(username, token)
            else:
                # 认证失败
                self.logger.warning(f"客户端 {self.id} (IP: {self.ip_address}) 认证失败: {username}")
                self._send_client_response("login_failed", "用户名或密码错误")
                
        except Exception as e:
            self._log_error("处理登录请求时出错", e)
            self._send_client_response("login_failed", "登录过程中发生错误")
    
    def _handle_successful_login(self, username, token):
        """处理成功的登录请求"""
        try:
            # 检查该用户是否已经在其他客户端登录
            existing_client = self.server.find_client_by_username(username)
            if existing_client and existing_client != self:
                self._handle_existing_login(existing_client)
            
            # 登录成功
            self.authenticated = True
            self.username = username
            self.token = token
            self.logger.info(f"客户端 {self.id} (IP: {self.ip_address}) 登录成功: {username}")
            
            # 发送登录成功消息
            self._send_client_response("login_success", token)
            
            # 注册到用户名索引
            self.server.register_authenticated_client(username, self)
            
            # 加载用户数据
            self._load_user_data(username)
            
        except Exception as e:
            self._log_error("处理成功登录过程中出错", e)
            self._send_client_response("login_failed", "登录后处理数据时出错")

    def _handle_existing_login(self, existing_client):
        """处理已存在的登录会话"""
        self.logger.warning(f"用户 {existing_client.username} 已在其他客户端登录，强制断开旧连接")
        # 告知旧客户端被踢出
        try:
            existing_client._send_client_response("kicked", "您的账号在其他设备登录")
        except:
            pass
        # 强制断开旧连接
        existing_client._request_client_removal()
    
    def _load_user_data(self, username):
        """加载用户数据"""
        user_data = db_manager.load_user_data(username)
        if user_data:
            self._send_client_response("userdata_update", user_data)
        else:
            self.logger.warning(f"用户 {username} 无用户数据")
            # 创建空的用户数据结构
            empty_data = json.dumps({"name": username})
            db_manager.save_user_data(username, empty_data)
            self._send_client_response("userdata_update", empty_data)
    
    @EXPOSED
    def userdata_load(self):
        """加载用户数据"""
        if not self._verify_auth_with_token("数据加载"):
            return
        
        # 加载用户数据
        user_data = db_manager.load_user_data(self.username)
        if user_data:
            self.logger.info(f"为用户 {self.username} 加载数据")
            self._send_client_response("userdata_update", user_data)
        else:
            self.logger.warning(f"无法加载用户 {self.username} 的数据")
            self._send_client_response("data_error", "加载数据失败")
    
    @EXPOSED
    def userdata_save(self, data_json):
        """保存用户数据"""
        if not self._verify_auth_with_token("数据保存"):
            return
        
        # 保存数据
        try:
            # 验证并处理JSON数据
            data_json = self._validate_json_data(data_json)
            if data_json is None:  # 验证失败
                return
                
            success = db_manager.save_user_data(self.username, data_json)
            
            if success:
                self.logger.info(f"成功保存用户 {self.username} 的数据")
                self._send_client_response("save_success")
            else:
                self.logger.warning(f"保存用户 {self.username} 的数据失败")
                self._send_client_response("data_error", "保存数据失败")
                
        except Exception as e:
            self._log_error("保存数据时出错", e)
            self._send_client_response("data_error", f"保存数据时出错: {str(e)}")
    
    def _validate_json_data(self, data):
        """验证JSON数据格式"""
        try:
            if isinstance(data, str):
                # 尝试验证JSON格式
                json.loads(data)
                return data
            else:
                # 如果不是字符串，尝试转换为JSON字符串
                return json.dumps(data)
                
        except json.JSONDecodeError:
            self.logger.error(f"无效的JSON数据格式")
            self._send_client_response("data_error", "无效的数据格式")
            return None
    
    @EXPOSED
    def hello_world_from_client(self, stat, msg):
        """处理来自客户端的问候"""
        if not self._verify_auth("发送消息"):
            return
            
        # 将消息添加到队列，而不是立即处理
        self.pending_messages.append(("hello", stat, msg))
    
    @EXPOSED
    def ping_test(self):
        """连接测试方法，不需要认证，客户端可以用来检查连接是否有效"""
        try:
            self.update_activity_time()
            self.logger.debug(f"收到客户端 {self.id} 的ping测试")
            self._send_client_response("pong_response", "连接正常")
            return True
        except Exception as e:
            self.logger.error(f"处理ping测试时出错: {str(e)}")
            return False
        
    def process_messages(self):
        """批量处理积累的消息"""
        if not self.pending_messages:
            return
            
        for msg_type, *args in self.pending_messages:
            try:
                if msg_type == "hello":
                    stat, msg = args
                    self.logger.info(f'服务器收到客户端消息: stat={stat}, msg={msg}, 客户端ID客户端ID: {self.id}, 用户: {self.username}')
                    self._send_client_response("recv_msg_from_server", stat + 1, f"服务器已收到: {msg}")
            except Exception as e:
                self._log_error("处理消息时出错", e)
                
        # 清空消息队列
        self.pending_messages = []
        
    @EXPOSED
    def exit(self):
        """处理客户端的退出请求"""
        self.update_activity_time()
        self.logger.info(f'服务器收到客户端退出请求, 客户端ID: {self.id}, 用户: {self.username if self.authenticated else "未登录"}')
        
        # 使token失效
        if self.authenticated and self.token:
            db_manager.invalidate_token(self.token)
            
        self._send_client_response("exit_confirmed")
        # 将客户端标记为待移除
        self._request_client_removal()

class MyGameServer(SimpleServer):
    """游戏服务器类"""
    _id_counter = 0
    
    def __init__(self):
        super(MyGameServer, self).__init__()
        # 使用单例日志系统
        self.logger = logger_instance.get_logger('GameServer')
        self.log_file = logger_instance._log_files.get('GameServer', '')
        self.logger.info("游戏服务器初始化")
        
        # 客户端管理
        self.clients = {}  # 存储客户端实体 {client_id: entity}
        self.clients_by_username = {}  # 按用户名索引客户端 {username: entity}
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
        """生成唯一的实体ID"""
        self._id_counter += 1
        return self._id_counter
        
    def find_client_by_username(self, username):
        """根据用户名查找客户端实体"""
        return self.clients_by_username.get(username)
        
    def register_authenticated_client(self, username, client):
        """注册已认证的客户端到用户名索引"""
        if username and client:
            self.clients_by_username[username] = client
            
    def mark_client_for_removal(self, client_id):
        """标记客户端待移除，避免在迭代过程中修改clients字典"""
        self.clients_to_remove.add(client_id)
    
    def _log_error(self, message, exception=None):
        """统一的错误日志记录方法"""
        error_msg = f"{message}: {str(exception)}" if exception else message
        self.logger.error(error_msg)
        if exception:
            self.logger.error(traceback.format_exc())
        
    def on_client_connected(self, client_id, client_stream):
        """处理客户端连接事件"""
        try:
            # 获取客户端IP地址
            ip_address = "unknown"
            if hasattr(client_stream, "peername") and client_stream.peername:
                try:
                    ip_address = client_stream.peername[0]
                except (IndexError, TypeError):
                    pass
                    
            self.logger.info(f"新客户端连接: ID={client_id}, IP={ip_address}")
            
            # 限制连接数量，防止DoS攻击
            if len(self.clients) >= 100:  # 最大连接数限制
                self.logger.warning(f"达到最大连接数量限制，拒绝客户端 {client_id} (IP: {ip_address})")
                # 模拟关闭连接，实际上会在下一个tick中处理
                self.mark_client_for_removal(client_id)
                return
                
            # 创建客户端实体
            client_entity = GameServerEntity(client_stream, self)
            self.clients[client_id] = client_entity
        except Exception as e:
            self._log_error(f"处理客户端连接时出错", e)
            # 确保在出错时仍然移除客户端
            self.mark_client_for_removal(client_id)
        
    def on_client_disconnected(self, client_id):
        """处理客户端断开连接事件"""
        try:
            client = self.clients.get(client_id)
            if client:
                self.logger.info(f"客户端断开连接: ID={client_id}, IP={client.ip_address}, " +
                              f"用户={client.username if client.authenticated else '未登录'}")
                
                # 将客户端标记为待移除
                self.mark_client_for_removal(client_id)
            else:
                self.logger.info(f"客户端断开连接: ID={client_id}")
                self.mark_client_for_removal(client_id)
        except Exception as e:
            self._log_error(f"处理客户端断开连接时出错", e)
            self.mark_client_for_removal(client_id)
            
    def on_client_data(self, client_id, data):
        """处理来自客户端的数据"""
        if client_id in self.clients:
            try:
                client_entity = self.clients[client_id]
                current_time = time.time()
                
                # 对还未认证的客户端强制限速
                if not client_entity.authenticated:
                    if current_time - client_entity.last_activity_time < 0.05:
                        self.logger.warning(f"客户端 {client_id} 数据请求频率过高，可能是攻击行为")
                        return  # 直接丢弃该请求
                
                # 更新活动时间
                client_entity.update_activity_time()
                
                # 记录数据统计
                data_size = len(data)
                self.network_stats['bytes_received'] += data_size
                self.network_stats['msgs_received'] += 1
                
                # 限制单个客户端的数据大小
                if data_size > 1024 * 1024:  # 1MB大小限制
                    self.logger.warning(f"客户端 {client_id} 发送超大数据包 ({data_size} 字节)，可能是攻击行为")
                    return
                
                # 确保实体和RPC代理有效
                if client_entity and hasattr(client_entity, 'caller') and client_entity.caller:
                    # 尝试解析RPC调用
                    client_entity.caller.parse_rpc(data)
                else:
                    self.logger.warning(f"客户端 {client_id} 的实体或RPC代理无效")
            except Exception as e:
                self._log_error(f"处理客户端 {client_id} 数据时出错", e)
    
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
        try:
            self._process_entity_messages()
            self._remove_marked_clients()
        except Exception as e:
            self._log_error(f"执行中频定时任务时出错", e)
    
    def _process_entity_messages(self):
        """处理所有实体的消息队列"""
        for client_id, entity in list(self.clients.items()):
            try:
                entity.process_messages()
                
                # 更新用户名索引 - 仅当实体已认证且未记录时
                if entity.authenticated and entity.username and self.clients_by_username.get(entity.username) != entity:
                    self.clients_by_username[entity.username] = entity
            except Exception as e:
                self._log_error(f"处理实体 {client_id} 消息时出错", e)
                # 处理过程中出错，标记移除该客户端
                self.mark_client_for_removal(client_id)
    
    def _remove_marked_clients(self):
        """移除所有标记为待删除的客户端"""
        if not self.clients_to_remove:
            return
            
        for client_id in self.clients_to_remove:
            self._remove_client(client_id)
        self.clients_to_remove.clear()
    
    def _remove_client(self, client_id):
        """移除指定的客户端"""
        if client_id not in self.clients:
            return
            
        try:
            client = self.clients[client_id]
            
            # 如果已认证，从用户名索引中移除
            if client.authenticated and client.username in self.clients_by_username:
                if self.clients_by_username[client.username] == client:
                    del self.clients_by_username[client.username]
            
            # 销毁客户端实体
            client.destroy()
            del self.clients[client_id]
        except Exception as e:
            self._log_error(f"删除客户端 {client_id} 时出错", e)
            # 确保即使出错也删除引用
            if client_id in self.clients:
                del self.clients[client_id]
    
    def on_low_frequency_tick(self):
        """100ms低频定时器回调 - 处理统计和其他非紧急任务"""
        self._update_performance_stats()
        self._check_inactive_clients()
        
        # 执行垃圾回收
        gc.collect()
    
    def _update_performance_stats(self):
        """更新和记录性能统计信息"""
        self.tick_count += 1
        current_time = time.time()
    
        # 每10秒记录一次详细的统计信息
        if current_time - self.last_stats_time >= 10.0:
            elapsed = current_time - self.last_stats_time
            self.last_stats_time = current_time
        
            # 计算网络统计
            bytes_recv_rate = self.network_stats['bytes_received'] / elapsed
            bytes_sent_rate = self.network_stats['bytes_sent'] / elapsed
            msgs_recv_rate = self.network_stats['msgs_received'] / elapsed
            msgs_sent_rate = self.network_stats['msgs_sent'] / elapsed
        
            # 使用debug级别记录详细统计，减少日志噪音
            self.logger.debug(f"服务器运行状态: {len(self.clients)}个客户端, "
                             f"接收速率: {bytes_recv_rate:.2f}B/s ({msgs_recv_rate:.2f}条/s), "
                             f"发送速率: {bytes_sent_rate:.2f}B/s ({msgs_sent_rate:.2f}条/s)")
        
            # 只在有实际客户端连接时使用info级别记录
            if len(self.clients) > 0:
                self.logger.info(f"服务器运行中: {len(self.clients)}个客户端连接")
            
            # 重置统计数据
            self.network_stats = {
                'bytes_received': 0,
                'bytes_sent': 0,
                'msgs_received': 0,
                'msgs_sent': 0
            }
    
    def _check_inactive_clients(self):
        """检查并移除不活跃的客户端"""
        current_time = time.time()
        inactive_threshold = 300  # 5分钟不活跃则断开
        
        for client_id, entity in list(self.clients.items()):
            if current_time - entity.last_activity_time > inactive_threshold:
                self.logger.warning(f"客户端 {client_id} 长时间不活跃，断开连接")
                self.mark_client_for_removal(client_id)
    
    def tick(self):
        """重写tick方法，使其更轻量级 - 主要流程由定时器处理"""
        # 运行定时器调度器
        TimerManager.scheduler()

    def shutdown_all_clients(self, reason="服务器正在关闭"):
        """优雅地关闭所有客户端连接"""
        self.logger.info(f"通知所有客户端服务器关闭: {reason}")
        for client_id, client in list(self.clients.items()):
            try:
                if client and hasattr(client, 'caller') and client.caller:
                    client._send_client_response("server_shutdown", reason)
            except Exception as e:
                self.logger.warning(f"通知客户端 {client_id} 服务器关闭时出错: {str(e)}")

def signal_handler(signum, frame):
    """处理系统信号"""
    logger = logger_instance.get_logger('SignalHandler')
    signal_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else f"Signal {signum}"
    logger.info(f"接收到信号: {signal_name}")
    
    # 触发优雅退出
    global should_exit
    should_exit = True

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='优化的游戏服务器')
    parser.add_argument('--port', type=int, default=2000, help='监听端口 (默认: 2000)')
    parser.add_argument('--bind', default='0.0.0.0', help='绑定地址 (默认: 0.0.0.0)')
    args = parser.parse_args()
    
    # 设置全局日志记录器
    logger = logger_instance.get_logger('Main')
    
    # 创建并初始化服务器
    server = None
    should_exit = False
    
    try:
        # 注册信号处理器
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, signal_handler)
        
        # 创建服务器实例
        server = MyGameServer()
        
        # 启动网络服务
        result = server.host.startup(args.port)
        if result != 0:
            logger.error(f"服务器启动失败，端口 {args.port} 可能已被占用")
            sys.exit(1)
        
        logger.info(f"服务器已启动，正在监听 {args.bind}:{args.port}...")
        
        # 主循环 - 只运行定时器调度
        while not should_exit:
            try:
                server.tick()
                time.sleep(0.001)  # 微小的延迟以减轻CPU负担
            except Exception as e:
                logger.error(f"服务器主循环中发生错误: {str(e)}")
                logger.error(traceback.format_exc())
                # 考虑是否退出服务器
                if should_exit:
                    break
                    
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，服务器正在关闭...")
    except Exception as e:
        logger.error(f"服务器运行时发生意外错误: {str(e)}")
        logger.error(traceback.format_exc())
    finally:
        # 优雅关闭
        if server:
            logger.info("正在关闭服务器...")
            # 通知所有客户端服务器将关闭
            server.shutdown_all_clients()
            
            # 关闭网络服务
            server.host.shutdown()
            
            # 清理资源
            logger.info("正在清理资源...")
            try:
                db_manager.cleanup()
            except Exception as e:
                logger.error(f"清理数据库资源时出错: {str(e)}")
                logger.error(traceback.format_exc())
            
            # 添加一个额外的清理日志记录，确保即使出现错误也能记录
            cleanup_logger = logger_instance.get_logger('Cleanup')
            cleanup_logger.info("正在清理全局资源...")
            
            # 强制执行垃圾收集以释放所有资源
            gc.collect()
            cleanup_logger.info("已执行垃圾回收以释放socket资源")
            
        logger.info("服务器已完全关闭。")