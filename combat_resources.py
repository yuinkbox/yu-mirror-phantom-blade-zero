"""Current GAS attributes, not the legacy backpack UI/storage counters."""
import math
import struct


def attribute(reader, actor, name):
    m=reader.m
    attr=m.u64(actor+0x758)
    if not attr or not reader.r.objects.valid_object(attr) or m.u64(attr+0x20)!=actor:
        return None
    field=reader.ref.field(attr,name)
    if not field or field[1]!=16:return None
    raw=m.read(attr+field[0]+12,4)  # FGameplayAttributeData.CurrentValue
    if len(raw)!=4:return None
    value=struct.unpack('<f',raw)[0]
    return value if math.isfinite(value) and value>=0 else None


def ultimate_resource(reader,player):
    count=attribute(reader,player,'UltimateChargeCount')
    maximum=attribute(reader,player,'UltimateChargeCount_Max')
    progress=attribute(reader,player,'UltimateCharge')
    limit=attribute(reader,player,'UltimateCharge_Max')
    valid=(count is not None and maximum is not None and 0<=count<=maximum<=100
           and abs(count-round(count))<.001 and abs(maximum-round(maximum))<.001)
    return dict(charge=round(count) if valid else None,
                charge_max=round(maximum) if valid else None,
                charge_progress=progress/limit if progress is not None and limit and progress<=limit else None,
                charge_source='player.UltimateChargeCount')
