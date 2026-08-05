package main

import (
	"bytes"
	"testing"
)

func TestMessageRoundTrip(t *testing.T) {
	var wire bytes.Buffer
	header := map[string]any{"type": "frame", "version": float64(1), "frame_id": float64(7)}
	payload := []byte{1, 2, 3, 4}
	if err := writeMessage(&wire, header, payload); err != nil {
		t.Fatal(err)
	}
	gotHeader, gotPayload, err := readMessage(&wire)
	if err != nil {
		t.Fatal(err)
	}
	if gotHeader["type"] != "frame" {
		t.Fatalf("unexpected header: %#v", gotHeader)
	}
	if !bytes.Equal(gotPayload, payload) {
		t.Fatalf("payload mismatch")
	}
}

func TestSplitNonEmpty(t *testing.T) {
	got := splitNonEmpty(" /dev/video0, ,/dev/video2 ")
	if len(got) != 2 || got[0] != "/dev/video0" || got[1] != "/dev/video2" {
		t.Fatalf("unexpected: %#v", got)
	}
}

func TestParseControlIntegerWithMenuLabel(t *testing.T) {
	value, ok := parseControlInteger("auto_exposure: 1 (Manual Mode)\n")
	if !ok || value != 1 {
		t.Fatalf("unexpected parse result: value=%d ok=%v", value, ok)
	}
	value, ok = parseControlInteger("exposure_time_absolute: 7000\n")
	if !ok || value != 7000 {
		t.Fatalf("unexpected exposure parse result: value=%d ok=%v", value, ok)
	}
}

func TestControlsMatchAcceptsV4L2MenuOutput(t *testing.T) {
	cfg := config{ManualExposure: true, Exposure: 7000}
	controls := map[string]any{
		"auto_exposure":          "auto_exposure: 1 (Manual Mode)",
		"exposure_time_absolute": 7000,
	}
	matched, reason := controlsMatch(cfg, controls)
	if !matched {
		t.Fatalf("controls should match: %s", reason)
	}
}

func TestAgentErrorMessage(t *testing.T) {
	var wire bytes.Buffer
	if err := writeMessage(&wire, map[string]any{
		"type":      "error",
		"version":   float64(1),
		"code":      "source-busy",
		"reason":    "another compute client is already subscribed",
		"retryable": true,
	}, nil); err != nil {
		t.Fatal(err)
	}
	header, payload, err := readMessage(&wire)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload) != 0 || header["type"] != "error" || header["code"] != "source-busy" {
		t.Fatalf("unexpected error message: %#v payload=%d", header, len(payload))
	}
	if retryable, ok := header["retryable"].(bool); !ok || !retryable {
		t.Fatalf("retryable not preserved: %#v", header["retryable"])
	}
}
