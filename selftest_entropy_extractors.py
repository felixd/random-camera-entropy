#!/usr/bin/env python3
from __future__ import annotations
import numpy as np
from entropy_extractors import repeated_von_neumann, von_neumann_split

def main() -> int:
    bits=np.array([0,1,1,0, 0,0,1,1],dtype=np.uint8)
    one=von_neumann_split(bits)
    expected=np.array([1,0],dtype=np.uint8)
    assert np.array_equal(one,expected),(one,expected)
    zero,metrics=repeated_von_neumann(bits,0)
    assert np.array_equal(zero,bits) and metrics==[]
    two,metrics=repeated_von_neumann(bits,2)
    assert len(metrics)==2
    assert metrics[0]['input_bits']==8 and metrics[0]['output_bits']==2
    assert metrics[1]['input_bits']==2
    assert np.array_equal(two,np.array([1],dtype=np.uint8))
    print('selftest_entropy_extractors: PASS')
    return 0
if __name__=='__main__': raise SystemExit(main())
