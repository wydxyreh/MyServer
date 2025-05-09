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
        self.active_tokens = {}  # {token: (username, expiry_time)}
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
    
    def _generate_token(self, username):
        """为用户生成唯一的令牌"""
        # 生成随机数，结合用户名和当前时间
        random_part = str(random.randint(10000, 99999))
        timestamp = str(time.time())
        token_base = f"{username}:{random_part}:{timestamp}"
        
        # 生成令牌
        token = hashlib.sha256(token_base.encode()).hexdigest()
        expiry_time = time.time() + self.token_validity
        
        # 存储令牌
        self.active_tokens[token] = (username, expiry_time)
        
        return token
    
    def authenticate(self, username, password):
        """验证用户身份并生成令牌"""
        self.logger.info(f"尝试验证用户: {username}")
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                hashed_pwd = self._hash_password(password)
                
                cursor.execute(
                    "SELECT * FROM users WHERE username = ? AND password = ?",
                    (username, hashed_pwd)
                )
                
                user = cursor.fetchone()
                
                if user:
                    # 验证成功，生成令牌
                    token = self._generate_token(username)
                    self.logger.info(f"用户 {username} 验证成功，生成令牌")
                    return token
                else:
                    self.logger.warning(f"用户 {username} 验证失败")
                    return None
                    
        except Exception as e:
            self.logger.error(f"验证用户时出错: {str(e)}")
            return None
    
    def validate_token(self, token):
        """验证令牌的有效性"""
        if token not in self.active_tokens:
            return None
            
        username, expiry_time = self.active_tokens[token]
        
        # 检查令牌是否过期
        if time.time() > expiry_time:
            # 令牌已过期，从活动令牌中移除
            del self.active_tokens[token]
            return None
            
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
        # 释放所有活动的令牌
        self.active_tokens.clear()
        # 如果有连接池或其他需要释放的资源，可以在这里处理

# 单例模式
db_manager = DatabaseManager()