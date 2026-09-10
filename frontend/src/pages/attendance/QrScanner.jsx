import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon } from '../../lib/icons';

/**
 * The camera, and one decoded badge per badge presented.
 *
 * TWO DECODERS, one preference. Where the browser has BarcodeDetector it is
 * used: it is native, off the main thread, and costs nothing to keep running.
 * Safari and every browser on iOS have no such thing, so a JS decoder is
 * lazy-import()ed instead — lazily, because it is the larger half of this chunk
 * and the majority of the machines that open this page never need it. The
 * decision is resolved ONCE per mount into a ref, not per frame.
 *
 * THE CAMERA IS STOPPED IN THE EFFECT TEARDOWN. A getUserMedia stream keeps the
 * device light on until its tracks are stopped, so leaving on navigation with
 * the light still burning is the bug this guards. It also means PAUSE IS AN
 * UNMOUNT: the parent renders this component or it does not, and a flag that
 * only stopped the decode loop would leave the camera live behind a stopped
 * preview.
 *
 * ONE BADGE PRESENTATION IS ONE REQUEST. The same payload is suppressed for
 * REPEAT_MS, which is what lets the camera keep running between attendees —
 * without it a badge held in front of the lens for two seconds would post
 * dozens of times, and the alternative, tearing the camera down after every
 * scan, means a fresh permission-shaped delay per person in the queue.
 *
 * THE JS DECODER IS THROTTLED AND DOWNSCALED, deliberately. It is a synchronous
 * main-thread decode over a full frame; run at the display's own rate it stutters
 * the preview badly enough that aiming becomes guesswork. 8fps over a ~640px
 * frame decodes a badge as fast as anybody can present one and leaves the
 * preview smooth.
 */

// The same code is ignored for this long, so one badge held up is one request.
const REPEAT_MS = 2500;
// The JS decoder only. BarcodeDetector runs on its own schedule and is cheap.
const DECODE_INTERVAL_MS = 125;
// The longest edge the JS decoder is handed. Decode cost is per pixel.
const MAX_FRAME_PX = 640;

/**
 * Camera constraints, WIDEST LAST, tried in order.
 *
 * `facingMode: { ideal: 'environment' }` and never the bare string. A bare
 * `facingMode: 'environment'` is a REQUIRED constraint, so a laptop with only a
 * front camera or a desk with a USB webcam throws OverconstrainedError — which
 * then reports as "no camera found" on a machine that has one. `ideal` asks for
 * the back camera and accepts whatever there is.
 *
 * WIDTH ONLY, AND NO HEIGHT. Asking for 1280x720 together pins an aspect ratio
 * as well as a size, and a 4:3 sensor asked for 16:9 is commonly satisfied by
 * CROPPING the sensor — a real optical-looking zoom, applied before any CSS
 * gets involved, and the other half of why this preview came out magnified.
 * Naming only the width leaves the shape of the frame to the device, which is
 * its own default view, and the CSS then fits that shape without cropping it
 * either.
 *
 * The width is still asked for, because it is the decoder's resolution and a
 * 320px frame does not read a badge from arm's length. `ideal` means the device
 * gives what it has rather than refusing.
 */
const LADDER = [
  { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } } },
  { video: { width: { ideal: 1280 } } },
  { video: true },
];

/** What went wrong, in words the person holding the phone can act on. */
async function describe(err) {
  const name = err?.name || '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return {
      title: 'Camera permission was refused',
      body: 'Allow camera access for this site in the browser’s address bar, then try again.',
    };
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return {
      title: 'The camera is in use by another app',
      body: 'Close anything else using the camera — a video call, another tab — then try again.',
    };
  }
  // NotFoundError and OverconstrainedError are NOT the same thing, and telling
  // somebody there is no camera when there is one sends them looking for
  // hardware. enumerateDevices() settles which it is.
  let seen = 0;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    seen = devices.filter((d) => d.kind === 'videoinput').length;
  } catch { /* the count is a nicety; the message below works without it */ }
  if (seen > 0) {
    return {
      title: 'A camera was detected but would not start',
      body: 'It may not support the requested video mode. Try again, or use Manual check-in.',
    };
  }
  return {
    title: 'No camera found',
    body: 'This device has no camera the browser can see. Use Manual check-in instead.',
  };
}

/** getUserMedia is only exposed on a secure origin, which is its own message. */
function insecure() {
  return !window.isSecureContext || !navigator.mediaDevices?.getUserMedia;
}

