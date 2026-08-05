// Camera Entropy Agent continuously drains an uncompressed V4L2 YUYV stream,
// extracts the Y8 plane and serves frames through the CEYTLS01 mTLS protocol.
package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"os/user"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	appVersion      = "2026.08.05.camera-entropy-go-agent.8.0.4"
	protocolVersion = 1
	protocolMagic   = "CEYTLS01"
	maxHeaderBytes  = 64 * 1024
	maxPayloadBytes = 128 * 1024 * 1024
)

type config struct {
	Device          string
	ScanDevices     []string
	Width           int
	Height          int
	FPS             float64
	ManualExposure  bool
	Exposure        int
	Strict          bool
	ControlCheck    time.Duration
	ControlFailures int
	ListenHost      string
	ListenPort      int
	TLSCA           string
	TLSCert         string
	TLSKey          string
	AllowedClientCN string
	SourceID        string
	SocketTimeout   time.Duration
	ClientBacklog   int
	Probe           bool
	ProbeFrames     int
	Verbose         bool
	ShowVersion     bool
}

type frame struct {
	ID          uint64
	UnixNS      int64
	MonotonicNS int64
	Payload     []byte
	Controls    map[string]any
}

type subscription struct {
	frames chan *frame
	errs   chan error
	done   chan struct{}
	once   sync.Once
}

func (s *subscription) close() { s.once.Do(func() { close(s.done) }) }

type camera struct {
	cfg         config
	device      string
	started     time.Time
	startedUnix int64
	frameID     atomic.Uint64
	latestID    atomic.Uint64
	controls    atomic.Value // map[string]any
	mu          sync.Mutex
	subscriber  *subscription
	fatal       atomic.Value // errorBox
}

type errorBox struct{ Err error }

var (
	errClientBusy         = errors.New("another compute client is already subscribed")
	controlIntegerPattern = regexp.MustCompile(`[-+]?\d+`)
)

func envString(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}
func envInt(name string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
}
func envFloat(name string, fallback float64) float64 {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return fallback
	}
	return parsed
}
func envBool(name string, fallback bool) bool {
	value := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
	if value == "" {
		return fallback
	}
	switch value {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	}
	return fallback
}

func preLoadConfig(args []string) error {
	var path string
	for index := 0; index < len(args); index++ {
		if args[index] == "--config" && index+1 < len(args) {
			path = args[index+1]
			break
		}
		if strings.HasPrefix(args[index], "--config=") {
			path = strings.TrimPrefix(args[index], "--config=")
			break
		}
	}
	if path == "" {
		return nil
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			return fmt.Errorf("invalid config line: %s", line)
		}
		key = strings.TrimSpace(key)
		value = strings.Trim(strings.TrimSpace(value), "\"'")
		if os.Getenv(key) == "" {
			_ = os.Setenv(key, value)
		}
	}
	return scanner.Err()
}

