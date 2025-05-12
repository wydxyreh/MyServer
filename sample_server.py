import sys
import os
import time
import argparse
import gc
import weakref
import json
import signal
import traceback
import hashlib
import random

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

def log_function(func):
    """装饰器，记录函数调用的名称、参数和执行完成情况，包括详细的参数内容和返回值"""
    def wrapper(*args, **kwargs):
        instance = args[0] if args else None
        # 使用类实例的logger或全局logger
        logger = instance.logger if hasattr(instance, 'logger') else logger_instance.get_logger('FunctionLogger')
        
        # 记录函数调用信息
        args_repr = [repr(a) for a in args[1:]]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        
        # 获取调用者信息
        caller_info = ""
        if hasattr(instance, 'id'):
            caller_info += f"客户端ID={instance.id} "
        if hasattr(instance, 'ip_address') and instance.ip_address:
            caller_info += f"IP={instance.ip_address} "
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

# 定义KilledEnemies阈值
class KilledEnemiesThresholds:
    """用于标记用户击杀敌人数量达到的阈值级别"""
    THRESHOLD_ROOKIE = 1        # 新手
    THRESHOLD_FIGHTER = 2       # 战士
    THRESHOLD_VETERAN = 3      # 老兵
    THRESHOLD_MASTER = 10       # 大师
    THRESHOLD_LEGEND = 50       # 传奇

# 用于存储用户阈值达成状态的结构体
class AchievementStatus:
    """用于存储用户已达成的阈值成就状态"""
    def __init__(self):
        self.reached_thresholds = {}  # {阈值名称: 是否已处理}
    
    def has_reached_threshold(self, threshold_name):
        """检查是否已达到并处理过指定阈值"""
        return self.reached_thresholds.get(threshold_name, False)
    
    def mark_threshold_reached(self, threshold_name):
        """标记某阈值已处理"""
        self.reached_thresholds[threshold_name] = True
        
    def to_json(self):
        """转换为可序列化的字典"""
        return self.reached_thresholds
    
    @staticmethod
    def from_json(json_data):
        """从JSON数据创建实例"""
        status = AchievementStatus()
        if isinstance(json_data, dict):
            status.reached_thresholds = json_data
        return status

