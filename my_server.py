import sys
import os
import time

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server.simpleServer import SimpleServer

class MyGameServer(SimpleServer):
    _id_counter = 0
    
    def generateEntityID(self):
        self._id_counter += 1
        return self._id_counter

if __name__ == "__main__":
    # 创建并初始化服务器
    server = MyGameServer()
    
    # 启动网络服务（使用端口2000）
    server.host.startup(2000)
    
    print("服务器已启动，正在监听端口2000...")
    
    try:
        # 主循环
        while True:
            server.tick()
            time.sleep(0.01)  # 控制循环速度
    except KeyboardInterrupt:
        print("服务器正在关闭...")
        server.host.shutdown()
        print("服务器已关闭。")