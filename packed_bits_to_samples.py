#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert MSB-first packed bits to one-byte-per-sample format used by NIST EA."""
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
 ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('output',type=Path);ap.add_argument('--samples',type=int,default=1_000_000);ap.add_argument('--overwrite',action='store_true');args=ap.parse_args()
 if not args.input.is_file():ap.error(f'input not found: {args.input}')
 if args.samples<1:ap.error('samples must be positive')
 if args.output.exists() and not args.overwrite:ap.error(f'output exists: {args.output}')
 args.output.parent.mkdir(parents=True,exist_ok=True);remaining=args.samples;written=0;mode='wb' if args.overwrite else 'xb'
 with args.input.open('rb') as src,args.output.open(mode) as dst:
  while remaining>0:
   raw=src.read(min(1024*1024,(remaining+7)//8))
   if not raw:break
   bits=np.unpackbits(np.frombuffer(raw,dtype=np.uint8),bitorder='big')[:remaining]
   dst.write(bits.tobytes());written+=int(bits.size);remaining-=int(bits.size)
 manifest={'input':str(args.input.resolve()),'output':str(args.output.resolve()),'sample_format':'one unsigned byte per binary sample (0 or 1)','bit_order':'MSB first','requested_samples':args.samples,'written_samples':written,'input_sha256':sha256(args.input),'output_sha256':sha256(args.output)}
 args.output.with_suffix(args.output.suffix+'.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');print(json.dumps(manifest,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