class GameServerEntity:
    """游戏服务器实体类，处理单个客户端连接和逻辑"""
    
    @log_function
    def __init__(self, netstream, server):
        # 初始化EXPOSED_FUNC字典用于RPC暴露函数
        self.EXPOSED_FUNC = {}
        
        self.netstream = netstream
        self.caller = RpcProxy(self, netstream)
        self.server = weakref.proxy(server)  # 使用弱引用避免循环引用
        self.logger = server.logger
        self.id = server.generateEntityID()
        self.last_activity_time = time.time()
        self.pending_messages = []
        
        # 认证相关
        self.authenticated = False
        self.username = None
        self.token = None
        self.login_attempts = 0
        self.max_login_attempts = 5 # 最大登录尝试次数
        
        # 登出相关
        self.save_data_before_logout = False  # 标记是否需要在登出前保存数据
        
        # 成就相关
        self.achievement_status = AchievementStatus()
        
        # IP地址信息
        self.ip_address = self._get_ip_address(netstream)
        
        self.logger.info(f"创建新的游戏实体 ID: {self.id}, IP: {self.ip_address}")
        
    @log_function
    def _get_ip_address(self, netstream):
        """获取客户端IP地址"""
        try:
            if hasattr(netstream, "peername") and netstream.peername:
                return netstream.peername[0]
        except (IndexError, TypeError):
            pass
        return "unknown"
    
    @log_function
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
    
    @log_function
    def _request_client_removal(self):
        """请求服务器移除此客户端"""
        if hasattr(self, 'server') and self.server and hasattr(self, 'netstream') and self.netstream:
            try:
                self.server.mark_client_for_removal(self.netstream.hid)
            except Exception as e:
                self._log_error("请求移除客户端时出错", e)

    @log_function
    def _send_client_response(self, method, *args):
        """安全地发送响应到客户端"""
        try:
            if hasattr(self, 'caller') and self.caller:
                self.caller.remote_call(method, *args)
                return True
        except Exception as e:
            self._log_error(f"发送响应 '{method}' 到客户端时出错", e)
        return False

    @log_function
    def destroy(self):
        """销毁实体并清理资源"""
        try:
            self.logger.info(f"销毁游戏实体 ID: {self.id}, IP: {self.ip_address}")
            
            # 若已认证，使token失效
            if self.authenticated and self.token:
                self.server.invalidate_token(self.token)
                self.logger.debug(f"使用户 {self.username} 的令牌失效")
                
            if hasattr(self, 'caller') and self.caller:
                self.caller.close() # 确保正确关闭RPC代理
            
            # 清理对象引用
            self.caller = None
            self.netstream = None
        except Exception as e:
            self._log_error("销毁游戏实体时出错", e)
    
    @log_function
    def update_activity_time(self):
        """更新最后活动时间"""
        self.last_activity_time = time.time()
    
    @log_function
    def _verify_token(self):
        """验证用户令牌"""
        if not self.token:
            self.logger.warning(f"客户端 {self.id} 请求操作但无token")
            return False
            
        # 通过服务器验证token，同时验证client_id
        username = self.server.validate_token(self.token, self.id)
        # 确保token对应的用户名与当前认证的用户名一致
        is_valid = username is not None and username == self.username
        
        if not is_valid and username is not None:
            self.logger.warning(f"客户端 {self.id} 提供了有效token但用户名不匹配: 期望={self.username}, 实际={username}")
            
        return is_valid
        
    @log_function
    def _verify_auth(self, operation_name):
        """验证用户是否已认证，如未认证则发送错误消息"""
        self.update_activity_time()
        if not self.authenticated:
            self.logger.warning(f"未认证客户端 {self.id} 尝试进行 {operation_name} 操作")
            self._send_client_response("auth_error", "请先登录")
            return False
        return True
        
    @log_function
    def _verify_auth_with_token(self, operation_name):
        """验证用户认证状态和令牌有效性"""
        if not self._verify_auth(operation_name):
            return False
        
        # 严格验证token - 确保每次操作前都验证token的有效性
        if not self._verify_token():
            self.authenticated = False  # 重置认证状态
            self.token = None # 清除无效token
            self.logger.warning(f"客户端 {self.id} 的令牌无效或已过期")
            self._send_client_response("auth_error", "会话已过期，请重新登录")
            return False
        return True
    
    @EXPOSED
    @log_function
    def client_login(self, username=None, password=None, token=None):
        """处理客户端登录请求
        
        参数:
            username: 用户名
            password: 密码
            token: 认证令牌，如果提供则优先使用
        """
        try:
            self.update_activity_time()
            
            # 先尝试使用token登录
            if token:
                self.logger.info(f"客户端 {self.id} 尝试使用token认证")
                username = self.server.validate_token(token, self.id)
                
                if username:
                    # token有效，直接登录成功
                    self.logger.info(f"客户端 {self.id} 使用token认证成功: {username}")
                    self.token = token
                    self._handle_successful_login(username, token, is_token_login=True)
                    return
                else:
                    # token无效，通知客户端
                    self.logger.warning(f"客户端 {self.id} 提供的token无效")
                    self._send_client_response("token_invalid", "令牌无效，请使用账号密码登录")
                    return
            
            # 使用用户名密码登录 - 安全检查
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
            self.logger.info(f"客户端 {self.id} 尝试使用账密认证: {username}")
            
            # 检查登录次数限制（防止暴力破解）
            if self.login_attempts >= self.max_login_attempts:
                self.logger.warning(f"客户端 {self.id} (IP: {self.ip_address}) 认证尝试次数过多")
                self._send_client_response("login_failed", "认证尝试次数过多，请稍后再试")
                self._request_client_removal()
                return
                
            # 增加登录尝试计数
            self.login_attempts += 1
            
            # 使用数据库验证用户名和密码
            import hashlib
            hashed_pwd = hashlib.sha256(password.encode()).hexdigest()
            auth_success = db_manager.verify_user_credentials(username, hashed_pwd)
            
            if auth_success:
                # 认证成功，生成token
                token = self.server._generate_token(username, self.id)
                if token:
                    # 处理认证成功
                    self._handle_successful_login(username, token, is_token_login=False)
                else:
                    self.logger.error(f"客户端 {self.id} 认证成功但token生成失败")
                    self._send_client_response("login_failed", "内部处理错误")
            else:
                # 认证失败
                self.logger.warning(f"客户端 {self.id} 认证失败: {username}")
                self._send_client_response("login_failed", "用户名或密码错误")
                
        except Exception as e:
            self._log_error("处理登录请求时出错", e)
            self._send_client_response("login_failed", "登录过程中发生错误")
    
    @log_function
    def _handle_successful_login(self, username, token, is_token_login=False):
        """处理成功的登录请求
        
        参数:
            username: 用户名
            token: 认证令牌
            is_token_login: 是否是使用token登录的
        """
        try:
            # 检查该用户是否已经在其他客户端登录
            existing_client = self.server.find_client_by_username(username)
            if existing_client and existing_client != self:
                # 如果是新客户端用账密登录，需要处理旧客户端
                if not is_token_login:
                    # 先通知新客户端正在处理旧连接
                    self._send_client_response("process_existing_session", "正在处理旧连接，请稍候...")
                    
                    # 通知旧客户端被登出并保存数据
                    self._handle_existing_login(existing_client)
                    
                    # 由于我们改为异步处理旧客户端断开连接，这里不立即完成新客户端登录
                    # 设置标记，表示正在等待旧客户端断开
                    self.server.pending_logins[username] = (self, token)
                    self.logger.info(f"等待用户 {username} 的旧会话处理完成")
                    return
                else:
                    # 如果是使用token登录，token应该已经与客户端ID绑定，不应该出现这种情况
                    # 除非是服务端缓存不一致，这里按照token无效处理
                    self.logger.error(f"使用token登录但发现用户 {username} 已在其他客户端登录，可能是缓存不一致")
                    self._send_client_response("token_invalid", "会话不一致，请使用账号密码重新登录")
                    return
            
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

    @log_function
    def _handle_existing_login(self, existing_client):
        """处理已存在的登录会话"""
        self.logger.warning(f"用户 {existing_client.username} 已在其他客户端登录，强制登出旧连接")
        # 告知旧客户端被登出
        try:
            # 通知旧客户端被踢出
            existing_client._send_client_response("forced_logout", "您的账号在其他设备登录")
            
            # 要求旧客户端先保存数据
            existing_client.save_data_before_logout = True
            
            # 标记客户端需要断开连接，但不立即断开
            # 客户端会在数据保存完成后自行调用 _request_client_removal
            # 新客户端的登录会在 process_messages 中处理
        except Exception as e:
            self.logger.error(f"通知旧客户端时出错: {str(e)}")
            # 如果通知失败，强制断开旧连接
            existing_client._request_client_removal()
    
    @log_function
    def _load_user_data(self, username):
        """加载用户数据"""
        user_data = db_manager.load_user_data(username)
        if user_data:
            self._send_client_response("userdata_update", user_data)
        else:
            self.logger.warning(f"用户 {username} 无用户数据")
            # 通知客户端未找到用户数据
            self._send_client_response("data_not_found", "首次登录，未找到用户数据")
    
    @EXPOSED
    @log_function
    def userdata_load(self):
        """加载用户数据 - 从数据库获取最新版本的数据"""
        if not self._verify_auth_with_token("数据加载"):
            return
        
        # 加载用户数据
        user_data = db_manager.load_user_data(self.username)
        if user_data:
            self.logger.info(f"为用户 {self.username} 加载数据")
            self._send_client_response("userdata_update", user_data)
        else:
            self.logger.warning(f"未找到用户 {self.username} 的数据")
            # 明确告知客户端数据不存在
            self._send_client_response("data_not_found", "在数据库中未找到用户数据")
    
    @EXPOSED
    @log_function
    def userdata_save(self, data_json):
        """保存用户数据 - 将数据存储到数据库，与账密关联"""
        if not self._verify_auth_with_token("数据保存"):
            return
        
        # 保存数据
        try:
            # 验证JSON数据格式
            if not self._validate_json_data(data_json):
                return
            
            # 从JSON数据中检查KilledEnemies字段
            try:
                data = json.loads(data_json)
                
                # 尝试加载用户的成就状态
                old_data = db_manager.load_user_data(self.username)
                if old_data:
                    try:
                        old_data_obj = json.loads(old_data)
                        if "AchievementStatus" in old_data_obj:
                            self.achievement_status = AchievementStatus.from_json(old_data_obj["AchievementStatus"])
                    except:
                        # 如果加载失败，继续使用默认空状态
                        pass
                
                # 检查KilledEnemies字段是否存在
                if "KilledEnemies" in data:
                    killed_enemies = data["KilledEnemies"]
                    if isinstance(killed_enemies, (int, float)):
                        # 检查各个阈值
                        self._check_killed_enemies_thresholds(killed_enemies)
                
                # 将成就状态保存回数据对象
                data["AchievementStatus"] = self.achievement_status.to_json()
                # 更新JSON数据
                data_json = json.dumps(data)
            except json.JSONDecodeError:
                self.logger.warning(f"检查KilledEnemies字段时JSON解析失败")
            except Exception as e:
                self.logger.warning(f"处理KilledEnemies阈值时出错: {str(e)}")
            
            # 使用用户名作为唯一索引键存储数据    
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
    
    @log_function
    def _check_killed_enemies_thresholds(self, killed_enemies):
        """检查击杀敌人数量是否达到阈值并处理相应逻辑"""
        # 定义阈值和对应的通知消息
        thresholds = [
            (KilledEnemiesThresholds.THRESHOLD_ROOKIE, "THRESHOLD_ROOKIE", "新手猎手"),
            (KilledEnemiesThresholds.THRESHOLD_FIGHTER, "THRESHOLD_FIGHTER", "战斗精英"),
            (KilledEnemiesThresholds.THRESHOLD_VETERAN, "THRESHOLD_VETERAN", "老练猎人"),
            (KilledEnemiesThresholds.THRESHOLD_MASTER, "THRESHOLD_MASTER", "猎杀大师"),
            (KilledEnemiesThresholds.THRESHOLD_LEGEND, "THRESHOLD_LEGEND", "传奇杀手")
        ]
        
        for threshold_value, threshold_name, threshold_title in thresholds:
            # 检查是否达到阈值且之前未处理过
            if killed_enemies >= threshold_value and not self.achievement_status.has_reached_threshold(threshold_name):
                self.logger.info(f"用户 {self.username} 达到 KilledEnemies 阈值 {threshold_name}: {threshold_value}")
                
                # 标记为已处理
                self.achievement_status.mark_threshold_reached(threshold_name)
                
                # 广播成就到所有已登录的客户端
                self.server.broadcast_achievement(self.username, threshold_title, threshold_value)
    
    @log_function
    def _validate_json_data(self, data):
        """验证JSON数据格式
        
        Args:
            data: 要验证的JSON数据
            
        Returns:
            bool: 数据有效返回True，否则返回False
        """
        try:
            if isinstance(data, str):
                # 尝试验证JSON格式
                json.loads(data)
            # 如果数据已经是字典或其他Python对象，视为有效
            
            return True
                
        except json.JSONDecodeError:
            self.logger.error(f"无效的JSON数据格式")
            self._send_client_response("data_error", "无效的数据格式")
            return False
    
    def process_messages(self):
        """批量处理积累的消息"""
        if not self.pending_messages:
            # 处理被登出逻辑
            if self.save_data_before_logout and self.authenticated:
                self.logger.info(f"用户 {self.username} 被强制登出，正在请求客户端保存数据")
                self.save_data_before_logout = False
                
                # 通知客户端保存数据
                self._send_client_response("on_save_data_request")
                self.logger.info(f"已发送保存数据请求给用户 {self.username} 的客户端")
                
                # 延迟断开连接，确保客户端有足够时间保存并上传数据
                import threading
                threading.Timer(1.0, self._request_client_removal).start()
            return
            
        self.pending_messages = []  # 清空消息队列
        
    @EXPOSED
    @log_function
    def client_logout(self):
        """处理客户端登出请求"""
        if not self._verify_auth("登出"):
            return
            
        self.logger.info(f'客户端 {self.id} (用户: {self.username}) 请求登出')
        
        # 使token失效
        if self.token:
            self.server.invalidate_token(self.token)
            self.token = None
            
        # 修改认证状态
        self.authenticated = False
        
        # 从用户名索引中移除
        if self.username in self.server.clients_by_username:
            if self.server.clients_by_username[self.username] == self:
                del self.server.clients_by_username[self.username]
        
        # 发送登出成功消息
        self._send_client_response("logout_success")
        
        # 保持连接，但重置用户名
        old_username = self.username
        self.username = None
        self.logger.info(f'用户 {old_username} 登出成功，但保持连接')
    
    @EXPOSED
    @log_function
    def exit(self):
        """处理客户端的退出请求"""
        self.update_activity_time()
        self.logger.info(f'服务器收到客户端退出请求, 客户端ID: {self.id}, 用户: {self.username if self.authenticated else "未登录"}')
        
        # 如果客户端已认证，则先请求保存数据
        if self.authenticated:
            self.logger.info(f"客户端ID: {self.id}, 用户: {self.username} 退出前请求保存数据")
            # 通知客户端保存数据
            self._send_client_response("on_save_data_request")
            
            # 使token失效
            if self.token:
                self.server.invalidate_token(self.token)
        
        self._send_client_response("exit_confirmed")
        
        # 延迟断开连接，确保客户端有时间保存数据
        import threading
        threading.Timer(0.5, self._request_client_removal).start()

