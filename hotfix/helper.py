#-*-coding:utf-8 -*-
import sys

def hotfix(o):
	cls_name = o.__class__.__name__
	mod_name = o.__module__
	
	del sys.modules[mod_name]
	mod = __import__(mod_name)

	new = getattr(mod, cls_name, None)
	if new != None:
		print '**reload:', mod, new
		o.__class__ = new
		








