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

修改客户端与服务端交互逻辑，多个客户端通过多个函数调用来与服务端进行通信：
1.客户端有client_login函数，传入形参分别为账号和密码，每次客户端连接到服务端时，服务端强制且优先调用该函数，然后服务端会对账号密码进行验证（与存储在数据库中的账号密码进行比对），验证通过后，将账密加上随机数进行哈希（函数化），返回一个token（唯一）给客户端作为临时身份ID，后续每次客户端与服务端在该次连接下的会话只需要验证该token即可，并且每次连接断开后，token都失效。（客户端的client_login调用传入的账密以全局变量的形式提供）
2.客户端有userdata_load和userdata_save两个函数，将以JSON格式存储的数据从客户端传递给服务端，服务端接收到后对JSON数据进行解析，获取解析后的userdata（函数化），并将对应的数据存储到数据库中。注意，每个客户端的相关JSON数据在数据库中都是与其账号密码信息绑定的，即可以根据账密唯一地从数据库中读取到相关userdata数据。
如：
{
name:wydx
bullet:20
exp:23
}
对于数据库，使用python3标准库自带的sqlite进行连接，在服务器数据中内建好三个账号：netease1,netease2,netease3,密码都是123；



优化
C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py
1.对于客户端，其逻辑处理

包括但不限于：
1.将其中的print改为logging；
2.优化其登录逻辑，添加各种错误处理，并提高鲁棒性；
3.优化服务端客户端性能
4.将其进行模块化设计和整合，降低各个模块和函数的耦合度

优化客户端和服务端的代码，将客户端目前的选项交互方式进行大幅度修改，每个选项不再是通过信息输入选择，而是写死在代码中，由一个函数def test来逐个测试客户端与服务端通信和交互方式，每个函数的输入参数均在def test函数开头由变量定义；
def test主要依次测试：save失败（未登录），load失败（未登录），login账密登录失败（账密错误）、load失败（未登录）、login账密登录成功（账密正确），load失败（该账密下没有对应的json数据），save成功（将样例json存储在对应账密下），reconnect，login（token登录无效），load失败（未登录），login账密登录成功（账密正确），load成功（成功从该账密下读取数据），logout（退出登录，主动使token失效，但仍然保持同一连接）

修改C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py代码，减少冗余日志输出

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
移除该文件中和测试相关的部分代码，只保留正常的功能代码，只需要给出修改的部分即可

优化下列文件代码逻辑：
C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
在不影响逻辑的情况下，进行合理的精简