func parseConfig() config {
	_ = preLoadConfig(os.Args[1:])
	var cfg config
	var configPath string
	var scan string
	flag.StringVar(&configPath, "config", "", "optional KEY=VALUE configuration file")
	flag.StringVar(&cfg.Device, "device", envString("DEVICE", "auto"), "V4L2 device or auto")
	flag.StringVar(&scan, "scan-devices", envString("SCAN_DEVICES", "/dev/video0,/dev/video1,/dev/video2"), "comma-separated devices considered by auto mode")
	flag.IntVar(&cfg.Width, "width", envInt("WIDTH", 1280), "frame width")
	flag.IntVar(&cfg.Height, "height", envInt("HEIGHT", 720), "frame height")
	flag.Float64Var(&cfg.FPS, "camera-fps", envFloat("CAMERA_FPS", 10), "requested camera FPS")
	flag.BoolVar(&cfg.ManualExposure, "manual-exposure", envBool("MANUAL_EXPOSURE", true), "enforce manual exposure")
	flag.IntVar(&cfg.Exposure, "exposure-value", envInt("EXPOSURE", 7000), "exposure_time_absolute")
	flag.BoolVar(&cfg.Strict, "strict-mode", envBool("STRICT_MODE", true), "reject a camera that cannot produce requested YUYV geometry")
	checkSeconds := flag.Float64("control-check-seconds", envFloat("CONTROL_CHECK_SECONDS", 60), "control validation interval")
	flag.IntVar(&cfg.ControlFailures, "control-fail-consecutive", envInt("CONTROL_FAIL_CONSECUTIVE", 1), "consecutive control mismatches before failure")
	flag.StringVar(&cfg.ListenHost, "listen-host", envString("LISTEN_HOST", "0.0.0.0"), "listen address")
	flag.IntVar(&cfg.ListenPort, "listen-port", envInt("TLS_PORT", 9443), "listen port")
	flag.StringVar(&cfg.TLSCA, "tls-ca", envString("TLS_CA", "pki/ca.crt"), "client CA")
	flag.StringVar(&cfg.TLSCert, "tls-cert", envString("TLS_CERT", "pki/agent.crt"), "server certificate")
	flag.StringVar(&cfg.TLSKey, "tls-key", envString("TLS_KEY", "pki/agent.key"), "server private key")
	flag.StringVar(&cfg.AllowedClientCN, "allowed-client-cn", envString("ALLOWED_CLIENT_CN", "camera-compute"), "allowed client certificate common name; empty accepts any trusted client")
	hostname, _ := os.Hostname()
	flag.StringVar(&cfg.SourceID, "source-id", envString("SOURCE_ID", hostname), "source identifier")
	socketSeconds := flag.Float64("socket-timeout-seconds", envFloat("SOCKET_TIMEOUT_SECONDS", 60), "socket write timeout")
	flag.IntVar(&cfg.ClientBacklog, "client-backlog-frames", envInt("CLIENT_BACKLOG_FRAMES", 16), "bounded per-client queue")
	flag.BoolVar(&cfg.Probe, "probe", false, "test candidate cameras and exit")
	flag.IntVar(&cfg.ProbeFrames, "probe-frames", 2, "frames read by each probe")
	flag.BoolVar(&cfg.Verbose, "verbose", envBool("VERBOSE", false), "verbose logging")
	flag.BoolVar(&cfg.ShowVersion, "version", false, "print agent version and exit")
	flag.Parse()
	_ = configPath
	cfg.ScanDevices = splitNonEmpty(scan)
	cfg.ControlCheck = time.Duration(*checkSeconds * float64(time.Second))
	cfg.SocketTimeout = time.Duration(*socketSeconds * float64(time.Second))
	if !cfg.ShowVersion && (cfg.Width <= 0 || cfg.Height <= 0 || cfg.FPS <= 0 || cfg.ClientBacklog < 2 || cfg.ProbeFrames < 1) {
		log.Fatal("invalid dimensions, FPS, backlog or probe frame count")
	}
	return cfg
}

func splitNonEmpty(value string) []string {
	var out []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out
}

