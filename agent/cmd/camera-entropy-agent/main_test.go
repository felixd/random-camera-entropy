package main

import (
	"math"
	"testing"
	"time"
)

func TestFrameHeaderIncludesSourceWarmup(t *testing.T) {
	f := &frame{
		ID:          7,
		UnixNS:      123,
		MonotonicNS: int64(90*time.Second + 250*time.Millisecond),
		Payload:     []byte{1, 2, 3, 4},
		Controls:    map[string]any{},
	}
	header := frameHeader(config{SourceID: "camera", Width: 2, Height: 2}, f)
	warmup, ok := header["source_warmup_seconds"].(float64)
	if !ok {
		t.Fatalf("source_warmup_seconds missing or not float64: %#v", header["source_warmup_seconds"])
	}
	if math.Abs(warmup-90.25) > 1e-9 {
		t.Fatalf("unexpected source warm-up: %.9f", warmup)
	}
}
