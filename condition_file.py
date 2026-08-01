#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline SHA3-512 conditioning of fixed-size packed input blocks.

This tool reproduces the online conditioner for validation. It does not create
entropy and must not be used to claim more entropy than the assessed input has.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

EMPTY_SHA3_512 = (
    "a69f73cca23a9ac5c8b567dc185a756e97c982164fe25859e0d1dcc1475c80a6"
    "15b2123af1f5f94c11e3e9402c3ac558f500199d95b6d3e301758586281dcd26"
)


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        while data:=f.read(8*1024*1024): h.update(data)
    return h.hexdigest()


def main()->int:
    ap=argparse.ArgumentParser(description='Offline fixed-block SHA3-512 conditioner')
    ap.add_argument('input',type=Path);ap.add_argument('output',type=Path)
    ap.add_argument('--input-bits',type=int,default=2048)
    ap.add_argument('--max-output-bytes',type=int,default=0)
    ap.add_argument('--overwrite',action='store_true')
    args=ap.parse_args()
    if hashlib.sha3_512(b'').hexdigest()!=EMPTY_SHA3_512: raise SystemExit('SHA3-512 self-test failed')
    if not args.input.is_file(): ap.error(f'input not found: {args.input}')
    if args.input_bits<512 or args.input_bits%8: ap.error('input-bits must be a multiple of 8 and >=512')
    if args.max_output_bytes<0: ap.error('max-output-bytes cannot be negative')
    if args.output.exists() and not args.overwrite: ap.error(f'output exists: {args.output}')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    block_bytes=args.input_bits//8; written=blocks=0; previous=None; repeated=0
    mode='wb' if args.overwrite else 'xb'
    with args.input.open('rb') as src, args.output.open(mode,buffering=0) as dst:
        while True:
            block=src.read(block_bytes)
            if len(block)<block_bytes: break
            digest=hashlib.sha3_512(block).digest()
            if previous is not None and digest==previous:
                repeated+=1
                raise RuntimeError(f'repeated consecutive digest at block {blocks+1}')
            previous=digest; blocks+=1
            if args.max_output_bytes:
                digest=digest[:max(0,args.max_output_bytes-written)]
            if not digest: break
            dst.write(digest);written+=len(digest)
            if args.max_output_bytes and written>=args.max_output_bytes: break
        os.fsync(dst.fileno())
    manifest={
        'algorithm':'SHA3-512','input':str(args.input.resolve()),'output':str(args.output.resolve()),
        'input_bits_per_block':args.input_bits,'output_bits_per_full_block':512,
        'compression_ratio':args.input_bits/512,'blocks':blocks,'written_bytes':written,
        'discarded_trailing_input_bytes':args.input.stat().st_size-blocks*block_bytes,
        'repeated_consecutive_digest_failures':repeated,
        'input_sha256':sha256_file(args.input),'output_sha256':sha256_file(args.output),
        'entropy_claim':'none; output entropy is bounded by the assessed input entropy and conditioner rules',
    }
    args.output.with_suffix(args.output.suffix+'.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2));return 0

if __name__=='__main__': raise SystemExit(main())
