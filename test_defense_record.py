from types import SimpleNamespace
import json
from defense_record import DefenseRecorder


def state(frames=None, **kw):
    data = dict(valid=True, player=1, world=2, game_time=100., hp=850., hp_max=850.,
                medicine=2, threats=frames or [], diagnostics={})
    data.update(kw)
    return SimpleNamespace(**data)


def threat(kind='red', **kw):
    data = dict(id=10, source=30, hit_time=100.3, attack_type=kind, dodge=True)
    data.update(kw)
    return data


def test_ordinary_play_uses_bounded_memory_without_writing(tmp_path):
    rows=[];r=DefenseRecorder(tmp_path/'capture.jsonl',write=rows.append)
    for i in range(200):r.observe(i*.02,state(),False)
    assert not rows and len(r.history)<=64
    assert not (tmp_path/'capture.jsonl').exists()


def test_color_warning_records_context_once_and_expires(tmp_path):
    rows=[];r=DefenseRecorder(tmp_path/'capture.jsonl',write=rows.append)
    for i in range(25):r.observe(i*.02,state(),False)
    r.observe(.5,state([threat()]),False)
    r.observe(.52,state([threat()]),False)
    assert sum(x['event']=='warning' for x in rows)==1
    assert any(x['event']=='sample' and x['monotonic']<.5 for x in rows)
    r.observe(1.8,state(),False);count=len(rows)
    r.observe(2,state(),False)
    assert len(rows)==count


def test_paused_recording_never_claims_automated_input(tmp_path):
    rows=[];r=DefenseRecorder(tmp_path/'capture.jsonl',write=rows.append)
    r.observe(0,state([threat('blue')]),False)
    assert rows and all(x['event']!='input' for x in rows)
    assert all(x['armed'] is False for x in rows)


def test_scene_change_discards_history_and_recycled_frame_ids(tmp_path):
    rows=[];r=DefenseRecorder(tmp_path/'capture.jsonl',write=rows.append)
    r.observe(0,state([threat()]),False)
    rows.clear()
    r.observe(.1,state(world=3),False)
    r.observe(.2,state([threat()],world=3),False)
    assert sum(x['event']=='warning' for x in rows)==1
    assert all(x['state']['world']==3 for x in rows)


def test_input_records_latency_without_guessing_perfect_success(tmp_path):
    rows=[];r=DefenseRecorder(tmp_path/'capture.jsonl',write=rows.append)
    r.input(1,state([threat()]),('tap','Shift'),.055,.002,.11)
    row=rows[-1]
    assert row['event']=='input' and row['duration_ms']==55 and row['sample_age_ms']==2
    assert row['perfect_outcome']=='unconfirmed'


def test_capture_disk_failure_disables_only_recording(tmp_path):
    r=DefenseRecorder(tmp_path)  # A directory cannot be opened as the log file.
    r.observe(0,state([threat()]),False)
    assert r.disabled
    r.observe(.1,state([threat()]),True)
    r.close()


def test_capture_files_rotate_with_a_fixed_size_limit(tmp_path):
    path=tmp_path/'capture.jsonl';r=DefenseRecorder(path)
    r.emit({'event':'begin'})
    r.handler.maxBytes=256
    for i in range(30):r.emit({'event':'sample','index':i,'value':'x'*50})
    r.close()
    files=list(tmp_path.iterdir())
    assert len(files)<=3
    for p in files:
        assert p.stat().st_size<256
        assert all(json.loads(line)['event'] in ('begin','sample') for line in p.read_text('utf-8').splitlines())


def test_hit_or_paused_frame_does_not_start_a_new_warning(tmp_path):
    rows=[];r=DefenseRecorder(tmp_path/'capture.jsonl',write=rows.append)
    r.observe(0,state(diagnostics={'defense_frames':[threat(hit=True),threat(paused=True)]}),False)
    assert not rows