func commandOutput(timeout time.Duration, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	output, err := cmd.CombinedOutput()
	if ctx.Err() != nil {
		return string(output), ctx.Err()
	}
	if err != nil {
		return string(output), fmt.Errorf("%s %v: %w: %s", name, args, err, strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func accessDescription(path string) string {
	current, _ := user.Current()
	groups, _ := current.GroupIds()
	info, err := os.Stat(path)
	if err != nil {
		return fmt.Sprintf("missing (%v); user=%s uid=%s groups=%v", err, current.Username, current.Uid, groups)
	}
	rw := ""
	if syscall.Access(path, 4) == nil {
		rw += "r"
	} else {
		rw += "-"
	}
	if syscall.Access(path, 2) == nil {
		rw += "w"
	} else {
		rw += "-"
	}
	return fmt.Sprintf("mode=%s access=%s user=%s uid=%s groups=%v", info.Mode(), rw, current.Username, current.Uid, groups)
}

func setControls(cfg config, device string) error {
	if !cfg.ManualExposure {
		return nil
	}
	_, err := commandOutput(5*time.Second, "v4l2-ctl", "-d", device, "--set-ctrl", "auto_exposure=1")
	if err != nil {
		return err
	}
	_, err = commandOutput(5*time.Second, "v4l2-ctl", "-d", device, "--set-ctrl", fmt.Sprintf("exposure_time_absolute=%d", cfg.Exposure))
	return err
}

func parseControlInteger(output string) (int, bool) {
	value := output
	if index := strings.LastIndex(value, ":"); index >= 0 {
		value = value[index+1:]
	}
	match := controlIntegerPattern.FindString(value)
	if match == "" {
		return 0, false
	}
	parsed, err := strconv.Atoi(match)
	if err != nil {
		return 0, false
	}
	return parsed, true
}

func readControls(device string) map[string]any {
	result := map[string]any{}
	for _, name := range []string{"auto_exposure", "exposure_time_absolute"} {
		out, err := commandOutput(5*time.Second, "v4l2-ctl", "-d", device, "--get-ctrl", name)
		if err != nil {
			result[name] = nil
			continue
		}
		if value, ok := parseControlInteger(out); ok {
			result[name] = value
			continue
		}
		result[name] = strings.TrimSpace(out)
	}
	return result
}

func controlInteger(value any) (int, bool) {
	switch current := value.(type) {
	case int:
		return current, true
	case int8:
		return int(current), true
	case int16:
		return int(current), true
	case int32:
		return int(current), true
	case int64:
		return int(current), true
	case uint:
		return int(current), true
	case uint8:
		return int(current), true
	case uint16:
		return int(current), true
	case uint32:
		return int(current), true
	case uint64:
		if current > uint64(^uint(0)>>1) {
			return 0, false
		}
		return int(current), true
	case float64:
		return int(current), current == float64(int(current))
	case string:
		return parseControlInteger(current)
	default:
		return 0, false
	}
}

func controlsMatch(cfg config, controls map[string]any) (bool, string) {
	if !cfg.ManualExposure {
		return true, "manual exposure enforcement disabled"
	}
	autoExposure, autoOK := controlInteger(controls["auto_exposure"])
	exposure, exposureOK := controlInteger(controls["exposure_time_absolute"])
	if !autoOK || !exposureOK {
		return false, fmt.Sprintf(
			"cannot parse controls auto_exposure=%v exposure_time_absolute=%v",
			controls["auto_exposure"], controls["exposure_time_absolute"],
		)
	}
	if autoExposure != 1 || exposure != cfg.Exposure {
		return false, fmt.Sprintf(
			"auto_exposure=%d expected=1 exposure_time_absolute=%d expected=%d",
			autoExposure, exposure, cfg.Exposure,
		)
	}
	return true, "controls match"
}

func streamArgs(cfg config, device string) []string {
	return []string{
		"-d", device,
		"--set-fmt-video", fmt.Sprintf("width=%d,height=%d,pixelformat=YUYV", cfg.Width, cfg.Height),
		"--set-parm", strconv.FormatFloat(cfg.FPS, 'f', -1, 64),
		"--stream-mmap=4", "--stream-to=-",
	}
}

func probeDevice(cfg config, device string) error {
	if _, err := os.Stat(device); err != nil {
		return err
	}
	if syscall.Access(device, 4|2) != nil {
		return fmt.Errorf("no read/write access: %s", accessDescription(device))
	}
	if err := setControls(cfg, device); err != nil {
		return fmt.Errorf("controls: %w", err)
	}
	args := streamArgs(cfg, device)
	args = append(args, fmt.Sprintf("--stream-count=%d", cfg.ProbeFrames))
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "v4l2-ctl", args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	expected := cfg.Width * cfg.Height * 2 * cfg.ProbeFrames
	read, readErr := io.CopyN(io.Discard, stdout, int64(expected))
	waitErr := cmd.Wait()
	if readErr != nil {
		return fmt.Errorf("short YUYV probe: %d/%d bytes: %w; %s", read, expected, readErr, strings.TrimSpace(stderr.String()))
	}
	if waitErr != nil {
		return fmt.Errorf("capture failed: %w; %s", waitErr, strings.TrimSpace(stderr.String()))
	}
	return nil
}

func selectDevice(cfg config) (string, error) {
	candidates := cfg.ScanDevices
	if cfg.Device != "" && cfg.Device != "auto" {
		candidates = []string{cfg.Device}
	}
	var failures []string
	for _, device := range candidates {
		log.Printf("camera probe %s: %s", device, accessDescription(device))
		if err := probeDevice(cfg, device); err != nil {
			failures = append(failures, fmt.Sprintf("%s: %v", device, err))
			log.Printf("camera probe failed %s: %v", device, err)
			continue
		}
		log.Printf("camera probe passed: %s (%dx%d YUYV @ %.3f FPS)", device, cfg.Width, cfg.Height, cfg.FPS)
		return device, nil
	}
	return "", fmt.Errorf("no usable camera; %s", strings.Join(failures, " | "))
}

func newCamera(cfg config, device string) *camera {
	c := &camera{cfg: cfg, device: device, started: time.Now(), startedUnix: time.Now().UnixNano()}
	c.controls.Store(readControls(device))
	c.fatal.Store(errorBox{})
	return c
}

func (c *camera) setFatal(err error) {
	c.fatal.Store(errorBox{Err: err})
	c.mu.Lock()
	sub := c.subscriber
	c.subscriber = nil
	c.mu.Unlock()
	if sub != nil {
		select {
		case sub.errs <- err:
		default:
		}
		sub.close()
	}
}
func (c *camera) fatalError() error { return c.fatal.Load().(errorBox).Err }

func (c *camera) subscribe() (*subscription, error) {
	if err := c.fatalError(); err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.subscriber != nil {
		return nil, errClientBusy
	}
	sub := &subscription{frames: make(chan *frame, c.cfg.ClientBacklog), errs: make(chan error, 1), done: make(chan struct{})}
	c.subscriber = sub
	return sub, nil
}
func (c *camera) unsubscribe(sub *subscription) {
	c.mu.Lock()
	if c.subscriber == sub {
		c.subscriber = nil
	}
	c.mu.Unlock()
	sub.close()
}

func (c *camera) publish(f *frame) {
	c.latestID.Store(f.ID)
	c.mu.Lock()
	sub := c.subscriber
	c.mu.Unlock()
	if sub == nil {
		return
	}
	select {
	case sub.frames <- f:
	default:
		c.unsubscribe(sub)
		select {
		case sub.errs <- errors.New("client frame backlog overflow; compute node did not drain Y8 fast enough"):
		default:
		}
	}
}

func (c *camera) capture(ctx context.Context) {
	if err := setControls(c.cfg, c.device); err != nil {
		c.setFatal(err)
		return
	}
	initialControls := readControls(c.device)
	c.controls.Store(initialControls)
	if matched, reason := controlsMatch(c.cfg, initialControls); !matched {
		c.setFatal(fmt.Errorf("camera controls invalid after configuration: %s; state=%v", reason, initialControls))
		return
	}
	log.Printf("camera controls verified: %v", initialControls)
	cmd := exec.CommandContext(ctx, "v4l2-ctl", streamArgs(c.cfg, c.device)...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		c.setFatal(err)
		return
	}
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		c.setFatal(err)
		return
	}
	log.Printf("continuous capture started: device=%s %dx%d YUYV @ %.3f FPS", c.device, c.cfg.Width, c.cfg.Height, c.cfg.FPS)
	raw := make([]byte, c.cfg.Width*c.cfg.Height*2)
	lastControls := time.Now()
	failures := 0
	for {
		if _, err := io.ReadFull(stdout, raw); err != nil {
			if ctx.Err() != nil {
				break
			}
			c.setFatal(fmt.Errorf("camera stream ended: %w; %s", err, strings.TrimSpace(stderr.String())))
			_ = cmd.Process.Kill()
			return
		}
		y := make([]byte, c.cfg.Width*c.cfg.Height)
		for i := range y {
			y[i] = raw[i*2]
		}
		id := c.frameID.Add(1)
		now := time.Now()
		controls, _ := c.controls.Load().(map[string]any)
		c.publish(&frame{ID: id, UnixNS: now.UnixNano(), MonotonicNS: time.Since(c.started).Nanoseconds(), Payload: y, Controls: cloneMap(controls)})
		if c.cfg.ControlCheck > 0 && time.Since(lastControls) >= c.cfg.ControlCheck {
			lastControls = time.Now()
			current := readControls(c.device)
			c.controls.Store(current)
			matched, reason := controlsMatch(c.cfg, current)
			if matched {
				failures = 0
				continue
			}

			log.Printf("camera controls mismatch detected: %s; attempting to restore expected controls", reason)
			restoreErr := setControls(c.cfg, c.device)
			corrected := readControls(c.device)
			c.controls.Store(corrected)
			correctedMatch, correctedReason := controlsMatch(c.cfg, corrected)
			if restoreErr == nil && correctedMatch {
				log.Printf("camera controls restored: %v", corrected)
				failures = 0
				continue
			}

			failures++
			log.Printf(
				"camera controls remain invalid after restore attempt %d/%d: restore_error=%v state=%v reason=%s",
				failures, c.cfg.ControlFailures, restoreErr, corrected, correctedReason,
			)
			if failures >= c.cfg.ControlFailures {
				c.setFatal(fmt.Errorf(
					"camera controls cannot be restored after %d consecutive checks: %s; state=%v; restore_error=%v",
					failures, correctedReason, corrected, restoreErr,
				))
				_ = cmd.Process.Kill()
				return
			}
		}
	}
	_ = cmd.Wait()
}

func cloneMap(input map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range input {
		out[key] = value
	}
	return out
}

func writeMessage(writer io.Writer, header map[string]any, payload []byte) error {
	body, err := json.Marshal(header)
	if err != nil {
		return err
	}
	if len(body) == 0 || len(body) > maxHeaderBytes || len(payload) > maxPayloadBytes {
		return errors.New("protocol message too large")
	}
	prefix := make([]byte, 16)
	copy(prefix[:8], []byte(protocolMagic))
	binary.BigEndian.PutUint32(prefix[8:12], uint32(len(body)))
	binary.BigEndian.PutUint32(prefix[12:16], uint32(len(payload)))
	if _, err := writer.Write(prefix); err != nil {
		return err
	}
	if _, err := writer.Write(body); err != nil {
		return err
	}
	if len(payload) > 0 {
		_, err = writer.Write(payload)
	}
	return err
}

func readExact(reader io.Reader, size int) ([]byte, error) {
	output := make([]byte, size)
	_, err := io.ReadFull(reader, output)
	return output, err
}
func readMessage(reader io.Reader) (map[string]any, []byte, error) {
	prefix, err := readExact(reader, 16)
	if err != nil {
		return nil, nil, err
	}
	if string(prefix[:8]) != protocolMagic {
		return nil, nil, errors.New("bad protocol magic")
	}
	hs, ps := int(binary.BigEndian.Uint32(prefix[8:12])), int(binary.BigEndian.Uint32(prefix[12:16]))
	if hs <= 0 || hs > maxHeaderBytes || ps < 0 || ps > maxPayloadBytes {
		return nil, nil, errors.New("invalid protocol sizes")
	}
	body, err := readExact(reader, hs)
	if err != nil {
		return nil, nil, err
	}
	header := map[string]any{}
	if err := json.Unmarshal(body, &header); err != nil {
		return nil, nil, err
	}
	payload, err := readExact(reader, ps)
	return header, payload, err
}

func certificateCN(conn *tls.Conn) string {
	state := conn.ConnectionState()
	if len(state.PeerCertificates) == 0 {
		return ""
	}
	return state.PeerCertificates[0].Subject.CommonName
}

func tlsConfig(cfg config) (*tls.Config, error) {
	cert, err := tls.LoadX509KeyPair(cfg.TLSCert, cfg.TLSKey)
	if err != nil {
		return nil, err
	}
	caBytes, err := os.ReadFile(cfg.TLSCA)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caBytes) {
		return nil, errors.New("cannot parse TLS CA")
	}
	return &tls.Config{Certificates: []tls.Certificate{cert}, ClientCAs: pool, ClientAuth: tls.RequireAndVerifyClientCert, MinVersion: tls.VersionTLS13}, nil
}

func frameHeader(cfg config, f *frame) map[string]any {
	digest := sha256.Sum256(f.Payload)
	return map[string]any{"type": "frame", "version": protocolVersion, "source_id": cfg.SourceID, "frame_id": f.ID, "captured_unix_ns": f.UnixNS, "captured_monotonic_ns": f.MonotonicNS, "width": cfg.Width, "height": cfg.Height, "pixel_format": "Y8", "payload_bytes": len(f.Payload), "sha256": hex.EncodeToString(digest[:]), "controls": f.Controls, "control_value_schema": "integer-v1", "dropped_frames": 0}
}

func writeAgentError(conn *tls.Conn, cfg config, sessionID uint64, code, reason string, retryable bool) {
	_ = conn.SetWriteDeadline(time.Now().Add(cfg.SocketTimeout))
	header := map[string]any{
		"type":       "error",
		"version":    protocolVersion,
		"session_id": sessionID,
		"code":       code,
		"reason":     reason,
		"retryable":  retryable,
	}
	if err := writeMessage(conn, header, nil); err != nil {
		log.Printf("cannot send agent error session=%d code=%s: %v", sessionID, code, err)
	}
}

func serveClient(ctx context.Context, cfg config, c *camera, raw net.Conn, sessionID uint64) {
	defer raw.Close()
	conn, ok := raw.(*tls.Conn)
	if !ok {
		return
	}
	if err := conn.HandshakeContext(ctx); err != nil {
		log.Printf("TLS handshake failed: %v", err)
		return
	}
	cn := certificateCN(conn)
	if cfg.AllowedClientCN != "" && cn != cfg.AllowedClientCN {
		log.Printf("rejected client CN=%q expected=%q", cn, cfg.AllowedClientCN)
		writeAgentError(conn, cfg, sessionID, "client-not-authorized", "client certificate CN is not authorized", false)
		return
	}
	sub, err := c.subscribe()
	if err != nil {
		code, retryable := "camera-unavailable", false
		if errors.Is(err, errClientBusy) {
			code, retryable = "source-busy", true
		}
		log.Printf("client rejected session=%d code=%s: %v", sessionID, code, err)
		writeAgentError(conn, cfg, sessionID, code, err.Error(), retryable)
		return
	}
	defer c.unsubscribe(sub)
	warmup := time.Since(c.started).Seconds()
	controls, _ := c.controls.Load().(map[string]any)
	hello := map[string]any{
		"type": "hello", "version": protocolVersion, "app_version": appVersion, "source_id": cfg.SourceID, "session_id": sessionID,
		"device": c.device, "mode": map[string]any{"fourcc": "YUYV", "width": cfg.Width, "height": cfg.Height, "reported_fps": cfg.FPS, "controls": controls, "control_value_schema": "integer-v1"},
		"control_value_schema": "integer-v1",
		"payload":              "uncompressed direct Y bytes extracted from YUYV", "agent_started_unix_ns": c.startedUnix,
		"capture_started_unix_ns": c.startedUnix, "agent_uptime_seconds": warmup, "source_warmup_seconds": warmup,
		"latest_global_frame_id": c.latestID.Load(), "continuous_capture": true, "idle_buffering": false,
	}
	_ = conn.SetWriteDeadline(time.Now().Add(cfg.SocketTimeout))
	if err := writeMessage(conn, hello, nil); err != nil {
		log.Printf("hello write failed: %v", err)
		return
	}
	controlCh := make(chan map[string]any, 1)
	controlErr := make(chan error, 1)
	go func() {
		for {
			header, payload, err := readMessage(conn)
			if err != nil {
				controlErr <- err
				return
			}
			if len(payload) != 0 {
				controlErr <- errors.New("control payload must be empty")
				return
			}
			controlCh <- header
		}
	}()
	log.Printf("authenticated client CN=%q session=%d warmup=%.3fs", cn, sessionID, warmup)
	for {
		select {
		case <-ctx.Done():
			return
		case err := <-controlErr:
			if !errors.Is(err, io.EOF) {
				log.Printf("client control ended session=%d: %v", sessionID, err)
			}
			return
		case header := <-controlCh:
			if fmt.Sprint(header["type"]) == "close" {
				_ = conn.SetWriteDeadline(time.Now().Add(cfg.SocketTimeout))
				_ = writeMessage(conn, map[string]any{"type": "close-ack", "version": protocolVersion, "session_id": sessionID, "reason": header["reason"]}, nil)
				return
			}
			log.Printf("unsupported client control session=%d: %v", sessionID, header)
			writeAgentError(conn, cfg, sessionID, "unsupported-control", "unsupported client control message", false)
			return
		case err := <-sub.errs:
			log.Printf("session=%d stopped: %v", sessionID, err)
			writeAgentError(conn, cfg, sessionID, "stream-stopped", err.Error(), false)
			return
		case f := <-sub.frames:
			_ = conn.SetWriteDeadline(time.Now().Add(cfg.SocketTimeout))
			if err := writeMessage(conn, frameHeader(cfg, f), f.Payload); err != nil {
				log.Printf("frame write failed session=%d: %v", sessionID, err)
				return
			}
		}
	}
}

func main() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds | log.LUTC)
	cfg := parseConfig()
	if cfg.ShowVersion {
		fmt.Println(appVersion)
		return
	}
	if _, err := exec.LookPath("v4l2-ctl"); err != nil {
		log.Fatal("v4l2-ctl is required (package v4l-utils)")
	}
	device, err := selectDevice(cfg)
	if err != nil {
		log.Fatal(err)
	}
	if cfg.Probe {
		fmt.Printf("OK %s %dx%d YUYV @ %.3f FPS\n", device, cfg.Width, cfg.Height, cfg.FPS)
		return
	}
	for _, path := range []string{cfg.TLSCA, cfg.TLSCert, cfg.TLSKey} {
		if info, err := os.Stat(path); err != nil || info.IsDir() {
			log.Fatalf("TLS file unavailable: %s", path)
		}
	}
	tlsCfg, err := tlsConfig(cfg)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	c := newCamera(cfg, device)
	go c.capture(ctx)
	listener, err := tls.Listen("tcp", net.JoinHostPort(cfg.ListenHost, strconv.Itoa(cfg.ListenPort)), tlsCfg)
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()
	log.Printf("starting %s", appVersion)
	log.Printf("mTLS Y8 endpoint: %s:%d device=%s continuous capture active", cfg.ListenHost, cfg.ListenPort, device)
	go func() { <-ctx.Done(); _ = listener.Close() }()
	var session atomic.Uint64
	for {
		raw, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			log.Printf("accept failed: %v", err)
			continue
		}
		id := session.Add(1)
		go serveClient(ctx, cfg, c, raw, id)
	}
	if err := c.fatalError(); err != nil {
		log.Fatalf("camera capture failed: %v", err)
	}
	log.Printf("agent stopped")
}
