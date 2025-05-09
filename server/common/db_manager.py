# -*- coding: utf-8 -*-
import os
import sqlite3
import json
import hashlib
import random
import time
from contextlib import contextmanager

from server.common.logger import logger_instance

class DatabaseManager:
    """数据库管理器，负责用户认证和数据存储"""
    
    def __init__(self, db_path='server_data.db'):
        self.db_path = db_path
        self.logger = logger_instance.get_logger('DatabaseManager')
        # 内存中存储token的字典，不会持久化到数据库
        self.active_tokens = {}  # {token: (username, expiry_time, client_id)}
        self.token_validity = 7200  # token有效期(秒)
        self._initialize_db()
        
    def _initialize_db(self):
        """初始化数据库，创建用户表和用户数据表"""
        self.logger.info(f"初始化数据库: {self.db_path}")
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 创建用户表
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                
                # 创建用户数据表
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (username) REFERENCES users(username),
                    UNIQUE(username)
                )
                ''')
                
                # 检查是否需要添加预设账号
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                
                if user_count == 0:
                    # 添加预设账号
                    default_accounts = [
                        ('netease1', '123'),
                        ('netease2', '123'),
                        ('netease3', '123')
                    ]
                    
                    for username, password in default_accounts:
                        hashed_pwd = self._hash_password(password)
                        cursor.execute(
                            "INSERT INTO users (username, password) VALUES (?, ?)",
                            (username, hashed_pwd)
                        )
                        
                        # 为每个用户创建默认数据
                        default_data = {
                            'name': username,
                            'bullet': 20,
                            'exp': 0
                        }
                        cursor.execute(
                            "INSERT INTO user_data (username, data_json) VALUES (?, ?)",
                            (username, json.dumps(default_data))
                        )
                    
                    self.logger.info(f"已添加 {len(default_accounts)} 个默认账号")
                
                conn.commit()
                
        except Exception as e:
            self.logger.error(f"初始化数据库失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
    
    @contextmanager
    def _get_connection(self):
        """获取数据库连接上下文管理器"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # 使查询结果可以通过列名访问
            yield conn
        except Exception as e:
            self.logger.error(f"数据库连接错误: {str(e)}")
            raise
        finally:
            if conn:
                conn.close()
    
    def _hash_password(self, password):
        """简单的密码哈希"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _generate_token(self, username, client_id=None):
        """为用户生成唯一的令牌
        
        Args:
            username (str): 用户名
            client_id (int, optional): 客户端ID，用于区分不同的连接
            
        Returns:
            str: 生成的token字符串，生成失败则返回None
        """
        try:
            # 参数验证
            if not username or not isinstance(username, str):
                self.logger.error("生成令牌失败: 无效的用户名")
                return None
            
            # 增强熵源，提高安全性
            random_part = str(random.randint(100000, 999999))
            timestamp = str(time.time())
            entropy = os.urandom(16).hex()  # 增加熵值大小
            client_part = str(client_id if client_id is not None else random.randint(0, 1000000))
            token_base = f"{username}:{random_part}:{timestamp}:{entropy}:{client_part}"
            
            # 生成令牌
            token = hashlib.sha256(token_base.encode()).hexdigest()
            expiry_time = time.time() + self.token_validity
            
            # 使旧令牌失效 (如果用户已登录在其他地方)
            self._invalidate_tokens_for_user(username)
            
            # 存储令牌在内存中（不写入数据库）
            self.active_tokens[token] = (username, expiry_time, client_id)
            self.logger.debug(f"生成新token: 用户={username}, 客户端ID={client_id}, 过期时间={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry_time))}")
            
            return token
        except Exception as e:
            self.logger.error(f"生成令牌时发生错误: {str(e)}")
            return None
            
    def _invalidate_tokens_for_user(self, username):
        """使指定用户的所有令牌失效"""
        tokens_to_remove = []
        for token, (token_username, _) in self.active_tokens.items():
            if token_username == username:
                tokens_to_remove.append(token)
                
        for token in tokens_to_remove:
            del self.active_tokens[token]
            
        if tokens_to_remove:
            self.logger.info(f"已使用户 {username} 的 {len(tokens_to_remove)} 个旧令牌失效")
    
    def authenticate(self, username, password, client_id=None):
        """验证用户身份并生成令牌
        
        Args:
            username (str): 用户名
            password (str): 密码
            client_id (int, optional): 客户端ID，用于关联生成的token
            
        Returns:
            str: 成功则返回生成的token，失败返回None
        """
        self.logger.info(f"验证用户身份: {username}")
        
        # 参数验证
        if not username or not isinstance(username, str) or not password or not isinstance(password, str):
            self.logger.warning("认证失败: 无效的用户名或密码参数")
            return None
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                hashed_pwd = self._hash_password(password)
                
                # 查询用户
                cursor.execute(
                    "SELECT * FROM users WHERE username = ? AND password = ?",
                    (username, hashed_pwd)
                )
                
                user = cursor.fetchone()
                
                if user:
                    # 认证成功才生成令牌
                    self.logger.info(f"用户 {username} 认证成功")
                    token = self._generate_token(username, client_id)
                    
                    if not token:  # 确保token生成成功
                        self.logger.error(f"用户 {username} 认证成功但token生成失败")
                        return None
                        
                    self.logger.info(f"用户 {username} 令牌生成成功" + (f", 关联客户端ID: {client_id}" if client_id else ""))
                    return token
                else:
                    self.logger.warning(f"用户 {username} 认证失败: 用户名或密码错误")
                    return None
                    
        except Exception as e:
            self.logger.error(f"验证用户时出错: {str(e)}")
            return None
    
    def validate_token(self, token, client_id=None):
        """验证令牌的有效性
        
        Args:
            token (str): 要验证的token
            client_id (int, optional): 客户端ID，如果提供则进行额外验证
            
        Returns:
            str: 如果token有效，返回对应的用户名；否则返回None
        """
        if not token or not isinstance(token, str):
            self.logger.warning("验证令牌失败: 无效的令牌格式")
            return None
            
        if token not in self.active_tokens:
            self.logger.debug("验证令牌失败: 令牌不存在")
            return None
            
        username, expiry_time, stored_client_id = self.active_tokens[token]
        
        # 检查令牌是否过期
        if time.time() > expiry_time:
            # 令牌已过期，从活动令牌中移除
            self.logger.info(f"用户 {username} 的令牌已过期")
            del self.active_tokens[token]
            return None
        
        # 如果提供了client_id，确保匹配
        if client_id is not None and stored_client_id is not None and client_id != stored_client_id:
            self.logger.warning(f"令牌验证失败: 客户端ID不匹配 (预期: {stored_client_id}, 实际: {client_id})")
            return None
            
        # 延长令牌有效期（可选，取决于你的安全策略）
        # self.active_tokens[token] = (username, time.time() + self.token_validity, stored_client_id)
        
        return username
    
    def invalidate_token(self, token):
        """使令牌失效"""
        if token in self.active_tokens:
            del self.active_tokens[token]
            return True
        return False
    
    def save_user_data(self, username, data_json):
        """保存用户数据"""
        self.logger.info(f"保存用户数据: {username}")
        
        try:
            # 验证JSON数据
            try:
                if isinstance(data_json, str):
                    json_data = json.loads(data_json)
                else:
                    json_data = data_json
                    data_json = json.dumps(data_json)
            except json.JSONDecodeError:
                self.logger.error(f"无效的JSON数据: {data_json}")
                return False
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查用户是否存在
                cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                if not cursor.fetchone():
                    self.logger.error(f"用户 {username} 不存在")
                    return False
                
                # 更新或插入用户数据
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO user_data (username, data_json, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (username, data_json)
                )
                
                conn.commit()
                self.logger.info(f"成功保存用户 {username} 的数据")
                return True
                
        except Exception as e:
            self.logger.error(f"保存用户数据时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def load_user_data(self, username):
        """加载用户数据"""
        self.logger.info(f"加载用户数据: {username}")
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute(
                    "SELECT data_json FROM user_data WHERE username = ?",
                    (username,)
                )
                
                result = cursor.fetchone()
                
                if result:
                    return result['data_json']
                else:
                    self.logger.warning(f"未找到用户 {username} 的数据")
                    return None
                    
        except Exception as e:
            self.logger.error(f"加载用户数据时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def cleanup(self):
        """清理资源，在服务器关闭时调用"""
        self.logger.info("清理数据库管理器资源")
        
        # 记录当前活动token数量
        token_count = len(self.active_tokens)
        if token_count > 0:
            self.logger.info(f"清除 {token_count} 个活动token")
            
        # 释放所有活动的令牌（仅内存中的操作）
        self.active_tokens.clear()
        
        # 如果有连接池或其他需要释放的资源，可以在这里处理

# 单例模式
db_manager = DatabaseManager()