class MyGameServer(SimpleServer):
    """游戏服务器类"""
    _id_counter = 0
    
    @log_function
    def __init__(self):
        super(MyGameServer, self).__init__()
        self.logger = logger_instance.get_logger('GameServer')
        self.log_file = logger_instance._log_files.get('GameServer', '')
        self.logger.info("游戏服务器初始化")
        
        # 客户端管理
        self.clients = {}  # 存储客户端实体 {client_id: entity}
        self.clients_by_username = {}  # 按用户名索引客户端 {username: entity}
        self.clients_to_remove = set()  # 存储待移除的客户端ID
        
        # Token管理
        self.active_tokens = {}  # {token: (username, expiry_time, client_id)}
        self.token_validity = 7200  # token有效期(秒)
        
        # 等待登录完成的客户端 {username: (client_entity, token)}
        self.pending_logins = {}
        
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
        
        # 未登录客户端超时时间（秒）
        self.login_timeout = 30  # 30秒内必须完成登录
        
        # 注册网络事件处理
        self.host.onConnected = self.on_client_connected
        self.host.onDisconnected = self.on_client_disconnected
        self.host.onData = self.on_client_data
        
        # 设置多级定时器
        self._setup_timers()
        
    @log_function
    def _setup_timers(self):
        """设置三个不同频率的定时器"""
        # 1ms高频定时器 - 只处理紧急的网络事件
        TimerManager.addRepeatTimer(0.001, self.on_high_frequency_tick)
        
        # 10ms中频定时器 - 处理消息队列和实体状态更新
        TimerManager.addRepeatTimer(0.01, self.on_medium_frequency_tick)
        
        # 100ms低频定时器 - 处理统计信息和垃圾回收等任务
        TimerManager.addRepeatTimer(0.1, self.on_low_frequency_tick)
    
    @log_function
    def generateEntityID(self):
        """生成唯一的实体ID"""
        self._id_counter += 1
        return self._id_counter
        
    @log_function
    def find_client_by_username(self, username):
        """根据用户名查找客户端实体"""
        return self.clients_by_username.get(username)
        
    @log_function
    def register_authenticated_client(self, username, client):
        """注册已认证的客户端到用户名索引"""
        if username and client:
            self.clients_by_username[username] = client
            
    @log_function
    def broadcast_achievement(self, username, threshold_title, threshold_value):
        """向所有已连接且登录的客户端广播用户的成就
        
        Args:
            username: 达到阈值的用户名
            threshold_title: 阈值的标题/名称
            threshold_value: 达到的具体阈值值
        """
        self.logger.info(f"广播用户 {username} 的成就: {threshold_title} ({threshold_value})")
        
        # 构建广播消息
        message = f"{username} 已达到 '{threshold_title}' 成就! 击杀数: {threshold_value}"
        
        # 遍历所有认证客户端并发送消息
        for client in self.clients_by_username.values():
            if client and client.authenticated:
                try:
                    client._send_client_response("achievement_broadcast", username, threshold_title, threshold_value)
                except Exception as e:
                    self.logger.warning(f"向客户端 {client.id} 广播成就时出错: {str(e)}")
            
    @log_function
    def mark_client_for_removal(self, client_id):
        """标记客户端待移除"""
        self.clients_to_remove.add(client_id)
    
    @log_function
    def _log_error(self, message, exception=None):
        """统一的错误日志记录方法"""
        error_msg = f"{message}: {str(exception)}" if exception else message
        self.logger.error(error_msg)
        if exception:
            self.logger.error(traceback.format_exc())
        
    @log_function
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
            
            if len(self.clients) >= 100:  # 最大连接数限制
                self.logger.warning(f"达到最大连接数量限制，拒绝客户端 {client_id} (IP: {ip_address})")
                # 模拟关闭连接，实际上会在下一个tick中处理
                self.mark_client_for_removal(client_id)
                return
                
            # 创建客户端实体
            self.clients[client_id] = GameServerEntity(client_stream, self)
        except Exception as e:
            self._log_error(f"处理客户端连接时出错", e)
            # 确保在出错时仍然移除客户端
            self.mark_client_for_removal(client_id)
        
    @log_function
    def on_client_disconnected(self, client_id):
        """处理客户端断开连接事件"""
        try:
            client = self.clients.get(client_id)
            if client:
                self.logger.info(f"客户端断开连接: ID={client_id}, IP={client.ip_address}, " +
                               f"用户={client.username if client.authenticated else '未登录'}")
            else:
                self.logger.info(f"客户端断开连接: ID={client_id}")
                # 客户端已经不存在，无需再次标记移除
                return
        
            self.mark_client_for_removal(client_id)
        except Exception as e:
            self._log_error(f"处理客户端断开连接时出错", e)
            self.mark_client_for_removal(client_id)
            
    @log_function
    def on_client_data(self, client_id, data):
        """处理来自客户端的数据"""
        if client_id not in self.clients:
            return
            
        try:
            client_entity = self.clients[client_id]
            
            # 安全检查：请求频率和数据包大小限制
            current_time = time.time()
            
            # 初始化客户端的请求计数器（如果不存在）
            if not hasattr(client_entity, 'request_stats'):
                client_entity.request_stats = {
                    'last_check_time': current_time,
                    'request_count': 0,
                    'data_volume': 0,
                    'warning_count': 0
                }
            
            # 更新请求统计
            stats = client_entity.request_stats
            stats['request_count'] += 1
            stats['data_volume'] += len(data)
            
            # 自适应请求频率限制 - 基于时间窗口的动态调整
            time_window = 1.0  # 1秒时间窗口
            elapsed = current_time - stats['last_check_time']
            
            if elapsed >= time_window:
                # 计算请求速率（每秒请求数和数据量）
                requests_per_sec = stats['request_count'] / elapsed
                data_per_sec = stats['data_volume'] / elapsed
                
                # 动态阈值：基础值 + 认证状态调整 + 历史行为加权
                # 已认证用户有更高的请求限制
                auth_factor = 2.0 if client_entity.authenticated else 1.0
                base_req_limit = 20 * auth_factor  # 基础请求数限制
                
                # 动态数据量限制 (单位：字节/秒)
                base_data_limit = 100000 * auth_factor  # 约100KB/秒
                
                # 检查是否超过限制
                if (requests_per_sec > base_req_limit or data_per_sec > base_data_limit):
                    stats['warning_count'] += 1
                    self.logger.warning(
                        f"客户端 {client_id} 请求速率异常: "
                        f"{requests_per_sec:.2f}请求/秒, {data_per_sec:.2f}字节/秒, "
                        f"警告次数: {stats['warning_count']}"
                    )
                    
                    # 针对连续多次超限的处理（3次警告后采取措施）
                    if stats['warning_count'] >= 3:
                        # 仅对未认证客户端采取严格限制
                        if not client_entity.authenticated:
                            self.logger.error(f"客户端 {client_id} 持续高频请求，可能是攻击行为，断开连接")
                            self.mark_client_for_removal(client_id)
                            return
                        else:
                            # 对认证客户端发出警告但不断开连接
                            client_entity._send_client_response("rate_limit_warning", 
                                                               "您的请求频率过高，请减缓请求速度")
                            # 重置警告计数，给予宽限
                            stats['warning_count'] = 1
                else:
                    # 正常请求速率，逐渐减少警告计数
                    stats['warning_count'] = max(0, stats['warning_count'] - 1)
                
                # 重置统计
                stats['last_check_time'] = current_time
                stats['request_count'] = 0
                stats['data_volume'] = 0
                
            # 单个数据包大小限制(1MB)
            max_packet_size = 1048576  # 1MB
            if len(data) > max_packet_size:
                self.logger.warning(f"客户端 {client_id} 发送过大的数据包: {len(data)}字节")
                client_entity._send_client_response("data_error", "数据包过大")
                return
                
            # 更新客户端活动时间并记录统计
            client_entity.update_activity_time()
            self.network_stats['bytes_received'] += len(data)
            self.network_stats['msgs_received'] += 1
            
            # 解析RPC调用
            if client_entity.caller:
                client_entity.caller.parse_rpc(data)
        except Exception as e:
            self._log_error(f"处理客户端 {client_id} 数据时出错", e)
    
    def on_high_frequency_tick(self):
        """1ms高频定时器回调 - 只处理网络IO"""
        from server.common import conf
        
        # 处理网络事件
        self.host.process()
        
        # 每次最多处理10个网络事件以避免阻塞
        for _ in range(10):
            event_type, hid, data = self.host.read()
            if event_type == -1:
                break
                
            # 根据事件类型分发处理
            if event_type == conf.NET_CONNECTION_NEW and self.host.onConnected:
                self.host.onConnected(hid, self.host.clients[hid & conf.MAX_HOST_CLIENTS_INDEX])
            elif event_type == conf.NET_CONNECTION_LEAVE and self.host.onDisconnected:
                self.host.onDisconnected(hid)
            elif event_type == conf.NET_CONNECTION_DATA and self.host.onData:
                self.host.onData(hid, data)
    
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
                
                # 更新用户名索引
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
    
    @log_function
    def _remove_client(self, client_id):
        """移除指定的客户端"""
        if client_id not in self.clients:
            return
            
        try:
            client = self.clients[client_id]
            username = client.username if client.authenticated else None
            
            # 如果已认证，从用户名索引中移除
            if client.authenticated and client.username in self.clients_by_username:
                if self.clients_by_username[client.username] == client:
                    del self.clients_by_username[client.username]
            
            # 销毁客户端实体
            client.destroy()
            del self.clients[client_id]
            
            # 检查是否有待处理的登录请求，如果有则完成登录
            if username and username in self.pending_logins:
                new_client, token = self.pending_logins.pop(username)
                if new_client and new_client in self.clients.values():
                    self.logger.info(f"旧客户端已断开，现在完成用户 {username} 的新登录")
                    # 异步完成登录，避免递归调用
                    import threading
                    threading.Timer(0.1, lambda: self._complete_pending_login(new_client, username, token)).start()
        except Exception as e:
            self._log_error(f"删除客户端 {client_id} 时出错", e)
            if client_id in self.clients:
                del self.clients[client_id]
                
    @log_function
    def _complete_pending_login(self, client, username, token):
        """完成待处理的登录请求"""
        try:
            # 确保客户端仍然有效
            if client and client in self.clients.values():
                client.authenticated = True
                client.username = username
                client.token = token
                self.logger.info(f"客户端 {client.id} (IP: {client.ip_address}) 登录成功: {username}")
                
                # 发送登录成功消息
                client._send_client_response("login_success", token)
                
                # 注册到用户名索引
                self.register_authenticated_client(username, client)
                
                # 加载用户数据
                client._load_user_data(username)
        except Exception as e:
            self._log_error(f"完成待处理登录时出错: {str(e)}")
    
    @log_function
    def _generate_token(self, username, client_id=None):
        """为用户生成唯一的令牌"""
        try:
            if not username or not isinstance(username, str):
                self.logger.error("生成令牌失败: 无效的用户名")
                return None
            
            # 组合安全令牌基础
            token_base = (
                f"{username}:"
                f"{random.randint(100000, 999999)}:"
                f"{time.time()}:"
                f"{os.urandom(16).hex()}:"
                f"{client_id if client_id is not None else random.randint(0, 1000000)}"
            )
            
            # 生成并存储令牌
            token = hashlib.sha256(token_base.encode()).hexdigest()
            expiry_time = time.time() + self.token_validity
            
            # 使同一用户的旧令牌失效
            self._invalidate_tokens_for_user(username)
            
            # 存储新令牌
            self.active_tokens[token] = (username, expiry_time, client_id)
            self.logger.debug(f"生成新token: 用户={username}, 客户端ID={client_id}")
            
            return token
        except Exception as e:
            self.logger.error(f"生成令牌时发生错误: {str(e)}")
            return None
    
    @log_function
    def _invalidate_tokens_for_user(self, username):
        """使指定用户的所有令牌失效"""
        tokens_to_remove = []
        for token, (token_username, _, _) in self.active_tokens.items():
            if token_username == username:
                tokens_to_remove.append(token)
                
        for token in tokens_to_remove:
            del self.active_tokens[token]
            
        if tokens_to_remove:
            self.logger.info(f"已使用户 {username} 的 {len(tokens_to_remove)} 个旧令牌失效")
    
    @log_function
    def validate_token(self, token, client_id=None):
        """验证令牌的有效性"""
        # 基本验证
        if not token or not isinstance(token, str) or token not in self.active_tokens:
            return None
            
        username, expiry_time, stored_client_id = self.active_tokens[token]
        
        # 过期检查
        if time.time() > expiry_time:
            self.logger.info(f"用户 {username} 的令牌已过期")
            del self.active_tokens[token]
            return None
        
        # 客户端ID匹配检查
        if client_id is not None and stored_client_id is not None and client_id != stored_client_id:
            self.logger.warning(f"令牌验证失败: 客户端ID不匹配")
            return None
            
        return username
    
    @log_function
    def invalidate_token(self, token):
        """使令牌失效"""
        if token in self.active_tokens:
            del self.active_tokens[token]
            return True
        return False
    
    def on_low_frequency_tick(self):
        """100ms低频定时器回调 - 处理统计和清理任务"""
        self._update_performance_stats()
        self._check_inactive_clients()
        self._check_login_timeout_clients()  # 检查未登录客户端超时
        gc.collect()  # 执行垃圾回收
    
    def _update_performance_stats(self):
        """更新和记录性能统计信息"""
        self.tick_count += 1
        current_time = time.time()
    
        # 每10秒记录一次统计信息
        if current_time - self.last_stats_time >= 10.0:
            elapsed = current_time - self.last_stats_time
            self.last_stats_time = current_time
            
            # 计算并记录网络速率
            if elapsed > 0:
                stats = {k: v/elapsed for k, v in {
                    '接收速率': self.network_stats['bytes_received'],
                    '发送速率': self.network_stats['bytes_sent'],
                    '接收消息数': self.network_stats['msgs_received'],
                    '发送消息数': self.network_stats['msgs_sent']
                }.items()}
                
                self.logger.debug(f"服务器状态: {len(self.clients)}个客户端, "
                                f"接收: {stats['接收速率']:.2f}B/s ({stats['接收消息数']:.2f}条/s), "
                                f"发送: {stats['发送速率']:.2f}B/s ({stats['发送消息数']:.2f}条/s)")
            
            # 重置统计数据
            self.network_stats = {k: 0 for k in self.network_stats}

    def _check_inactive_clients(self):
        """检查并移除不活跃的客户端"""
        current_time = time.time()
        inactive_threshold = 300  # 5分钟不活跃则断开
        
        for client_id, entity in list(self.clients.items()):
            if current_time - entity.last_activity_time > inactive_threshold:
                self.logger.warning(f"客户端 {client_id} 长时间不活跃，断开连接")
                self.mark_client_for_removal(client_id)
    
    def _check_login_timeout_clients(self):
        """检查未完成登录的客户端是否超时"""
        current_time = time.time()
        for client_id, entity in list(self.clients.items()):
            # 检查未认证客户端且连接时间超过登录超时时间
            if not entity.authenticated and \
               current_time - entity.last_activity_time > self.login_timeout:
                self.logger.warning(f"客户端 {client_id} 未在规定时间内完成登录，断开连接")
                # 通知客户端将被断开
                if hasattr(entity, 'caller') and entity.caller:
                    entity._send_client_response("connection_closed", "登录超时，连接已断开")
                self.mark_client_for_removal(client_id)
    
    def tick(self):
        """重写tick方法 - 主要流程由定时器处理"""
        TimerManager.scheduler()

    @log_function
    def disconnect_client(self, client_id, reason="服务器主动断开连接"):
        """主动断开指定客户端的连接
        
        Args:
            client_id: 客户端ID
            reason: 断开原因
        
        Returns:
            bool: 断开成功返回True，否则返回False
        """
        if client_id not in self.clients:
            self.logger.warning(f"尝试断开不存在的客户端连接: {client_id}")
            return False
            
        try:
            client = self.clients[client_id]
            # 通知客户端将被断开连接
            if client and client.caller:
                client._send_client_response("connection_closed", reason)
                
            self.logger.info(f"服务器主动断开客户端 {client_id} 连接: {reason}")
            # 标记客户端为待移除
            self.mark_client_for_removal(client_id)
            return True
        except Exception as e:
            self.logger.error(f"断开客户端 {client_id} 连接时出错: {str(e)}")
            return False
            
    @log_function
    def shutdown_all_clients(self):
        """关闭所有客户端连接
        
        在服务器关闭时调用，通知所有客户端服务器即将关闭
        """
        self.logger.info(f"正在关闭所有客户端连接，当前连接数: {len(self.clients)}")
        
        for client_id in list(self.clients.keys()):
            try:
                client = self.clients.get(client_id)
                if client and client.caller:
                    # 通知客户端服务器关闭
                    client._send_client_response("server_shutdown", "服务器正在关闭")
                    
                # 标记客户端待移除
                self.mark_client_for_removal(client_id)
            except Exception as e:
                self.logger.error(f"关闭客户端 {client_id} 连接时出错: {str(e)}")
                
        # 处理待移除的客户端
        self._remove_marked_clients()

