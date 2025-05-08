##服务器目录结构 
```
common:传统的消息结构定义以及示例
common_server：服务器基础组件，包含定时器等模块的实现
network：网络相关代码
test：testcase
dispatcher.py  
simpleServer.py
```
##模块说明  
  1.`SimpleServer.py`提供了一个简单的游戏服务器类，里面包含`SimpleHost`以及`Dispatcher`2个子对象。SimpleServer还提供了一个简单的对象注册以及tick机制。  
  2.`SimpleHost`管理了所有的客户端连接。在每帧逻辑`process`里管理了连接的建立、销毁，以及数据的读取、发送。  
  3.`SimpleHost`将每个客户端连接抽象为`NetStream`对象。`NetStream`封装了socket的相关操作，包括连接、读取、发送数据。因为在示例代码中使用的是Tcp协议。因此`NetStream`还处理了收发缓冲区相关的逻辑。
  
 ##消息机制  
  #示例代码提供了2套消息机制供参考使用：  
  >a.基于Events以及Service  
  >b.基于RPC

  
  
  
#基于Events以及Service 
```
#以登陆为例，我们需要客户端向服务器发送登陆相关的信息，因此首先需要定义消息体本身
class MsgCSLogin(SimpleHeader):
	def __init__(self, name = '', icon = -1):
		super(MsgCSLogin, self).__init__(conf.MSG_CS_LOGIN)
		self.appendParam('name', name, 's')
		self.appendParam('icon', icon, 'i')

#上述类定义了这样一个消息结构， MsgCSLogin含有2个成员变量，变量名分别为`name`以及`icon`,他们的类型分别为 `string(s)`以及`int(i)`

#在定义好结构体后，我们需要实现Service来实现对应消息的处理代码，相关基类已经放在`dispatcher.py`里。并将Service注册至`Dispatcher`中。
#最后一步则是在SimpleServer合适的实际调用dispatcher的dispatch函数即可。
#示例可以参考`test/unitTest.py`中的`TestService`相关代码
```

#基于RPC
```
#rpc可以以调用函数的方式来进行消息传递
client_entity.caller.hello_world_from_client(stat, 'Hello, world !!')

#只需要在对应类上实现响应的处理函数即可,其中修饰器EXPOSED代表暴露出来，可以供RPC调用
# SERVER CODE
@EXPOSED
def hello_world_from_client(self, stat, msg):
	print 'server recv msg from client:', stat, msg
	self.stat = stat + 1
	self.caller.recv_msg_from_server(self.stat, msg)

#示例可以参考`test/unitTest.py`中的`GameEntity`相关代码
```


##说明
消息传递不论是基于Events或者RPC,本质都是将信息转换为字节流，通过socket传输。所以客户端只需要按照同样的方式来处理字节流，即可正确获取传递来的消息，并进行相应处理。相关代码可以参考:
>1.header.py `marshal`/`unmarshal` (基于Events)
>2.netStream.py `RpcProxy`(Rpc)