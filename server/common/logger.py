import os
import logging
import datetime
import socket
import atexit
from logging.handlers import RotatingFileHandler

class LoggerSingleton:
    """
    日志记录器单例类
    """
    _instance = None
    _initialized = False
    _loggers = {}
    _log_files = {}
    _cleanup_registered = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LoggerSingleton, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not LoggerSingleton._initialized:
            # 创建日志文件夹
            self._log_folder = 'Logs'
            if not os.path.exists(self._log_folder):
                os.makedirs(self._log_folder)
                
            # 创建全局格式化器
            self._formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            
            # 注册退出清理函数
            if not LoggerSingleton._cleanup_registered:
                LoggerSingleton._cleanup_registered = True
                atexit.register(self.cleanup_resources)
                
            LoggerSingleton._initialized = True
            
    def get_logger(self, name, log_level=logging.DEBUG, console_level=logging.INFO, file_level=logging.DEBUG):
        """
        获取指定名称的日志记录器，如果不存在则创建
        
        :param name: 日志记录器名称
        :param log_level: 日志记录器级别
        :param console_level: 控制台输出级别
        :param file_level: 文件记录级别
        :return: 日志记录器实例
        """
        # 如果已创建过该名称的logger，直接返回
        if name in LoggerSingleton._loggers:
            return LoggerSingleton._loggers[name]
        
        # 创建新的日志记录器
        logger = logging.getLogger(name)
        
        # 避免重复添加处理器
        if not logger.handlers:
            logger.setLevel(log_level)
            
            # 创建控制台处理器
            console_handler = logging.StreamHandler()
            console_handler.setLevel(console_level)
            console_handler.setFormatter(self._formatter)
            
            # 创建带时间戳的日志文件名
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(self._log_folder, f"{name}_{timestamp}.log")
            
            # 创建文件处理器
            file_handler = RotatingFileHandler(
                log_file, maxBytes=10*1024*1024, backupCount=5
            )
            file_handler.setLevel(file_level)
            file_handler.setFormatter(self._formatter)
            
            # 添加处理器到日志记录器
            logger.addHandler(console_handler)
            logger.addHandler(file_handler)
            
            # 存储日志文件路径
            LoggerSingleton._log_files[name] = log_file
            
        # 缓存日志记录器实例
        LoggerSingleton._loggers[name] = logger
        return logger
    
    def cleanup_resources(self):
        """清理全局资源，确保程序退出时所有资源被正确释放"""
        cleanup_logger = self.get_logger('Cleanup')
        cleanup_logger.info("正在清理全局资源...")
        
        # 在Python 3中不再需要手动关闭socket，垃圾回收会处理
        try:
            # 强制垃圾回收帮助释放资源
            import gc
            gc.collect()
            cleanup_logger.info("已执行垃圾回收以释放socket资源")
        except Exception as e:
            cleanup_logger.error(f"执行垃圾回收时出错: {str(e)}")
        
        # 关闭所有日志处理器
        for name, logger in LoggerSingleton._loggers.items():
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
            cleanup_logger.debug(f"已关闭日志处理器: {name}")
        cleanup_logger.info("所有日志处理器已关闭")


# 创建全局单例实例
logger_instance = LoggerSingleton()

# 为了向后兼容，# 为了向后兼容，保留静态方法接口
class Logger:
    @staticmethod
    def setup_logger(name, log_folder='Logs'):
        """
        设置日志记录器（向后兼容方法）
        :param name: 日志记录器名称
        :param log_folder: 日志保存文件夹
        :return: 日志记录器实例和日志文件路径
        """
        global logger_instance
        logger = logger_instance.get_logger(name)
        log_file = LoggerSingleton._log_files.get(name, "")
        return logger, log_file
    
    @staticmethod
    def cleanup_resources():
        """清理全局资源（向后兼容方法）"""
        global logger_instance
        logger_instance.cleanup_resources()