@log_function
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
    
    # 初始化
    logger = logger_instance.get_logger('Main')
    server = None
    should_exit = False
    
    try:
        # 注册信号处理
        for sig in (signal.SIGINT, signal.SIGTERM):
            if hasattr(signal, sig.name):
                signal.signal(sig, signal_handler)
        
        # 启动服务器
        server = MyGameServer()
        if server.host.startup(args.port) != 0:
            logger.error(f"服务器启动失败，端口 {args.port} 可能已被占用")
            sys.exit(1)
        
        logger.info(f"服务器已启动，正在监听 {args.bind}:{args.port}...")
        
        # 主循环
        while not should_exit:
            server.tick()
            time.sleep(0.001)  # 减轻CPU负担
                    
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，服务器正在关闭...")
    except Exception as e:
        logger.error(f"服务器运行时发生意外错误: {str(e)}")
        logger.error(traceback.format_exc())
        
    finally:
        # 优雅关闭
        if server:
            logger.info("正在关闭服务器...")
            server.shutdown_all_clients()
            server.host.shutdown()
            
            # 清理资源
            logger.info("正在清理资源...")
            try:
                db_manager.cleanup()
            except Exception as e:
                logger.error(f"清理数据库资源时出错: {str(e)}")
                logger.error(traceback.format_exc())
            
            # 最终清理
            gc.collect()
            
        logger.info("服务器已完全关闭。")