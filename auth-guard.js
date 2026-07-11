/**
 * auth-guard.js
 * Inclure dans toutes les pages protégées.
 * Vérifie /auth/me et redirige vers /login si non authentifié.
 * Injecte le badge utilisateur dans #userBadge et l'indicateur moteur IA.
 *
 * Stratégie : affichage immédiat depuis sessionStorage si disponible,
 * puis vérification réseau en arrière-plan pour détecter déconnexion.
 */
(async () => {
  function escHtml(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function injectBadge(user) {
    const badge = document.getElementById('userBadge');
    if (!badge) return;
    badge.innerHTML = `
      <span id="engineDot" style="
        display:none;align-items:center;gap:6px;
        font-family:var(--serif-engraved,Cinzel,serif);font-size:0.62rem;
        letter-spacing:0.08em;color:#e2b96f;
        margin-right:12px;
        background:rgba(226,185,111,0.08);
        border:1px solid rgba(226,185,111,0.25);
        border-radius:20px;padding:3px 10px 3px 8px;
      ">
        <span id="engineDotCircle" style="
          width:8px;height:8px;border-radius:50%;flex-shrink:0;
          background:#e2b96f;
        "></span>
        <span id="engineDotLabel">Moteur IA…</span>
      </span>
      <style>
        @keyframes _mm_pulse {
          0%   { box-shadow: 0 0 0 0 rgba(226,185,111,0.5); }
          70%  { box-shadow: 0 0 0 5px rgba(226,185,111,0); }
          100% { box-shadow: 0 0 0 0 rgba(226,185,111,0); }
        }
        #engineDotCircle.loading { animation: _mm_pulse 1.6s ease-out infinite; }
      </style>
      <span style="font-family:var(--serif-engraved,Cinzel,serif);font-size:0.68rem;letter-spacing:0.08em;color:var(--parch-500,#9c8e79)">
        ${escHtml(user.display_name || user.email)}
        ${user.role === 'admin' ? ' · <a href="/admin" style="color:var(--gold,#c9a45c);text-decoration:none;">Admin</a>' : ''}
      </span>
      <button onclick="logout()" style="
        padding:4px 10px;border-radius:4px;border:1px solid rgba(201,164,92,0.3);
        background:transparent;color:var(--parch-500,#9c8e79);
        font-family:var(--serif-engraved,Cinzel,serif);font-size:0.62rem;letter-spacing:0.08em;
        cursor:pointer;margin-left:8px;
      ">Déconnexion</button>`;
  }

  // ── Affichage immédiat depuis le cache si disponible ──────────────────────
  const CACHE_KEY = '_mm_user';
  let cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY)); } catch {}
  if (cached) {
    injectBadge(cached);
  }

  // ── Vérification réseau (toujours, même si cache présent) ─────────────────
  try {
    const res  = await fetch('/auth/me');
    const data = await res.json();
    if (!data.authenticated) {
      sessionStorage.removeItem(CACHE_KEY);
      window.location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
      return;
    }
    // Mettre à jour le cache et le badge si pas encore affiché (ou données changées)
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(data.user));
    if (!cached) {
      injectBadge(data.user);
    }
  } catch {
    // Si réseau échoue mais cache présent → laisser l'utilisateur voir la page
    // Si pas de cache → rediriger vers login
    if (!cached) {
      window.location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
    }
  }

  window.logout = async () => {
    sessionStorage.removeItem(CACHE_KEY);
    await fetch('/auth/logout', { method: 'POST' });
    window.location.href = '/login';
  };


})();
