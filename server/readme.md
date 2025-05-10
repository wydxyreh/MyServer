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
保留注释

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\server\common\db_manager.py
对于服务端的_verify_token、_verify_auth、_verify_auth_with_token等函数，以及db_manager的invalidate_token、validate_token、_invalidate_tokens_for_user等函数，本质上是将服务端的token放在了数据库部分进行处理，这和我的预期不符，我期望token仅仅是每次服务端和客户端通信连接建立时，临时使用的一次性身份凭证，不需要和数据库进行交互处理，在服务端中设置一个数据结构对其进行管理即可；
所以，移除数据库中对token的处理和验证部分代码，同时将相关token处理放在服务端完成

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py
C:\Users\wydx\Documents\Unreal Projects\Server\server\common\db_manager.py
修改客户端和服务端代码，要求：
1.服务端向客户端提供数据保存def userdata_save(self, data_json)和加载def userdata_load(self)的操作，当客户端调用服务端userdata_save时，客户端会向服务端提供一段json数据，然后服务端接受到该json数据后，会结合客户端的账密作为数据库中的唯一索引键，将其存储在数据库中，如果数据库中该账密已有旧数据，则按照时间顺序对新旧数据进行排序，之后客户端再调用userdata_load时，服务端会读取数据库中该账密下最新的json数据并返回给客户端。
2.考虑到客户端登录首次使用账密，后续使用临时token（每个客户端账密同一时间只有一个临时token有效），服务端对客户端的身份识别和数据库中的数据对应，要以账密作为唯一索引键，而数据库中不要有token的介入，tokne只存储于服务端当中；这样无论客户端是以账密登录，还是以token登录，服务端都可以将其转换成账密，在数据库中对该账密下的json数据进行为索引。

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py
修改客户端和服务端代码，要求：
1.双端支持登陆、登出，功能，由客户端主动发起，服务端自动响应；
2.客户端连接到服务端并成功账密登录时，由服务端生成一次性的token，在该次连接中，后续客户端进行其他的操作时，都可以携带该token进行身份验证，无需再使用账密，并且客户端如果有token，则客户端默认使用token登录，如果token无效，则客户端再使用账密登录。
3.在客户端连接断开后，服务端会使该一次性token失效，同时客户端也会删除该token；如果后续服务端再次收到该token的登录请求，服务端会返回token无效，登录失败。
4.当有新客户端使用已登录的账密尝试再次登录时，服务端会先告知新的客户端“正在处理旧连接”，然后服务端会通知之前已登录的旧客户端被登出，旧客户端接收到被登出通知后，会强制调用一次userdata_save操作，将当前的用户数据在服务端进行保存，保存结束后，服务端正常向客户端发送保存结果的通知，然后服务端便会和旧客户端断开连接（断开连接时，服务端会自动无效旧token）；服务端和旧客户端的连接断开完成后，服务端会生成新的token，给最新使用账密登录的新客户端使用。

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py
修改客户端和服务端代码，要求：
1.服务端和客户端都具备相应的、主动断开连接的函数；

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py
修改客户端和服务端代码，要求：
1.移除客户端和服务端reconnect相关功能，连接断开之后，不再有任何重连尝试；
2.连接建立后，服务端会标记该客户端，如果客户端在一定时间内没有完成登录操作，则服务端会自动断开与客户端之间的连接；
3.客户端在连接建立后，如果发送RPC请求在一定时间内没有收到来自服务端的回复，则客户端也会自动断开与服务端之间的连接；

C:\Users\wydx\Documents\Unreal Projects\Server\sample_server.py
C:\Users\wydx\Documents\Unreal Projects\Server\sample_client.py
1.服务端的process_messages函数中，对于用户数据在登出前的保存操作，只是从数据库读取了数据然后又存回了数据库，这是不合逻辑的，应该是服务端通知旧客户端，让旧客户端运行save_user_data函数来将最新的客户端数据传输到服务端，然后服务端再将其保存到数据库。
2.所有服务端断开连接时，都有要求对应客户端执行数据保存这一步。
