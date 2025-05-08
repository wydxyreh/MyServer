#-*-coding:utf-8 -*-

class MyClass(object):
	def __init__(self, x):
		super(MyClass, self).__init__()
		self.var = x 


	def foo(self):
		print '__bar__:', self.var

