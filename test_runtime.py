import struct
import pytest
from runtime import RuntimeReader, ObjectReader


class FakeMemory:
    def __init__(self):self.data={}
    def put(self,address,data):
        for at,value in enumerate(data):self.data[address+at]=value
    def read(self,address,size):
        try:return bytes(self.data[address+i] for i in range(size))
        except KeyError:return b''
    def u64(self,address):return int.from_bytes(self.read(address,8),'little')


def test_sparse_map_excludes_freed_entry():
    m=FakeMemory();header=bytearray(80)
    struct.pack_into('<Qii',header,0,0x20000,3,4)
    struct.pack_into('<I',header,0x10,0b101)
    struct.pack_into('<i',header,0x28,3)
    struct.pack_into('<i',header,0x34,1)
    m.put(0x10000,header)
    data=bytearray(72)
    for i in range(3):struct.pack_into('<i4xQ',data,i*24,10+i,100+i)
    m.put(0x20000,data)
    reader=RuntimeReader.__new__(RuntimeReader);reader.memory=m
    assert reader.sparse_map(0x10000,24)==[(10,struct.pack('<Q',100)),(12,struct.pack('<Q',102))]


def test_weak_pointer_uses_serial_and_object_slot_at_eight():
    m=FakeMemory();m.put(0x10000,struct.pack('<Q',0x20000))
    item=bytearray(24);struct.pack_into('<Q',item,8,0x30000);struct.pack_into('<i',item,16,88)
    m.put(0x20000+5*24,item)
    objects=ObjectReader.__new__(ObjectReader);objects.mem=m;objects.table=0x10000;objects.count=100
    assert objects.resolve(struct.pack('<ii',5,88))==0x30000
    assert objects.resolve(struct.pack('<ii',5,87))==0
    struct.pack_into('<I',item,4,0x10000000);m.put(0x20000+5*24,item)
    assert objects.resolve(struct.pack('<ii',5,88))==0


class ActionObjects:
    def __init__(self,source=0x30000,parent=0x40000,valid=True):
        self.source=source;self.parent=parent;self.valid=valid
    def resolve(self,_):return self.source
    def valid_object(self,_):return self.valid
    def derives(self,cls,parent):return cls==0x60000 and parent==self.parent


@pytest.mark.parametrize('kind,offset,type_id,expected',[
    ('duration_bullet',0x100,2,'blue'),('duration_bullet',0x100,3,'red'),
    ('burst_bullet',0x108,2,'blue'),('burst_bullet',0x108,3,'red')])
def test_native_action_layouts_read_color_only_after_class_check(kind,offset,type_id,expected):
    m=FakeMemory();m.put(0x30010,struct.pack('<Q',0x60000))
    m.put(0x30000+offset,bytes([type_id]))
    # The other layout deliberately contains an unrelated value.
    m.put(0x30000+(0x108 if offset==0x100 else 0x100),b'\x01')
    r=RuntimeReader.__new__(RuntimeReader);r.memory=m;r.objects=ActionObjects()
    r.classes={'duration_bullet':0x40000 if kind=='duration_bullet' else 0x50000,
               'burst_bullet':0x40000 if kind=='burst_bullet' else 0x50000}
    assert r.attack_type(b'12345678')==(expected,0x30000)


@pytest.mark.parametrize('objects',[ActionObjects(source=0),ActionObjects(valid=False),ActionObjects(parent=0x70000)])
def test_stale_or_unrecognized_action_never_guesses_a_color(objects):
    m=FakeMemory();m.put(0x30010,struct.pack('<Q',0x60000));m.put(0x30100,b'\x03')
    r=RuntimeReader.__new__(RuntimeReader);r.memory=m;r.objects=objects
    r.classes={'duration_bullet':0x40000,'burst_bullet':0x50000}
    from types import SimpleNamespace
    r.projectiles=SimpleNamespace(ref=SimpleNamespace(field=lambda *a:None))
    assert r.attack_type(b'12345678')[0]=='unknown'
