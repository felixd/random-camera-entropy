#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast diagnostics for packed binary entropy files.

Lag-N counts are computed directly on packed bytes with vectorized shifts and
popcount, avoiding a full unpacked copy per lag. This is a diagnostic companion,
not an SP 800-90B entropy estimator.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

DEFAULT_LAGS = (1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 24, 32, 48, 64, 128, 256, 512, 1024)
POPCOUNT = np.array([i.bit_count() for i in range(256)], dtype=np.uint8)


def parse_lags(text: str) -> tuple[int, ...]:
    values = sorted({int(x.strip()) for x in text.split(",") if x.strip()})
    if not values or values[0] < 1:
        raise argparse.ArgumentTypeError("lags must be positive integers")
    return tuple(values)


def shannon_from_counts(counts: np.ndarray) -> float:
    total = int(counts.sum())
    if total == 0:
        return 0.0
    p = counts[counts > 0].astype(np.float64) / total
    return float(-np.sum(p * np.log2(p)))


def binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


def gammaincc(a: float, x: float) -> float:
    if a <= 0.0 or x < 0.0:
        return math.nan
    if x == 0.0:
        return 1.0
    gln = math.lgamma(a)
    eps, fpmin, itmax = 3e-14, 1e-300, 10000
    if x < a + 1.0:
        ap, summ, delta = a, 1.0 / a, 1.0 / a
        for _ in range(itmax):
            ap += 1.0
            delta *= x / ap
            summ += delta
            if abs(delta) <= abs(summ) * eps:
                break
        p = summ * math.exp(-x + a * math.log(x) - gln)
        return min(1.0, max(0.0, 1.0 - p))
    b = x + 1.0 - a
    c = 1.0 / fpmin
    d = 1.0 / max(abs(b), fpmin)
    h = d
    for i in range(1, itmax + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) <= eps:
            break
    return min(1.0, max(0.0, math.exp(-x + a * math.log(x) - gln) * h))


def chi_square_sf(value: float, degrees: int) -> float:
    return gammaincc(degrees / 2.0, value / 2.0)


def phi_from_counts(n00: int, n01: int, n10: int, n11: int) -> float:
    denominator = math.sqrt((n10+n11)*(n00+n01)*(n01+n11)*(n00+n10))
    return ((n11*n00 - n10*n01) / denominator) if denominator else math.nan


def pair_metrics(counts: Iterable[int]) -> dict[str, float | int]:
    n00, n01, n10, n11 = (int(x) for x in counts)
    pairs = n00+n01+n10+n11
    return {
        "n00": n00, "n01": n01, "n10": n10, "n11": n11, "pairs": pairs,
        "same_rate": (n00+n11)/pairs if pairs else math.nan,
        "change_rate": (n01+n10)/pairs if pairs else math.nan,
        "p01": n01/(n00+n01) if n00+n01 else math.nan,
        "p10": n10/(n10+n11) if n10+n11 else math.nan,
        "phi": phi_from_counts(n00,n01,n10,n11),
    }


