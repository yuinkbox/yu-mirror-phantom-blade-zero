import struct
from types import SimpleNamespace
from move_runtime import MoveReader


def reader():
    store={10+0x750:20,20+100+0xe8:20}
    header=struct.pack('<Qii',1000,2,2)
    raw={20+100+0xc8:header,1000:struct.pack('<4I',1,0,2,0)}
    memory=SimpleNamespace(u64=lambda a:store.get(a,0),read=lambda a,n:raw.get(a,b''))
    ref=SimpleNamespace(field=lambda o,k:(100,240,0),name=lambda b:{1:'CharacterState.Space.Ground',2:'CharacterState.WeaponUpgrade.Tang.Combo_01'}.get(struct.unpack('<II',b)[0],''))
    source=SimpleNamespace(r=SimpleNamespace(objects=SimpleNamespace(valid_object=lambda p:p==20)),m=memory,ref=ref)
    return MoveReader(source),store,raw


def test_native_upgrade_tags_require_correct_container_owner():
    m,store,raw=reader()
    assert 'CharacterState.WeaponUpgrade.Tang.Combo_01' in m.owned_tags(10)
    store[20+100+0xe8]=21
    assert m.owned_tags(10) is None


def test_partial_tags_do_not_unlock_moves():
    m,store,raw=reader();raw[1000]=bytes(8)
    assert m.owned_tags(10) is None


def test_torn_tag_header_is_rejected():
    m,store,raw=reader();calls=[0]
    # Correct address: ASC 20 + property 100 + explicit-tags offset 200.
    def unstable(a,n):
        if a==320:
            calls[0]+=1
            return raw[a] if calls[0]==1 else bytes(16)
        return raw.get(a,b'')
    m.m.read=unstable
    assert m.owned_tags(10) is None
