#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import hashlib,tempfile,threading,socket,sys,types
from pathlib import Path
import numpy as np

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ModuleNotFoundError:
    FLASK_AVAILABLE = False
    module = types.ModuleType("flask")
    module.Flask = object
    module.Response = object
    module.abort = lambda *args, **kwargs: None
    module.render_template_string = lambda *args, **kwargs: ""
    module.send_from_directory = lambda *args, **kwargs: None
    sys.modules["flask"] = module

from app.core import camera_entropy_server as app
from app.sources.frame_transport import send_message,recv_message,frame_header,verify_frame_message
from app.sources.frame_sources import redact_url

def main()->int:
    assert hashlib.sha3_512(b'').hexdigest()==app.Sha3ConditionerWriter.EMPTY_SHA3_512
    obj=object.__new__(app.Service);obj.spatial_pattern_cache={}
    obj.args=types.SimpleNamespace(
        spatial_mask_pattern="legacy",spatial_sampling="full",
        spatial_step_x=1,spatial_step_y=1,spatial_phase_x=0,spatial_phase_y=0,
        spatial_block_width=4,spatial_block_height=4,
        temporal_spatial_offset_x=0,temporal_spatial_offset_y=0,
    )
    patterns=app.Service.build_spatial_patterns(obj,(8,10))
    even=patterns['checkerboard-even'];odd=patterns['checkerboard-odd']
    assert not np.any(even&odd);assert np.all(even|odd);assert even.sum()==odd.sum()==40
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'conditioned.bin';w=app.Sha3ConditionerWriter(path,True,2048,128)
        a=bytes(range(256));b=bytes(reversed(range(256)))
        bits=np.unpackbits(np.frombuffer(a+b,dtype=np.uint8),bitorder='big')
        status=w.write_bits(bits);w.close()
        assert path.read_bytes()==hashlib.sha3_512(a).digest()+hashlib.sha3_512(b).digest()
        assert status['complete'] and not status['latched']

    # Complementary dual-weave: both spatial orders consume every active sample
    # exactly once across C0/C1 and the balanced conditioner takes equal halves.
    with tempfile.TemporaryDirectory() as td:
        class Args:
            validation_output_bytes=4096
            write_output=True
            max_output_bytes=1024
            conditioner='sha3-512'
            write_conditioned_output=True
            conditioner_input_bits=2048
            conditioned_output_bytes=1024
            assessed_min_entropy=0.5
            health_alpha=2**-20
            apt_window=1024
        exp=app.DualWeaveExperiment(True,('serpentine','row-major'),('same-group','stagger-1','stagger-2'),Path(td),Args())
        rng=np.random.default_rng(123)
        mask=np.ones((32,32),dtype=bool)
        events=[]
        for i in range(100):
            change=rng.integers(0,2,(32,32),dtype=np.uint8)
            failure=exp.consume(change,mask,True,app.utc_timestamp(),i,i,lambda *e:events.append(e))
            assert failure is None
            if exp.all_complete(): break
        status=exp.status(1.0)
        assert status['complete'] and not status['latched'] and not events
        for variant in exp.variants.values():
            for alignment in variant.alignment_variants.values():
                assert alignment.conditioner.c0_bits_consumed==alignment.conditioner.c1_bits_consumed
                assert alignment.conditioner.written_bytes==1024
                assert alignment.conditioner.status()['output_bps_until_complete'] > 0
        exp.write_summary(1.0);exp.close()

    # Exact stagger-2 scheduling: after groups g, g+1 and g+2 the first
    # serialized conditioner block must be C0_g || C1_(g+2).
    with tempfile.TemporaryDirectory() as td:
        class AlignmentArgs:
            validation_output_bytes=4096
            conditioner='sha3-512'
            write_conditioned_output=True
            conditioner_input_bits=2048
            conditioned_output_bytes=64
        alignment=app.DualWeaveAlignmentVariant('row-major','stagger-2',Path(td),AlignmentArgs())
        zeros=np.zeros(1024,dtype=np.uint8)
        ones=np.ones(1024,dtype=np.uint8)
        alternating=np.tile(np.array([0,1],dtype=np.uint8),512)
        alignment.consume(zeros,alternating)       # g: C1 intentionally discarded
        alignment.consume(alternating,zeros)       # g+1: C1 intentionally discarded
        status=alignment.consume(ones,ones)         # g+2: C0_g || C1_(g+2)
        alignment.close()
        serialized=alignment.conditioner_input_path.read_bytes()
        assert serialized==bytes(128)+bytes([0xff])*128
        assert status['discarded_initial_c1_groups']==2
        assert status['group_pairs_fed']==1
        assert status['pending_tail_c0_groups']==2

    # Framed Y8 transport round-trip and independent payload hash.
    left,right=socket.socketpair()
    payload=bytes(range(64))
    hdr=frame_header(source_id='selftest',frame_id=1,captured_unix_ns=1,captured_monotonic_ns=2,width=8,height=8,payload=payload,controls={})
    send_message(left,hdr,payload);msg=recv_message(right);verify_frame_message(msg)
    assert msg.payload==payload
    left.close();right.close()
    assert redact_url('rtsp://user:secret@example/cam')=='rtsp://***@example/cam'

    # Runner/server API contract.  A mismatch here caused v6.1 smoke tests to
    # time out even though Flask and the camera thread had started correctly.
    class DummyArgs:
        web_images=False
        log_file=Path('/tmp/camera-entropy-selftest.log')
    class DummyDiagnostics:
        def snapshot(self, include_counts=True):
            return {'enabled': True, 'stages': [], 'heatmap': {'file': None, 'sequence': 0}}
        def image(self):
            return None
    class DummyService:
        args=DummyArgs()
        lock=threading.RLock()
        output_dir=Path('/tmp')
        byte_diagnostics=DummyDiagnostics()
        def status(self):
            return {'state':'STARTING','app_version':app.APP_VERSION}
        def files(self):
            return {}
    if FLASK_AVAILABLE:
        client=app.create_app(DummyService()).test_client()
        for route in ('/api/stats','/api/status','/api/byte-diagnostics'):
            response=client.get(route)
            assert response.status_code==200, (route,response.status_code)
            payload=response.get_json()
            if route == '/api/byte-diagnostics':
                assert payload['enabled'] is True
            else:
                assert payload['state']=='STARTING'
    print('camera-entropy distributed v8.0.4 self-test: PASS');return 0
if __name__=='__main__':raise SystemExit(main())