def packed_pair_counts(data: np.ndarray, lag: int, block_bytes: int = 16*1024*1024) -> np.ndarray:
    """Exact N00/N01/N10/N11 for MSB-first packed bits at a positive lag."""
    total_bits = int(data.size) * 8
    pair_bits = total_bits - lag
    if pair_bits <= 0:
        return np.zeros(4, dtype=np.int64)
    byte_shift, bit_shift = divmod(lag, 8)
    output_bytes = (pair_bits + 7) // 8
    remainder = pair_bits & 7
    ones_a = ones_b = n11 = 0
    for start in range(0, output_bytes, block_bytes):
        length = min(block_bytes, output_bytes - start)
        a = np.asarray(data[start:start+length], dtype=np.uint8)
        if bit_shift == 0:
            b = np.asarray(data[byte_shift+start:byte_shift+start+length], dtype=np.uint8)
        else:
            left = np.asarray(data[byte_shift+start:byte_shift+start+length], dtype=np.uint16)
            right = np.asarray(data[byte_shift+start+1:byte_shift+start+length+1], dtype=np.uint16)
            if left.size != length:
                padded = np.zeros(length, dtype=np.uint16)
                padded[:left.size] = left
                left = padded
            if right.size != length:
                padded = np.zeros(length, dtype=np.uint16)
                padded[:right.size] = right
                right = padded
            b = (((left << bit_shift) & 0xFF) | (right >> (8-bit_shift))).astype(np.uint8)
        # Mask unused low bits in the final aligned byte.
        if remainder and start + length == output_bytes:
            mask = np.uint8((0xFF << (8-remainder)) & 0xFF)
            if length > 1:
                ones_a += int(POPCOUNT[a[:-1]].sum(dtype=np.uint64))
                ones_b += int(POPCOUNT[b[:-1]].sum(dtype=np.uint64))
                n11 += int(POPCOUNT[np.bitwise_and(a[:-1], b[:-1])].sum(dtype=np.uint64))
            aa = np.uint8(a[-1] & mask)
            bb = np.uint8(b[-1] & mask)
            ones_a += int(POPCOUNT[aa]); ones_b += int(POPCOUNT[bb]); n11 += int(POPCOUNT[np.uint8(aa & bb)])
        else:
            ones_a += int(POPCOUNT[a].sum(dtype=np.uint64))
            ones_b += int(POPCOUNT[b].sum(dtype=np.uint64))
            n11 += int(POPCOUNT[np.bitwise_and(a,b)].sum(dtype=np.uint64))
    n10 = ones_a - n11
    n01 = ones_b - n11
    n00 = pair_bits - n11 - n10 - n01
    return np.array([n00,n01,n10,n11], dtype=np.int64)


@dataclass
class RunTracker:
    longest_symbol: int = 0
    longest_length: int = 0
    longest_position: int = 0
    current_symbol: int | None = None
    current_length: int = 0
    current_start: int = 0
    position: int = 0

    def consume(self, bits: np.ndarray) -> None:
        if bits.size == 0:
            return
        old_position = self.position
        boundaries = np.flatnonzero(bits[1:] != bits[:-1]) + 1
        starts = np.concatenate(([0], boundaries)).astype(np.int64, copy=False)
        ends = np.concatenate((boundaries, [bits.size])).astype(np.int64, copy=False)
        symbols = bits[starts]
        lengths = (ends-starts).astype(np.int64, copy=False)
        global_starts = old_position + starts
        if self.current_symbol is not None and int(symbols[0]) == self.current_symbol:
            lengths[0] += self.current_length
            global_starts[0] = self.current_start
        best = int(np.argmax(lengths))
        if int(lengths[best]) > self.longest_length:
            self.longest_symbol = int(symbols[best])
            self.longest_length = int(lengths[best])
            self.longest_position = int(global_starts[best])
        self.current_symbol = int(symbols[-1])
        self.current_length = int(lengths[-1])
        self.current_start = int(global_starts[-1])
        self.position += int(bits.size)