export default function QrScanner({ onCode, busy }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const decoderRef = useRef(null);      // resolved once per mount
  const lastRef = useRef({ code: '', at: 0 });
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);   // the Try again button
  const [ready, setReady] = useState(false);

  // Held in a ref so the decode loop never re-subscribes when the parent
  // re-renders with a new callback identity.
  const onCodeRef = useRef(onCode);
  onCodeRef.current = onCode;
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const emit = useCallback((code) => {
    if (!code) return;
    const now = Date.now();
    const last = lastRef.current;
    if (code === last.code && now - last.at < REPEAT_MS) return;
    lastRef.current = { code, at: now };
    onCodeRef.current?.(code);
  }, []);

  useEffect(() => {
    if (insecure()) {
      setError({
        title: 'The camera needs a secure connection',
        body: 'Browsers only allow camera access over HTTPS, or on localhost. '
            + 'Open the CRM on its https:// address, or use Manual check-in.',
      });
      return undefined;
    }

    let stream = null;
    let raf = 0;
    let timer = 0;
    let live = true;
    // Captured here rather than read off the ref in the teardown: by the time
    // cleanup runs React may already have swapped or unmounted the node, and
    // this is the element whose srcObject has to be released.
    const video = videoRef.current;

    async function decoder() {
      if (decoderRef.current) return decoderRef.current;
      if ('BarcodeDetector' in window) {
        try {
          const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
          decoderRef.current = { kind: 'native', detector };
          return decoderRef.current;
        } catch { /* fall through to the JS decoder */ }
      }
      const mod = await import('jsqr');
      decoderRef.current = { kind: 'js', decode: mod.default || mod };
      return decoderRef.current;
    }

    async function open() {
      let lastErr = null;
      for (const constraints of LADDER) {
        try {
          return await navigator.mediaDevices.getUserMedia(constraints);
        } catch (err) {
          lastErr = err;
          // A refusal is a refusal. Walking the rest of the ladder only
          // re-prompts, or re-fails, for a decision already made.
          if (err?.name === 'NotAllowedError' || err?.name === 'SecurityError') break;
        }
      }
      throw lastErr;
    }

    (async () => {
      try {
        stream = await open();
        if (!live) return;
        if (!video) return;
        video.srcObject = stream;
        await video.play().catch(() => {});
        if (!live) return;
        setReady(true);

        const engine = await decoder();
        if (!live) return;

        if (engine.kind === 'native') {
          const tick = async () => {
            if (!live) return;
            if (!busyRef.current && videoRef.current?.readyState >= 2) {
              try {
                const found = await engine.detector.detect(videoRef.current);
                if (found?.length) emit(found[0].rawValue);
              } catch { /* a dropped frame is not an error worth showing */ }
            }
            if (live) raf = requestAnimationFrame(tick);
          };
          raf = requestAnimationFrame(tick);
        } else {
          timer = window.setInterval(() => {
            const v = videoRef.current;
            const canvas = canvasRef.current;
            if (busyRef.current || !live || !v || !canvas || v.readyState < 2) return;
            const scale = Math.min(1, MAX_FRAME_PX / Math.max(v.videoWidth, v.videoHeight || 1));
            const w = Math.max(1, Math.round(v.videoWidth * scale));
            const h = Math.max(1, Math.round(v.videoHeight * scale));
            if (canvas.width !== w || canvas.height !== h) {
              canvas.width = w;
              canvas.height = h;
            }
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.drawImage(v, 0, 0, w, h);
            const { data } = ctx.getImageData(0, 0, w, h);
            const hit = engine.decode(data, w, h, { inversionAttempts: 'dontInvert' });
            if (hit?.data) emit(hit.data);
          }, DECODE_INTERVAL_MS);
        }
      } catch (err) {
        if (live) setError(await describe(err));
      }
    })();

    return () => {
      live = false;
      cancelAnimationFrame(raf);
      clearInterval(timer);
      // STOP THE TRACKS, or the camera light stays on after this unmounts.
      stream?.getTracks().forEach((track) => track.stop());
      if (video) video.srcObject = null;
      setReady(false);
    };
  }, [attempt, emit]);

  if (error) {
    return (
      <div className="att-camera-error">
        <Icon name="warn" size={22} />
        <h4>{error.title}</h4>
        <p>{error.body}</p>
        <button className="btn" onClick={() => { setError(null); setAttempt((n) => n + 1); }}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="att-camera">
      {/* playsInline AND muted, or iOS takes the video fullscreen the moment it
          plays and the page underneath is gone. */}
      <video ref={videoRef} playsInline muted autoPlay className="att-video" />
      <canvas ref={canvasRef} className="att-frame-buffer" aria-hidden="true" />
      <div className="att-reticle" aria-hidden="true" />
      <p className="att-camera-hint">
        {ready ? 'Hold a badge inside the frame.' : 'Starting the camera…'}
      </p>
    </div>
  );
}
