#!/usr/bin/env python3
from __future__ import annotations
import json,tempfile,logging
from pathlib import Path
from control_server import ALLOWED_PROFILES,JobManager,Settings,create_app,load_sources,safe_path,scan_data_root

def main()->int:
    with tempfile.TemporaryDirectory() as raw:
        base=Path(raw); data=base/'data'; data.mkdir(); (data/'sample').mkdir(); (data/'sample'/'x.json').write_text('{}')
        # Regression: an ordinary run report has no dual_weave_report.json.
        ordinary=data/'ordinary-run'; ordinary.mkdir(); (ordinary/'run_report.html').write_text('<h1>run</h1>',encoding='utf-8')
        # Incomplete report metadata must never break the persistent control plane.
        broken=data/'broken-dual-run'; broken.mkdir(); (broken/'run_report.html').write_text('<h1>run</h1>',encoding='utf-8'); (broken/'dual_weave_report.json').write_text('null',encoding='utf-8')
        campaign=data/'broken-campaign'; campaign.mkdir(); (campaign/'dual_weave_campaign_report.html').write_text('<h1>campaign</h1>',encoding='utf-8'); (campaign/'dual_weave_campaign_summary.json').write_text('{',encoding='utf-8')
        qualification=data/'broken-qualification'; qualification.mkdir(); (qualification/'qualification_report.html').write_text('<h1>qualification</h1>',encoding='utf-8'); (qualification/'qualification_summary.json').write_text('[]',encoding='utf-8')
        sources=base/'sources.json'; sources.write_text(json.dumps({'sources':[{'id':'local','label':'Local','source_type':'v4l2','env':{'DEVICE':'/dev/null'}}]}))
        secret=base/'secret'; secret.write_text('x'*64)
        settings=Settings(root=Path(__file__).resolve().parent,data_root=data,sources_file=sources,credentials_file=base/'unused',secret_file=secret,host='127.0.0.1',port=8087,worker_host='127.0.0.1',worker_port=18087,auth_mode='none',secure_cookie=False,trust_proxy=False,share_token_file=None)
        assert ALLOWED_PROFILES['dual-weave-stagger2']=='smoke_dual_weave_stagger2.sh'
        assert ALLOWED_PROFILES['temporal-sha3']=='smoke_temporal_sha3.sh'
        loaded_sources=load_sources(sources)
        assert loaded_sources[0]['id']=='local'
        manager=JobManager(settings,loaded_sources,logging.getLogger('control-selftest'))
        env,slug,_output=manager._build_environment({
            'web_images':False,'mask_snapshot_images':False,'live_byte_diagnostics':True,
            'live_heatmap_interval_seconds':'15','live_heatmap_max_stages':'7',
            'live_heatmap_min_bytes':'8192',
        },loaded_sources[0],'dual-weave-stagger2','1234567890abcdef')
        assert env['WEB_IMAGES']=='0' and env['MASK_SNAPSHOT_IMAGES']=='0' and env['LIVE_BYTE_DIAGNOSTICS']=='1'
        assert env['LIVE_HEATMAP_INTERVAL_SECONDS']=='15'
        assert env['LIVE_HEATMAP_MAX_STAGES']=='7'
        assert env['LIVE_HEATMAP_MIN_BYTES']=='8192'
        assert slug.startswith('web-dual-weave-stagger2-')
        assert safe_path(data,'sample/x.json').is_file()
        rows=scan_data_root(data,20)
        assert {row['name'] for row in rows} >= {'ordinary-run','broken-dual-run','broken-campaign','broken-qualification'}
        app=create_app(settings); app.testing=True
        client=app.test_client()
        assert client.get('/healthz').status_code==200
        assert client.get('/').status_code==200
        assert client.get('/data/').status_code==200
        assert client.get('/data/sample/x.json').status_code==200
        assert client.get('/data/../secret').status_code==404
    print('control_selftest: PASS')
    return 0
if __name__=='__main__': raise SystemExit(main())