class BasicStats:
    def __init__(self, track_runs: bool = False) -> None:
        self.hist = np.zeros(256, dtype=np.int64)
        self.bytes = self.bits = self.ones = 0
        self.track_runs = bool(track_runs)
        self.runs = RunTracker()
        self.pi_remainder = b""
        self.pi_inside = self.pi_points = 0

    def consume(self, raw: bytes) -> None:
        if not raw: return
        arr = np.frombuffer(raw,dtype=np.uint8)
        self.hist += np.bincount(arr,minlength=256).astype(np.int64)
        self.bytes += int(arr.size); self.bits += int(arr.size)*8
        self.ones += int(POPCOUNT[arr].sum(dtype=np.uint64))
        if self.track_runs:
            self.runs.consume(np.unpackbits(arr,bitorder="big"))
        pi_data=self.pi_remainder+raw; full=(len(pi_data)//6)*6
        if full:
            blocks=np.frombuffer(pi_data[:full],dtype=np.uint8).reshape(-1,6).astype(np.uint64)
            x=(blocks[:,0]<<16)|(blocks[:,1]<<8)|blocks[:,2]
            y=(blocks[:,3]<<16)|(blocks[:,4]<<8)|blocks[:,5]
            r=np.uint64((1<<24)-1)
            self.pi_inside += int(np.count_nonzero(x*x+y*y <= r*r)); self.pi_points += int(blocks.shape[0])
        self.pi_remainder=pi_data[full:]

    def summary(self, lag1_counts: np.ndarray | None = None) -> dict[str,object]:
        p1=self.ones/self.bits if self.bits else math.nan
        most=int(np.argmax(self.hist)) if self.bytes else 0
        pmax=int(self.hist[most])/self.bytes if self.bytes else 0.0
        expected=self.bytes/256 if self.bytes else 0.0
        chi=float(np.sum((self.hist-expected)**2/expected)) if expected else 0.0
        pi=4*self.pi_inside/self.pi_points if self.pi_points else math.nan
        lag1=pair_metrics(lag1_counts) if lag1_counts is not None else None
        markov=math.nan
        if lag1 and lag1["pairs"]:
            p01=float(lag1["p01"]); p10=float(lag1["p10"])
            p0=(int(lag1["n00"])+int(lag1["n01"]))/int(lag1["pairs"])
            markov=p0*binary_entropy(p01)+(1-p0)*binary_entropy(p10)
        return {
            "total_bytes":self.bytes,"total_bits":self.bits,"zeros":self.bits-self.ones,"ones":self.ones,"p1":p1,
            "marginal_min_entropy_bits_per_bit":-math.log2(max(p1,1-p1)) if self.bits else 0.0,
            "byte_shannon_entropy_bits_per_byte":shannon_from_counts(self.hist),
            "byte_min_entropy_bits_per_byte":-math.log2(pmax) if pmax else 0.0,
            "most_common_byte":most,"most_common_byte_hex":f"0x{most:02X}","most_common_byte_count":int(self.hist[most]),"most_common_byte_fraction":pmax,
            "byte_chi_square":chi,"byte_chi_square_degrees":255,"byte_chi_square_p_value":chi_square_sf(chi,255),
            "monte_carlo_pi":pi,"monte_carlo_pi_error_percent":abs(pi-math.pi)/math.pi*100 if math.isfinite(pi) else math.nan,
            "longest_run_symbol":self.runs.longest_symbol if self.track_runs else None,
            "longest_run_length":self.runs.longest_length if self.track_runs else None,
            "longest_run_position_bits":self.runs.longest_position if self.track_runs else None,
            "first_order_markov_entropy_rate_bits_per_bit":markov,"lag1":lag1,
        }


def scan_basic(path:Path, read_bytes:int, track_runs:bool=False) -> BasicStats:
    st=BasicStats(track_runs)
    with path.open('rb') as f:
        while raw:=f.read(read_bytes): st.consume(raw)
    return st


def analyze_chunks(path:Path, chunk_bytes:int, read_bytes:int, track_runs:bool=False) -> list[dict[str,object]]:
    rows=[]; offset=0; index=0
    with path.open('rb') as f:
        while True:
            raw=f.read(chunk_bytes)
            if not raw: break
            st=BasicStats(track_runs)
            for start in range(0,len(raw),read_bytes): st.consume(raw[start:start+read_bytes])
            arr=np.frombuffer(raw,dtype=np.uint8)
            summ=st.summary(packed_pair_counts(arr,1,min(len(arr),16*1024*1024)))
            rows.append({"chunk_index":index,"offset_bytes":offset,"length_bytes":len(raw),"p1":summ["p1"],"marginal_hmin":summ["marginal_min_entropy_bits_per_bit"],"lag1_phi":summ["lag1"]["phi"],"byte_shannon":summ["byte_shannon_entropy_bits_per_byte"],"byte_hmin":summ["byte_min_entropy_bits_per_byte"],"byte_chi_square":summ["byte_chi_square"],"byte_chi_square_p_value":summ["byte_chi_square_p_value"],"monte_carlo_pi":summ["monte_carlo_pi"],"longest_run":summ["longest_run_length"]})
            offset+=len(raw); index+=1
    return rows


def write_csv(path:Path, rows:list[dict[str,object]]) -> None:
    if not rows: path.write_text('',encoding='utf-8'); return
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def svg_line(path:Path, rows:list[dict[str,object]]) -> None:
    W,H=1000,430; ml,mr,mt,mb=80,30,45,65
    vals=[abs(float(r['phi'])) for r in rows if math.isfinite(float(r['phi']))]; ymax=max(vals+[1e-6])*1.1; xmax=max(int(r['lag']) for r in rows)
    xp=lambda l: ml+math.log2(l)/max(1.0,math.log2(xmax))*(W-ml-mr)
    yp=lambda v: mt+(1-min(abs(v)/ymax,1))*(H-mt-mb)
    pts=' '.join(f"{xp(int(r['lag'])):.2f},{yp(float(r['phi'])):.2f}" for r in rows)
    circles=''.join(f'<circle cx="{xp(int(r["lag"])):.2f}" cy="{yp(float(r["phi"])):.2f}" r="3"><title>lag {r["lag"]}: φ={float(r["phi"]):.9g}</title></circle>' for r in rows)
    ticks=''.join(f'<text x="{xp(int(r["lag"])):.2f}" y="{H-mb+24}" text-anchor="middle">{r["lag"]}</text>' for r in rows if int(r['lag']) in (1,2,4,8,16,32,64,128,256,512,1024))
    path.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"><style>text{{font-family:system-ui;font-size:13px}}polyline{{fill:none;stroke:#1677b8;stroke-width:2.5}}circle{{fill:#1677b8}}</style><text x="500" y="25" text-anchor="middle" font-size="18">Korelacja bitowa |φ| względem lagu</text><line x1="{ml}" y1="{H-mb}" x2="{W-mr}" y2="{H-mb}" stroke="#222"/><line x1="{ml}" y1="{mt}" x2="{ml}" y2="{H-mb}" stroke="#222"/><polyline points="{pts}"/>{circles}{ticks}<text x="500" y="418" text-anchor="middle">lag (oś log₂)</text><text x="{ml-8}" y="{mt+5}" text-anchor="end">{ymax:.4g}</text><text x="{ml-8}" y="{H-mb+5}" text-anchor="end">0</text></svg>''',encoding='utf-8')


def main()->int:
    ap=argparse.ArgumentParser(description='Fast diagnostics for packed binary data')
    ap.add_argument('input',type=Path);ap.add_argument('--output-dir',type=Path);ap.add_argument('--lags',type=parse_lags,default=DEFAULT_LAGS);ap.add_argument('--read-mib',type=int,default=4);ap.add_argument('--chunk-mib',type=int,default=10);ap.add_argument('--lag-block-mib',type=int,default=8);ap.add_argument('--longest-run',action='store_true',help='Enable slower exact longest-run scan')
    args=ap.parse_args()
    if not args.input.is_file(): ap.error(f'input file not found: {args.input}')
    out=args.output_dir or args.input.with_name(f'analysis_{args.input.stem}');out.mkdir(parents=True,exist_ok=True)
    readb=max(1,args.read_mib)*1024*1024; data=np.memmap(args.input,dtype=np.uint8,mode='r')
    lag_rows=[]; lag_counts={}
    for lag in args.lags:
        counts=packed_pair_counts(data,lag,max(1,args.lag_block_mib)*1024*1024);lag_counts[lag]=counts;lag_rows.append({'lag':lag}|pair_metrics(counts))
    basic=scan_basic(args.input,readb,args.longest_run); summary=basic.summary(lag_counts.get(1))|{'input':str(args.input.resolve()),'file_size_bytes':args.input.stat().st_size,'diagnostic_only':True}
    chunks=analyze_chunks(args.input,max(1,args.chunk_mib)*1024*1024,readb,args.longest_run)
    histogram=[{'value':i,'hex':f'0x{i:02X}','count':int(c),'fraction':int(c)/basic.bytes if basic.bytes else 0.0} for i,c in enumerate(basic.hist)]
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    write_csv(out/'summary.csv',[{k:v for k,v in summary.items() if not isinstance(v,(dict,list))}]);write_csv(out/'lag_correlation.csv',lag_rows);write_csv(out/'chunks.csv',chunks);write_csv(out/'byte_histogram.csv',histogram);svg_line(out/'lag_correlation.svg',lag_rows)
    print(json.dumps({'input':str(args.input),'bytes':summary['total_bytes'],'p1':summary['p1'],'lag1_phi':summary['lag1']['phi'] if summary['lag1'] else None,'byte_hmin':summary['byte_min_entropy_bits_per_byte'],'byte_chi_square':summary['byte_chi_square'],'byte_chi_square_p_value':summary['byte_chi_square_p_value'],'output_dir':str(out)},indent=2));return 0

if __name__=='__main__': raise SystemExit(main())
