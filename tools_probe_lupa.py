import lupa

lua = lupa.LuaRuntime(unpack_returned_tuples=True)


class Node:
    def __init__(self, name):
        self.name = name

    def IsValid(self):
        return True

    def GetAddress(self):
        return 7


lua.globals()['NODE'] = Node('x')
print(lua.eval('function(o) return type(o) end')(lua.globals()['NODE']))
for index in range(3):
    print('colon IsValid:', lua.eval('function(o) local ok, v = pcall(function() return o:IsValid() end) return ok, tostring(v) end')(lua.globals()['NODE']))
    print('dot   IsValid:', lua.eval('function(o) local ok, v = pcall(function() return o.IsValid() end) return ok, tostring(v) end')(lua.globals()['NODE']))
