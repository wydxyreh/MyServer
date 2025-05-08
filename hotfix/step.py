import sample
import helper

a = sample.MyClass(1)
a.foo()


#TODO: modify MyClass.foo


helper.hotfix(a)
a.foo()

callback = a.foo

#TODO: modify MyClass.foo

helper.hotfix(a)

a.foo()
callback()
