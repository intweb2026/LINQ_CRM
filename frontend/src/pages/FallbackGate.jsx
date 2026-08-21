import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * /170405 → silently redirects to /loginpage with a gate flag.
 * Without the flag, /loginpage refuses to render and sends the user
 * back to /login. This prevents direct-URL access to the fallback form.
 */
export default function FallbackGate() {
  const nav = useNavigate();

  useEffect(() => {
    nav('/loginpage', { state: { gate: true }, replace: true });
  }, [nav]);

  return null;
}
