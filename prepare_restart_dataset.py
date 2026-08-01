#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an SP 800-90B row dataset from independent packed-bit restart files."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np

def sha256(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  while b:=f.read(8*1024*1024):h.update(b)
 return h.hexdigest()

def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('output',type=Path);ap.add_argument('inputs',nargs='+',type=Path);ap.add_argument('--samples-per-restart',type=int,default=1000);ap.add_argument('--overwrite',action='store_true');args=ap.parse_args()
 if args.samples_per_restart<1:ap.error('samples-per-restart must be positive')
 missing=[str(p) for p in args.inputs if not p.is_file()]
 if missing:ap.error('missing inputs: '+', '.join(missing[:5]))
 if args.output.exists() and not args.overwrite:ap.error(f'output exists: {args.output}')
 args.output.parent.mkdir(parents=True,exist_ok=True);mode='wb' if args.overwrite else 'xb';rows=[]
 with args.output.open(mode) as dst:
  for path in args.inputs:
   need=(args.samples_per_restart+7)//8;raw=path.read_bytes()[:need]
   bits=np.unpackbits(np.frombuffer(raw,dtype=np.uint8),bitorder='big')[:args.samples_per_restart]
   if bits.size!=args.samples_per_restart:raise SystemExit(f'not enough samples in {path}')
   dst.write(bits.tobytes());rows.append({'file':str(path.resolve()),'sha256':sha256(path)})
 manifest={'output':str(args.output.resolve()),'row_count':len(rows),'samples_per_row':args.samples_per_restart,'bits_per_symbol':1,'sample_format':'one byte per binary sample','rows':rows,'output_sha256':sha256(args.output)}
 args.output.with_suffix(args.output.suffix+'.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in manifest.items() if k!='rows'},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
