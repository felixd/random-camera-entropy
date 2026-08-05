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
