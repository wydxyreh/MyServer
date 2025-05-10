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
                
                # 创建用户数据表 (修改为支持多个数据版本)
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (username) REFERENCES users(username)
                )
                ''')
                
                # 为username和updated_at创建索引以加速查询
                cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_user_data_username ON user_data(username)
                ''')
                
                cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_user_data_timestamp ON user_data(updated_at)
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
    
    def verify_user_credentials(self, username, hashed_password):
        """验证用户凭据
        
        Args:
            username (str): 用户名
            hashed_password (str): 哈希后的密码
            
        Returns:
            bool: 验证成功返回True，否则返回False
        """
        self.logger.info(f"验证用户凭据: {username}")
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 查询用户
                cursor.execute(
                    "SELECT * FROM users WHERE username = ? AND password = ?",
                    (username, hashed_password)
                )
                
                user = cursor.fetchone()
                
                if user:
                    self.logger.info(f"用户 {username} 凭据验证成功")
                    return True
                else:
                    self.logger.warning(f"用户 {username} 凭据验证失败")
                    return False
                    
        except Exception as e:
            self.logger.error(f"验证用户凭据时出错: {str(e)}")
            return False
    
    # 令牌管理已移至 MyGameServer 类，此处移除重复的令牌管理方法
    
    def save_user_data(self, username, data_json):
        """保存用户数据 - 按时间顺序存储多个版本
        
        Args:
            username: 用户名，作为数据库中的索引键
            data_json: JSON格式的数据，直接存储不做额外转换
        """
        self.logger.info(f"保存用户数据: {username}")
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查用户是否存在
                cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                if not cursor.fetchone():
                    self.logger.error(f"用户 {username} 不存在")
                    return False
                
                # 插入新数据记录（不替换旧记录，而是添加新版本）
                cursor.execute(
                    """
                    INSERT INTO user_data (username, data_json, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (username, data_json)
                )
                
                conn.commit()
                self.logger.info(f"成功保存用户 {username} 的数据（新版本）")
                return True
                
        except Exception as e:
            self.logger.error(f"保存用户数据时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def load_user_data(self, username):
        """加载用户数据 - 获取最新版本"""
        self.logger.info(f"加载用户 {username} 的最新数据")
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 获取该用户最新的数据记录
                cursor.execute(
                    """
                    SELECT data_json FROM user_data 
                    WHERE username = ? 
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (username,)
                )
                
                result = cursor.fetchone()
                
                if result:
                    self.logger.info(f"成功获取到用户 {username} 的最新数据")
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

# 单例模式
db_manager = DatabaseManager()